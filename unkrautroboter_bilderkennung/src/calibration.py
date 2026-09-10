"""
Integrierte ChArUco-Kalibrierung für den DISTORTION-Modus.
Sammelt per Joystick-Button Snapshots (ohne Overlay im Live-Stream).
"""

from pathlib import Path
import numpy as np
import cv2
from . import camera, status_bus

# Board-Konfiguration (wie im Standalone-Skript)
SQUARES_X = 10
SQUARES_Y = 15
SQUARE_MM = 50.0
MARKER_MM = 35.0
DICT_NAME = "DICT_5X5_1000"

OUT_DIR = Path("./calibration")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "cam_calib_charuco.npz"


def ensure_aruco_support():
    ar = cv2.aruco
    has_detect = hasattr(ar, "ArucoDetector") or hasattr(ar, "detectMarkers")
    if not has_detect:
        raise RuntimeError("OpenCV ArUco nicht verfügbar (contrib-Module fehlen).")


def get_aruco_dict():
    ar = cv2.aruco
    d = getattr(ar, DICT_NAME)
    return ar.getPredefinedDictionary(d)


def make_charuco_board(aruco_dict):
    ar = cv2.aruco
    if hasattr(ar, "CharucoBoard_create"):
        return ar.CharucoBoard_create(
            SQUARES_X, SQUARES_Y, SQUARE_MM, MARKER_MM, aruco_dict
        )
    return ar.CharucoBoard((SQUARES_X, SQUARES_Y), SQUARE_MM, MARKER_MM, aruco_dict)


def detect_charuco(gray, aruco_dict, board):
    """Erkennt ChArUco-Ecken. Rückgabe: (ch_corners, ch_ids, mk_corners, mk_ids).

    Bevorzugt die moderne `CharucoDetector`-API (OpenCV >= 4.7); fällt sonst auf
    `detectMarkers` + `interpolateCornersCharuco` zurück (< 4.9). Ab OpenCV 4.9
    existiert `interpolateCornersCharuco` nicht mehr – ohne `CharucoDetector`
    gäbe es dann gar keine ChArUco-Ecken.
    """
    ar = cv2.aruco
    if hasattr(ar, "CharucoDetector"):
        detector = ar.CharucoDetector(board)
        ch_corners, ch_ids, mk_corners, mk_ids = detector.detectBoard(gray)
        return ch_corners, ch_ids, mk_corners, mk_ids

    if hasattr(ar, "DetectorParameters"):
        params = ar.DetectorParameters()
    else:
        params = ar.DetectorParameters_create()
    if hasattr(ar, "ArucoDetector"):
        detector = ar.ArucoDetector(aruco_dict, params)
        corners, ids, _ = detector.detectMarkers(gray)
    else:
        corners, ids, _ = ar.detectMarkers(gray, aruco_dict, parameters=params)
    if ids is None or len(ids) == 0:
        return None, None, corners, ids
    if hasattr(ar, "interpolateCornersCharuco"):
        _, ch_corners, ch_ids = ar.interpolateCornersCharuco(corners, ids, gray, board)
        return ch_corners, ch_ids, corners, ids
    return None, None, corners, ids


def calibrate_from_accum(marker_snaps, all_ch_corners, all_ch_ids, img_size, board):
    ar = cv2.aruco
    if hasattr(ar, "calibrateCameraCharuco"):
        filtered = [
            (c, i)
            for c, i in zip(all_ch_corners, all_ch_ids)
            if i is not None and len(i) >= 4
        ]
        if len(filtered) >= 4:
            f_corners, f_ids = zip(*filtered)
            ret, K, D, _, _ = ar.calibrateCameraCharuco(
                list(f_corners), list(f_ids), board, img_size, None, None
            )
            return ret, K, D
    if hasattr(ar, "calibrateCameraAruco"):
        all_corners = []
        all_ids = []
        counter = []
        for mk_corners, mk_ids, _ in marker_snaps:
            if mk_ids is None or len(mk_ids) == 0:
                continue
            all_corners.extend(mk_corners)
            all_ids.append(mk_ids)
            counter.append(int(len(mk_ids)))
        if not all_corners:
            raise RuntimeError("Keine Marker-Daten für calibrateCameraAruco vorhanden.")
        ids_concat = np.concatenate(all_ids, axis=0)
        ret, K, D, _, _ = ar.calibrateCameraAruco(
            all_corners, ids_concat, counter, board, img_size, None, None
        )
        return ret, K, D
    raise RuntimeError("ArUco-Kalibrierfunktionen fehlen.")


