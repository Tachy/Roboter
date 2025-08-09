"""
Modul für das Training und die Bildaufnahme des Unkrautroboters.
"""

import os
import glob
from . import config, camera

def get_next_image_number():
    """Ermittelt die nächste Bildnummer für das Training."""
    if not os.path.exists(config.TRAINING_IMAGE_DIR):
        os.makedirs(config.TRAINING_IMAGE_DIR)
    files = glob.glob(os.path.join(config.TRAINING_IMAGE_DIR, "bild_*.jpg"))
    if not files:
        return 1
    numbers = [int(os.path.basename(f).split("_")[1].split(".")[0]) for f in files]
    return max(numbers) + 1

def save_training_image():
    """Nimmt ein Bild auf und speichert es im Trainingsverzeichnis."""
    next_number = get_next_image_number()
    filename = os.path.join(config.TRAINING_IMAGE_DIR, f"bild_{next_number:04d}.jpg")
    # Stop streaming/preview if running
    if hasattr(camera.picam2, "stop_preview"):
        camera.picam2.stop_preview()
    elif hasattr(camera.picam2, "stop"):
        camera.picam2.stop()
    camera.picam2.capture_file(filename)
    # Optionally restart streaming/preview if needed
    if hasattr(camera.picam2, "start_preview"):
        camera.picam2.start_preview()
    elif hasattr(camera.picam2, "start"):
        camera.picam2.start()
    print(f"Bild gespeichert: {filename}")
