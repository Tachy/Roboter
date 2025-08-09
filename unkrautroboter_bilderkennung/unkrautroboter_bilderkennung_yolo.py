import serial
import time
import os
import socket
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading, io
from PIL import Image, ImageDraw, ImageFont
import glob

# Setup
USE_SIMULATED_SERIAL = True  # Auf True setzen, um die lokale Simulation zu aktivieren
SIMULATED_SERIAL_PORT = '/dev/pts/2'  # Virtueller Port für die Simulation
SERIAL_PORT = '/dev/serial0'  # Echter serieller Port
BAUDRATE = 115200

# UDP-Steuerkanäle
UDP_IP = "0.0.0.0"  # Hört auf alle Schnittstellen
UDP_CONTROL_PORT = 5005  # Port für Modusumschaltung
UDP_JOYSTICK_PORT = 5006  # Port für Joystick-Kommandos

# Dummy-Modus aktivieren
USE_DUMMY = True  # Auf False setzen, wenn das echte YOLO-Modell verwendet wird

if not USE_DUMMY:
    from ultralytics import YOLO
    model = YOLO("pfad/zum/modell.pt")  # z. B. "best.pt"

# Globale Variablen
mode = "AUTO"  # AUTO oder MANUAL
lock = threading.Lock()
stream_active = False  # Status des Streams

# Serielle Verbindung
if USE_SIMULATED_SERIAL:
    if not os.path.exists(SIMULATED_SERIAL_PORT):
        raise FileNotFoundError(f"Simulierter serieller Port {SIMULATED_SERIAL_PORT} existiert nicht. Starte socat!")
    ser = serial.Serial(SIMULATED_SERIAL_PORT, BAUDRATE, timeout=1)
    print(f"Verwende simulierten seriellen Port: {SIMULATED_SERIAL_PORT}")
else:
    ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
    print(f"Verwende echten seriellen Port: {SERIAL_PORT}")

time.sleep(2)  # Zeit für Verbindungsaufbau

# Kamera-Setup für Livestream
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (1280, 720)}))

