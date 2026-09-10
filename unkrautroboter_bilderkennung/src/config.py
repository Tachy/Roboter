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
BAUDRATE = 115200  # Baudrate der seriellen Verbindung

# UDP Setup
UDP_IP = "0.0.0.0"  # Hört auf alle Schnittstellen
UDP_CONTROL_PORT = 5005  # Port für Modusumschaltung
UDP_JOYSTICK_PORT = 5006  # Port für Joystick-Kommandos
UDP_HEARTBEAT_PORT = 5007  # Port für Heartbeat-Messages
# UDP_STATUS_BROADCAST_PORT wurde entfernt (nicht verwendet)
HEARTBEAT_TIMEOUT = 5.0  # Sekunden, wie lange der Stream nach letztem Heartbeat läuft

# Optionale Whitelist für UDP-Steuerung/Joystick (Absender-IP-Adressen oder CIDR-Netze)
# Beispiel: ALLOWED_UDP_SOURCES = ["192.168.179.10", "192.168.179.0/24"]
# Leer lassen, um alle Quellen zu erlauben.
ALLOWED_UDP_SOURCES = ["192.168.179.17", "192.168.179.186", "192.168.179.4"]

# HTTP Server Setup
HTTP_PORT = 8080

# YOLO Setup
USE_DUMMY = False  # Auf False setzen, wenn das echte YOLO-Modell verwendet wird
YOLO_MODEL_PATH = "./model/best.pt"  # z. B. "best.pt"

# Inferenz-Parameter (Subprozess mit Timeout)
YOLO_TIMEOUT_SEC = 40
YOLO_IMG_SIZE = 1280  # Netzgröße; 720p wird auf 1280x736 letterboxed (YOLO26s NCNN FP16)
YOLO_CONF = 0.25  # Konfidenzschwelle
YOLO_IOU = 0.45  # IoU-Schwelle

# Camera Setup
# EIN Kamera-Modus mit zwei Ausgängen (kein Mode-Switch -> Stream reißt nie ab):
#  - main  = CAPTURE_RESOLUTION: alle Einzelbilder (GETXY, EXTRINSIK, DISTORTION,
#            Training) in voller IMX477-Auflösung 4056x3040.
#  - lores = CAMERA_RESOLUTION:  läuft durchgehend als MJPEG-Stream/Vorschau.
# lores wird von picamera2 aus dem VOLLEN `main`-Bildfeld heruntergerechnet
# (kein eigener Crop). Daher muss CAMERA_RESOLUTION dasselbe Seitenverhältnis
# wie CAPTURE_RESOLUTION haben (4:3), sonst wird das Vorschaubild anamorph
# gestaucht. So decken Stream und Einzelbilder dieselbe Sensor-FOV ab, nur in
# unterschiedlicher Auflösung.
CAPTURE_RESOLUTION = (4056, 3040)
CAMERA_RESOLUTION = (1024, 768)
# EXTRINSIK nutzt die volle `main`-Auflösung (subpixelgenaue Ecken). GETXY wird
# aus `main` auf diese Größe heruntergerechnet (kleine, schnelle PNGs); das
# Polynom skaliert die Pixel über ref_wh automatisch hoch.
STILL_RESOLUTION_EXTRINSIK = CAPTURE_RESOLUTION
STILL_RESOLUTION_GETXY = (2028, 1520)

# Kleines ChArUco-Board NUR für den DISTORTION-Modus (K, D). Passt quer auf A4
# (7x35 = 245 mm x 5x35 = 175 mm) und lässt sich plan aufziehen. Dictionary =
# dasselbe wie das große Boden-Board (calibration.DICT_NAME). Für die Intrinsik
# ist der absolute Maßstab egal – nur das Feld/Marker-Verhältnis zählt.
# Board-Bild erzeugen: python tools/gen_distortion_board.py
DISTORTION_BOARD_SQUARES = (7, 5)       # (x, y)
DISTORTION_BOARD_SQUARE_MM = 35.0
DISTORTION_BOARD_MARKER_MM = 26.0

# Training Setup
TRAINING_IMAGE_DIR = "./training/"

