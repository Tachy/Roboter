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
from . import config

logger = logging.getLogger("geometry")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

# Standardpfade
CALIB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "calibration")
)
H_FILE = os.path.join(CALIB_DIR, "ground_homography.npz")
EXTR_FILE = os.path.join(CALIB_DIR, "extrinsics.npz")
POLY_FILE = os.path.join(CALIB_DIR, "ground_poly.npz")

# Globale Zustände
_H: Optional[np.ndarray] = None
_K: Optional[np.ndarray] = None
_R: Optional[np.ndarray] = None
_t: Optional[np.ndarray] = None  # (3,)
_plane_n: Optional[np.ndarray] = None  # (3,)
_plane_d: Optional[float] = None
_plane_is_z0: bool = False
# "Kurvenmatrix": Polynom Rohpixel -> Boden-mm (bevorzugter Pfad)
_C: Optional[np.ndarray] = None          # (n_terms, 2)
_C_degree: int = 0
_C_bbox: Optional[np.ndarray] = None     # [u_min, u_max, v_min, v_max] (Ref-Auflösung)
_C_ref_wh: Optional[np.ndarray] = None   # (w, h) der EXTRINSIK-Aufnahme


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
        logger.warning(
            f"[Geom] Ungültige Formen K{K.shape}, R{R.shape}, t{t.shape} in {p}."
        )
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
            logger.info(
                f"[Geom] Extrinsik geladen; keine Ebene gefunden – Z=0 als Fallback."
            )
            _plane_is_z0 = True
    return True


def load_ground_poly(path: Optional[str] = None) -> bool:
    """Lädt die Pixel->mm-Polynom-"Kurvenmatrix" (Rohbild-Pipeline).

    Erwartet in der npz: 'C' (n_terms, 2), 'degree' (int), optional 'pix_bbox'
    ([u_min,u_max,v_min,v_max]).
    """
    global _C, _C_degree, _C_bbox, _C_ref_wh
    p = path or POLY_FILE
    d = _safe_load_npz(p)
    if not d:
        return False
    C = d.get("C")
    deg = d.get("degree")
    if C is None or deg is None:
        logger.warning(f"[Geom] 'C'/'degree' fehlen in {p}.")
        return False
    C = np.asarray(C, dtype=float)
    degree = int(np.asarray(deg).reshape(-1)[0])
    n_terms = (degree + 1) * (degree + 2) // 2
    if C.shape != (n_terms, 2):
        logger.warning(f"[Geom] Ungültige C-Form {C.shape} für Grad {degree} in {p}.")
        return False
    _C = C
    _C_degree = degree
    bb = d.get("pix_bbox")
    _C_bbox = None if bb is None else np.asarray(bb, dtype=float).reshape(4)
    rw = d.get("ref_wh")
    _C_ref_wh = None if rw is None else np.asarray(rw, dtype=float).reshape(2)
    logger.info(
        f"[Geom] Polynom (Grad {degree}, Ref {None if _C_ref_wh is None else tuple(_C_ref_wh.astype(int))}) "
        f"geladen aus {p}."
    )
    return True


def is_world_transform_ready() -> bool:
    """True, wenn Polynom, Homographie oder Extrinsik+Ebene geladen sind."""
    return (
        _C is not None
        or _H is not None
        or (_K is not None and _R is not None and _t is not None)
    )


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


