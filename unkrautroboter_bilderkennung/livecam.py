from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading, io, time

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (1280, 720)}))

# MJPEGOutput anpassen, damit es wie ein gepufferter IO-Stream funktioniert
class MJPEGOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.lock = threading.Lock()

    def write(self, buf):
        with self.lock:
            self.frame = buf

    def read(self, size=-1):
        with self.lock:
            return self.frame if self.frame else b""

out = MJPEGOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(out))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/stream':
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Age', '0')
        self.send_header('Cache-Control', 'no-cache, private')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
        self.end_headers()
        try:
            while True:
                with out.lock:
                    frame = out.frame
                if frame:
                    self.wfile.write(b'--FRAME\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(0.05)  # ~20 fps; für „extrem langsam“ z.B. 0.5
        except Exception:
            pass

# HTTP-Server starten
HTTPServer(('', 8080), Handler).serve_forever()