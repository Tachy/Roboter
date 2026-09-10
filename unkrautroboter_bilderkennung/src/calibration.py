"""
Integrierte ChArUco-Kalibrierung für den DISTORTION-Modus.
Sammelt per Joystick-Button Snapshots (ohne Overlay im Live-Stream).
"""

from pathlib import Path
import numpy as np
import cv2
from . import camera, status_bus, config

# Haupt-Board (EXTRINSIK): das große Board am Boden.
SQUARES_X = 10
SQUARES_Y = 15
SQUARE_MM = 50.0
MARKER_MM = 35.0
DICT_NAME = "DICT_5X5_1000"

# Eigenes, kleineres Board NUR für DISTORTION (K,D) – Parameter aus config.
# Passt quer auf A4, lässt sich absolut plan aufziehen. Gleiches Wörterbuch wie
# das Haupt-Board. Für die Intrinsik ist der ABSOLUTE Maßstab egal (K,D sind
# skaleninvariant) – wichtig sind Planheit und volle Sichtbarkeit.
DISTORTION_SQUARES_X, DISTORTION_SQUARES_Y = config.DISTORTION_BOARD_SQUARES
DISTORTION_SQUARE_MM = float(config.DISTORTION_BOARD_SQUARE_MM)
DISTORTION_MARKER_MM = float(config.DISTORTION_BOARD_MARKER_MM)

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


def make_charuco_board(
    aruco_dict,
    squares_x=SQUARES_X,
    squares_y=SQUARES_Y,
    square_mm=SQUARE_MM,
    marker_mm=MARKER_MM,
):
    ar = cv2.aruco
    if hasattr(ar, "CharucoBoard_create"):
        return ar.CharucoBoard_create(
            squares_x, squares_y, square_mm, marker_mm, aruco_dict
        )
    return ar.CharucoBoard((squares_x, squares_y), square_mm, marker_mm, aruco_dict)


