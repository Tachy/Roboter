# Logging Setup
import logging

LOGLEVEL = (
    logging.INFO
)  # z.B. logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR
"""
Konfigurationsmodul für den Unkrautroboter.
Enthält alle wichtigen Konstanten und Einstellungen.
"""

# Serial Setup
SIMULATED_SERIAL_PORT = "/tmp/ttyV8"  # Virtueller Port für die Simulation
SERIAL_PORT = "/dev/serial0"  # Echter serieller Port
BAUDRATE = 115200

# UDP Setup
UDP_IP = "0.0.0.0"  # Hört auf alle Schnittstellen
UDP_CONTROL_PORT = 5005  # Port für Modusumschaltung
UDP_JOYSTICK_PORT = 5006  # Port für Joystick-Kommandos
UDP_HEARTBEAT_PORT = 5007  # Port für Heartbeat-Messages
UDP_STATUS_BROADCAST_PORT = 5008  # Port für Status-Broadcast
HEARTBEAT_TIMEOUT = 5.0  # Sekunden, wie lange der Stream nach letztem Heartbeat läuft

# Optionale Whitelist für UDP-Steuerung/Joystick (Absender-IP-Adressen oder CIDR-Netze)
# Beispiel: ALLOWED_UDP_SOURCES = ["192.168.179.10", "192.168.179.0/24"]
# Leer lassen, um alle Quellen zu erlauben.
ALLOWED_UDP_SOURCES = ["192.168.179.17", "192.168.179.186", "192.168.179.4"]

# HTTP Server Setup
HTTP_PORT = 8080

# YOLO Setup
USE_DUMMY = True  # Auf False setzen, wenn das echte YOLO-Modell verwendet wird
YOLO_MODEL_PATH = "./model/best.pt"  # z. B. "best.pt"

# Inferenz-Parameter (Subprozess mit Timeout)
YOLO_TIMEOUT_SEC = 40
YOLO_IMG_SIZE = 640  # Netzgröße (h, w); rechteckig für 720p→736x1280 mit wenig Padding
YOLO_CONF = 0.25  # Konfidenzschwelle
YOLO_IOU = 0.45  # IoU-Schwelle

# Camera Setup
CAMERA_RESOLUTION = (1280, 720)

# Training Setup
TRAINING_IMAGE_DIR = "./training/"

# Weltkoordinaten: optionaler XY-Versatz (mm), um den Ursprung zu verschieben (z. B. unter die linke Bürste)
# Beispiel: WORLD_OFFSET_XY_MM = (x_mm, y_mm) – wird von pixel_to_world subtrahiert
WORLD_OFFSET_XY_MM = (0.0, 0.0)
