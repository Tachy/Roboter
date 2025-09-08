"""
Hauptmodul für die Robotersteuerung.
"""

import threading
import time
import logging
import os
import cv2
import numpy as np
from pathlib import Path
from . import (
    config,
    camera,
    serial_manager,
    yolo_detector,
    udp_server,
    status_ws_server,
    status_bus,
)
from .calibration import CalibrationSession
from . import geometry
import subprocess
import shutil

# Logger einrichten
logger = logging.getLogger("robot_control")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

# Persistenz-Helfer (vor Nutzung definieren)
# Basisverzeichnis des Projekts (../ vom src-Ordner)
_BASE_DIR = Path(__file__).resolve().parent.parent
_STATE_DIR = _BASE_DIR / "state"
_MODE_FILE = _STATE_DIR / "mode.txt"


def _persist_mode(mode: str) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _MODE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(mode.strip().upper())
        os.replace(tmp, _MODE_FILE)
    except Exception as e:
        raise


def _load_persisted_mode():
    try:
        if not _MODE_FILE.exists():
            return None
        with open(_MODE_FILE, "r", encoding="utf-8") as f:
            val = f.read().strip().upper()
        return val if val in {"AUTO", "MANUAL", "DISTORTION", "EXTRINSIK"} else None
    except Exception:
        return None


