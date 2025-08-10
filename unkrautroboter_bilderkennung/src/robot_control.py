"""
Hauptmodul für die Robotersteuerung.
"""

import threading
import time
import logging
from . import config
from . import config, camera, serial_manager, yolo_detector, udp_server, status_ws_server

# Logger einrichten
logger = logging.getLogger("robot_control")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )

class RobotControl:
    def __init__(self):
        self.mode = "AUTO"
        self.mode_lock = threading.Lock()
        self.serial = serial_manager.SerialManager()
        self.last_joystick = {"x": 0, "y": 0}
        self.last_joystick_lock = threading.Lock()
        msg = "START"
        logger.info(f"-> Arduino: {msg}")
        self.send_command(msg)

    def get_mode(self):
        """Gibt den aktuellen Modus zurück."""
        with self.mode_lock:
            return self.mode

    def set_mode(self, new_mode):
        """Setzt den Betriebsmodus (AUTO oder MANUAL)."""
        with self.mode_lock:
            self.mode = new_mode
            msg = f"MODE:{self.mode}"
            logger.info(f"-> Arduino: {msg}")
            self.send_command(msg)

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.send_command(command)

    def process_auto_mode(self):
        """Verarbeitet die automatische Steuerung."""
        line = self.serial.read_line()
        if line == "GETXY":
            logger.info("<- Arduino: GETXY")

            # Frame aufnehmen und verarbeiten
            img_path = camera.capture_frame()
            if not img_path:
                return

            # Koordinaten mit YOLO ermitteln
            coords = yolo_detector.process_image(img_path)

            # Koordinaten an Arduino senden
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
        if self.get_mode() == "MANUAL":
            if ",BUTTON:1" in command:
                command = command.replace(",BUTTON:1", "")
            self.send_command(command)
            return True
        return False

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
