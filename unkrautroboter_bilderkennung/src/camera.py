"""
Modul für die Kamera- und Stream-Funktionalität des Unkrautroboters.
"""

import threading
import time
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2  # JPEG-Encode für den MJPEG-Stream
from picamera2 import Picamera2  # type: ignore

from . import config

# Logger einrichten
logger = logging.getLogger("camera")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


class _StreamFrameBuffer:
    """Thread-sicherer Halter für das jüngste JPEG-Frame des MJPEG-Streams."""

    def __init__(self):
        self.frame = None
        self.lock = threading.Lock()


class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # HTTP-Requests ins Logging-Modul umleiten
        logger.info(
            "%s - - [%s] %s"
            % (self.client_address[0], self.log_date_time_string(), format % args)
        )

    def do_GET(self):
        # /last_capture: immer bedienen, auch wenn der Stream aus ist
        if self.path.startswith("/last_capture"):
            # Letztes aufgenommenes Bild ausliefern (PNG, Originalauflösung)
            with _last_capture_lock:
                data = _last_capture_bytes
                ts = _last_capture_ts
            if data:
                try:
                    self.send_response(200)
                    self.send_header("Age", "0")
                    self.send_header(
                        "Cache-Control", "no-cache, private, max-age=0, must-revalidate"
                    )
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                    try:
                        human_ts = (
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                            if ts
                            else "-"
                        )
                        logger.info(
                            f"/last_capture an {self.client_address[0]} gesendet ({len(data)} Bytes, ts={human_ts})"
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                self.send_response(404)
                self.end_headers()
            return

        # /stream nur bedienen, wenn aktiviert; erlaube auch /stream?irgendwas
        if self.path.startswith("/stream"):
            if not stream_active:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"Stream ist deaktiviert.")
                return
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.end_headers()
            try:
                while stream_active:
                    with stream_output.lock:
                        frame = stream_output.frame
                    if frame:
                        self.wfile.write(
                            b"--FRAME\r\nContent-Type: image/jpeg\r\n\r\n"
                            + frame
                            + b"\r\n"
                        )
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


# Letztes aufgenommenes Bild im Speicher halten, inkl. Zeitstempel. Immer
# verlustfrei als PNG in Originalauflösung — Skalierung fürs Anzeigen macht
# der Browser, nicht der Server.
_last_capture_lock = threading.Lock()
_last_capture_bytes: bytes | None = None
_last_capture_ts: float | None = None


def _set_last_capture_bytes(data: bytes) -> None:
    """Safely store last-capture PNG bytes and timestamp."""
    global _last_capture_bytes, _last_capture_ts
    with _last_capture_lock:
        _last_capture_bytes = data
        _last_capture_ts = time.time()


def _encode_and_store_last_capture(bgr_image) -> bool:
    """Encode a BGR image to PNG (Originalauflösung, verlustfrei) and store it
    for /last_capture.png. Returns True on success."""
    try:
        ok_enc, enc = cv2.imencode(".png", bgr_image)
        if ok_enc:
            _set_last_capture_bytes(enc.tobytes())
            return True
    except Exception:
        pass
    return False


def get_last_capture_timestamp():
    """Gibt den Zeitstempel (epoch seconds, float) des letzten capture_image-Aufrufs zurück, sonst None."""
    with _last_capture_lock:
        return _last_capture_ts


def _stream_loop():
    """Software-MJPEG: kleines `lores`-Frame holen, JPEG kodieren, ablegen.
    Läuft durchgehend; `main` bleibt für Einzelbilder ungestört verfügbar."""
    period = 1.0 / 12.0  # ~12 fps für die Vorschau
    q = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
    while not _stream_stop.is_set():
        t0 = time.monotonic()
        try:
            yuv = picam2.capture_array("lores")
            bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
            ok, jpg = cv2.imencode(".jpg", bgr, q)
            if ok:
                with stream_output.lock:
                    stream_output.frame = jpg.tobytes()
        except Exception as e:
            logger.debug(f"Stream-Frame fehlgeschlagen: {e}")
            time.sleep(0.2)
        dt = time.monotonic() - t0
        if dt < period:
            time.sleep(period - dt)


def start_stream():
    """Startet den Software-MJPEG-Stream (lores). `main` (Vollauflösung) bleibt
    parallel für Einzelbilder verfügbar – kein Mode-Switch, kein Encoder-Neustart."""
    global stream_active, _stream_thread
    try:
        if stream_active:
            return
        if not picam2.started:
            picam2.start()
            time.sleep(0.3)
        _stream_stop.clear()
        _stream_thread = threading.Thread(
            target=_stream_loop, name="mjpeg-sw", daemon=True
        )
        _stream_thread.start()
        stream_active = True
        logger.info("Stream (Software-MJPEG / lores) aktiviert.")
    except Exception as e:
        logger.error(f"Fehler beim Starten des Streams: {str(e)}")
        stream_active = False


