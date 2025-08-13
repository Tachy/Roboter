"""
Modul für die YOLO-Integration des Unkrautroboters.
"""

from . import config, camera
import cv2

if not config.USE_DUMMY:
    from ultralytics import YOLO
    model = YOLO(config.YOLO_MODEL_PATH)

def extract_xy(results):
    """Extrahiert die Koordinaten aus den YOLO-Ergebnissen."""
    if config.USE_DUMMY:
        # Dummy-Koordinaten zurückgeben
        return [(100.0, 200.0)]  # Beispielkoordinaten
    else:
        # Echte Koordinaten aus YOLO-Ergebnissen extrahieren
        coordinates = []
        for result in results:
            for box in result.boxes:
                x_center = float(box.xywh[0])
                y_center = float(box.xywh[1])
                coordinates.append((x_center, y_center))
        return coordinates

def process_image(image_path):
    """Verarbeitet ein Bild mit YOLO und gibt die Koordinaten zurück."""
    if config.USE_DUMMY:
        coords = extract_xy(None)
        # Optional: Dummy-Overlay in der Vorschau anzeigen
        try:
            img = cv2.imread(image_path)
            if img is not None and len(coords) > 0:
                x, y = int(coords[0][0]), int(coords[0][1])
                cv2.circle(img, (x, y), 10, (0, 255, 0), 2)
                camera._encode_and_store_last_capture(img, quality=85)
        except Exception:
            pass
        return coords
    else:
        results = model(image_path)
        # Annotiertes Bild als "Letzte Aufnahme" veröffentlichen
        try:
            if results and len(results) > 0:
                annotated = results[0].plot()  # numpy-Array mit eingezeichneten Boxen/Labels (BGR)
                if annotated is not None:
                    if annotated.ndim == 3 and annotated.shape[2] == 4:
                        annotated = cv2.cvtColor(annotated, cv2.COLOR_RGBA2BGR)
                    # bei 3 Kanälen nehmen wir BGR direkt
                    camera._encode_and_store_last_capture(annotated, quality=85)
        except Exception:
            pass
        return extract_xy(results)
