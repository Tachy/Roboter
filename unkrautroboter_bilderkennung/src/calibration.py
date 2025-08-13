"""
Integrierte ChArUco-Kalibrierung für den DISTORTION-Modus.
Sammelt per Joystick-Button Snapshots (ohne Overlay im Live-Stream).
"""

from pathlib import Path
import numpy as np
import cv2
from . import camera

# Board-Konfiguration (wie im Standalone-Skript)
SQUARES_X = 5
SQUARES_Y = 7
SQUARE_MM = 40.0
MARKER_MM = 30.0
DICT_NAME = "DICT_4X4_50"

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
        return ar.CharucoBoard_create(SQUARES_X, SQUARES_Y, SQUARE_MM, MARKER_MM, aruco_dict)
    return ar.CharucoBoard((SQUARES_X, SQUARES_Y), SQUARE_MM, MARKER_MM, aruco_dict)


def detect_charuco(gray, aruco_dict, board):
    ar = cv2.aruco
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
        filtered = [(c, i) for c, i in zip(all_ch_corners, all_ch_ids) if i is not None and len(i) >= 4]
        if len(filtered) >= 4:
            f_corners, f_ids = zip(*filtered)
            ret, K, D, _, _ = ar.calibrateCameraCharuco(list(f_corners), list(f_ids), board, img_size, None, None)
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
        ret, K, D, _, _ = ar.calibrateCameraAruco(all_corners, ids_concat, counter, board, img_size, None, None)
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
        ch_corners, ch_ids, mk_corners, mk_ids = detect_charuco(gray, self.aruco_dict, self.board)
        n_mk = 0 if mk_ids is None else len(mk_ids)
        n_ch = 0 if ch_ids is None else len(ch_ids)
        self.last_counts = (n_mk, n_ch)
        return ch_corners, ch_ids, mk_corners, mk_ids, (n_mk, n_ch)

    # Overlay-Funktion entfällt

    def capture_snapshot(self):
        # aktuelles Frame holen
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
            preview = cv2.resize(bgr, (target_w, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
            text = f"Aufnahme {self.snapshots}/{self.target}"
            cv2.putText(preview, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2, cv2.LINE_AA)
            camera._encode_and_store_last_capture(preview, quality=85)
        except Exception:
            pass
        return True, counts

    def finalize(self):
        # Bildgröße aus aktuellem Frame ableiten
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
        ret, K, D = calibrate_from_accum(self.marker_snapshots, self.all_ch_corners, self.all_ch_ids, img_size, self.board)
        newK, roi = cv2.getOptimalNewCameraMatrix(K, D, img_size, alpha=0)
        map1, map2 = cv2.initUndistortRectifyMap(K, D, None, newK, img_size, cv2.CV_16SC2)
        np.savez(
            OUT_FILE,
            K=K, D=D, newK=newK, roi=np.array(roi),
            map1=map1, map2=map2,
            img_size=np.array(img_size),
            reproj_err=float(ret),
            board_squares=(SQUARES_X, SQUARES_Y),
            square_mm=SQUARE_MM,
            marker_mm=MARKER_MM,
            aruco_dict=DICT_NAME
        )
        # Kamera-Kalibrierung neu laden
        try:
            camera.reload_calibration()
        except Exception:
            pass
        return OUT_FILE, ret

    def stop(self):
        pass
