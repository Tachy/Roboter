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
import sys
import importlib

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


# --- Vorschaubild-Helfer (L3: war 6x als Copy-Paste im Modul) ---
def _to_bgr(arr):
    """picamera2-Array (RGBA/RGB/sonstiges) nach BGR wandeln."""
    if arr is None:
        return None
    if arr.ndim == 3 and arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if arr.ndim == 3 and arr.shape[2] == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return arr


def _publish_preview(bgr, text=None, target_w=320, quality=85):
    """Verkleinertes Vorschaubild (+ optionale Statusmeldung) veröffentlichen."""
    if bgr is None:
        return
    h, w = bgr.shape[:2]
    scale = target_w / float(w)
    preview = cv2.resize(
        bgr, (target_w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA
    )
    if text is not None:
        try:
            status_bus.set_message(text)
        except Exception:
            pass
    camera._encode_and_store_last_capture(preview, quality=quality)


def _capture_preview(text=None):
    """Aktuelles Kamerabild holen und als Vorschau veröffentlichen."""
    try:
        _publish_preview(_to_bgr(camera.picam2.capture_array()), text=text)
    except Exception:
        logger.debug("Vorschau konnte nicht erzeugt werden", exc_info=True)


class RobotControl:
    def __init__(self):
        self.mode = "AUTO"
        self.mode_lock = threading.Lock()
        self.serial = serial_manager.SerialManager()
        self.last_joystick = {"x": 0, "y": 0}
        self.last_joystick_lock = threading.Lock()
        self.calib_session = None
        # GETXY (Bildaufnahme + YOLO) läuft in einem eigenen Thread, damit die
        # Hauptschleife nicht bis zu YOLO_TIMEOUT_SEC blockiert (M5).
        self._getxy_thread = None
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
                    _capture_preview("Extrinsik: Klick zum Starten")
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
                    if camera.is_camera_started():
                        _capture_preview()
            except Exception:
                pass

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.send_command(command)

    def process_auto_mode(self):
        """Verarbeitet die automatische Steuerung."""
        line = self.serial.read_line()
        if line == "WAITING":
            logger.info("<- Arduino: WAITING")
            if self.get_mode() != "AUTO":
                self.send_command("MODE:MANUAL")
                logger.info("-> Arduino: MANUAL")
            else:
                self.send_command("MODE:AUTO")
                logger.info("-> Arduino: AUTO")

        elif line == "GETXY":
            logger.info("<- Arduino: GETXY")
            # Bildaufnahme + YOLO NICHT in der Hauptschleife (blockiert sonst bis
            # YOLO_TIMEOUT_SEC). In einem Worker-Thread abarbeiten (M5).
            if self._getxy_thread is not None and self._getxy_thread.is_alive():
                logger.warning("[AUTO] GETXY ignoriert – vorherige Verarbeitung läuft noch.")
                return
            self._getxy_thread = threading.Thread(
                target=self._handle_getxy, name="getxy-worker", daemon=True
            )
            self._getxy_thread.start()

    def _handle_getxy(self):
        """Worker: Einzelbild aufnehmen, YOLO auswerten, Koordinaten an den Arduino
        senden. Läuft in einem eigenen Thread (siehe process_auto_mode)."""
        try:
            # Welttransformation (Pixel -> mm) muss vorhanden sein. Ohne sie würde der
            # Arduino rohe Pixelwerte als Millimeter interpretieren und unkontrolliert
            # fahren -> in diesem Fall KEINE Koordinaten senden.
            try:
                use_world = (
                    getattr(config, "WORLD_TRANSFORM_ACTIVE", True)
                    and geometry.is_world_transform_ready()
                )
            except Exception:
                use_world = geometry.is_world_transform_ready()

            if not use_world:
                logger.error(
                    "[AUTO] Keine Welttransformation (Homographie/Extrinsik) geladen – "
                    "AUTO-Fahrt ohne Kalibrierung deaktiviert. Sende NOCALIB."
                )
                try:
                    status_bus.set_message(
                        "AUTO gestoppt: Kalibrierung fehlt (keine Welttransformation)"
                    )
                except Exception:
                    pass
                self.send_command("NOCALIB")
                logger.info("-> Arduino: NOCALIB")
                return

            # Entzerrtes Einzelbild aufnehmen und verarbeiten (immer undistortiert für GETXY)
            filename = "frame.jpg"
            camera.capture_image(filename, undistort=True)
            img_path = filename

            coords = yolo_detector.process_image(img_path)

            # Modus könnte sich während der Inferenz geändert haben
            if self.get_mode() != "AUTO":
                logger.info("[AUTO] Modus nicht mehr AUTO – sende keine Koordinaten.")
                return

            sent = 0
            skipped = 0
            for x, y in coords:
                if self.get_mode() != "AUTO":
                    logger.info("[AUTO] Moduswechsel – Koordinatenversand abgebrochen.")
                    return
                try:
                    w = geometry.pixel_to_world(float(x), float(y))
                except Exception as e:
                    logger.warning(
                        f"[AUTO] pixel_to_world fehlgeschlagen für ({x:.1f},{y:.1f}): {e}"
                    )
                    w = None
                if w is None:
                    # Keine gültige Welt-Umrechnung -> überspringen statt Pixel zu senden
                    skipped += 1
                    continue
                xw, yw = w
                msg = f"XY:{xw:.1f},{yw:.1f}"
                logger.info(f"-> Arduino: {msg}")
                self.send_command(msg)
                sent += 1
                time.sleep(0.05)

            if skipped:
                logger.warning(
                    f"[AUTO] {skipped} Koordinate(n) ohne gültige Welt-Umrechnung übersprungen."
                )

            # Abschlussmeldung
            self.send_command("DONE")
            logger.info(f"-> Arduino: DONE ({sent} Koordinate(n) gesendet)")
        except Exception as e:
            logger.exception(f"[AUTO] Fehler in GETXY-Worker: {e}")

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
                logger.debug(
                    f"Joystick-Kommando nicht parsebar: {command!r}", exc_info=True
                )
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
            # Bannerbild "Klick zum Starten" als letzte Aufnahme veröffentlichen
            _capture_preview("Kalibrierung: Klick zum Starten")
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
                # Abschlussbanner zeigen
                _capture_preview("Kalibrierung abgeschlossen")
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
            _capture_preview("Extrinsik: Keine K/D gefunden")
            return

        # Bild holen
        try:
            bgr = _to_bgr(camera.picam2.capture_array())
            if bgr is None:
                raise RuntimeError("Kein Kamerabild verfügbar.")
        except Exception:
            return

        # Extrinsik schätzen und speichern via geometry
        ok, draw, text = geometry.compute_and_save_extrinsics_from_charuco(
            bgr, K, D, newK=newK
        )

        # Preview/Banner schreiben (draw ist bereits BGR)
        _publish_preview(draw, text=text)

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

            # Optional: einfacher GPIO-Reset (einmalig, kein Retry)
            try:
                if getattr(config, "FW_RESET_GPIO", None) is not None:
                    gpio_pin = int(config.FW_RESET_GPIO)
                    logger.info(f"Versuche Mega-Reset via GPIO {gpio_pin}")
                    fw_pulse = getattr(config, "FW_RESET_PULSE_SEC", 0.05)
                    fw_post_wait = getattr(config, "FW_RESET_POST_PULSE_WAIT", 0.1)
                    try:
                        import RPi.GPIO as GPIO  # type: ignore

                        GPIO.setmode(GPIO.BCM)
                        # Für Optokoppler: idle LOW, Puls HIGH
                        GPIO.setup(gpio_pin, GPIO.OUT, initial=GPIO.LOW)
                        # Setze kurz HIGH, dann wieder LOW
                        GPIO.output(gpio_pin, GPIO.HIGH)
                        time.sleep(float(fw_pulse))
                        GPIO.output(gpio_pin, GPIO.LOW)
                        time.sleep(float(fw_post_wait))
                        GPIO.cleanup(gpio_pin)
                    except Exception as e_gpio:
                        # Kein gpiozero-Fallback: nur RPi.GPIO verwenden, bei Fehler nur warnen
                        logger.warning(
                            "GPIO-Reset nicht möglich (RPi.GPIO): %s", str(e_gpio)
                        )
            except Exception as e:
                logger.warning(f"Fehler beim Versuch GPIO-Reset: {e}")

            # Hinweis: Datei-basiertes Logging unter `state/` wurde entfernt.
            # Der Persistenz-Ordner wird weiterhin für Modus-Persistenz verwendet.

            avrdude_cmd = [
                "avrdude",
                "-v",
                "-p",
                "m2560",
                "-c",
                "wiring",
                "-P",
                f"{config.SERIAL_PORT}",
                "-b",
                f"{config.BAUDRATE}",
                "-D",
                "-U",
                f"flash:w:{str(hexpath)}:i",
            ]
            logger.info("Aufruf: %s", " ".join(avrdude_cmd))

            try:
                proc = subprocess.run(
                    avrdude_cmd, capture_output=True, text=True, timeout=300
                )

                # Logge avrdude-Ausgabe ausschließlich über den Python-Logger.
                try:
                    if proc.stdout:
                        logger.debug("avrdude STDOUT:\n%s", proc.stdout)
                    if proc.stderr:
                        if proc.returncode == 0:
                            logger.warning("avrdude STDERR:\n%s", proc.stderr)
                        else:
                            logger.error("avrdude STDERR:\n%s", proc.stderr)
                except Exception:
                    logger.debug(
                        "Fehler beim Loggen der avrdude-Ausgabe", exc_info=True
                    )

                if proc.returncode == 0:
                    logger.info(f"Flash erfolgreich: {hexpath}")
                    target = hexpath.with_suffix(hexpath.suffix + ".uploaded")
                    shutil.move(str(hexpath), str(target))
                else:
                    logger.error(f"avrdude failed (returncode {proc.returncode})")
                    target = hexpath.with_suffix(hexpath.suffix + ".failed")
                    try:
                        shutil.move(str(hexpath), str(target))
                    except Exception:
                        logger.debug("Konnte .hex nicht verschieben", exc_info=True)

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


# Singleton – wird erst bei Bedarf erzeugt (L1: kein Hardware-/Serial-Zugriff
# beim reinen Importieren des Moduls).
robot = None


def get_robot() -> "RobotControl":
    """Liefert die (bei Bedarf erzeugte) globale RobotControl-Instanz."""
    global robot
    if robot is None:
        robot = RobotControl()
    return robot
