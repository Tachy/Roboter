"""Erzeugt das kleine ChArUco-Board für den DISTORTION-Modus als druckfertiges PNG.

Parameter kommen aus src/config.py (DISTORTION_BOARD_*), damit Druck und
Erkennung garantiert zusammenpassen. Standard: 7x5 Felder, 35 mm Feld,
26 mm Marker, DICT_5X5_1000 -> 245 x 175 mm Boardfläche, passt quer auf A4.

Aufruf (im Verzeichnis unkrautroboter_bilderkennung):
    python tools/gen_distortion_board.py [ziel.png] [--dpi 300] [--margin-mm 12]

Danach auf **Quer-A4 bei 100 % / tatsächliche Größe** drucken und absolut plan
auf einen steifen Träger ziehen. Für die Intrinsik (K, D) ist der exakte Maßstab
egal – Planheit und volle Sichtbarkeit sind entscheidend.
"""

import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src import config  # noqa: E402

DICT_NAME = "DICT_5X5_1000"  # muss zu calibration.DICT_NAME passen


def _make_board(sx, sy, sq_mm, mk_mm):
    ar = cv2.aruco
    d = ar.getPredefinedDictionary(getattr(ar, DICT_NAME))
    if hasattr(ar, "CharucoBoard_create"):
        return ar.CharucoBoard_create(sx, sy, sq_mm, mk_mm, d)
    return ar.CharucoBoard((sx, sy), sq_mm, mk_mm, d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="distortion_charuco_a4.png")
    ap.add_argument("--dpi", type=float, default=300.0)
    ap.add_argument("--margin-mm", type=float, default=12.0)
    a = ap.parse_args()

    sx, sy = config.DISTORTION_BOARD_SQUARES
    sq_mm = float(config.DISTORTION_BOARD_SQUARE_MM)
    mk_mm = float(config.DISTORTION_BOARD_MARKER_MM)
    px_per_mm = a.dpi / 25.4

    board = _make_board(sx, sy, sq_mm, mk_mm)
    content_w = int(round(sx * sq_mm * px_per_mm))
    content_h = int(round(sy * sq_mm * px_per_mm))
    margin_px = int(round(a.margin_mm * px_per_mm))

    img = board.generateImage(
        (content_w + 2 * margin_px, content_h + 2 * margin_px),
        marginSize=margin_px,
        borderBits=1,
    )
    if not cv2.imwrite(a.out, img):
        raise SystemExit(f"Konnte {a.out} nicht schreiben")

    bw, bh = sx * sq_mm, sy * sq_mm
    print(f"geschrieben: {a.out}  ({img.shape[1]} x {img.shape[0]} px @ {a.dpi:.0f} dpi)")
    print(
        f"Boardfläche: {bw:.0f} x {bh:.0f} mm  ({sx}x{sy} Felder, {sq_mm:.0f} mm Feld, "
        f"{mk_mm:.0f} mm Marker, {DICT_NAME})"
    )
    print(
        f"Rand {a.margin_mm:.0f} mm -> Blattbedarf {bw + 2 * a.margin_mm:.0f} x "
        f"{bh + 2 * a.margin_mm:.0f} mm (quer A4: 297 x 210 mm)"
    )
    print("Drucken: Quer-A4, 100 % / tatsächliche Größe, dann plan aufziehen.")


if __name__ == "__main__":
    main()