def pixel_to_world(
    px: float, py: float, src_wh=None
) -> Optional[Tuple[float, float]]:
    """Konvertiert Pixelkoordinaten (px,py) nach Welt-mm.

    Priorität: Polynom ("Kurvenmatrix", auf ROHpixeln) > Homographie > Extrinsik.
    src_wh: (w,h) der Auflösung, in der (px,py) vorliegen. Weicht sie von der
    Referenzauflösung des Polynoms ab (z.B. GETXY 2028x1520 vs. EXTRINSIK
    4056x3040), werden die Koordinaten skaliert. None -> bereits Referenz.
    Gibt None zurück, wenn nicht möglich. `WORLD_OFFSET_XY_MM` wird abgezogen.
    """
    ox, oy = getattr(config, "WORLD_OFFSET_XY_MM", (0.0, 0.0))

    # 0) Polynom (Rohbild-Pipeline)
    if _C is not None:
        if _C_ref_wh is not None and src_wh is not None:
            sw, sh = float(src_wh[0]), float(src_wh[1])
            if sw > 0 and sh > 0:
                px = px * (_C_ref_wh[0] / sw)
                py = py * (_C_ref_wh[1] / sh)
        if _C_bbox is not None:
            u0, u1, v0, v1 = _C_bbox
            m = 0.08 * max(u1 - u0, v1 - v0)  # großzügiger Rand
            if not (u0 - m <= px <= u1 + m and v0 - m <= py <= v1 + m):
                logger.debug(
                    f"[Geom] Pixel ({px:.0f},{py:.0f}) außerhalb Poly-Gültigkeit."
                )
                return None
        X, Y = eval_pixel_to_world_poly(_C, _C_degree, px, py)
        return float(X - ox), float(Y - oy)

    # 1) Homographie (Legacy)
    if _H is not None:
        res = _apply_homography(px, py)
        if res is not None:
            return float(res[0] - ox), float(res[1] - oy)
    # 2) Extrinsik + Ebene (Legacy)
    if _K is not None and _R is not None and _t is not None:
        res = _ray_plane_intersection(px, py)
        if res is not None:
            return float(res[0] - ox), float(res[1] - oy)
    return None


def try_autoload() -> None:
    """Beim Start laden: Polynom > Homographie > Extrinsik (erstes gewinnt)."""
    for fn in (load_ground_poly, load_homography, load_extrinsics):
        try:
            if fn():
                return
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

    # Board + Detektion aus dem gemeinsamen Helfer (kein dupliziertes Setup mehr;
    # calibration.detect_charuco nutzt die moderne CharucoDetector-API).
    try:
        from . import calibration as _calib

        ar = cv2.aruco
        aruco_dict = _calib.get_aruco_dict()
        board = _calib.make_charuco_board(aruco_dict)
        ch_corners, ch_ids, mk_corners, mk_ids = _calib.detect_charuco(
            gray, aruco_dict, board
        )
    except Exception as e:
        return False, draw, f"ChArUco-Erkennung fehlgeschlagen: {e}"

    if ch_ids is None or len(ch_ids) < 4:
        return False, draw, "Zu wenige ChArUco-Ecken gefunden."

    rvec = None
    tvec = None
    ok_pose = False
    # Bevorzugt: direkte Charuco-Pose (Legacy-API, falls vorhanden)
    if hasattr(ar, "estimatePoseCharucoBoard"):
        try:
            retval, rvec, tvec = ar.estimatePoseCharucoBoard(
                ch_corners, ch_ids, board, K, D, None, None
            )
            ok_pose = bool(retval)
        except Exception:
            ok_pose = False
    # Fallback (und Standard auf OpenCV >= 4.9): solvePnP mit den ChArUco-Weltpunkten
    if not ok_pose:
        try:
            imgp = np.asarray(ch_corners, dtype=np.float32).reshape(-1, 2)
            ids_flat = np.asarray(ch_ids).reshape(-1).astype(int)
            obj_all = board_chessboard_corners_mm(board)  # (N,2), mm
            objp = np.hstack(
                [obj_all[ids_flat], np.zeros((len(ids_flat), 1))]
            ).astype(np.float32)
            flag = getattr(
                cv2, "SOLVEPNP_IPPE", getattr(cv2, "SOLVEPNP_ITERATIVE", 0)
            )
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
            note="EXTRINSIK: Pose aus Charuco; Z=0 Boden; mm; X rechts, Y vor",
        )
        try:
            load_extrinsics(p)
        except Exception:
            pass
    except Exception:
        return False, draw, "Extrinsik: Speichern fehlgeschlagen"

    return True, draw, "Extrinsik gespeichert"


