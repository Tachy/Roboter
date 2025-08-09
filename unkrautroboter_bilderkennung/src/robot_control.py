"""
Hauptmodul für die Robotersteuerung.
"""

import threading
import time
from . import config, camera, udp_server, serial_manager, yolo_detector

class RobotControl:
    def __init__(self):
        self.mode = "AUTO"
        self.mode_lock = threading.Lock()
        self.serial = serial_manager.SerialManager()

    def get_mode(self):
        """Gibt den aktuellen Modus zurück."""
        with self.mode_lock:
            return self.mode

    def set_mode(self, new_mode):
        """Setzt den Betriebsmodus (AUTO oder MANUAL)."""
        with self.mode_lock:
            self.mode = new_mode

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.send_command(command)

    def process_auto_mode(self):
        """Verarbeitet die automatische Steuerung."""
        line = self.serial.read_line()
        if line == "GETXY":
            print("Empfangen: GETXY")

            # Frame aufnehmen und verarbeiten
            img_path = camera.capture_frame()
            if not img_path:
                return

            # Koordinaten mit YOLO ermitteln
            coords = yolo_detector.process_image(img_path)

            # Koordinaten an Arduino senden
            for x, y in coords:
                msg = f"XY:{x:.1f},{y:.1f}"
                print("Sende:", msg)
                self.send_command(msg)
                time.sleep(0.05)

            # Abschlussmeldung
            self.send_command("DONE")
            print("Sende: DONE")

    def run(self):
        """Hauptschleife der Robotersteuerung."""
        try:
            print("Starte HTTP-Server...")
            threading.Thread(target=camera.start_http_server, daemon=True).start()
            
            print("Starte UDP-Steuerkanal...")
            threading.Thread(target=udp_server.start_control_server, daemon=True).start()
            
            print("Starte UDP-Joystick-Server...")
            threading.Thread(target=udp_server.start_joystick_server, daemon=True).start()
            
            print("Starte Hauptloop...")
            while True:
                if self.get_mode() == "AUTO":
                    self.process_auto_mode()
                time.sleep(0.1)

        except KeyboardInterrupt:
            print("Beendet.")
        finally:
            self.serial.close()
            if camera.stream_active:
                camera.stop_stream()

# Globale Instanz für den Zugriff aus anderen Modulen
robot = RobotControl()
