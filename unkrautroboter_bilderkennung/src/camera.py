"""
Modul für die Kamera- und Stream-Funktionalität des Unkrautroboters.
"""

import io
from pathlib import Path
import threading
import time
import logging
from . import config
import numpy as np
import cv2  # für Undistortion-Remap
from picamera2 import Picamera2 # type: ignore
from picamera2.encoders import MJPEGEncoder # type: ignore
from picamera2.outputs import FileOutput # type: ignore
from http.server import BaseHTTPRequestHandler, HTTPServer
from . import config

# Logger einrichten
logger = logging.getLogger("camera")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )

class MJPEGOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.lock = threading.Lock()

    def write(self, buf):
        # Wird vom Hardware-MJPEG-Encoder aufgerufen
        with self.lock:
            self.frame = buf

    def read(self, size=-1):
        with self.lock:
            return self.frame if self.frame else b""

class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # HTTP-Requests ins Logging-Modul umleiten
        logger.info("%s - - [%s] %s" % (
            self.client_address[0],
            self.log_date_time_string(),
            format % args
        ))
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

# ==== Kalibrierung / Undistortion (nur für Einzelbilder) ====
_calib_loaded = False
_calib_K = None
_calib_D = None
_calib_img_size = None  # (W, H) aus der Datei
_calib_map1 = None
_calib_map2 = None
_undistort_cache = {}  # {(w,h): (map1, map2)}

def _ensure_calibration_loaded():
    """Lädt Kalibrierungsdaten aus ./calibration/cam_calib_charuco.npz, wenn vorhanden."""
    global _calib_loaded, _calib_K, _calib_D, _calib_img_size, _calib_map1, _calib_map2
    if _calib_loaded:
        return True
    calib_path = Path("./calibration/cam_calib_charuco.npz")
    if not calib_path.exists():
        logger.warning("Keine Kalibrierungsdatei gefunden: ./calibration/cam_calib_charuco.npz – speichere ungefilterte Bilder.")
        _calib_loaded = False
        return False
    try:
        d = np.load(str(calib_path), allow_pickle=True)
        _calib_K = d["K"].astype(np.float64)
        _calib_D = d["D"].astype(np.float64)
        try:
            sz = d["img_size"]
            _calib_img_size = (int(sz[0]), int(sz[1]))  # (W,H)
        except Exception:
            _calib_img_size = None
        # map1/map2 optional verwenden, wenn Größen passen
        _calib_map1 = d.get("map1", None)
        _calib_map2 = d.get("map2", None)
        _calib_loaded = True
        logger.info(f"Kalibrierung geladen (K,D) aus ./calibration/cam_calib_charuco.npz; img_size={_calib_img_size}")
        return True
    except Exception as e:
        logger.error(f"Kalibrierung konnte nicht geladen werden: {e}")
        _calib_loaded = False
        return False

def _get_maps_for_size(width: int, height: int):
    """Erzeugt/cached Remap-Tabellen für gegebene Größe basierend auf K,D.
    Berechnet newK für die Zielgröße automatisch (alpha=0).
    """
    key = (width, height)
    if key in _undistort_cache:
        return _undistort_cache[key]
    if not _ensure_calibration_loaded():
        return None
    try:
        # Wenn die Größen exakt passen und map1/map2 vorhanden sind, nutze sie direkt
        if _calib_img_size == (width, height) and _calib_map1 is not None and _calib_map2 is not None:
            logger.debug("Verwende gespeicherte Remap-Tabellen aus Kalibrierungsdatei.")
            map1, map2 = _calib_map1, _calib_map2
        else:
            # Prüfe Aspect-Ratio – bei Abweichung warnen
            if _calib_img_size is not None:
                cw, ch = _calib_img_size
                ar0 = cw / ch
                ar1 = width / height
                if abs(ar0 - ar1) > 1e-3:
                    logger.warning(f"Abweichende Aspect-Ratio (calib {cw}x{ch} vs capture {width}x{height}) – Verzerrungen möglich.")
                # Skaliere K auf Zielgröße
                sx = width / cw
                sy = height / ch
                K_scaled = _calib_K.copy()
                K_scaled[0,0] *= sx
                K_scaled[0,2] *= sx
                K_scaled[1,1] *= sy
                K_scaled[1,2] *= sy
            else:
                # keine Info zu Kalibriergröße – versuche unskaliert (kann verzerren)
                logger.warning("Kalibriergröße unbekannt – verwende unskaliertes K. Besser mit gleicher Auflösung kalibrieren.")
                K_scaled = _calib_K

            img_size = (width, height)
            newK, roi = cv2.getOptimalNewCameraMatrix(K_scaled, _calib_D, img_size, alpha=0)
            map1, map2 = cv2.initUndistortRectifyMap(K_scaled, _calib_D, None, newK, img_size, cv2.CV_16SC2)
        _undistort_cache[key] = (map1, map2)
        return map1, map2
    except Exception as e:
        logger.error(f"Fehler beim Erzeugen der Remap-Tabellen: {e}")
        return None