class RobotControl:
    def __init__(self):
        self.mode = "AUTO"
        self.mode_lock = threading.Lock()
        self.serial = serial_manager.SerialManager()
        self.last_joystick = {"x": 0, "y": 0}
        self.last_joystick_lock = threading.Lock()
        self.calib_session = None
        msg = "START"
        logger.info(f"-> Arduino: {msg}")
        self.send_command(msg)
        # Persistierten Modus laden und anwenden (falls vorhanden und gültig)
        persisted = _load_persisted_mode()
        if (
            persisted in {"AUTO", "MANUAL", "DISTORTION", "EXTRINSIK"}
            and persisted != self.mode
        ):
            logger.info(f"Lade letzten Modus: {persisted}")
            self.set_mode(persisted)
        elif persisted is None:
            # Erster Start: Standardmodus persistieren
            try:
                _persist_mode(self.mode)
            except Exception:
                pass

    def get_mode(self):
        """Gibt den aktuellen Modus zurück."""
        with self.mode_lock:
            return self.mode

    def set_mode(self, new_mode):
        """Setzt den Betriebsmodus (AUTO, MANUAL, DISTORTION, EXTRINSIK)."""
        with self.mode_lock:
            if new_mode == self.mode:
                return
            self.mode = new_mode
            msg = f"MODE:{self.mode}"
            logger.info(f"-> Arduino: {msg}")
            self.send_command(msg)
            # Modus persistent speichern
            try:
                _persist_mode(self.mode)
            except Exception as e:
                logger.warning(f"Modus konnte nicht persistiert werden: {e}")
            if self.calib_session is not None:
                try:
                    self.calib_session.stop()
                except Exception as e:
                    pass
                self.calib_session = None
            # Beim Wechsel in EXTRINSIK: Bannerbild in Vorschau
            try:
                if self.mode == "EXTRINSIK" and camera.is_camera_started():
                    arr = camera.picam2.capture_array()
                    if arr is not None:
                        if arr.ndim == 3 and arr.shape[2] == 4:
                            bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
                        elif arr.ndim == 3 and arr.shape[2] == 3:
                            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                        else:
                            bgr = arr
                        h, w = bgr.shape[:2]
                        target_w = 320
                        scale = target_w / float(w)
                        preview = cv2.resize(
                            bgr,
                            (target_w, max(1, int(h * scale))),
                            interpolation=cv2.INTER_AREA,
                        )
                        text = "Extrinsik: Klick zum Starten"
                        try:
                            status_bus.set_message(text)
                        except Exception:
                            pass
                        camera._encode_and_store_last_capture(preview, quality=85)
                # Beim Wechsel in DISTORTION: Erste Phase ohne Klick starten und Status setzen
                if self.mode == "DISTORTION":
                    # Kalibriersession anlegen
                    try:
                        self.calib_session = CalibrationSession(target_snapshots=20)
                    except Exception:
                        self.calib_session = None
                    # Statusmeldung sofort anzeigen
                    try:
                        status_bus.set_message("Kalibrierung: Klick zum Starten")
                    except Exception:
                        pass
                    # Optional: aktuelle Vorschau ohne Overlay speichern
                    try:
                        if camera.is_camera_started():
                            arr = camera.picam2.capture_array()
                            if arr is not None:
                                if arr.ndim == 3 and arr.shape[2] == 4:
                                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
                                elif arr.ndim == 3 and arr.shape[2] == 3:
                                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                                else:
                                    bgr = arr
                                h, w = bgr.shape[:2]
                                target_w = 320
                                scale = target_w / float(w)
                                preview = cv2.resize(
                                    bgr,
                                    (target_w, max(1, int(h * scale))),
                                    interpolation=cv2.INTER_AREA,
                                )
                                camera._encode_and_store_last_capture(
                                    preview, quality=85
                                )
                    except Exception:
                        pass
            except Exception:
                pass

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.send_command(command)

    def process_auto_mode(self):
        """Verarbeitet die automatische Steuerung."""
        line = self.serial.read_line()
        if line == "GETXY":
            logger.info("<- Arduino: GETXY")

            # Entzerrtes Einzelbild aufnehmen und verarbeiten (immer undistortiert für GETXY)
            filename = "frame.jpg"
            camera.capture_image(filename, undistort=True)
            img_path = filename

            coords = yolo_detector.process_image(img_path)
            # Falls Welttransformation verfügbar: Pixel -> Welt (mm)
            use_world = False
            try:
                use_world = (
                    getattr(config, "WORLD_TRANSFORM_ACTIVE", True)
                    and geometry.is_world_transform_ready()
                )
            except Exception:
                use_world = geometry.is_world_transform_ready()
            for x, y in coords:
                if use_world:
                    try:
                        w = geometry.pixel_to_world(float(x), float(y))
                        if w is not None:
                            xw, yw = w
                            msg = f"XY:{xw:.1f},{yw:.1f}"
                        else:
                            msg = f"XY:{x:.1f},{y:.1f}"
                    except Exception:
                        msg = f"XY:{x:.1f},{y:.1f}"
                else:
                    msg = f"XY:{x:.1f},{y:.1f}"
                logger.info(f"-> Arduino: {msg}")
                self.send_command(msg)
                time.sleep(0.05)

            # Abschlussmeldung
            self.send_command("DONE")
            logger.info("-> Arduino: DONE")

    def handle_command(self, command):
        """Verarbeitet ein empfangenes Kommando."""
        # Extrahiere Joystick-Daten
        if command.startswith("JOYSTICK:"):
            try:
                parts = command[len("JOYSTICK:") :].split(",")
                x = y = None
                for p in parts:
                    if p.startswith("X="):
                        x = int(p[2:])
                    elif p.startswith("Y="):
                        y = int(p[2:])
                if x is not None and y is not None:
                    with self.last_joystick_lock:
                        self.last_joystick = {"x": x, "y": y}
            except Exception:
                pass
        mode = self.get_mode()
        if mode == "MANUAL":
            if ",B=1" in command:
                command = command.replace(",B=1", "")
            self.send_command(command)
            return True
        elif mode == "DISTORTION":
            # DISTORTION: zur Sicherheit an Arduino wie AUTO (d. h. keine direkten Joystick-Kommandos),
            # Button-Handling wird im UDP-Server ausgelöst
            return True
        elif mode == "EXTRINSIK":
            # Keine direkten Joystick-Kommandos im EXTRINSIK-Modus; Button handled separat
            return True
        return False

    def calibration_button_pressed(self):
        """Wird aufgerufen, wenn im DISTORTION-Modus der Joystick-Button gedrückt wurde."""
        if self.get_mode() != "DISTORTION":
            return
        # Wenn Kamera nicht läuft (kein Stream aktiv), Klick ignorieren
        from . import camera

        if not camera.is_camera_started():
            # Optional: Logging
            import logging

            logging.info("[Calib] Klick ignoriert: Kamera/Stream nicht aktiv.")
            return
        if self.calib_session is None:
            # Erster Klick: Kalibriervorgang starten, aber noch kein Snapshot
            self.calib_session = CalibrationSession(target_snapshots=20)
            logger.info(
                "[Calib] Kalibriervorgang gestartet. Nächster Klick nimmt das erste Bild auf."
            )
            # Bannerbild "Klick zum Starten" als letzte Aufnahme veröffentlichen (Größe wie "Aufnahme X/Y")
            try:
                arr = camera.picam2.capture_array()
                if arr is not None:
                    if arr.ndim == 3 and arr.shape[2] == 4:
                        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
                    elif arr.ndim == 3 and arr.shape[2] == 3:
                        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                    else:
                        bgr = arr
                    h, w = bgr.shape[:2]
                    target_w = 320
                    scale = target_w / float(w)
                    preview = cv2.resize(
                        bgr,
                        (target_w, max(1, int(h * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                    text = "Kalibrierung: Klick zum Starten"
                    try:
                        status_bus.set_message(text)
                    except Exception:
                        pass
                    camera._encode_and_store_last_capture(preview, quality=85)
            except Exception:
                pass
            finally:
                pass
            return
        # Ab hier: Session existiert -> Snapshots sammeln
        ok, counts = self.calib_session.capture_snapshot()
        if not ok:
            return
        logger.info(
            f"[Calib] Snapshot {self.calib_session.snapshots}/{self.calib_session.target} (Marker {counts[0]}, Charuco {counts[1]})"
        )
        if self.calib_session.snapshots >= self.calib_session.target:
            try:
                out_file, err = self.calib_session.finalize()
                logger.info(f"[Calib] gespeichert: {out_file} (reproj_err={err:.4f})")
                # Abschlussbanner zeigen (Größe wie "Aufnahme X/Y")
                try:
                    arr = camera.picam2.capture_array()
                    if arr is not None:
                        if arr.ndim == 3 and arr.shape[2] == 4:
                            bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
                        elif arr.ndim == 3 and arr.shape[2] == 3:
                            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                        else:
                            bgr = arr
                        h, w = bgr.shape[:2]
                        target_w = 320
                        scale = target_w / float(w)
                        preview = cv2.resize(
                            bgr,
                            (target_w, max(1, int(h * scale))),
                            interpolation=cv2.INTER_AREA,
                        )
                        text2 = "Kalibrierung abgeschlossen"
                        try:
                            status_bus.set_message(text2)
                        except Exception:
                            pass
                        camera._encode_and_store_last_capture(preview, quality=85)
                except Exception:
                    pass
                finally:
                    pass
            except Exception as e:
                logger.error(f"[Calib] Fehler bei Finalisierung: {e}")
            finally:
                try:
                    self.calib_session.stop()
                except Exception:
                    pass
                self.calib_session = None
                # Nach Abschluss: keine Software-Overlay/Undistortion im Stream
                logger.info(
                    "[Calib] abgeschlossen. Hardware-Stream bleibt roh; Kalibrierdaten werden für Offscreen-Verarbeitung genutzt."
                )

    def extrinsic_button_pressed(self):
        """One-Shot-Extrinsik: im EXTRINSIK-Modus genau ein Bild auswerten und R,t speichern."""
        if self.get_mode() != "EXTRINSIK":
            return
        if not camera.is_camera_started():
            logging.info("[Extr] Klick ignoriert: Kamera/Stream nicht aktiv.")
            return
        # Lade Intrinsik (K,D,newK) aus Kalibrierungsdatei
        try:
            calib_path = Path("./calibration/cam_calib_charuco.npz")
            if not calib_path.exists():
                raise FileNotFoundError("Kein cam_calib_charuco.npz vorhanden.")
            d = np.load(str(calib_path), allow_pickle=True)
            K = d["K"].astype(float)
            D = d["D"].astype(float)
            newK = d.get("newK")
            if newK is not None:
                newK = newK.astype(float)
        except Exception:
            # Fehlerbanner: keine K/D
            try:
                arr = camera.picam2.capture_array()
                if arr is not None:
                    if arr.ndim == 3 and arr.shape[2] == 4:
                        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
                    elif arr.ndim == 3 and arr.shape[2] == 3:
                        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                    else:
                        bgr = arr
                    h, w = bgr.shape[:2]
                    target_w = 320
                    scale = target_w / float(w)
                    preview = cv2.resize(
                        bgr,
                        (target_w, max(1, int(h * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                    text3 = "Extrinsik: Keine K/D gefunden"
                    try:
                        status_bus.set_message(text3)
                    except Exception:
                        pass
                    camera._encode_and_store_last_capture(preview, quality=85)
            except Exception:
                pass
            return

        # Bild holen
        try:
            arr = camera.picam2.capture_array()
            if arr is None:
                raise RuntimeError("Kein Kamerabild verfügbar.")
            if arr.ndim == 3 and arr.shape[2] == 4:
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            elif arr.ndim == 3 and arr.shape[2] == 3:
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            else:
                bgr = arr
        except Exception:
            return

        # Extrinsik schätzen und speichern via geometry
        ok, draw, text = geometry.compute_and_save_extrinsics_from_charuco(
            bgr, K, D, newK=newK
        )

        # Preview/Banner schreiben
        try:
            h, w = draw.shape[:2]
            target_w = 320
            scale = target_w / float(w)
            preview = cv2.resize(
                draw, (target_w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA
            )
            color = (0, 255, 0) if ok else (0, 0, 255)
            try:
                status_bus.set_message(text)
            except Exception:
                pass
            camera._encode_and_store_last_capture(preview, quality=85)
        except Exception:
            pass

    def get_joystick_status(self):
        with self.last_joystick_lock:
            return dict(self.last_joystick)

    def run(self):
        """Hauptschleife der Robotersteuerung."""
        try:
            # Callbacks registrieren
            udp_server.on_mode_change = self.set_mode
            udp_server.on_command = self.handle_command

            logger.info("Starte HTTP-Server...")
            threading.Thread(target=camera.start_http_server, daemon=True).start()

            logger.info("Starte UDP-Steuerkanal...")
            threading.Thread(
                target=udp_server.start_control_server, daemon=True
            ).start()

            logger.info("Starte UDP-Joystick-Server...")
            threading.Thread(
                target=udp_server.start_joystick_server, daemon=True
            ).start()

            # Starte Heartbeat-Listener für Videostream (UDP)
            udp_server.start_heartbeat_monitor()
            # Starte WebSocket-Status-Server (im Hintergrund)
            threading.Thread(
                target=status_ws_server.start_status_ws_server, daemon=True
            ).start()

            logger.info("Starte Hauptloop...")
            while True:
                # Check for firmware uploads in MANUAL mode
                try:
                    if self.get_mode() == "MANUAL":
                        upload_dir = Path(config.UPLOAD_DIR)
                        if upload_dir.exists():
                            for p in upload_dir.iterdir():
                                if p.suffix.lower() == ".hex":
                                    logger.info(f"Gefundene Firmware: {p}")
                                    # flash file p with avrdude
                                    self._flash_hex_to_mega(p)
                                    break
                except Exception as e:
                    logger.error(f"Fehler beim Scan des Upload-Verzeichnisses: {e}")

                if self.get_mode() == "AUTO":
                    self.process_auto_mode()
                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Beendet.")
        finally:
            self.serial.close()
            if camera.stream_active:
                camera.stop_stream()

    def _flash_hex_to_mega(self, hexpath: Path) -> None:
        """Flash the given .hex to the Mega using avrdude on config.SERIAL_PORT.

        Procedure:
        - Stop serial reader, close serial port
        - Run avrdude (non-blocking call) and wait
        - Move .hex to .uploaded or .failed
        - Reopen serial connection
        """
        logger.info(f"Starte Flash auf MEGA mit {hexpath}")
        try:
            # Close serial manager to release /dev/serial0
            try:
                self.serial.close()
            except Exception:
                pass

            # Optional: toggle RESET via GPIO so Mega bootloader accepts upload
            try:
                if getattr(config, "FW_RESET_GPIO", None) is not None:
                    gpio_pin = int(config.FW_RESET_GPIO)
                    logger.info(f"Versuche Mega-Reset via GPIO {gpio_pin}")
                    try:
                        import RPi.GPIO as GPIO

                        GPIO.setmode(GPIO.BCM)
                        GPIO.setup(gpio_pin, GPIO.OUT, initial=GPIO.HIGH)
                        # Reset aktiv LOW: pulse LOW briefly
                        GPIO.output(gpio_pin, GPIO.LOW)
                        time.sleep(0.05)
                        GPIO.output(gpio_pin, GPIO.HIGH)
                        time.sleep(0.1)
                        GPIO.cleanup(gpio_pin)
                    except Exception:
                        # Fallback auf gpiozero falls vorhanden
                        try:
                            from gpiozero import OutputDevice

                            dev = OutputDevice(
                                gpio_pin, active_high=True, initial_value=True
                            )
                            dev.off()
                            time.sleep(0.05)
                            dev.on()
                            time.sleep(0.1)
                            dev.close()
                        except Exception as e:
                            logger.warning(f"GPIO-Reset nicht möglich: {e}")
            except Exception as e:
                logger.warning(f"Fehler beim Versuch GPIO-Reset: {e}")

            avrdude_cmd = [
                "avrdude",
                "-v",
                "-patmega2560",
                "-cwiring",
                f"-P{config.SERIAL_PORT}",
                f"-b{config.BAUDRATE}",
                "-D",
                f"-Uflash:w:{str(hexpath)}:i",
            ]
            logger.info("Aufruf: %s", " ".join(avrdude_cmd))
            proc = subprocess.run(
                avrdude_cmd, capture_output=True, text=True, timeout=300
            )
            if proc.returncode == 0:
                logger.info(f"Flash erfolgreich: {hexpath}")
                target = hexpath.with_suffix(hexpath.suffix + ".uploaded")
                shutil.move(str(hexpath), str(target))
            else:
                logger.error(
                    f"avrdude failed: {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
                )
                target = hexpath.with_suffix(hexpath.suffix + ".failed")
                shutil.move(str(hexpath), str(target))
        except subprocess.TimeoutExpired:
            logger.error("avrdude Timeout beim Flashen")
            target = hexpath.with_suffix(hexpath.suffix + ".failed")
            try:
                shutil.move(str(hexpath), str(target))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Fehler beim Flashen: {e}")
            target = hexpath.with_suffix(hexpath.suffix + ".failed")
            try:
                shutil.move(str(hexpath), str(target))
            except Exception:
                pass
        finally:
            # Recreate serial manager so robot resumes communication
            try:
                self.serial = serial_manager.SerialManager()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Fehler beim Reopen der seriellen Schnittstelle: {e}")


# Globale Instanz für den Zugriff aus anderen Modulen
robot = RobotControl()