def stop_stream():
    """Stoppt den Software-MJPEG-Stream."""
    global stream_active, _stream_thread
    try:
        if not stream_active:
            return
        _stream_stop.set()
        if _stream_thread is not None:
            _stream_thread.join(timeout=1.5)
        _stream_thread = None
        stream_active = False
        logger.info("Stream (Software) deaktiviert.")
    except Exception as e:
        logger.error(f"Fehler beim Stoppen des Streams: {str(e)}")
        stream_active = False


def is_streaming():
    """Prüft, ob der Stream aktiv ist."""
    return stream_active


# Kamera-Helfer
def is_camera_started() -> bool:
    return picam2.started


def ensure_camera_started() -> bool:
    """Sicherstellen, dass die Kamera läuft. Liefert True, wenn sie hier gestartet wurde."""
    if not picam2.started:
        logger.debug("Starte Kamera...")
        picam2.start()
        time.sleep(0.5)
        return True
    return False


def capture_image(filename: str, size):
    """Nimmt ein Rohbild in `size` (w,h) vom `main`-Stream auf, aktualisiert die
    `/last_capture`-Vorschau und speichert es unter `filename` (Format nach
    Endung, .png = verlustfrei). Wirft bei Fehlschlag."""
    logger.debug("Starte Bildaufnahme...")
    started_here = ensure_camera_started()
    try:
        bgr = capture_still_array(size)
        _encode_and_store_last_capture(bgr)
        if not cv2.imwrite(filename, bgr):
            raise RuntimeError(f"cv2.imwrite fehlgeschlagen: {filename}")
        logger.info(f"Bild aufgenommen: {filename}")
        return filename
    finally:
        if started_here and not stream_active:
            try:
                picam2.stop()
                logger.info("Kamera nach Einzelaufnahme gestoppt (kein aktiver Stream).")
            except Exception:
                pass


def start_http_server():
    """Startet den HTTP-Server für den Stream."""
    server = ThreadingHTTPServer(("", config.HTTP_PORT), StreamHandler)
    logger.info(f"HTTP-Server läuft auf Port {config.HTTP_PORT}...")
    server.serve_forever()


# Kamera-Setup: EIN Modus, zwei Ausgänge (kein Mode-Switch).
#  main  = RGB888, volle Auflösung -> Einzelbilder (capture_array("main"))
#  lores = YUV420, klein -> MJPEG-Stream (Software-JPEG in einem Thread,
#          läuft durchgehend, wird nie umkonfiguriert)
picam2 = Picamera2()
_video_config = picam2.create_video_configuration(
    main={"size": tuple(config.CAPTURE_RESOLUTION), "format": "RGB888"},
    lores={"size": tuple(config.CAMERA_RESOLUTION), "format": "YUV420"},
    buffer_count=4,
)
picam2.configure(_video_config)
stream_output = _StreamFrameBuffer()
stream_active = False
_stream_thread = None
_stream_stop = threading.Event()


def _arr_to_bgr(arr):
    """picamera2-Array -> BGR-ndarray für OpenCV.

    Das `main`-Stream-Format ist "RGB888" konfiguriert, aber picamera2 liefert
    dafür (laut eigenem FORMAT_TABLE in picamera2/request.py: "RGB888": "BGR")
    bereits Bytes in B,G,R-Reihenfolge — exakt was OpenCV erwartet. Ein
    zusätzlicher COLOR_RGB2BGR-Tausch hier würde Rot und Blau ein zweites Mal
    vertauschen und Bilder falsch einfärben.
    """
    if arr is None:
        return None
    if arr.ndim == 3 and arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    return arr


def capture_still_array(size=None, n: int = 1):
    """Holt n Bilder vom `main`-Stream (volle Auflösung). KEIN Mode-Switch – der
    MJPEG-Stream (`lores`) läuft ununterbrochen weiter.

    size: nur zum optionalen Runterskalieren; None/Vollauflösung -> unverändert.
    Rückgabe: BGR-ndarray (n == 1) oder Liste von BGR-ndarrays.
    Wirft bei Fehlschlag.
    """
    ensure_camera_started()
    frames = []
    for i in range(max(1, n)):
        bgr = _arr_to_bgr(picam2.capture_array("main"))
        if bgr is None:
            raise RuntimeError("capture_array('main') lieferte kein Bild")
        if size is not None:
            tw, th = int(size[0]), int(size[1])
            if (bgr.shape[1], bgr.shape[0]) != (tw, th):
                bgr = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
        frames.append(bgr)
    return frames[0] if n == 1 else frames
