"""
Modul für das Training und die Bildaufnahme des Unkrautroboters.
"""

import os
import glob
import logging
from . import config
from . import config, camera

# Logger einrichten
logger = logging.getLogger("training")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )

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
    logger.info("Bild speichern....")
    next_number = get_next_image_number()
    filename = os.path.join(config.TRAINING_IMAGE_DIR, f"bild_{next_number:04d}.jpg")
    # Manuelle Trainingsaufnahme: mit Kalibrierung speichern, falls vorhanden
    camera.capture_image(filename, apply_calibration=True)
    logger.info(f"Bild gespeichert: {filename}")
