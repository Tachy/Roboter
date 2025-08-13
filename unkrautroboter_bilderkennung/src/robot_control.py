"""
Hauptmodul für die Robotersteuerung.
"""

import threading
import time
import logging
import os
from pathlib import Path
from . import config, camera, serial_manager, yolo_detector, udp_server, status_ws_server
from .calibration import CalibrationSession

# Logger einrichten
logger = logging.getLogger("robot_control")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
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
        return val if val in {"AUTO", "MANUAL", "DISTORTION"} else None
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
        if persisted in {"AUTO", "MANUAL", "DISTORTION"} and persisted != self.mode:
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
        """Setzt den Betriebsmodus (AUTO, MANUAL oder DISTORTION)."""
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

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.send_command(command)

    def process_auto_mode(self):
        """Verarbeitet die automatische Steuerung."""
        line = self.serial.read_line()
        if line == "GETXY":
            logger.info("<- Arduino: GETXY")

            # Entzerrtes Einzelbild aufnehmen und verarbeiten
            filename = "frame.jpg"
            camera.capture_image(filename)
            img_path = filename

            coords = yolo_detector.process_image(img_path)
            for x, y in coords:
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
                parts = command[len("JOYSTICK:"):].split(",")
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
            if ",BUTTON:1" in command:
                command = command.replace(",BUTTON:1", "")
            self.send_command(command)
            return True
        elif mode == "DISTORTION":
            # DISTORTION: zur Sicherheit an Arduino wie AUTO (d. h. keine direkten Joystick-Kommandos),
            # Button-Handling wird im UDP-Server ausgelöst
            return True
        return False

    def calibration_button_pressed(self):
        """Wird aufgerufen, wenn im DISTORTION-Modus der Joystick-Button gedrückt wurde."""
        if self.get_mode() != "DISTORTION":
            return
        if self.calib_session is None:
            # Erster Klick: Kalibriervorgang starten, aber noch kein Snapshot
            self.calib_session = CalibrationSession(target_snapshots=20)
            logger.info("[Calib] Kalibriervorgang gestartet. Nächster Klick nimmt das erste Bild auf.")
            return
        # Ab hier: Session existiert -> Snapshots sammeln
        ok, counts = self.calib_session.capture_snapshot()
        if ok:
            logger.info(f"[Calib] Snapshot {self.calib_session.snapshots}/{self.calib_session.target} (Marker {counts[0]}, Charuco {counts[1]})")
            if self.calib_session.snapshots >= self.calib_session.target:
                try:
                    out_file, err = self.calib_session.finalize()
                    logger.info(f"[Calib] gespeichert: {out_file} (reproj_err={err:.4f})")
                except Exception as e:
                    logger.error(f"[Calib] Fehler bei Finalisierung: {e}")
                finally:
                    try:
                        self.calib_session.stop()
                    except Exception:
                        pass
                    self.calib_session = None
                    # Nach Abschluss: keine Software-Overlay/Undistortion im Stream
                    logger.info("[Calib] abgeschlossen. Hardware-Stream bleibt roh; Kalibrierdaten werden für Offscreen-Verarbeitung genutzt.")

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
            threading.Thread(target=udp_server.start_control_server, daemon=True).start()
            
            logger.info("Starte UDP-Joystick-Server...")
            threading.Thread(target=udp_server.start_joystick_server, daemon=True).start()
            
            # Starte Heartbeat-Listener für Videostream (UDP)
            udp_server.start_heartbeat_monitor()
            # Starte WebSocket-Status-Server (im Hintergrund)
            threading.Thread(target=status_ws_server.start_status_ws_server, daemon=True).start()
            
            logger.info("Starte Hauptloop...")
            while True:
                if self.get_mode() == "AUTO":
                    self.process_auto_mode()
                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Beendet.")
        finally:
            self.serial.close()
            if camera.stream_active:
                camera.stop_stream()

# Globale Instanz für den Zugriff aus anderen Modulen
robot = RobotControl()