# Firmware upload directory for .hex files (Pi -> Mega flashing)
UPLOAD_DIR = "./upload/"

# --- Modell-OTA: model_<ts>.tar in MODEL_UPLOAD_DIR ablegen, Pi tauscht in MANUAL ---
MODEL_UPLOAD_DIR = "./model_upload/"          # Inbox für Modell-Tars
MODEL_DIR = "./model/"                        # Zielverzeichnis (best.pt, best_ncnn_model/, ...)
YOLO_NCNN_DIR = "./model/best_ncnn_model"     # genutzt bei YOLO_RUNTIME == "ncnn"
YOLO_RUNTIME = "ncnn"                         # "pt" | "ncnn" | "onnx"
YOLO_EXPECTED_CLASSES = ["unkraut", "moos"]   # Reihenfolge maßgeblich; OTA weist bei Abweichung ab
# Läuft auf YOLO26s NCNN FP16 @ imgsz 1280 (Pi 4B ~5-8 s, ultralytics 8.4.146).
# Das aktuelle Modell ist ein Bootstrap auf Fake-Bildern (nur Architektur-/Pipeline-
# Umstieg) – wird beim ersten Echtdaten-Lauf über die Modell-OTA ersetzt.
# Zurück auf v8m im Notfall: YOLO_RUNTIME="pt", YOLO_IMG_SIZE=640,
# cp model/best.pt.v8m model/best.pt.

# GPIO-Pin (BCM) an der Raspberry Pi, der mit dem RESET-Pin des Mega verbunden ist.
# Wenn None, wird kein Reset per GPIO durchgeführt. Hinweis: RESET ist aktiv LOW.
FW_RESET_GPIO = 23  # z.B. 23
# Firmware reset tuning (seconds)
# Duration die RESET-Leitung kurz aktivieren (Sekunden)
FW_RESET_PULSE_SEC = 0.1
# Verzögerung nach dem Loslassen der RESET-Leitung bevor avrdude startet (Sekunden)
FW_RESET_POST_PULSE_WAIT = 0.1

# Weltkoordinaten: optionaler XY-Versatz (mm), um den Ursprung zu verschieben (z. B. unter die linke Bürste)
# Beispiel: WORLD_OFFSET_XY_MM = (x_mm, y_mm) – wird von pixel_to_world subtrahiert
WORLD_OFFSET_XY_MM = (0.0, 0.0)

# EXTRINSIK: Grad des Pixel->mm-Polynoms ("Kurvenmatrix"), das die Extrinsik aus
# Pose + K/D backt und als ground_poly.npz speichert. 3 reicht für normale
# Objektive; finalize hebt bei zu großem Fit-Residuum selbst auf 4 an.
EXTRINSIK_POLY_DEGREE = 3

# Schlitten-X (mm), von der aus die Kamera ihr Bild macht – sowohl für jede
# AUTO-Erkennung (Firmware: MITTEX) als auch für die EXTRINSIK-Aufnahme. Beide
# MÜSSEN übereinstimmen, sonst passt das kalibrierte Polynom nicht zur
# AUTO-Ansicht. Bei Änderung auch #define MITTEX in der Mega-Firmware anpassen
# und EXTRINSIK neu laufen lassen.
EXTRINSIK_CAPTURE_X_MM = 300

# EXTRINSIK: Anzahl der Kamerabilder, die pro Kalibrierung gepoolt werden.
# Kamera sitzt auf der X-Achse; die Aufnahme erfolgt bei EXTRINSIK_CAPTURE_X_MM,
# Board + Kamera stehen dabei still -> eins würde genügen, ein paar mitteln nur
# das Sensor-/Ecken-Rauschen. Die Board-Ecke (0,0) liegt (Schlitten zuvor auf
# X=0) unter dem Bürstenmittelpunkt, Board-X-Achse parallel zur Bürstenfahrt ->
# Board-mm == mechanische mm. Feinkorrektur des Ursprungs nur über
# WORLD_OFFSET_XY_MM.
EXTRINSIK_NUM_FRAMES = 5