def make_distortion_board(aruco_dict):
    """Kleines A4-Board – wird ausschließlich im DISTORTION-Modus genutzt."""
    return make_charuco_board(
        aruco_dict,
        DISTORTION_SQUARES_X,
        DISTORTION_SQUARES_Y,
        DISTORTION_SQUARE_MM,
        DISTORTION_MARKER_MM,
    )


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
        # DISTORTION nutzt das kleine A4-Board (plan aufziehbar), nicht das
        # große Boden-Board der EXTRINSIK.
        self.board = make_distortion_board(self.aruco_dict)
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
        # Aufnahme in voller EXTRINSIK-Auflösung (4056x3040) -> K,D passen zur
        # späteren EXTRINSIK-Aufnahme.
        try:
            bgr = camera.capture_still_array(config.STILL_RESOLUTION_EXTRINSIK)
        except Exception as e:
            status_bus.set_message(f"Kalibrierung: Aufnahme fehlgeschlagen ({e})")
            return False, (0, 0)
        if bgr is None:
            return False, (0, 0)
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
        # img_size aus einer Aufnahme in EXTRINSIK-Auflösung.
        bgr = camera.capture_still_array(config.STILL_RESOLUTION_EXTRINSIK)
        if bgr is None:
            raise RuntimeError("Kein Kamerabild verfügbar für Finalisierung.")
        h, w = bgr.shape[:2]
        img_size = (w, h)
        ret, K, D = calibrate_from_accum(
            self.marker_snapshots,
            self.all_ch_corners,
            self.all_ch_ids,
            img_size,
            self.board,
        )
        newK, _roi = cv2.getOptimalNewCameraMatrix(K, D, img_size, alpha=0)
        # map1/map2 werden NICHT gespeichert: bei 4056x3040 sind das ~100 MB;
        # die Rohbild-Pipeline (v3.1) entzerrt keine Vollbilder mehr.
        np.savez(
            OUT_FILE,
            K=K,
            D=D,
            newK=newK,
            img_size=np.array(img_size),
            reproj_err=float(ret),
            board_squares=(DISTORTION_SQUARES_X, DISTORTION_SQUARES_Y),
            square_mm=DISTORTION_SQUARE_MM,
            marker_mm=DISTORTION_MARKER_MM,
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
POLY_FILE = OUT_DIR / "ground_poly.npz"      # Laufzeit-Artefakt (Rohpixel -> mm)
EXTR_FILE = OUT_DIR / "extrinsics.npz"       # Debug / Overlay
GROUND_H_FILE = OUT_DIR / "ground_homography.npz"  # nur noch Legacy-Fallback


class ExtrinsicSession:
    """EXTRINSIK v3.1 – Rohbild-Pipeline, Ausgabe als Polynom "Kurvenmatrix".

    Voraussetzung (physisch am Gerät hergestellt): Bürste auf Schlitten-X=0,
    Board-Ecke (0,0) unter den Bürstenmittelpunkt, Board-X-Achse exakt parallel
    zur Bürstenfahrt -> **Board-mm == mechanische mm**. Die Aufnahme erfolgt
    von der Kameraposition config.EXTRINSIK_CAPTURE_X_MM (= MITTEX / AUTO-Aufnahme).

    Ablauf: ChArUco auf den ROHbildern erkennen (mehrere gepoolt) ->
    cv2.solvePnP(K, D) -> Pose. Dann ein Welt-mm-Gitter über den sichtbaren
    Board-Bereich per cv2.projectPoints(K, D) auf Rohpixel abbilden und daraus
    ein Polynom C (Grad ~3) fitten: [X_mm, Y_mm] = Φ(u,v)·C. Laufzeit rechnet
    dann rein numpy auf Rohpixeln, ohne Entzerrung.
    """

    def __init__(self):
        ensure_aruco_support()
        self.aruco_dict = get_aruco_dict()
        self.board = make_charuco_board(self.aruco_dict)
        from . import geometry, config

        self._obj_all_mm = geometry.board_chessboard_corners_mm(self.board)  # (M,2)
        self.degree = int(getattr(config, "EXTRINSIK_POLY_DEGREE", 3))

        # Rohe Intrinsik K, D aus der DISTORTION-Kalibrierung.
        self.K = None
        self.D = np.zeros(5)
        try:
            d = np.load("./calibration/cam_calib_charuco.npz", allow_pickle=True)
            self.K = np.asarray(d["K"], dtype=np.float64)
            self.D = np.asarray(d["D"], dtype=np.float64).reshape(-1)
        except Exception:
            self.K = None

        self.img_pts = []  # je Frame: (n,2) ROHpixel
        self.obj_pts = []  # je Frame: (n,3) Board-mm, Z=0
        self.img_shape = None  # (h, w) des Rohbilds
        self.n_frames = 0
        self.last_reproj_px = float("nan")
        self.last_fit_rms_mm = float("nan")
        self.last_R = None
        self.last_t = None
        self.board_x_span = (0.0, 0.0)
        self.board_y_span = (0.0, 0.0)

    def _obj3(self, ids):
        ids = np.asarray(ids).reshape(-1).astype(int)
        xy = self._obj_all_mm[ids]
        return np.hstack([xy, np.zeros((len(ids), 1))]).astype(np.float64)

    def add_frame(self, bgr_raw):
        """ChArUco im ROHbild erkennen, Ecken akkumulieren (keine Entzerrung).

        Rückgabe: (ok: bool, msg: str, preview_bgr).
        """
        if bgr_raw is None:
            return False, "kein Kamerabild", None
        if self.K is None:
            return False, "keine Kamera-Kalibrierung (cam_calib_charuco.npz)", bgr_raw
        self.img_shape = bgr_raw.shape[:2]

        gray = cv2.cvtColor(bgr_raw, cv2.COLOR_BGR2GRAY)
        ch_corners, ch_ids, mk_corners, mk_ids = detect_charuco(
            gray, self.aruco_dict, self.board
        )
        n_ch = 0 if ch_ids is None else len(ch_ids)

        draw = bgr_raw.copy()
        try:
            if mk_ids is not None and len(mk_ids) > 0:
                cv2.aruco.drawDetectedMarkers(draw, mk_corners, mk_ids)
            if ch_corners is not None and n_ch > 0:
                cv2.aruco.drawDetectedCornersCharuco(draw, ch_corners, ch_ids)
        except Exception:
            pass

        if n_ch < 12:
            return False, f"Board zu schwach erkannt ({n_ch} Ecken)", draw

        self.img_pts.append(np.asarray(ch_corners, dtype=np.float64).reshape(-1, 2))
        self.obj_pts.append(self._obj3(ch_ids))
        self.n_frames += 1
        return True, f"{n_ch} Ecken", draw

    def finalize(self):
        """Pose (solvePnP K,D) -> Welt-Gitter -> Polynom C, ground_poly.npz +
        extrinsics.npz schreiben und heiß nachladen.

        Rückgabe: (pfad, reproj_err_px, fit_rms_mm).
        """
        from . import geometry

        if self.n_frames == 0 or not self.img_pts or self.K is None:
            raise RuntimeError("Keine verwertbare Aufnahme.")
        imgp = np.vstack(self.img_pts)
        objp = np.vstack(self.obj_pts)

        x0, x1 = float(objp[:, 0].min()), float(objp[:, 0].max())
        y0, y1 = float(objp[:, 1].min()), float(objp[:, 1].max())
        x_span, y_span = x1 - x0, y1 - y0
        uniq = len(np.unique(np.round(objp[:, :2], 1), axis=0))

        # Abdeckungs-Prüfung: zu wenig / zu kleiner Board-Ausschnitt -> nichts
        # speichern, den Bediener anleiten.
        if uniq < 40 or x_span < 200.0 or y_span < 150.0:
            raise RuntimeError(
                f"Board zu wenig im Bild: {uniq} versch. Ecken, sichtbar "
                f"x {x_span:.0f} mm / y {y_span:.0f} mm. Board größer/näher/"
                f"schärfer ins Bild bringen (mehr Marker, weniger Glanz)."
            )

        flag = getattr(cv2, "SOLVEPNP_ITERATIVE", 0)
        ok, rvec, tvec = cv2.solvePnP(objp, imgp, self.K, self.D, flags=flag)
        if not ok:
            raise RuntimeError("solvePnP fehlgeschlagen.")
        R, _ = cv2.Rodrigues(rvec)
        t = tvec.reshape(3)

        proj, _ = cv2.projectPoints(objp, rvec, tvec, self.K, self.D)
        reproj = float(
            np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - imgp) ** 2, axis=1)))
        )

        # Welt-mm-Gitter über den sichtbaren Board-Bereich (15 % gepolstert),
        # per projectPoints auf ROHpixel; nur Paare im Bild behalten.
        px = 0.15 * x_span
        py = 0.15 * y_span
        gx = np.linspace(x0 - px, x1 + px, 40)
        gy = np.linspace(y0 - py, y1 + py, 40)
        GX, GY = np.meshgrid(gx, gy)
        world = np.column_stack([GX.ravel(), GY.ravel(), np.zeros(GX.size)])
        gpx, _ = cv2.projectPoints(world.astype(np.float64), rvec, tvec, self.K, self.D)
        gpx = gpx.reshape(-1, 2)
        h, w = self.img_shape if self.img_shape else (720, 1280)
        m = 40  # etwas über den Bildrand hinaus zulassen
        inb = (
            (gpx[:, 0] > -m)
            & (gpx[:, 0] < w + m)
            & (gpx[:, 1] > -m)
            & (gpx[:, 1] < h + m)
        )
        gpx, world_xy = gpx[inb], world[inb, :2]
        if len(gpx) < 50:
            raise RuntimeError("Zu wenige Gitterpunkte im Bild für den Poly-Fit.")

        degree = self.degree
        C, rms, mx = geometry.fit_pixel_to_world_poly(gpx, world_xy, degree)
        if rms > 0.3 and degree < 4:
            degree = 4
            C, rms, mx = geometry.fit_pixel_to_world_poly(gpx, world_xy, degree)

        pix_bbox = np.array(
            [gpx[:, 0].min(), gpx[:, 0].max(), gpx[:, 1].min(), gpx[:, 1].max()]
        )

        self.last_reproj_px = reproj
        self.last_fit_rms_mm = float(rms)
        self.last_R = R
        self.last_t = t
        self.board_x_span = (x0, x1)
        self.board_y_span = (y0, y1)

        h_img, w_img = self.img_shape if self.img_shape else (0, 0)
        np.savez(
            POLY_FILE,
            C=C,
            degree=int(degree),
            pix_bbox=pix_bbox,
            ref_wh=np.array([int(w_img), int(h_img)]),
            fit_rms_mm=float(rms),
            fit_max_mm=float(mx),
            reproj_px=float(reproj),
            n_frames=int(self.n_frames),
            n_points=int(len(imgp)),
            board_x_span=np.array(self.board_x_span),
            board_y_span=np.array(self.board_y_span),
            R=R,
            t=t,
            K=self.K,
            D=self.D,
            note="EXTRINSIK v3.1: Polynom ROHpixel->mm; Board-mm == mechanische mm",
        )
        np.savez(
            EXTR_FILE,
            K=self.K,
            newK=self.K,
            D=self.D,
            R=R,
            t=t,
            plane_z0=True,
            note="EXTRINSIK v3.1: Pose (ROHbild / K,D); Boden Z=0",
        )
        try:
            geometry.load_ground_poly(str(POLY_FILE))
        except Exception:
            pass
        return POLY_FILE, reproj, float(rms)

    def grid_overlay(self, bgr):
        """ROH-BGR-Bild mit dem projizierten mechanischen mm-Raster."""
        from . import geometry

        if self.last_R is None or self.K is None:
            return bgr
        try:
            return geometry.draw_mechanical_grid(
                bgr, self.last_R, self.last_t, self.K, D=self.D
            )
        except Exception:
            return bgr

    def stop(self):
        pass