# ==== Helfer für die mechanik-gekoppelte EXTRINSIK-Kalibrierung ====
#
# Idee: Die ChArUco-Erkennung liefert eine robuste Pixel->Board-mm-Homographie
# (H_board). Die Bürste an bekannten Schlittenpositionen liefert Punktpaare
# (Board-mm <-> mechanische mm); daraus wird eine 2D-Ähnlichkeitstransformation
# S bestimmt. H_mech = S ∘ H_board bildet Pixel direkt auf die mechanische
# X/Y-Achse des Schlittens ab und wird als ground_homography.npz gespeichert.


def board_chessboard_corners_mm(board) -> np.ndarray:
    """Board-mm-Koordinaten (M,2) aller inneren Schachbrett-Ecken (id 0..M-1)."""
    cc = None
    if hasattr(board, "getChessboardCorners"):
        cc = board.getChessboardCorners()
    elif hasattr(board, "chessboardCorners"):
        cc = board.chessboardCorners
    if cc is None:
        raise ValueError("Board liefert keine chessboardCorners.")
    cc = np.asarray(cc, dtype=float).reshape(-1, 3)
    return cc[:, :2]


def estimate_board_homography(ch_corners, ch_ids, board) -> Optional[np.ndarray]:
    """Pixel -> Board-mm-Homographie aus ChArUco-Eckdetektionen.

    ch_corners: (N,1,2) Pixel, ch_ids: (N,1) Ecken-IDs, board: ChArUco-Board.
    Gibt die 3x3-Homographie zurück oder None (zu wenige Ecken).
    """
    if ch_corners is None or ch_ids is None:
        return None
    obj_mm = board_chessboard_corners_mm(board)
    ids = np.asarray(ch_ids).reshape(-1).astype(int)
    img_pts = np.asarray(ch_corners, dtype=np.float64).reshape(-1, 2)
    if len(ids) != len(img_pts) or len(img_pts) < 4:
        return None
    if ids.min() < 0 or ids.max() >= len(obj_mm):
        return None
    dst_mm = obj_mm[ids].astype(np.float64)
    try:
        import cv2
    except Exception:
        return None
    # Kleinste-Quadrate über alle Ecken (subpixelgenau, planar) – kein RANSAC.
    H, _ = cv2.findHomography(img_pts, dst_mm, 0)
    if H is None:
        return None
    return np.asarray(H, dtype=float)


def similarity_from_point_pairs(
    src_xy, dst_xy
) -> Tuple[np.ndarray, float, float]:
    """2D-Ähnlichkeitstransformation (Rotation + einheitl. Maßstab + Translation).

    Bildet src_xy (z. B. Board-mm) auf dst_xy (mechanische mm) ab.
    Rückgabe: (S 2x3, Maßstab, RMS-Residuum in mm).
    Bei genau einem Punktpaar: reine Translation (Maßstab 1, Residuum 0).
    """
    src = np.asarray(src_xy, dtype=float).reshape(-1, 2)
    dst = np.asarray(dst_xy, dtype=float).reshape(-1, 2)
    if len(src) != len(dst) or len(src) == 0:
        raise ValueError("src/dst müssen gleich viele Punkte (>=1) haben.")

    if len(src) == 1:
        t = dst[0] - src[0]
        S = np.array([[1.0, 0.0, t[0]], [0.0, 1.0, t[1]]], dtype=float)
        return S, 1.0, 0.0

    import cv2

    S, _ = cv2.estimateAffinePartial2D(
        src.reshape(-1, 1, 2), dst.reshape(-1, 1, 2), method=cv2.LMEDS
    )
    if S is None:
        # Fallback: Schwerpunkt-Translation
        t = dst.mean(axis=0) - src.mean(axis=0)
        S = np.array([[1.0, 0.0, t[0]], [0.0, 1.0, t[1]]], dtype=float)
        return S, 1.0, float("nan")

    S = np.asarray(S, dtype=float)
    scale = float(np.hypot(S[0, 0], S[1, 0]))
    proj = (S[:, :2] @ src.T).T + S[:, 2]
    resid = float(np.sqrt(np.mean(np.sum((proj - dst) ** 2, axis=1))))
    return S, scale, resid


