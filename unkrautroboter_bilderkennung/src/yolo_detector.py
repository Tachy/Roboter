"""
Modul für die YOLO-Integration des Unkrautroboters.
"""

from . import config, camera
import cv2
import logging
import os
import multiprocessing as mp
import numpy as np
import time
import shutil

# Logger einrichten
logger = logging.getLogger("yolo_detector")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=getattr(config, 'LOGLEVEL', logging.INFO), format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')

if not config.USE_DUMMY:
    from ultralytics import YOLO
    _weights = getattr(config, 'YOLO_MODEL_PATH', 'best.pt')
    _weights_abs = None
    model = None
    try:
        _weights_abs = os.path.abspath(_weights)
        if not os.path.exists(_weights_abs):
            logger.error(f"[YOLO] Gewichtsdatei nicht gefunden: {_weights} (abspath={_weights_abs})")
        elif not os.path.isfile(_weights_abs):
            logger.error(f"[YOLO] Gewichts-Pfad ist kein File: {_weights} (abspath={_weights_abs})")
        else:
            size = 0
            try:
                size = os.path.getsize(_weights_abs)
            except Exception:
                pass
            model = YOLO(_weights_abs)
            logger.info(f"[YOLO] Modell geladen: {_weights_abs} ({size} Bytes)")
    except Exception as e:
        logger.exception(f"[YOLO] Konnte Modell nicht laden: {e}")

# Globale Maxima für Ressourcenverbrauch (über Laufzeit)
_PEAK_RSS_KB = 0
_PEAK_TMP_USED_BYTES = 0

# Multiprocessing-Startmethode festlegen
# Hinweis: Auf Linux bevorzugen wir 'fork', um einen Re-Import von main und damit
# eine erneute Kamera-Initialisierung im Kindprozess zu vermeiden. Auf Plattformen
# ohne 'fork' (z. B. Windows) verwenden wir 'spawn'.
try:
    methods = mp.get_all_start_methods()
    if 'fork' in methods:
        mp.set_start_method('fork')
    else:
        mp.set_start_method('spawn')
except RuntimeError:
    # Bereits gesetzt – ignorieren
    pass

def _mp_predict_worker(queue, image_path, weights, device, imgsz, conf, iou, use_parent_model=False):
    """Subprozess-Worker: Lädt YOLO, führt Inferenz aus und gibt (coords, annotated_jpeg_path) zurück.

    Wichtiger Hinweis: Um Deadlocks aufgrund begrenzter Pipe-Puffer zu vermeiden, wird die annotierte
    Vorschau nicht über die Queue (Bytes) übertragen, sondern in eine temporäre Datei geschrieben und
    nur der Dateipfad übergeben.
    """
    try:
        # Threads drosseln, um Stabilität zu erhöhen
        import os as _os
        _os.environ.setdefault('OMP_NUM_THREADS', '1')
        _os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
        _os.environ.setdefault('MKL_NUM_THREADS', '1')
        _os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')
        try:
            import torch as _torch
            _torch.set_num_threads(1)
            if hasattr(_torch, 'set_num_interop_threads'):
                _torch.set_num_interop_threads(1)
        except Exception:
            pass
        # WICHTIG: Nur Ultralytics und OpenCV importieren; keine Projekt-Module importieren,
        # damit der Kindprozess keine Kamera initialisiert o. Ä.
        mdl = None
        if use_parent_model and ('model' in globals()) and (globals().get('model') is not None):
            # Unter 'fork' können wir das bereits geladene Modell nutzen (schneller, da kein Reload)
            mdl = globals().get('model')
        else:
            from ultralytics import YOLO as _YOLO
            mdl = _YOLO(weights)
        import cv2 as _cv2
        # Vorhersage ausführen
        res = mdl.predict(source=image_path, device=device, imgsz=imgsz, conf=conf, iou=iou, verbose=False, stream=False, save=False, workers=0)
        coords = []
        try:
            for r in res:
                # Direkt über den xywh-Tensor iterieren (Nx4: x,y,w,h)
                if hasattr(r, 'boxes') and hasattr(r.boxes, 'xywh') and r.boxes.xywh is not None:
                    for xywh in r.boxes.xywh:
                        try:
                            x_center = float(xywh[0].item())
                            y_center = float(xywh[1].item())
                            coords.append((x_center, y_center))
                        except Exception:
                            continue
        except Exception:
            coords = []
        annotated_path = None
        ann_size = None
        tmp_used_after = None
        tmp_total = None
        try:
            if res and len(res) > 0:
                ann = res[0].plot()
                if ann is not None:
                    if ann.ndim == 3 and ann.shape[2] == 4:
                        ann = _cv2.cvtColor(ann, _cv2.COLOR_RGBA2BGR)
                    import tempfile as _tmp
                    import os as _os2
                    # In temporäre Datei schreiben
                    fd, tmppath = _tmp.mkstemp(prefix="yolo_ann_", suffix=".jpg")
                    try:
                        _os2.close(fd)
                        ok = _cv2.imwrite(tmppath, ann, [int(_cv2.IMWRITE_JPEG_QUALITY), 85])
                        if ok:
                            annotated_path = tmppath
                            try:
                                ann_size = _os2.path.getsize(tmppath)
                            except Exception:
                                ann_size = None
                            try:
                                du = shutil.disk_usage('/tmp')
                                tmp_used_after = du.used
                                tmp_total = du.total
                            except Exception:
                                tmp_used_after = None
                                tmp_total = None
                        else:
                            try:
                                _os2.remove(tmppath)
                            except Exception:
                                pass
                    except Exception:
                        try:
                            _os2.remove(tmppath)
                        except Exception:
                            pass
        except Exception:
            annotated_path = None
        # Peak-RAM erfassen (nur Unix): ru_maxrss in KB
        mem_peak_kb = None
        try:
            import resource as _resource
            mem_peak_kb = int(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss)
        except Exception:
            mem_peak_kb = None
        # Ergebnisse als Dict zurückgeben
        queue.put({
            'coords': coords,
            'ann_path': annotated_path,
            'mem_peak_kb': mem_peak_kb,
            'tmp_used': tmp_used_after,
            'tmp_total': tmp_total,
            'ann_size': ann_size,
        })
    except Exception:
        # Bei Fehlern leeres Ergebnis zurückgeben
        try:
            queue.put(([], None))
        except Exception:
            pass
    # Optional: Threads/Resourcen-Logging (unterdrückt, um Rauschen zu vermeiden)

