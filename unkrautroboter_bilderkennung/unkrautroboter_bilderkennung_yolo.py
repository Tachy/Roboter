import serial
import time
import cv2

# Setup
SERIAL_PORT = '/dev/ttyUSB0'  # oder /dev/ttyAMA0, je nach Anschluss
BAUDRATE = 115200
CAMERA_INDEX = 0

# Dummy-Modus aktivieren
USE_DUMMY = True  # Auf False setzen, wenn das echte YOLO-Modell verwendet wird

if not USE_DUMMY:
    from ultralytics import YOLO
    model = YOLO("pfad/zum/modell.pt")  # z. B. "best.pt"

# Serielle Verbindung
ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
time.sleep(2)  # Zeit für Verbindungsaufbau

def capture_image(filename="frame.jpg"):
    cap = cv2.VideoCapture(CAMERA_INDEX)
    ret, frame = cap.read()
    cap.release()
    if ret:
        cv2.imwrite(filename, frame)
        return filename
    else:
        print("Fehler beim Kamerazugriff")
        return None

def extract_xy(results):
    if USE_DUMMY:
        # Dummy-Koordinaten zurückgeben
        return [(100.0, 200.0)]  # Beispielkoordinaten
    else:
        # Echte Koordinaten aus YOLO-Ergebnissen extrahieren
        coordinates = []
        for result in results:
            for box in result.boxes:
                x_center = float(box.xywh[0][0])
                y_center = float(box.xywh[0][1])
                coordinates.append((x_center, y_center))
        return coordinates

def main_loop():
    while True:
        if ser.in_waiting:
            line = ser.readline().decode().strip()
            if line == "GETXY":
                print("Empfangen: GETXY")

                # Bild aufnehmen
                img_path = capture_image()
                if not img_path:
                    continue

                # YOLO ausführen oder Dummy verwenden
                if USE_DUMMY:
                    results = None  # Keine Ergebnisse im Dummy-Modus
                else:
                    results = model(img_path)

                # Koordinaten extrahieren (XY relativ zur Kamera)
                coords = extract_xy(results)

                # Koordinaten an Arduino senden
                for x, y in coords:
                    msg = f"XY:{x:.1f},{y:.1f}\n"
                    print("Sende:", msg.strip())
                    ser.write(msg.encode())
                    time.sleep(0.05)

                # Abschlussmeldung
                ser.write(b"DONE\n")
                print("Sende: DONE")

# Start
try:
    print("Starte Hauptloop...")
    main_loop()
except KeyboardInterrupt:
    print("Beendet.")
finally:
    ser.close()