def get_cpu_temperature():
    """Liest die CPU-Temperatur des Raspberry Pi aus und gibt sie als String zurück."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = int(f.read().strip()) / 1000.0  # Temperatur in °C umrechnen
        return f"{temp:.0f} °C"
    except FileNotFoundError:
        return "N/A"  # Falls die Datei nicht existiert

class MJPEGOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.lock = threading.Lock()

    def write(self, buf):
        with self.lock:
            # Frame als Bild laden
            image = Image.open(io.BytesIO(buf))
            draw = ImageDraw.Draw(image)

            # Text hinzufügen: CPU-Temperatur
            cpu_temp = get_cpu_temperature()
            text_temp = f"CPU: {cpu_temp}"
            font = ImageFont.load_default()  # Standard-Schriftart
            draw.text((10, 10), text_temp, font=font, fill="white")

            # Text hinzufügen: Aktueller Modus
            global mode
            text_mode = f"Modus: {mode}"
            draw.text((10, 30), text_mode, font=font, fill="white")

            # Bild zurück in Bytes konvertieren
            output = io.BytesIO()
            image.save(output, format="JPEG")
            self.frame = output.getvalue()

    def read(self, size=-1):
        with self.lock:
            return self.frame if self.frame else b""

out = MJPEGOutput()

# HTTP-Handler für Livestream
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global stream_active
        if not stream_active:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Stream ist deaktiviert.")
            return

        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while stream_active:
                    with out.lock:
                        frame = out.frame
                    if frame:
                        self.wfile.write(b'--FRAME\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    time.sleep(0.05)  # ~20 fps
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()

def start_http_server():
    server = HTTPServer(('', 8080), Handler)
    print("HTTP-Server läuft auf Port 8080...")
    server.serve_forever()

# Funktion, um den Stream zu starten oder zu stoppen
def update_stream_status():
    global stream_active
    with lock:
        if mode == "MANUAL" and not stream_active:
            picam2.start_recording(MJPEGEncoder(), FileOutput(out))
            stream_active = True
            print("Stream aktiviert.")
        elif mode == "AUTO" and stream_active:
            picam2.stop_recording()
            stream_active = False
            print("Stream deaktiviert.")

# UDP-Steuerkanal für Modusumschaltung
def udp_control_server():
    global mode
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_CONTROL_PORT))
    print(f"UDP-Steuerkanal läuft auf Port {UDP_CONTROL_PORT}...")
    while True:
        data, addr = sock.recvfrom(1024)  # Empfang von Daten (max. 1024 Bytes)
        command = data.decode().strip().upper()
        if command in ["AUTO", "MANUAL"]:
            with lock:
                mode = command
            print(f"Modus auf {mode} geändert (von {addr})")
            update_stream_status()
        else:
            print(f"Unbekannter Befehl: {command} (von {addr})")

# Funktion, um die nächste Bildnummer zu ermitteln
def get_next_image_number(directory="./training/"):
    if not os.path.exists(directory):
        os.makedirs(directory)  # Verzeichnis erstellen, falls nicht vorhanden
    files = glob.glob(os.path.join(directory, "bild_*.jpg"))
    if not files:
        return 1  # Start bei 1, wenn keine Dateien vorhanden sind
    numbers = [int(os.path.basename(f).split("_")[1].split(".")[0]) for f in files]
    return max(numbers) + 1

# Funktion, um ein Bild aufzunehmen und zu speichern
def save_training_image():
    img_path = "./training/"
    next_number = get_next_image_number(img_path)
    filename = os.path.join(img_path, f"bild_{next_number:04d}.jpg")
    picam2.capture_file(filename)
    print(f"Bild gespeichert: {filename}")

# UDP-Server für Joystick-Kommandos
def udp_joystick_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_JOYSTICK_PORT))
    print(f"UDP-Joystick-Server läuft auf Port {UDP_JOYSTICK_PORT}...")
    while True:
        data, addr = sock.recvfrom(1024)  # Empfang von Daten (max. 1024 Bytes)
        command = data.decode().strip()
        with lock:
            if mode == "MANUAL":
                print(f"Joystick-Befehl empfangen: {command} (von {addr})")
                if "BUTTON:1" in command:  # Feuerknopf 1 gedrückt
                    save_training_image()
                # Joystick-Befehl an Arduino weiterleiten
                ser.write(f"{command}\n".encode())
                print(f"Joystick-Befehl an Arduino gesendet: {command}")
            else:
                print(f"Joystick-Befehl ignoriert, da Modus {mode} aktiv ist.")

# Funktion, um ein einzelnes Frame aus dem Stream zu holen
def capture_frame(filename="frame.jpg"):
    with out.lock:
        frame = out.frame
    if frame:
        with open(filename, "wb") as f:
            f.write(frame)
        return filename
    else:
        print("Kein Frame verfügbar")
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

# Hauptloop
def main_loop():
    global mode
    while True:
        time.sleep(0.1)  # CPU-Entlastung

        with lock:
            current_mode = mode
        if current_mode == "AUTO" and ser.in_waiting:
            line = ser.readline().decode().strip()
            if line == "GETXY":
                print("Empfangen: GETXY")

                # Einzelnes Frame aus dem Stream holen
                img_path = capture_frame()
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
        elif current_mode == "MANUAL":
            continue
        
# Start
try:
    print("Starte HTTP-Server...")
    threading.Thread(target=start_http_server, daemon=True).start()
    print("Starte UDP-Steuerkanal...")
    threading.Thread(target=udp_control_server, daemon=True).start()
    print("Starte UDP-Joystick-Server...")
    threading.Thread(target=udp_joystick_server, daemon=True).start()
    print("Starte Hauptloop...")
    main_loop()
except KeyboardInterrupt:
    print("Beendet.")
finally:
    ser.close()
    if stream_active:
        picam2.stop_recording()