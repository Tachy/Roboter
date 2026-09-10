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
from .calibration import CalibrationSession, ExtrinsicSession
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
        # EXTRINSIK: Session + Flag, das den Serial-Poller der Hauptschleife
        # pausiert, während die Kalibriersequenz die serielle Leitung besitzt.
        self.extr_session = None
        self._extr_seq_active = False
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
            if self.extr_session is not None:
                try:
                    self.extr_session.stop()
                except Exception:
                    pass
                self.extr_session = None
            # Beim Wechsel in EXTRINSIK: Session anlegen, Schlitten fährt (durch
            # MODE:EXTRINSIK oben) auf X=0; ein Worker wartet auf XREACHED und
            # schaltet dann den Hinweis auf "Board auflegen".
            try:
                if self.mode == "EXTRINSIK":
                    try:
                        self.extr_session = ExtrinsicSession()
                    except Exception as e:
                        self.extr_session = None
                        logger.warning(f"[Extr] Session-Init fehlgeschlagen: {e}")
                    status_bus.set_message(
                        "Extrinsik: Schlitten fährt auf X=0 – bitte warten ..."
                    )
                    if camera.is_camera_started():
                        _capture_preview("Extrinsik: Schlitten fährt auf X=0 ...")
                    # Fenster schließen, in dem process_auto_mode das XREACHED
                    # wegkonsumieren könnte:
                    self._extr_seq_active = True
                    threading.Thread(
                        target=self._extrinsic_home_wait,
                        name="extr-home",
                        daemon=True,
                    ).start()
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
        # Während der EXTRINSIK-Sequenz besitzt deren Worker die serielle
        # Leitung (wartet gezielt auf XREACHED/FAULT). Hier nichts lesen, sonst
        # würde die Antwort hier konsumiert und verworfen.
        if self._extr_seq_active:
            return
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
                    "[AUTO] Keine Kurvenmatrix geladen (ground_poly.npz fehlt) – "
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

            # ROHbild in GETXY-Auflösung aufnehmen (keine Entzerrung), verlustfrei
            # als PNG ablegen. geometry.pixel_to_world skaliert die Pixel intern
            # auf die EXTRINSIK-Referenzauflösung.
            bgr = camera.capture_still_array(config.STILL_RESOLUTION_GETXY)
            src_h, src_w = bgr.shape[:2]
            filename = "frame.png"
            if not cv2.imwrite(filename, bgr):
                raise RuntimeError("frame.png konnte nicht geschrieben werden")

            coords = yolo_detector.process_image(filename)

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
                    w = geometry.pixel_to_world(
                        float(x), float(y), src_wh=(src_w, src_h)
                    )
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

    def _extrinsic_home_wait(self):
        """Wartet nach dem Wechsel in EXTRINSIK auf XREACHED (Schlitten auf X=0)
        und schaltet den Hinweistext dann auf 'Board auflegen'."""
        try:
            # Z hoch + X-Referenzfahrt (kalibriereX, bis zu ~15 s Timeout)
            deadline = time.monotonic() + 32.0
            line = None
            while time.monotonic() < deadline:
                if self.get_mode() != "EXTRINSIK":
                    return
                line = self.serial.wait_for(("XREACHED:", "FAULT:"), timeout=1.0)
                if line is not None:
                    break
            if self.get_mode() != "EXTRINSIK":
                return
            if line and line.startswith("FAULT"):
                status_bus.set_message(
                    f"Extrinsik: {line} beim Anfahren von X=0 – Mega prüfen"
                )
            else:
                status_bus.set_message(
                    "Extrinsik: Schlitten auf X=0 – Board mit Ecke (0,0) unter den "
                    "Bürstenmittelpunkt, X-Achse parallel zur Fahrtrichtung. Dann "
                    "'Bild aufnehmen' (Kamera fährt zur Aufnahme auf die AUTO-Position)"
                )
                if camera.is_camera_started():
                    _capture_preview(
                        "Extrinsik: Board (0,0) unter Bürste, parallel, dann 'Bild aufnehmen'"
                    )
        finally:
            self._extr_seq_active = False

    def extrinsic_button_pressed(self):
        """Startet die mechanik-gekoppelte EXTRINSIK-Sequenz (ein Klick genügt).

        Der Schlitten fährt automatisch die Zielpositionen an
        (ExtrinsicSession.targets_mm), an jeder wird ein Bild ausgewertet; am
        Ende wird ground_homography.npz geschrieben.
        """
        if self.get_mode() != "EXTRINSIK":
            return
        if not camera.is_camera_started():
            logging.info("[Extr] Klick ignoriert: Kamera/Stream nicht aktiv.")
            return
        if self._extr_seq_active:
            logging.info("[Extr] Sequenz läuft bereits – Klick ignoriert.")
            return
        if self.extr_session is None:
            try:
                self.extr_session = ExtrinsicSession()
            except Exception as e:
                _capture_preview(f"Extrinsik: Init fehlgeschlagen ({e})")
                return
        self._extr_seq_active = True
        threading.Thread(
            target=self._run_extrinsic_sequence, name="extr-seq", daemon=True
        ).start()

    def _run_extrinsic_sequence(self):
        """Worker: Schlitten (= Kamera, sitzt auf der X-Achse) auf die
        AUTO-Aufnahmeposition (config.EXTRINSIK_CAPTURE_X_MM = MITTEX) fahren,
        dann N Rohbilder aufnehmen, ChArUco-Ecken poolen, Pose + Polynom
        schätzen und ground_poly.npz schreiben. Das Board (X-Achse parallel zur
        Bürstenfahrt, Ecke (0,0) unter der Bürste bei X=0) definiert das
        Koordinatensystem (Board-mm == mech-mm)."""
        try:
            sess = self.extr_session
            if sess is None:
                return
            n = int(getattr(config, "EXTRINSIK_NUM_FRAMES", 8))
            cap_x = float(getattr(config, "EXTRINSIK_CAPTURE_X_MM", 300))

            # Kamera auf die AUTO-Aufnahmeposition (muss = MITTEX der Firmware sein)
            status_bus.set_message(
                f"Extrinsik: Kamera fährt auf X={cap_x:.0f} (AUTO-Position) ..."
            )
            self.send_command(f"GOTOX:{cap_x:.0f}")
            line = self.serial.wait_for(("XREACHED:", "FAULT:"), timeout=45.0)
            if line is None or line.startswith("FAULT"):
                status_bus.set_message(
                    f"Extrinsik: Anfahren X={cap_x:.0f} fehlgeschlagen ({line}) – abgebrochen"
                )
                return
            time.sleep(0.5)  # Nachschwingen abklingen lassen

            # N Bilder in voller Auflösung.
            status_bus.set_message(f"Extrinsik: nehme {n} Bilder auf ...")
            try:
                frames = camera.capture_still_array(
                    config.STILL_RESOLUTION_EXTRINSIK, n=max(2, n)
                )
            except Exception as e:
                status_bus.set_message(f"Extrinsik: Aufnahme fehlgeschlagen ({e})")
                return
            if not isinstance(frames, list):
                frames = [frames]
            for i, bgr in enumerate(frames):
                if self.get_mode() != "EXTRINSIK":
                    status_bus.set_message("Extrinsik: abgebrochen (Moduswechsel)")
                    return
                ok, msg, preview = sess.add_frame(bgr)
                _publish_preview(
                    preview if preview is not None else bgr,
                    text=f"Extrinsik: Bild {sess.n_frames}/{n} ({msg})",
                )
                if not ok:
                    logger.warning(f"[Extr] Bild {i + 1}: {msg}")

            if sess.n_frames < max(2, n // 2):
                status_bus.set_message(
                    f"Extrinsik: Board zu selten erkannt ({sess.n_frames}/{n}) – "
                    "nichts gespeichert"
                )
                return
            try:
                path, reproj, fit_rms = sess.finalize()
                bx0, bx1 = sess.board_x_span
                by0, by1 = sess.board_y_span
                status_bus.set_message(
                    f"Extrinsik gespeichert: {sess.n_frames} Bilder, "
                    f"Reproj {reproj:.2f} px, Poly-Fehler {fit_rms:.2f} mm, "
                    f"sichtbar board-x {bx0:.0f}..{bx1:.0f} / y {by0:.0f}..{by1:.0f} mm"
                )
                logger.info(
                    f"[Extr] {path} reproj={reproj:.3f}px fit_rms={fit_rms:.3f}mm "
                    f"frames={sess.n_frames}"
                )
                # Sichtprüfungs-Overlay (mechanisches mm-Raster) auf dem Rohbild
                try:
                    raw = _to_bgr(camera.picam2.capture_array())
                    _publish_preview(
                        sess.grid_overlay(raw),
                        text=(
                            f"Extrinsik OK – Raster prüfen (Reproj {reproj:.2f} px, "
                            f"Poly {fit_rms:.2f} mm). Sitzt es daneben: Board neu "
                            "ausrichten."
                        ),
                    )
                except Exception:
                    logger.debug("[Extr] Overlay-Vorschau fehlgeschlagen", exc_info=True)
                if reproj > 2.0 or fit_rms > 1.0:
                    logger.warning(
                        f"[Extr] Qualität grenzwertig: reproj {reproj:.2f} px, "
                        f"fit {fit_rms:.2f} mm"
                    )
            except Exception as e:
                status_bus.set_message(f"Extrinsik fehlgeschlagen: {e}")
                logger.exception("[Extr] finalize fehlgeschlagen")
        except Exception as e:
            logger.exception(f"[Extr] Sequenzfehler: {e}")
            try:
                status_bus.set_message(f"Extrinsik-Sequenzfehler: {e}")
            except Exception:
                pass
        finally:
            self._extr_seq_active = False
            self.extr_session = None

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

                # Check for model uploads (model_<ts>.tar) in MANUAL mode
                try:
                    if self.get_mode() == "MANUAL":
                        self._check_model_upload()
                except Exception as e:
                    logger.error(f"Fehler beim Scan des Modell-Upload-Verzeichnisses: {e}")

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

    # ------------------------------------------------------------------ #
    # Model OTA – mirrors the .hex firmware flow: drop model_<ts>.tar in
    # config.MODEL_UPLOAD_DIR, robot validates + atomically swaps in MANUAL.
    # ------------------------------------------------------------------ #
    def _check_model_upload(self) -> None:
        import json
        import hashlib
        import tarfile

        up = Path(config.MODEL_UPLOAD_DIR).resolve()
        if not up.exists():
            return
        tars = sorted(p for p in up.iterdir() if p.suffix.lower() == ".tar")
        if not tars:
            return
        tar_path = tars[0]
        # skip a file that is still being written (mtime not settled)
        try:
            if time.time() - tar_path.stat().st_mtime < 3.0:
                return
        except OSError:
            return

        # don't swap while an AUTO inference worker is running
        t = getattr(self, "_getxy_thread", None)
        if t is not None and t.is_alive():
            logger.info("Modell-Upload: GETXY-Worker aktiv, verschiebe Swap")
            return

        logger.info(f"Modell-Upload gefunden: {tar_path}")
        staging = up / f".staging_{os.getpid()}"
        model_dir = Path(config.MODEL_DIR).resolve()
        ok = False
        try:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            with tarfile.open(tar_path) as tf:
                names = tf.getnames()
                if any(n.startswith("/") or ".." in Path(n).parts for n in names):
                    raise ValueError("unsicherer Pfad im Tar")
                tf.extractall(staging)

            man = json.loads((staging / "manifest.json").read_text())
            if list(man.get("classes", [])) != list(config.YOLO_EXPECTED_CLASSES):
                raise ValueError(f"Klassen != erwartet: {man.get('classes')}")
            if int(man.get("imgsz", 0)) != int(config.YOLO_IMG_SIZE):
                raise ValueError(f"imgsz {man.get('imgsz')} != config {config.YOLO_IMG_SIZE}")

            for rel, want in (man.get("sha256") or {}).items():
                fp = staging / rel
                if not fp.is_file():
                    raise ValueError(f"Datei aus manifest fehlt: {rel}")
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != want:
                    raise ValueError(f"sha256 mismatch: {rel}")

            self._model_load_test(staging)

            # atomic swap of best.pt / best.onnx / best_ncnn_model/
            model_dir.mkdir(parents=True, exist_ok=True)
            swapped = []
            try:
                for name in ("best.pt", "best.onnx", "best_ncnn_model"):
                    src = staging / name
                    if not src.exists():
                        continue
                    dst = model_dir / name
                    old = model_dir / (name + ".old")
                    if old.exists():
                        (shutil.rmtree if old.is_dir() else os.remove)(old)
                    if dst.exists():
                        dst.rename(old)
                        swapped.append((dst, old))
                    shutil.move(str(src), str(dst))
                    swapped.append((None, dst))
            except Exception:
                for a, b in reversed(swapped):
                    try:
                        if a is None:
                            (shutil.rmtree if b.is_dir() else os.remove)(b)
                        else:
                            b.rename(a)
                    except Exception:
                        pass
                raise

            yolo_detector.reload_model()
            ok = True
            logger.info(f"Modell-Swap erfolgreich (ts={man.get('trained_ts', '?')})")
        except Exception as e:
            logger.error(f"Modell-Upload abgewiesen: {e}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            suffix = ".uploaded" if ok else ".failed"
            try:
                shutil.move(str(tar_path), str(tar_path) + suffix)
            except Exception:
                pass
            try:
                hist = Path(config.MODEL_DIR).resolve().parent / "state" / "model_history.jsonl"
                hist.parent.mkdir(parents=True, exist_ok=True)
                with open(hist, "a") as f:
                    f.write(json.dumps({
                        "ts": time.time(), "tar": tar_path.name,
                        "result": "uploaded" if ok else "failed",
                    }) + "\n")
            except Exception:
                pass

    def _model_load_test(self, staging: Path) -> None:
        """Load the staged model in a subprocess so a bad model can't crash us."""
        runtime = getattr(config, "YOLO_RUNTIME", "pt")
        target = {
            "pt": staging / "best.pt",
            "onnx": staging / "best.onnx",
            "ncnn": staging / "best_ncnn_model",
        }.get(runtime, staging / "best.pt")
        target = target.resolve()
        if not target.exists():
            raise ValueError(f"Artefakt für runtime={runtime} fehlt: {target.name}")
        test_img = ""
        _train = Path(config.TRAINING_IMAGE_DIR).resolve()
        for cand in (Path(config.MODEL_DIR).resolve() / "test_1280.jpg",
                     *sorted(_train.glob("bild_*.png"))[-1:]):
            if cand.is_file():
                test_img = str(cand)
                break
        code = (
            "import sys;from ultralytics import YOLO;"
            f"m=YOLO(r'{target}');"
            "n={int(k):v for k,v in m.names.items()};"
            f"exp={{i:c for i,c in enumerate({list(config.YOLO_EXPECTED_CLASSES)})}};"
            "assert n==exp, ('names %r != %r'%(n,exp));"
            + (f"m.predict(r'{test_img}',imgsz={int(config.YOLO_IMG_SIZE)},device='cpu',verbose=False);"
               if test_img else "")
            + "print('load-test ok')"
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise ValueError(f"Ladetest fehlgeschlagen: {r.stderr.strip()[-300:]}")


# Singleton – wird erst bei Bedarf erzeugt (L1: kein Hardware-/Serial-Zugriff
# beim reinen Importieren des Moduls).
robot = None


def get_robot() -> "RobotControl":
    """Liefert die (bei Bedarf erzeugte) globale RobotControl-Instanz."""
    global robot
    if robot is None:
        robot = RobotControl()
    return robot