def extract_xy(results):
    """Extrahiert die Koordinaten aus den YOLO-Ergebnissen robust aus dem xywh-Tensor."""
    if config.USE_DUMMY:
        # Dummy-Koordinaten zurückgeben
        return [(100.0, 200.0)]  # Beispielkoordinaten
    coordinates = []
    try:
        for r in results or []:
            # Erwartet r.boxes.xywh als Tensor der Form (N,4)
            if hasattr(r, 'boxes') and hasattr(r.boxes, 'xywh') and r.boxes.xywh is not None:
                for xywh in r.boxes.xywh:
                    try:
                        x_center = float(xywh[0].item())
                        y_center = float(xywh[1].item())
                        coordinates.append((x_center, y_center))
                    except Exception:
                        continue
    except Exception:
        # Bei Strukturänderungen in Ultralytics lieber leer zurückgeben als crashen
        return []
    return coordinates

def process_image(image_path):
    """Verarbeitet ein Bild mit YOLO und gibt die Koordinaten zurück."""
    if config.USE_DUMMY:
        logger.info(f"[YOLO] Dummy-Modus aktiv. Bild: {image_path}")
        coords = extract_xy(None)
        # Optional: Dummy-Overlay in der Vorschau anzeigen
        try:
            img = cv2.imread(image_path)
            if img is not None and len(coords) > 0:
                x, y = int(coords[0][0]), int(coords[0][1])
                cv2.circle(img, (x, y), 10, (0, 255, 0), 2)
                camera._encode_and_store_last_capture(img, quality=85)
                logger.info(f"[YOLO] Dummy-Preview aktualisiert. Erste Position: ({x},{y})")
        except Exception:
            pass
        logger.info(f"[YOLO] Dummy-Ergebnisse: {len(coords)} Position(en)")
        return coords
    else:
        logger.info(f"[YOLO] Starte Inferenz: {image_path}")
        if 'model' not in globals() or model is None:
            logger.error("[YOLO] Kein Modell verfügbar. Prüfe YOLO_MODEL_PATH oder setze USE_DUMMY=True.")
            return []
        # Vorab Eingabe prüfen
        if not image_path or not os.path.isfile(image_path):
            logger.error(f"[YOLO] Bild nicht gefunden: {image_path}")
            return []
        try:
            _probe = cv2.imread(image_path)
            if _probe is None:
                logger.error(f"[YOLO] Bild konnte nicht gelesen werden: {image_path}")
                return []
        except Exception as e:
            logger.error(f"[YOLO] Bildlesefehler: {e}")
            return []
        # Parameter zusammenstellen
        device = getattr(config, 'YOLO_DEVICE', 'cpu')
        imgsz = int(getattr(config, 'YOLO_IMG_SIZE', 640))
        conf = float(getattr(config, 'YOLO_CONF', 0.25))
        iou = float(getattr(config, 'YOLO_IOU', 0.45))

    # Inferenz in separatem Prozess (robust gegen native Crashes)
    if _weights_abs or True:
            use_fork = ('fork' in mp.get_all_start_methods())
            ctx = mp.get_context('fork' if use_fork else 'spawn')
            q = ctx.Queue(maxsize=1)
            p = ctx.Process(target=_mp_predict_worker, args=(q, image_path, _weights_abs or _weights, device, imgsz, conf, iou, use_fork))
            p.start()
            t0 = time.time()
            timeout_s = float(getattr(config, 'YOLO_TIMEOUT_SEC', 30))
            p.join(timeout=timeout_s)
            if p.is_alive():
                try:
                    p.terminate()
                except Exception:
                    pass
                logger.error(f"[YOLO] Inferenz-Timeout – Subprozess beendet (>{timeout_s:.1f}s).")
                return []
            if p.exitcode != 0:
                dur = (time.time() - t0) * 1000.0
                logger.error(f"[YOLO] Inferenz-Subprozess exitcode={p.exitcode} nach {dur:.0f}ms")
                return []
            try:
                payload = q.get_nowait()
            except Exception:
                payload = None
            coords, ann_path = [], None
            mem_peak_kb = None
            tmp_used = None
            tmp_total = None
            ann_size = None
            if isinstance(payload, dict):
                coords = payload.get('coords') or []
                ann_path = payload.get('ann_path')
                mem_peak_kb = payload.get('mem_peak_kb')
                tmp_used = payload.get('tmp_used')
                tmp_total = payload.get('tmp_total')
                ann_size = payload.get('ann_size')
            elif isinstance(payload, (list, tuple)) and len(payload) >= 2:
                coords, ann_path = payload[0], payload[1]
            # Preview veröffentlichen (lesen aus temporärer Datei)
            if ann_path:
                try:
                    with open(ann_path, 'rb') as f:
                        ann_bytes = f.read()
                    try:
                        if hasattr(camera, '_set_last_capture_bytes'):
                            camera._set_last_capture_bytes(ann_bytes)  # type: ignore
                        else:
                            nparr = np.frombuffer(ann_bytes, dtype=np.uint8)
                            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            if img is not None:
                                camera._encode_and_store_last_capture(img, quality=85)
                    finally:
                        try:
                            import os as _os3
                            _os3.remove(ann_path)
                        except Exception:
                            pass
                except Exception:
                    pass
            # Globale Maxima aktualisieren und loggen
            global _PEAK_RSS_KB, _PEAK_TMP_USED_BYTES
            if isinstance(mem_peak_kb, int) and mem_peak_kb > 0:
                if mem_peak_kb > _PEAK_RSS_KB:
                    _PEAK_RSS_KB = mem_peak_kb
                try:
                    cur_mb = mem_peak_kb / 1024.0
                    max_mb = _PEAK_RSS_KB / 1024.0
                    logger.info(f"[YOLO] RAM: max_peak={max_mb:.1f} MB (dieser Lauf: {cur_mb:.1f} MB)")
                except Exception:
                    pass
            if isinstance(tmp_used, int) and tmp_used > 0:
                if tmp_used > _PEAK_TMP_USED_BYTES:
                    _PEAK_TMP_USED_BYTES = tmp_used
                try:
                    used_mb = tmp_used / (1024.0*1024.0)
                    total_mb = (tmp_total or 0) / (1024.0*1024.0)
                    max_used_mb = _PEAK_TMP_USED_BYTES / (1024.0*1024.0)
                    if total_mb > 0:
                        logger.info(f"[YOLO] /tmp: used={used_mb:.1f}/{total_mb:.1f} MB (max_used={max_used_mb:.1f} MB), ann_size={(ann_size or 0)/1024:.0f} KB")
                    else:
                        logger.info(f"[YOLO] /tmp: used={used_mb:.1f} MB (max_used={max_used_mb:.1f} MB), ann_size={(ann_size or 0)/1024:.0f} KB")
                except Exception:
                    pass
            dur = (time.time() - t0) * 1000.0
            logger.info(f"[YOLO] Ergebnisse: {len(coords)} Position(en) in {dur:.0f}ms")
            if coords:
                try:
                    x0, y0 = coords[0]
                    logger.info(f"[YOLO] Erste Position: ({x0:.1f},{y0:.1f})")
                except Exception:
                    pass
            return coords