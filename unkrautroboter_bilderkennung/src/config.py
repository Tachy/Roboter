# Logging Setup
import logging
LOGLEVEL = logging.INFO  # z.B. logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR
"""
Konfigurationsmodul für den Unkrautroboter.
Enthält alle wichtigen Konstanten und Einstellungen.
"""

# Serial Setup
SIMULATED_SERIAL_PORT = '/tmp/ttyV8'  # Virtueller Port für die Simulation
SERIAL_PORT = '/dev/serial0'  # Echter serieller Port
BAUDRATE = 115200

 # UDP Setup
UDP_IP = "0.0.0.0"  # Hört auf alle Schnittstellen
UDP_CONTROL_PORT = 5005  # Port für Modusumschaltung
UDP_JOYSTICK_PORT = 5006  # Port für Joystick-Kommandos
UDP_HEARTBEAT_PORT = 5007  # Port für Heartbeat-Messages
UDP_STATUS_BROADCAST_PORT = 5008  # Port für Status-Broadcast
HEARTBEAT_TIMEOUT = 5.0    # Sekunden, wie lange der Stream nach letztem Heartbeat läuft

# HTTP Server Setup
HTTP_PORT = 8080

# YOLO Setup
USE_DUMMY = True  # Auf False setzen, wenn das echte YOLO-Modell verwendet wird
YOLO_MODEL_PATH = "pfad/zum/modell.pt"  # z. B. "best.pt"

# Camera Setup
CAMERA_RESOLUTION = (1280, 720)

# Stream Undistortion Settings
# Wenn True, wird der Live-Stream per Software entzerrt (cv2.remap -> JPEG).
# Das kostet CPU, ist aber qualitativ besser. Bei False wird der rohe MJPEG-Encoder genutzt.
UNDISTORT_STREAM = True
# Zielbildrate für den Software-Stream (nur wirksam bei UNDISTORT_STREAM=True)
STREAM_TARGET_FPS = 15
# JPEG-Qualität (0-100) für den Software-Stream (nur wirksam bei UNDISTORT_STREAM=True)
STREAM_JPEG_QUALITY = 80

# Training Setup
TRAINING_IMAGE_DIR = "./training/"
