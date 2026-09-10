"""Erzeugt das kleine ChArUco-Board für den DISTORTION-Modus druckfertig.

Parameter kommen aus src/config.py (DISTORTION_BOARD_*), damit Druck und
Erkennung garantiert zusammenpassen. Standard: 7x5 Felder, 35 mm Feld,
26 mm Marker, DICT_5X5_1000 -> 245 x 175 mm Boardfläche, zentriert auf Quer-A4.

Aufruf (im Verzeichnis unkrautroboter_bilderkennung):
    python tools/gen_distortion_board.py [ziel.pdf|ziel.png] [--dpi 600]

PDF (Standard): Seite = exakt A4 quer (297 x 210 mm), Board millimetergenau
platziert, mit 50-mm-Kontrollstrecke. **Bei 100 % / tatsächliche Größe drucken**,
dann die Kontrollstrecke mit dem Lineal prüfen (muss 50,0 mm sein) und das Blatt
absolut plan auf einen steifen Träger ziehen. Für die Intrinsik (K, D) ist der
exakte Maßstab unkritisch – Planheit und volle Sichtbarkeit zählen.
"""

import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src import config  # noqa: E402

DICT_NAME = "DICT_5X5_1000"  # muss zu calibration.DICT_NAME passen
A4_MM = (297.0, 210.0)  # quer (w, h)
MM_PER_IN = 25.4


def _make_board(sx, sy, sq_mm, mk_mm):
    ar = cv2.aruco
    d = ar.getPredefinedDictionary(getattr(ar, DICT_NAME))
    if hasattr(ar, "CharucoBoard_create"):
        return ar.CharucoBoard_create(sx, sy, sq_mm, mk_mm, d)
    return ar.CharucoBoard((sx, sy), sq_mm, mk_mm, d)


def _write_png(board_img, out, dpi, bw, bh, margin_mm=12.0):
    m = int(round(margin_mm * dpi / MM_PER_IN))
    h, w = board_img.shape[:2]
    canvas = 255 * __import__("numpy").ones((h + 2 * m, w + 2 * m), board_img.dtype)
    canvas[m:m + h, m:m + w] = board_img
    if not cv2.imwrite(out, canvas):
        raise SystemExit(f"Konnte {out} nicht schreiben")


def _write_pdf(board_img, out, dpi, bw_mm, bh_mm):
    from PIL import Image, ImageDraw, ImageFont

    def px(mm):
        return int(round(mm * dpi / MM_PER_IN))

    pw, ph = px(A4_MM[0]), px(A4_MM[1])
    page = Image.new("RGB", (pw, ph), "white")

    board = Image.fromarray(board_img).convert("RGB").resize(
        (px(bw_mm), px(bh_mm)), Image.NEAREST
    )
    ox, oy = (pw - board.width) // 2, (ph - board.height) // 2
    page.paste(board, (ox, oy))

    d = ImageDraw.Draw(page)
    try:
        font = ImageFont.load_default(size=px(3.2))
    except TypeError:
        font = ImageFont.load_default()

    # Eck-Passermarken (mit Abstand zum Board)
    g, ln = px(2.5), px(6)
    for cx, cy, sxd, syd in (
        (ox, oy, -1, -1), (ox + board.width, oy, 1, -1),
        (ox, oy + board.height, -1, 1), (ox + board.width, oy + board.height, 1, 1),
    ):
        d.line([(cx + sxd * g, cy), (cx + sxd * (g + ln), cy)], fill="black", width=2)
        d.line([(cx, cy + syd * g), (cx, cy + syd * (g + ln))], fill="black", width=2)

    # 50-mm-Kontrollstrecke unten
    y = ph - px(12)
    x0 = ox
    x1 = x0 + px(50)
    d.line([(x0, y), (x1, y)], fill="black", width=3)
    for xx in (x0, x1):
        d.line([(xx, y - px(2)), (xx, y + px(2))], fill="black", width=3)
    d.text((x0, y - px(6)), "50,0 mm  (bei 100 % drucken)", fill="black", font=font)

    sx, sy = config.DISTORTION_BOARD_SQUARES
    spec = (f"DISTORTION-Board  {sx}x{sy} Felder  {config.DISTORTION_BOARD_SQUARE_MM:.0f} mm "
            f"Feld  {config.DISTORTION_BOARD_MARKER_MM:.0f} mm Marker  {DICT_NAME}  "
            f"({bw_mm:.0f} x {bh_mm:.0f} mm)")
    d.text((ox, oy - px(6)), spec, fill="black", font=font)

    page.save(out, "PDF", resolution=float(dpi))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="distortion_charuco_a4.pdf")
    ap.add_argument("--dpi", type=float, default=600.0)
    a = ap.parse_args()

    sx, sy = config.DISTORTION_BOARD_SQUARES
    sq_mm = float(config.DISTORTION_BOARD_SQUARE_MM)
    mk_mm = float(config.DISTORTION_BOARD_MARKER_MM)
    bw_mm, bh_mm = sx * sq_mm, sy * sq_mm

    board = _make_board(sx, sy, sq_mm, mk_mm)
    board_img = board.generateImage(
        (int(round(bw_mm * a.dpi / MM_PER_IN)), int(round(bh_mm * a.dpi / MM_PER_IN))),
        marginSize=0,
        borderBits=1,
    )

    if a.out.lower().endswith(".pdf"):
        _write_pdf(board_img, a.out, a.dpi, bw_mm, bh_mm)
    else:
        _write_png(board_img, a.out, a.dpi, bw_mm, bh_mm)

    print(f"geschrieben: {a.out}  ({a.dpi:.0f} dpi)")
    print(
        f"Board {bw_mm:.0f} x {bh_mm:.0f} mm  ({sx}x{sy} Felder, {sq_mm:.0f} mm Feld, "
        f"{mk_mm:.0f} mm Marker, {DICT_NAME})"
    )
    if a.out.lower().endswith(".pdf"):
        print("Seite: A4 quer (297 x 210 mm). Drucken: 100 % / tatsächliche Größe.")
        print("Danach 50-mm-Strecke mit dem Lineal prüfen, dann plan aufziehen.")


if __name__ == "__main__":
    main()