def similarity_through_origin(
    src_meas_xy, dst_xy
) -> Tuple[np.ndarray, float, float, float]:
    """Ähnlichkeit board->mech mit Translation FEST 0 (gemeinsamer Ursprung).

    Modell je Messpunkt: A · src_meas_k - u = dst_k, mit
      A = [[a, -c], [c, a]]  (Rotation + einheitl. Maßstab)
      u  = A · b             (unbekannter, positionsunabhängiger Mess-Versatz b
                              zwischen Bürstenspitze und Schwerpunkt der
                              verdeckten Ecken)
    Weil der Bediener bei Schlitten-X=0 die Board-Ecke (0,0) unter die Bürste
    legt, fallen Board- und Mechanik-Ursprung zusammen -> die reale Abbildung
    ist mech = A · board (ohne Translation). b/u kürzen sich dabei heraus.

    Rückgabe: (A 2x2, Maßstab, Drehwinkel [Grad], RMS-Residuum in mm).
    Benötigt >= 2 Messpunkte (2 -> exakt, >=3 -> ausgleichend).
    """
    P = np.asarray(src_meas_xy, dtype=float).reshape(-1, 2)
    M = np.asarray(dst_xy, dtype=float).reshape(-1, 2)
    if len(P) != len(M) or len(P) < 2:
        raise ValueError("similarity_through_origin: mindestens 2 Punktpaare nötig.")

    rows = []
    rhs = []
    for (px, py), (mx, my) in zip(P, M):
        rows.append([px, -py, -1.0, 0.0])
        rhs.append(mx)
        rows.append([py, px, 0.0, -1.0])
        rhs.append(my)
    sol, *_ = np.linalg.lstsq(np.asarray(rows), np.asarray(rhs), rcond=None)
    a, c, ux, uy = (float(v) for v in sol)
    A = np.array([[a, -c], [c, a]], dtype=float)

    proj = (A @ P.T).T - np.array([ux, uy])
    resid = float(np.sqrt(np.mean(np.sum((proj - M) ** 2, axis=1))))
    scale = float(np.hypot(a, c))
    theta_deg = float(np.degrees(np.arctan2(c, a)))
    return A, scale, theta_deg, resid


def compose_affine_homography(S_2x3, H_3x3) -> np.ndarray:
    """S ∘ H: hängt die Affin-/Ähnlichkeitstransformation S (2x3) an H (3x3) an."""
    S = np.asarray(S_2x3, dtype=float).reshape(2, 3)
    H = np.asarray(H_3x3, dtype=float).reshape(3, 3)
    S3 = np.vstack([S, [0.0, 0.0, 1.0]])
    return S3 @ H


# ==== "Kurvenmatrix": Polynom Rohpixel -> Boden-mm ====


def _poly_terms(degree: int):
    """(i, j)-Exponenten für u^i * v^j, i+j <= degree. Reihenfolge nach
    aufsteigendem Gesamtgrad, innerhalb dessen u-Potenz absteigend:
    [1, u, v, u², uv, v², u³, u²v, uv², v³, …]."""
    return [(d - i, i) for d in range(degree + 1) for i in range(d + 1)]


