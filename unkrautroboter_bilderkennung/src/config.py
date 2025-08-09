"""
Konfigurationsmodul für den Unkrautroboter.
Enthält alle wichtigen Konstanten und Einstellungen.
"""

# Serial Setup
USE_SIMULATED_SERIAL = True  # Auf True setzen, um die lokale Simulation zu aktivieren
SIMULATED_SERIAL_PORT = '/dev/pts/2'  # Virtueller Port für die Simulation
SERIAL_PORT = '/dev/serial0'  # Echter serieller Port
BAUDRATE = 115200

# UDP Setup
UDP_IP = "0.0.0.0"  # Hört auf alle Schnittstellen
UDP_CONTROL_PORT = 5005  # Port für Modusumschaltung
UDP_JOYSTICK_PORT = 5006  # Port für Joystick-Kommandos

# HTTP Server Setup
HTTP_PORT = 8080

# YOLO Setup
USE_DUMMY = True  # Auf False setzen, wenn das echte YOLO-Modell verwendet wird
YOLO_MODEL_PATH = "pfad/zum/modell.pt"  # z. B. "best.pt"

# Camera Setup
CAMERA_RESOLUTION = (1280, 720)

# Training Setup
TRAINING_IMAGE_DIR = "./training/"