class CalibrationSession:
    def __init__(self, target_snapshots: int = 20):
        self.target = target_snapshots
        self.snapshots = 0
        self.all_ch_corners = []
        self.all_ch_ids = []
        self.marker_snapshots = []
        ensure_aruco_support()
        self.aruco_dict = get_aruco_dict()
        self.board = make_charuco_board(self.aruco_dict)
        self.last_counts = (0, 0)  # (n_mk, n_ch)

    # Kein Overlay mehr im Hardware-Stream

    def _detect_on_frame(self, bgr):
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        ch_corners, ch_ids, mk_corners, mk_ids = detect_charuco(
            gray, self.aruco_dict, self.board
        )
        n_mk = 0 if mk_ids is None else len(mk_ids)
        n_ch = 0 if ch_ids is None else len(ch_ids)
        self.last_counts = (n_mk, n_ch)
        return ch_corners, ch_ids, mk_corners, mk_ids, (n_mk, n_ch)

    # Overlay-Funktion entfällt

    def capture_snapshot(self):
        # aktuelles Frame holen – im Kalibrierungsmodus läuft der Stream
        arr = camera.picam2.capture_array()
        if arr is None:
            return False, (0, 0)
        # Korrekte Farbumwandlung: RGBA -> BGR, sonst unverändert
        if arr.ndim == 3 and arr.shape[2] == 4:
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        elif arr.ndim == 3 and arr.shape[2] == 3:
            # Einige Setups liefern RGB – hier ggf. in BGR wandeln; wenn Farben vertauscht wirken, weglassen
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        else:
            bgr = arr
        ch_corners, ch_ids, mk_corners, mk_ids, counts = self._detect_on_frame(bgr)
        # speichern
        if ch_corners is not None and ch_ids is not None:
            self.all_ch_corners.append(ch_corners)
            self.all_ch_ids.append(ch_ids)
            self.marker_snapshots.append((mk_corners, mk_ids, self.board))
        else:
            self.marker_snapshots.append((mk_corners, mk_ids, self.board))
        # Zähler hoch
        self.snapshots += 1

        # Mini-Vorschau mit Hinweis "Aufnahme X/Y" an Webserver schicken
        try:
            h, w = bgr.shape[:2]
            target_w = 320
            scale = target_w / float(w)
            preview = cv2.resize(
                bgr, (target_w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA
            )
            text = f"Aufnahme {self.snapshots}/{self.target}"
            camera._encode_and_store_last_capture(preview, quality=85)
            try:
                status_bus.set_message(text)
            except Exception:
                pass
        except Exception:
            pass
        return True, counts

    def finalize(self):
        # Bildgröße aus aktuellem Frame ableiten – im Kalibrierungsmodus läuft der Stream
        arr = camera.picam2.capture_array()
        if arr is None:
            raise RuntimeError("Kein Kamerabild verfügbar für Finalisierung.")
        if arr.ndim == 3 and arr.shape[2] == 4:
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        elif arr.ndim == 3 and arr.shape[2] == 3:
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        else:
            bgr = arr
        h, w = bgr.shape[:2]
        img_size = (w, h)
        ret, K, D = calibrate_from_accum(
            self.marker_snapshots,
            self.all_ch_corners,
            self.all_ch_ids,
            img_size,
            self.board,
        )
        newK, roi = cv2.getOptimalNewCameraMatrix(K, D, img_size, alpha=0)
        map1, map2 = cv2.initUndistortRectifyMap(
            K, D, None, newK, img_size, cv2.CV_16SC2
        )
        np.savez(
            OUT_FILE,
            K=K,
            D=D,
            newK=newK,
            roi=np.array(roi),
            map1=map1,
            map2=map2,
            img_size=np.array(img_size),
            reproj_err=float(ret),
            board_squares=(SQUARES_X, SQUARES_Y),
            square_mm=SQUARE_MM,
            marker_mm=MARKER_MM,
            aruco_dict=DICT_NAME,
        )
        # Kamera-Kalibrierung neu laden
        try:
            camera.reload_calibration()
        except Exception:
            pass
        return OUT_FILE, ret

    def stop(self):
        pass


# Ausgabedatei der mechanik-gekoppelten EXTRINSIK-Kalibrierung.
GROUND_H_FILE = OUT_DIR / "ground_homography.npz"


class ExtrinsicSession:
    """Mechanik-gekoppelte EXTRINSIK-Kalibrierung.

    Der Bediener legt bei Schlitten-X=0 die Board-Ecke (0,0) unter die Bürste und
    richtet die Board-X-Achse grob längs der Spindel aus. An jeder Zielposition
    verdeckt die Bürste ein Nest innerer ChArUco-Ecken; deren Schwerpunkt in
    Board-mm ist der Positions-Proxy. Alle Positionen werden gleich gemessen –
    der konstante Versatz Schwerpunkt<->Bürstenspitze ist damit Gleichtakt und
    geht sauber in die Translation der Ähnlichkeitstransformation ein, während
    Drehung und Maßstab aus den *relativen* Lagen der Schwerpunkte kommen.

    Aus den Punktpaaren (Board-mm <-> mechanische mm) wird eine
    Ähnlichkeitstransformation S bestimmt und mit der Board-Homographie H_board
    zu H_mech = S ∘ H_board verkettet (Pixel -> mechanik-mm) und als
    ground_homography.npz gespeichert.
    """

    # targets_mm[0] ist die Nullpunkt-Aufnahme (Bürste auf Board-Ecke (0,0) bei
    # Schlitten-X=0), der Rest sind Messpositionen. Drei Messpositionen (statt
    # der minimalen zwei) geben 2 Freiheitsgrade -> ein aussagekräftiges
    # Residuum als Qualitätsmaß für die fertige Kalibrierung.
    def __init__(
        self,
        targets_mm=(0.0, 150.0, 290.0, 440.0),
        search_radius_mm: float = 120.0,
    ):
        self.targets_mm = [float(x) for x in targets_mm]
        self.target = len(self.targets_mm)
        self.search_radius_mm = float(search_radius_mm)
        ensure_aruco_support()
        self.aruco_dict = get_aruco_dict()
        self.board = make_charuco_board(self.aruco_dict)
        from . import geometry

        self._corners_mm = geometry.board_chessboard_corners_mm(self.board)
        self.captures = []  # dicts: mech_xy, board_xy, H_board, n_ch
        self.count = 0
        self.last_scale = float("nan")
        self.last_residual = float("nan")

    # ------------------------------------------------------------------ #
    def _detect(self, bgr):
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return detect_charuco(gray, self.aruco_dict, self.board)

    def _preview(self, bgr, mk_corners, mk_ids, mark_px=None):
        draw = bgr.copy()
        try:
            if mk_ids is not None and len(mk_ids) > 0:
                cv2.aruco.drawDetectedMarkers(draw, mk_corners, mk_ids)
            if mark_px is not None:
                p = (int(round(mark_px[0])), int(round(mark_px[1])))
                cv2.circle(draw, p, 14, (0, 0, 255), 3)
                cv2.drawMarker(draw, p, (0, 0, 255), cv2.MARKER_CROSS, 26, 2)
        except Exception:
            pass
        return draw

    def _px_of_board_xy(self, H_board, board_xy):
        """Board-mm -> Pixel über die Inverse der Board-Homographie."""
        try:
            Hi = np.linalg.inv(np.asarray(H_board, dtype=float))
        except Exception:
            return None
        v = Hi @ np.array([board_xy[0], board_xy[1], 1.0])
        if abs(v[2]) < 1e-9:
            return None
        return (float(v[0] / v[2]), float(v[1] / v[2]))

    # ------------------------------------------------------------------ #
    def capture(self, bgr, mech_x_mm: float, is_origin: bool = False):
        """Ein Bild an mechanischer Position mech_x_mm auswerten.

        is_origin: True für die erste Aufnahme bei Schlitten-X=0, bei der der
        Bediener die Bürste auf die Board-Ecke (0,0) gesetzt hat. Ihre wahre
        Board-Lage ist damit (0,0); daraus wird der konstante Versatz zwischen
        Bürstenspitze und Schwerpunkt der verdeckten Ecken bestimmt und von
        allen Aufnahmen abgezogen (siehe finalize()).

        Rückgabe: (ok: bool, msg: str, preview_bgr)
        """
        from . import geometry

        if bgr is None:
            return False, "kein Kamerabild", None
        ch_corners, ch_ids, mk_corners, mk_ids = self._detect(bgr)
        n_ch = 0 if ch_ids is None else len(ch_ids)
        if n_ch < 8:
            return (
                False,
                f"Board nicht ausreichend erkannt ({n_ch} Ecken)",
                self._preview(bgr, mk_corners, mk_ids),
            )

        H_board = geometry.estimate_board_homography(ch_corners, ch_ids, self.board)
        if H_board is None:
            return False, "Board-Homographie fehlgeschlagen", self._preview(
                bgr, mk_corners, mk_ids
            )

        predicted = np.array([float(mech_x_mm), 0.0])
        board_xy_meas, info = geometry.identify_occluded_corner(
            ch_ids, self._corners_mm, predicted, self.search_radius_mm
        )
        if board_xy_meas is None:
            return (
                False,
                f"Bürsten-Position bei X={mech_x_mm:.0f} nicht bestimmbar: {info}",
                self._preview(bgr, mk_corners, mk_ids),
            )
        tag = "Nullpunkt (0,0), " if is_origin else ""
        msg = f"X={mech_x_mm:.0f} mm: {tag}{info}"

        self.captures.append(
            {
                "mech_xy": np.array([float(mech_x_mm), 0.0]),
                "board_xy_meas": np.asarray(board_xy_meas, dtype=float).reshape(2),
                "is_origin": bool(is_origin),
                "H_board": np.asarray(H_board, dtype=float),
                "n_ch": int(n_ch),
            }
        )
        self.count += 1
        mark_px = self._px_of_board_xy(H_board, board_xy_meas)
        return True, msg, self._preview(bgr, mk_corners, mk_ids, mark_px)

    # ------------------------------------------------------------------ #
    def finalize(self):
        """Aus den gesammelten Punktpaaren H_mech berechnen und speichern.

        Rückgabe: (Pfad, RMS-Residuum in mm).
        """
        from . import geometry

        if len(self.captures) < 1:
            raise RuntimeError("Keine verwertbare Position aufgenommen.")

        interior = [c for c in self.captures if not c["is_origin"]]
        has_origin = any(c["is_origin"] for c in self.captures)
        best = max(self.captures, key=lambda c: c["n_ch"])

        if has_origin and len(interior) >= 2:
            # Bevorzugt: Translation fest 0 (gemeinsamer Ursprung), der
            # konstante Mess-Versatz kürzt sich heraus.
            P = np.array([c["board_xy_meas"] for c in interior])
            M = np.array([c["mech_xy"] for c in interior])
            A, scale, theta_deg, resid = geometry.similarity_through_origin(P, M)
            S = np.hstack([A, np.zeros((2, 1))])
            method = "through_origin"
        else:
            # Rückfall: volle Ähnlichkeit aus allen Punkten (Versatz nicht
            # korrigiert -> ggf. systematischer Rest).
            src = np.array([c["board_xy_meas"] for c in self.captures])
            dst = np.array([c["mech_xy"] for c in self.captures])
            S, scale, resid = geometry.similarity_from_point_pairs(src, dst)
            theta_deg = float(np.degrees(np.arctan2(S[1, 0], S[0, 0])))
            method = "full_similarity_fallback"

        H_mech = geometry.compose_affine_homography(S, best["H_board"])

        self.last_scale = float(scale)
        self.last_residual = float(resid)
        self.last_theta_deg = float(theta_deg)
        self.last_method = method

        np.savez(
            GROUND_H_FILE,
            H=H_mech,
            residual_mm=float(resid),
            scale=float(scale),
            theta_deg=float(theta_deg),
            method=method,
            n_points=int(len(self.captures)),
            n_interior=int(len(interior)),
            has_origin=bool(has_origin),
            mech_x_mm=np.array([float(c["mech_xy"][0]) for c in self.captures]),
            board_xy_meas_mm=np.array([c["board_xy_meas"] for c in self.captures]),
            board_squares=(SQUARES_X, SQUARES_Y),
            square_mm=SQUARE_MM,
            note="EXTRINSIK: Pixel->mechanik-mm (S auf H_board); Boden Z=0",
        )
        try:
            geometry.load_homography(str(GROUND_H_FILE))
        except Exception:
            pass
        return GROUND_H_FILE, resid

    def stop(self):
        pass