def poly_basis(u, v, degree: int) -> np.ndarray:
    """Design-Matrix Φ. u, v skalar oder 1D-Array gleicher Länge.
    Rückgabe: (N, n_terms) bzw. (n_terms,) bei Skalaren."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    scalar = (u.ndim == 0)
    u = np.atleast_1d(u)
    v = np.atleast_1d(v)
    cols = [u ** i * v ** j for (i, j) in _poly_terms(degree)]
    Phi = np.stack(cols, axis=1)  # (N, n_terms)
    return Phi[0] if scalar else Phi


def fit_pixel_to_world_poly(pix_pts, world_pts, degree: int):
    """Least-Squares-Fit C so, dass Φ(u,v) · C ≈ (X_mm, Y_mm).

    Rückgabe: (C (n_terms, 2), rms_mm, max_mm).
    """
    pix = np.asarray(pix_pts, dtype=float).reshape(-1, 2)
    wld = np.asarray(world_pts, dtype=float).reshape(-1, 2)
    Phi = poly_basis(pix[:, 0], pix[:, 1], degree)
    C, *_ = np.linalg.lstsq(Phi, wld, rcond=None)
    resid = Phi @ C - wld
    d = np.sqrt(np.sum(resid ** 2, axis=1))
    return C, float(np.sqrt(np.mean(d ** 2))), float(np.max(d))


def eval_pixel_to_world_poly(C, degree: int, u, v):
    """Wertet das Polynom aus. Rückgabe (X_mm, Y_mm) bzw. (N,2) bei Arrays."""
    Phi = poly_basis(u, v, degree)
    out = Phi @ np.asarray(C, dtype=float)
    if out.ndim == 1:
        return float(out[0]), float(out[1])
    return out


def homography_from_pose(R, t, K) -> np.ndarray:
    """Pixel -> Boden-mm-Homographie (Ebene Z=0) aus Kamerapose + Intrinsik.

    Für Weltpunkte auf Z=0 gilt s·[u,v,1]^T = K·[r1 | r2 | t]·[X,Y,1]^T.
    Rückgabe ist die Inverse davon: Pixel -> (X_mm, Y_mm).
    R: 3x3 (Welt->Kamera), t: (3,), K: 3x3 (zur passenden – i. d. R. entzerrten –
    Bildauflösung).
    """
    R = np.asarray(R, dtype=float).reshape(3, 3)
    t = np.asarray(t, dtype=float).reshape(3)
    K = np.asarray(K, dtype=float).reshape(3, 3)
    M = K @ np.column_stack([R[:, 0], R[:, 1], t])  # 3x3, Welt(Z=0) -> Pixel
    return np.linalg.inv(M)


def draw_mechanical_grid(
    bgr,
    R,
    t,
    K,
    D=None,
    x_lines=(0, 50, 100, 150, 200, 250, 300, 350, 400, 450),
    y_lines=(0, 100, 200, 300, 400),
    color=(0, 200, 0),
):
    """Zeichnet ein mechanisches mm-Raster (Sichtprüfung) in ein BGR-Bild.

    Die Linien werden in Welt-mm (Z=0) definiert und über die Pose (mit
    Verzeichnung D, falls gegeben -> korrektes Zeichnen im Rohbild) ins Bild
    projiziert; die Y=0-Linie (Bürstenlinie) liegt i. d. R. extrapoliert
    unterhalb der sichtbaren Board-Region.
    """
    import cv2

    draw = bgr.copy()
    h, w = draw.shape[:2]
    R = np.asarray(R, dtype=float).reshape(3, 3)
    rvec, _ = cv2.Rodrigues(R)
    tvec = np.asarray(t, dtype=float).reshape(3, 1)
    K = np.asarray(K, dtype=float).reshape(3, 3)
    zero_d = np.zeros(5) if D is None else np.asarray(D, dtype=float).reshape(-1)

    y0, y1 = float(min(y_lines)), float(max(y_lines))
    x0, x1 = float(min(x_lines)), float(max(x_lines))

    def _project(pts_mm):
        obj = np.hstack([np.asarray(pts_mm, float), np.zeros((len(pts_mm), 1))])
        px, _ = cv2.projectPoints(obj.astype(np.float64), rvec, tvec, K, zero_d)
        return px.reshape(-1, 2)

    def _polyline(pts_mm, label=None):
        px = _project(pts_mm)
        if not np.all(np.isfinite(px)):
            return
        ipts = np.round(px).astype(np.int32)
        cv2.polylines(draw, [ipts], False, color, 1, cv2.LINE_AA)
        if label is not None:
            p = tuple(int(v) for v in ipts[-1])
            if -2000 < p[0] < w + 2000 and -2000 < p[1] < h + 2000:
                cv2.putText(
                    draw, label, p, cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA
                )

    for x in x_lines:
        _polyline([[x, y0], [x, y1]], f"X{int(x)}")
    for y in y_lines:
        _polyline([[x0, y], [x1, y]], f"Y{int(y)}")
    return draw