# Overlay-Unterstützung entfällt im Hardware-Stream vollständig

def reload_calibration():
    """Leert den Map-Cache und lädt Kalibrierung neu (z. B. nach neuer Kalibrierdatei)."""
    global _undistort_cache, _calib_loaded
    _undistort_cache.clear()
    _calib_loaded = False
    _ensure_calibration_loaded()

def start_stream():
    """Startet den Video-Stream (Hardware-MJPEG, keine Undistortion)."""
    global stream_active
    try:
        if not stream_active:
            if not picam2.started:
                picam2.start()
                time.sleep(0.5)
            picam2.start_recording(MJPEGEncoder(), FileOutput(stream_output))
            stream_active = True
            logger.info("Stream (Hardware MJPEG) aktiviert.")
    except Exception as e:
        logger.error(f"Fehler beim Starten des Streams: {str(e)}")
        stream_active = False

def stop_stream():
    """Stoppt den Video-Stream."""
    global stream_active
    try:
        if stream_active:
            picam2.stop_recording()
            stream_active = False
            logger.info("Stream (Hardware) deaktiviert.")
    except Exception as e:
        logger.error(f"Fehler beim Stoppen des Streams: {str(e)}")
        stream_active = False

def is_streaming():
    """Prüft, ob der Stream aktiv ist."""
    return stream_active

def capture_image(filename, apply_calibration: bool = False):
    """Nimmt ein einzelnes Bild auf. Optional mit Undistortion per Kalibrierungsdatei."""
    try:
        logger.debug("Starte Bildaufnahme...")
        # Stelle sicher, dass die Kamera läuft
        if not picam2.started:
            logger.debug("Starte Kamera...")
            picam2.start()
            time.sleep(0.5)
        if apply_calibration:
            # Array aufnehmen (RGB), zu BGR konvertieren für OpenCV, undistorten, speichern
            arr = picam2.capture_array()
            if arr is None:
                raise RuntimeError("capture_array lieferte None")
            # Picamera2 liefert RGB
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR) if arr.ndim == 3 and arr.shape[2] == 3 else arr
            h, w = bgr.shape[:2]
            # Hinweis loggen, falls Zielgröße nicht der Kalibriergröße entspricht
            if _ensure_calibration_loaded() and _calib_img_size and (w, h) != _calib_img_size:
                logger.info(f"Undistortion bei {w}x{h}, Kalibrierung bei {_calib_img_size} – skaliere K entsprechend.")
            mm = _get_maps_for_size(w, h)
            if mm is not None:
                map1, map2 = mm
                undist = cv2.remap(bgr, map1, map2, interpolation=cv2.INTER_LINEAR)
                ok = cv2.imwrite(filename, undist)
                if not ok:
                    raise RuntimeError("cv2.imwrite fehlgeschlagen")
                logger.info(f"Bild (undistorted) aufgenommen: {filename}")
            else:
                # Fallback: direkt speichern
                ok = cv2.imwrite(filename, bgr)
                if not ok:
                    raise RuntimeError("cv2.imwrite fehlgeschlagen (Fallback)")
                logger.info(f"Bild (ohne Kalibrierung) aufgenommen: {filename}")
        else:
            # Bild aufnehmen, ohne den Stream zu unterbrechen (direkt als Datei)
            picam2.capture_file(filename)
            logger.info(f"Bild erfolgreich aufgenommen: {filename}")
    except Exception as e:
        logger.error(f"Fehler bei der Bildaufnahme: {str(e)}")
    logger.debug("Stream aktiviert.")

def start_http_server():
    """Startet den HTTP-Server für den Stream."""
    server = HTTPServer(('', config.HTTP_PORT), StreamHandler)
    logger.info(f"HTTP-Server läuft auf Port {config.HTTP_PORT}...")
    server.serve_forever()

# Kamera-Setup
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": config.CAMERA_RESOLUTION}))
picam2.start()  # Kamera grundsätzlich starten
stream_output = MJPEGOutput()
stream_active = False

# Keine Software-Stream-Schleife mehr


