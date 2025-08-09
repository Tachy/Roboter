"""
Modul für die YOLO-Integration des Unkrautroboters.
"""

from . import config

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
        return extract_xy(None)
    else:
        results = model(image_path)
        return extract_xy(results)
