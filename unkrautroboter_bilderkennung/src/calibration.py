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


# Ausgabedateien der EXTRINSIK-Kalibrierung.
GROUND_H_FILE = OUT_DIR / "ground_homography.npz"
EXTR_FILE = OUT_DIR / "extrinsics.npz"


class ExtrinsicSession:
    """EXTRINSIK v3 – reine Board-Pose, die Bürste wird nie beobachtet.

    Voraussetzung (physisch am Gerät hergestellt): der Bediener fährt die Bürste
    auf Schlitten-X=0, legt die Board-Ecke (0,0) unter den Bürstenmittelpunkt und
    die Board-X-Achse exakt parallel zur Bürstenfahrt. Damit gilt
    **Board-mm == mechanische mm** (keine Drehung, kein Versatz, Maßstab 1).

    Die Routine bestimmt nur die Kamerapose aus dem ChArUco-Board (über mehrere
    Bilder gepoolt) und leitet daraus die Pixel->mm-Homographie ab. Die untere
    Board-Kante liegt i. d. R. unter dem Bildrand -> die Abbildung extrapoliert
    bis zur Bürstenlinie; die bekannte Intrinsik (newK) hält das in Form.
    """

    def __init__(self):
        ensure_aruco_support()
        self.aruco_dict = get_aruco_dict()
        self.board = make_charuco_board(self.aruco_dict)
        from . import geometry

        self._obj_all_mm = geometry.board_chessboard_corners_mm(self.board)  # (M,2)
        self.newK = None
        self.D0 = np.zeros(5)
        self.img_pts = []  # je Frame: (n,2) Pixel (entzerrt)
        self.obj_pts = []  # je Frame: (n,3) Board-mm, Z=0
        self.n_frames = 0
        self.last_reproj_px = float("nan")
        self.last_R = None
        self.last_t = None
        self.board_x_span = (0.0, 0.0)
        self.board_y_span = (0.0, 0.0)

    def _obj3(self, ids):
        ids = np.asarray(ids).reshape(-1).astype(int)
        xy = self._obj_all_mm[ids]
        return np.hstack([xy, np.zeros((len(ids), 1))]).astype(np.float64)

    def add_frame(self, bgr_raw):
        """Rohbild entzerren, ChArUco erkennen, Ecken akkumulieren.

        Rückgabe: (ok: bool, msg: str, preview_bgr).
        """
        if bgr_raw is None:
            return False, "kein Kamerabild", None
        und, newK = camera.undistort_bgr(bgr_raw)
        if newK is None:
            return False, "keine Kamera-Kalibrierung (cam_calib_charuco.npz)", und
        self.newK = np.asarray(newK, dtype=np.float64)

        gray = cv2.cvtColor(und, cv2.COLOR_BGR2GRAY)
        ch_corners, ch_ids, mk_corners, mk_ids = detect_charuco(
            gray, self.aruco_dict, self.board
        )
        n_ch = 0 if ch_ids is None else len(ch_ids)

        draw = und.copy()
        try:
            if mk_ids is not None and len(mk_ids) > 0:
                cv2.aruco.drawDetectedMarkers(draw, mk_corners, mk_ids)
            if ch_corners is not None and n_ch > 0:
                cv2.aruco.drawDetectedCornersCharuco(draw, ch_corners, ch_ids)
        except Exception:
            pass

        if n_ch < 6:
            return False, f"Board zu schwach erkannt ({n_ch} Ecken)", draw

        self.img_pts.append(np.asarray(ch_corners, dtype=np.float64).reshape(-1, 2))
        self.obj_pts.append(self._obj3(ch_ids))
        self.n_frames += 1
        return True, f"{n_ch} Ecken", draw

    def finalize(self):
        """Pose aus allen gepoolten Ecken (ein solvePnP), Homographie ableiten,
        ground_homography.npz + extrinsics.npz schreiben und heiß nachladen.

        Rückgabe: (pfad, reproj_err_px).
        """
        from . import geometry

        if self.n_frames == 0 or not self.img_pts or self.newK is None:
            raise RuntimeError("Keine verwertbare Aufnahme.")
        imgp = np.vstack(self.img_pts)
        objp = np.vstack(self.obj_pts)
        if len(imgp) < 8:
            raise RuntimeError(f"Zu wenige ChArUco-Ecken gesamt ({len(imgp)}).")

        flag = getattr(cv2, "SOLVEPNP_ITERATIVE", 0)
        ok, rvec, tvec = cv2.solvePnP(objp, imgp, self.newK, self.D0, flags=flag)
        if not ok:
            raise RuntimeError("solvePnP fehlgeschlagen.")

        R, _ = cv2.Rodrigues(rvec)
        t = tvec.reshape(3)
        proj, _ = cv2.projectPoints(objp, rvec, tvec, self.newK, self.D0)
        reproj = float(
            np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - imgp) ** 2, axis=1)))
        )

        self.last_reproj_px = reproj
        self.last_R = R
        self.last_t = t
        self.board_x_span = (float(objp[:, 0].min()), float(objp[:, 0].max()))
        self.board_y_span = (float(objp[:, 1].min()), float(objp[:, 1].max()))

        # Board-mm == mechanische mm -> Homographie direkt aus der Pose.
        H = geometry.homography_from_pose(R, t, self.newK)

        np.savez(
            GROUND_H_FILE,
            H=H,
            reproj_err_px=reproj,
            n_frames=int(self.n_frames),
            n_points=int(len(imgp)),
            board_x_span=np.array(self.board_x_span),
            board_y_span=np.array(self.board_y_span),
            theta_deg=0.0,
            scale=1.0,
            offset_mm=np.array([0.0, 0.0]),
            note="EXTRINSIK v3: Pixel->mm aus Board-Pose; Board-mm == mechanische mm",
        )
        np.savez(
            EXTR_FILE,
            K=self.newK,
            newK=self.newK,
            R=R,
            t=t,
            plane_z0=True,
            note="EXTRINSIK v3: Pose (entzerrt / newK); Boden Z=0",
        )
        try:
            geometry.load_homography(str(GROUND_H_FILE))
        except Exception:
            pass
        return GROUND_H_FILE, reproj

    def grid_overlay(self, bgr):
        """BGR-Bild mit dem projizierten mechanischen mm-Raster (Sichtprüfung)."""
        from . import geometry

        if self.last_R is None or self.newK is None:
            return bgr
        try:
            return geometry.draw_mechanical_grid(
                bgr, self.last_R, self.last_t, self.newK
            )
        except Exception:
            return bgr

    def stop(self):
        pass


