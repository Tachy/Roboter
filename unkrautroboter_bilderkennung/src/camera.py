"""
Modul für die Kamera- und Stream-Funktionalität des Unkrautroboters.
"""

import io
import threading
import time
from PIL import Image, ImageDraw, ImageFont
from picamera2 import Picamera2 # type: ignore
from picamera2.encoders import MJPEGEncoder # type: ignore
from picamera2.outputs import FileOutput # type: ignore
from http.server import BaseHTTPRequestHandler, HTTPServer
from . import config

class MJPEGOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.lock = threading.Lock()

    def write(self, buf):
        with self.lock:
            image = Image.open(io.BytesIO(buf))
            draw = ImageDraw.Draw(image)

            # Text hinzufügen: CPU-Temperatur
            cpu_temp = get_cpu_temperature()
            text_temp = f"CPU: {cpu_temp}"
            font = ImageFont.load_default()
            draw.text((10, 10), text_temp, font=font, fill="white")

            # Text hinzufügen: Aktueller Modus
            from . import robot_control  # Vermeidet zirkuläre Importe
            text_mode = f"Modus: {robot_control.robot.get_mode()}"
            draw.text((10, 30), text_mode, font=font, fill="white")

            output = io.BytesIO()
            image.save(output, format="JPEG")
            self.frame = output.getvalue()

    def read(self, size=-1):
        with self.lock:
            return self.frame if self.frame else b""

class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not stream_active:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Stream ist deaktiviert.")
            return

        # Erlaube auch /stream?irgendwas
        if self.path.startswith('/stream'):
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while stream_active:
                    with stream_output.lock:
                        frame = stream_output.frame
                    if frame:
                        self.wfile.write(b'--FRAME\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    time.sleep(0.05)  # ~20 fps
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()

def get_cpu_temperature():
    """Liest die CPU-Temperatur des Raspberry Pi aus und gibt sie als String zurück."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = int(f.read().strip()) / 1000.0
        return f"{temp:.0f} °C"
    except FileNotFoundError:
        return "N/A"

# Kamera-Setup
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": config.CAMERA_RESOLUTION}))
picam2.start()  # Kamera grundsätzlich starten
stream_output = MJPEGOutput()
stream_active = False

def start_stream():
    """Startet den Video-Stream."""
    global stream_active
    try:
        if not stream_active:
            # Stelle sicher, dass die Kamera läuft
            if not picam2.started:
                picam2.start()
                time.sleep(0.5)
            picam2.start_recording(MJPEGEncoder(), FileOutput(stream_output))
            stream_active = True
            print("Stream aktiviert.")
    except Exception as e:
        print(f"Fehler beim Starten des Streams: {str(e)}")
        stream_active = False  # Setze Status auf inaktiv bei Fehler

def stop_stream():
    """Stoppt den Video-Stream."""
    global stream_active
    try:
        if stream_active:
            picam2.stop_recording()
            stream_active = False
            print("Stream deaktiviert.")
    except Exception as e:
        print(f"Fehler beim Stoppen des Streams: {str(e)}")
        stream_active = False  # Setze Status trotzdem auf inaktiv

def is_streaming():
    """Prüft, ob der Stream aktiv ist."""
    return stream_active

def capture_image(filename):
    """Nimmt ein einzelnes Bild auf."""
    try:
        print("Debug: Starte Bildaufnahme...")
        # Stelle sicher, dass die Kamera läuft
        if not picam2.started:
            print("Debug: Starte Kamera...")
            picam2.start()
            time.sleep(0.5)
            
        # Bild aufnehmen, ohne den Stream zu unterbrechen
        picam2.capture_file(filename)
        print(f"Debug: Bild erfolgreich aufgenommen: {filename}")
            
    except Exception as e:
        print(f"Fehler bei der Bildaufnahme: {str(e)}")
    print("Stream aktiviert.")

def capture_frame(filename="frame.jpg"):
    """Speichert das aktuelle Frame als Bild."""
    if stream_active:
        with stream_output.lock:
            frame = stream_output.frame
        if frame:
            with open(filename, "wb") as f:
                f.write(frame)
            return filename
        else:
            print("Kein Frame verfügbar")
            return None
    else:
        # Stream ist nicht aktiv, Einzelbild aufnehmen
        image = picam2.capture_array()
        img = Image.fromarray(image)
        # Konvertiere zu RGB wenn nötig
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert('RGB')
        img.save(filename, format="JPEG")
        return filename

def start_http_server():
    """Startet den HTTP-Server für den Stream."""
    server = HTTPServer(('', config.HTTP_PORT), StreamHandler)
    print(f"HTTP-Server läuft auf Port {config.HTTP_PORT}...")
    server.serve_forever()
