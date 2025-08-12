"""
CLI-Tool: Prüft die Kalibrierungsdatei (NPZ) auf Plausibilität und gibt eine kompakte Zusammenfassung aus.

Aufruf (auf dem Gerät mit der Datei):
    python3 tools/check_calibration.py --path ./calibration/cam_calib_charuco.npz --target 1280x720
"""

from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np


def aspect_ratio(w: int, h: int) -> float:
    return float(w) / float(h) if h else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=str, default="./calibration/cam_calib_charuco.npz", help="Pfad zur NPZ-Kalibrierdatei")
    ap.add_argument("--target", type=str, default=None, help="Zielauflösung WxH zur Laufzeit (optional, z.B. 1280x720)")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        print(f"[ERR] Datei nicht gefunden: {p}")
        return 2

    d = np.load(str(p), allow_pickle=True)
    K = d.get("K", None)
    D = d.get("D", None)
    img_size = d.get("img_size", None)
    map1 = d.get("map1", None)
    map2 = d.get("map2", None)

    print("=== Calibration Summary ===")
    print(f"File: {p.resolve()}")
    if K is None or D is None:
        print("[ERR] K oder D fehlen in der Datei.")
        return 3

    K = K.astype(float)
    D = D.astype(float).reshape(-1)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    print(f"K fx,fy: {fx:.2f}, {fy:.2f}")
    print(f"K cx,cy: {cx:.2f}, {cy:.2f}")
    print(f"K skew:  {K[0,1]:.6f}")
    print(f"D (len={len(D)}): {np.array2string(D, precision=4)}")

    if img_size is not None:
        W0, H0 = int(img_size[0]), int(img_size[1])
        print(f"img_size (calib): {W0} x {H0}  AR={aspect_ratio(W0,H0):.5f}")
        # Principal Point Nähe Bildmitte (Heuristik)
        if W0 > 0 and H0 > 0:
            offx = abs(cx - W0 / 2) / (W0 / 2)
            offy = abs(cy - H0 / 2) / (H0 / 2)
            print(f"principal point offset: {offx*100:.2f}% horiz, {offy*100:.2f}% vert")
            if offx > 0.2 or offy > 0.2:
                print("[WARN] Principal Point weit weg vom Bildzentrum. Prüfe Kalibrierung/Aufbau.")
    else:
        print("[WARN] img_size fehlt in der Datei.")

    if map1 is not None and map2 is not None:
        print(f"map1 dtype/shape: {map1.dtype} {map1.shape}")
        print(f"map2 dtype/shape: {map2.dtype} {map2.shape}")
    else:
        print("[INFO] map1/map2 nicht gespeichert – werden typischerweise zur Laufzeit erzeugt.")

    # Heuristische Checks auf leichte Verzerrung
    k1 = float(D[0]) if len(D) >= 1 else 0.0
    if abs(k1) > 1.0:
        print("[WARN] |k1| > 1.0 – sehr starke Verzerrung. Für 'leichten Fischaugen-Effekt' ungewöhnlich.")
    elif abs(k1) > 0.6:
        print("[WARN] |k1| > 0.6 – eher starke Verzerrung.")
    else:
        print("[OK] k1 im moderaten Bereich.")

    if args.target:
        try:
            Wt, Ht = map(int, args.target.lower().split("x"))
            print(f"target size (runtime): {Wt} x {Ht}  AR={aspect_ratio(Wt,Ht):.5f}")
            if img_size is not None:
                W0, H0 = int(img_size[0]), int(img_size[1])
                if abs(aspect_ratio(W0,H0) - aspect_ratio(Wt,Ht)) > 1e-3:
                    print("[WARN] Abweichende Aspect-Ratio Kalibrierung vs. Laufzeit – Verzerrungen möglich.")
        except Exception:
            print("[WARN] --target konnte nicht geparst werden. Erwartet: WxH, z.B. 1280x720")

    print("=== Ende ===")


if __name__ == "__main__":
    raise SystemExit(main())
