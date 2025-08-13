"""
Geometrie-Helfer für die Umrechnung von Bildkoordinaten (Pixel) in Weltkoordinaten (mm).

Annahmen und Konventionen:
- Weltkoordinaten: X nach rechts, Y nach vorne. Einheit: Millimeter.
- Bevorzugt wird eine planare Homographie H (3x3), die aus UNDISTORTED Bildern
  (wie von camera.capture_image gespeichert) bestimmt wurde und Pixel -> (X,Y,1)
  in mm auf der Bodenebene abbildet.
- Alternativ können vollständige Extrinsiken (K, R, t) plus Bodenebene genutzt werden.

Dateien (optional, falls vorhanden):
- ./calibration/ground_homography.npz mit Schlüssel "H" (3x3)
- ./calibration/extrinsics.npz mit Schlüsseln "K" (3x3), "R" (3x3), "t" (3,),
  sowie entweder "plane_n" (3,) und "plane_d" (Skalar, mit Ebenengleichung n^T X + d = 0)
  oder "plane_z0"=True, was Z=0 in Weltkoordinaten impliziert.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("geometry")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')

# Standardpfade
CALIB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "calibration"))
H_FILE = os.path.join(CALIB_DIR, "ground_homography.npz")
EXTR_FILE = os.path.join(CALIB_DIR, "extrinsics.npz")

# Globale Zustände
_H: Optional[np.ndarray] = None
_K: Optional[np.ndarray] = None
_R: Optional[np.ndarray] = None
_t: Optional[np.ndarray] = None  # (3,)
_plane_n: Optional[np.ndarray] = None  # (3,)
_plane_d: Optional[float] = None
_plane_is_z0: bool = False


def _safe_load_npz(path: str) -> Optional[dict]:
    try:
        if os.path.exists(path):
            d = np.load(path, allow_pickle=True)
            return {k: d[k] for k in d.files}
    except Exception as e:
        logger.error(f"[Geom] Fehler beim Laden von {path}: {e}")
    return None


def load_homography(path: Optional[str] = None) -> bool:
    """Lädt eine 3x3-Homographie aus Datei. Erwartet Schlüssel 'H'.

    Die Homographie soll von (u,v,1)^T (Pixel im UNDISTORTED Bild) nach (X_mm, Y_mm, W)^T
    auf die Bodenebene abbilden; die Rückgabe erfolgt als (X/W, Y/W) in Millimetern.
    """
    global _H
    p = path or H_FILE
    d = _safe_load_npz(p)
    if not d:
        return False
    H = d.get("H")
    if H is None:
        logger.warning(f"[Geom] Keine 'H' in {p} gefunden.")
        return False
    H = np.asarray(H, dtype=float)
    if H.shape != (3, 3):
        logger.warning(f"[Geom] Ungültige H-Form {H.shape} in {p}.")
        return False
    _H = H
    logger.info(f"[Geom] Homographie geladen aus {p}.")
    return True


def load_extrinsics(path: Optional[str] = None) -> bool:
    """Lädt K,R,t und Ebeneninfo für Ray-Plane-Schnitt.

    Unterstützt:
    - plane_n (3,), plane_d (Skalar) mit Ebenengleichung n^T X + d = 0
    - plane_z0=True (setzt Welt-Ebene Z=0)
    """
    global _K, _R, _t, _plane_n, _plane_d, _plane_is_z0
    p = path or EXTR_FILE
    d = _safe_load_npz(p)
    if not d:
        return False
    K = d.get("K")
    R = d.get("R")
    t = d.get("t")
    if K is None or R is None or t is None:
        logger.warning(f"[Geom] K/R/t fehlen in {p}.")
        return False
    K = np.asarray(K, dtype=float)
    R = np.asarray(R, dtype=float)
    t = np.asarray(t, dtype=float).reshape(3)
    if K.shape != (3, 3) or R.shape != (3, 3) or t.shape != (3,):
        logger.warning(f"[Geom] Ungültige Formen K{K.shape}, R{R.shape}, t{t.shape} in {p}.")
        return False
    _K, _R, _t = K, R, t
    n = d.get("plane_n")
    plane_d = d.get("plane_d")
    _plane_is_z0 = bool(d.get("plane_z0", False))
    if n is not None and plane_d is not None:
        _plane_n = np.asarray(n, dtype=float).reshape(3)
        _plane_d = float(plane_d)
        _plane_is_z0 = False
        logger.info(f"[Geom] Extrinsik + Ebene (n,d) aus {p} geladen.")
    else:
        _plane_n = None
        _plane_d = None
        if _plane_is_z0:
            logger.info(f"[Geom] Extrinsik geladen; Ebene Z=0 angenommen.")
        else:
            logger.info(f"[Geom] Extrinsik geladen; keine Ebene gefunden – Z=0 als Fallback.")
            _plane_is_z0 = True
    return True


def is_world_transform_ready() -> bool:
    """Gibt True zurück, wenn Homographie oder Extrinsik+Ebene geladen sind."""
    return _H is not None or (_K is not None and _R is not None and _t is not None)


def _apply_homography(px: float, py: float) -> Optional[Tuple[float, float]]:
    if _H is None:
        return None
    vec = np.array([px, py, 1.0], dtype=float)
    out = _H @ vec
    w = out[2]
    if abs(w) < 1e-9:
        return None
    X = out[0] / w
    Y = out[1] / w
    return float(X), float(Y)


def _ray_plane_intersection(px: float, py: float) -> Optional[Tuple[float, float]]:
    """Schneidet den Bildstrahl durch Pixel (px,py) mit der Bodenebene und gibt (X,Y) in mm.

    Annahmen:
    - Bildkoordinaten beziehen sich auf das UNDISTORTED Bild zur Kamera-Intrinsik K.
    - Weltachsen: X rechts, Y vorwärts; Ebene ist Z=0 (wenn plane_z0) oder n^T X + d = 0.
    - R, t transformieren Welt -> Kamera: X_cam = R * X_world + t
      (üblich bei OpenCV solvePnP). Wir benötigen die Inversen für die Rücktransformation.
    """
    if _K is None or _R is None or _t is None:
        return None
    # Richtungsstrahl in Kamerakoordinaten
    Kinv = np.linalg.inv(_K)
    pix = np.array([px, py, 1.0], dtype=float)
    ray_cam = Kinv @ pix  # unskaliert
    ray_cam = ray_cam / np.linalg.norm(ray_cam)

    # Kamerazentrum in Weltkoordinaten: C = -R^T t
    Rinv = _R.T
    C = -Rinv @ _t
    # Strahlrichtung in Weltkoordinaten: d_world = R^T * ray_cam
    d_world = Rinv @ ray_cam

    # Ebene: entweder Z=0 oder allgemeine Ebene n^T X + d = 0
    if _plane_is_z0:
        # Schnitt mit Z=0: Cz + s*dz = 0 -> s = -Cz/dz
        dz = d_world[2]
        if abs(dz) < 1e-9:
            return None
        s = -C[2] / dz
        if s <= 0:
            return None
        Xw = C + s * d_world
        return float(Xw[0]), float(Xw[1])
    else:
        if _plane_n is None or _plane_d is None:
            return None
        n = _plane_n
        d = _plane_d
        denom = n @ d_world
        if abs(denom) < 1e-9:
            return None
        s = -(n @ C + d) / denom
        if s <= 0:
            return None
        Xw = C + s * d_world
        return float(Xw[0]), float(Xw[1])


def pixel_to_world(px: float, py: float) -> Optional[Tuple[float, float]]:
    """Konvertiert Pixelkoordinaten (px,py) aus dem UNDISTORTED Bild nach Welt (mm).

    Priorität: Homographie > Extrinsik+Ebene. Gibt None zurück, wenn nicht möglich.
    """
    # 1) Homographie
    if _H is not None:
        res = _apply_homography(px, py)
        if res is not None:
            return res
    # 2) Extrinsik
    if _K is not None and _R is not None and _t is not None:
        res = _ray_plane_intersection(px, py)
        if res is not None:
            return res
    return None


def try_autoload() -> None:
    """Versucht beim Start Homographie/Extrinsik zu laden (falls vorhanden)."""
    loaded = False
    try:
        loaded = load_homography()
    except Exception:
        pass
    if not loaded:
        try:
            load_extrinsics()
        except Exception:
            pass


# Autoload beim Import
try:
    try_autoload()
except Exception:
    pass


# ==== Extrinsik-Berechnung (Charuco) – optionaler Helfer ====
def compute_and_save_extrinsics_from_charuco(
    bgr: np.ndarray,
    K: np.ndarray,
    D: np.ndarray,
    newK: Optional[np.ndarray] = None,
    out_path: Optional[str] = None,
) -> Tuple[bool, np.ndarray, str]:
    """Schätzt Extrinsik (R,t) mit einem ChArUco-Bild und speichert sie.

    Eingaben:
    - bgr: Bild (BGR)
    - K, D: Kamera-Parameter (intrinsisch) passend zum aktuellen Bild
    - newK: bevorzugte Intrinsik für spätere Pixel->Welt-Umrechnung (optional)
    - out_path: Zielpfad für extrinsics.npz (optional; Standard EXTR_FILE)

    Rückgabe: (ok, draw_bgr, text)
    - ok: True, wenn Pose geschätzt und gespeichert
    - draw_bgr: Kopie des Bildes mit eingezeichneten Achsen (falls ok)
    - text: Statusnachricht
    """
    try:
        import cv2  # Lazy import
    except Exception as e:
        msg = f"OpenCV nicht verfügbar: {e}"
        return False, bgr.copy(), msg

    draw = bgr.copy()
    try:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    except Exception:
        gray = None

    # Standard-Charuco-Parameter (wie in calibration.py)
    SQUARES_X = 5
    SQUARES_Y = 7
    SQUARE_MM = 40.0
    MARKER_MM = 30.0
    DICT_NAME = "DICT_4X4_50"

    try:
        ar = cv2.aruco
    except Exception:
        return False, draw, "OpenCV ArUco nicht verfügbar."

    try:
        d_id = getattr(ar, DICT_NAME)
        aruco_dict = ar.getPredefinedDictionary(d_id)
    except Exception:
        return False, draw, "Aruco-Dictionary fehlt."

    # Board erstellen
    try:
        if hasattr(ar, "CharucoBoard_create"):
            board = ar.CharucoBoard_create(SQUARES_X, SQUARES_Y, SQUARE_MM, MARKER_MM, aruco_dict)
        else:
            board = ar.CharucoBoard((SQUARES_X, SQUARES_Y), SQUARE_MM, MARKER_MM, aruco_dict)
    except Exception:
        return False, draw, "Charuco-Board konnte nicht erzeugt werden."

    # Marker-Detektion
    try:
        if hasattr(ar, "DetectorParameters"):
            params = ar.DetectorParameters()
        else:
            params = ar.DetectorParameters_create()
        if hasattr(ar, "ArucoDetector"):
            detector = ar.ArucoDetector(aruco_dict, params)
            corners, ids, _ = detector.detectMarkers(gray)
        else:
            corners, ids, _ = ar.detectMarkers(gray, aruco_dict, parameters=params)
    except Exception:
        corners, ids = None, None

    if ids is None or len(ids) == 0:
        return False, draw, "Keine Marker gefunden."

    # Charuco-Ecken interpolieren
    ch_corners = None
    ch_ids = None
    try:
        if hasattr(ar, "interpolateCornersCharuco"):
            _, ch_corners, ch_ids = ar.interpolateCornersCharuco(corners, ids, gray, board)
    except Exception:
        ch_corners, ch_ids = None, None

    rvec = None
    tvec = None
    ok_pose = False
    if ch_corners is not None and ch_ids is not None and len(ch_ids) >= 4:
        # Bevorzugt: direkte Charuco-Pose
        if hasattr(ar, "estimatePoseCharucoBoard"):
            try:
                retval, rvec, tvec = ar.estimatePoseCharucoBoard(ch_corners, ch_ids, board, K, D, None, None)
                ok_pose = bool(retval)
            except Exception:
                ok_pose = False
        # Fallback: solvePnP mit den Charuco-Weltpunkten
        if not ok_pose:
            try:
                imgp = ch_corners.reshape(-1, 2).astype(np.float32)
                ids_flat = ch_ids.flatten().astype(int)
                obj_all = board.chessboardCorners  # (N,3)
                objp = obj_all[ids_flat].reshape(-1, 3).astype(np.float32)
                flag = getattr(cv2, 'SOLVEPNP_IPPE_SQUARE', getattr(cv2, 'SOLVEPNP_ITERATIVE', 0))
                ok, rvec, tvec = cv2.solvePnP(objp, imgp, K, D, flags=flag)
                ok_pose = bool(ok)
            except Exception:
                ok_pose = False

    if not ok_pose or rvec is None or tvec is None:
        text = "Extrinsik fehlgeschlagen"
        return False, draw, text

    # Achsen einzeichnen (Länge 50 mm)
    try:
        cv2.drawFrameAxes(draw, K, D, rvec, tvec, 50)
    except Exception:
        pass

    # R,t aus rvec,tvec ableiten
    try:
        R, _ = cv2.Rodrigues(rvec)
        t = tvec.reshape(3)
    except Exception:
        return False, draw, "Rodrigues fehlgeschlagen"

    # Speichern und in Speicher laden
    try:
        p = out_path or EXTR_FILE
        K_to_save = newK if newK is not None else K
        np.savez(
            p,
            K=K_to_save,
            newK=newK if newK is not None else K,
            R=R,
            t=t,
            plane_z0=True,
            note="EXTRINSIK: Pose aus Charuco; Z=0 Boden; mm; X rechts, Y vor"
        )
        try:
            load_extrinsics(p)
        except Exception:
            pass
    except Exception:
        return False, draw, "Extrinsik: Speichern fehlgeschlagen"

    return True, draw, "Extrinsik gespeichert"
