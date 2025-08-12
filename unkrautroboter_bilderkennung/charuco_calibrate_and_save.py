# charuco_calibrate_and_save.py
# Zweck: Einmalige Kamerakalibrierung (K,D) + Remap-Tabellen aus Live-Stream mit Charuco-Board
# Ausgabe: .npz mit K, D, newK, roi, map1, map2, img_size, reproj_err, board-Parametern
# Nutzung: python charuco_calibrate_and_save.py
#
# Hinweise:
# - Für die Korrektur (Entzerrung) brauchst du K,D/newK, map1/map2.
# - Diese nutzt du a) bei der XY-Kalibrierung mit dem Board und b) im Livebetrieb für die Auswertung.
# - Fürs Training: optional. Wenn du zur Laufzeit entzerrst, ist es konsistent, auch Trainingsbilder vorher zu entzerren.

import os
import sys
import cv2
import numpy as np
import time
from pathlib import Path

# Kamera: Picamera2 für Frame-Erfassung, cv2 für Verarbeitung
try:
    from picamera2 import Picamera2 # type: ignore
except ImportError as e:
    raise RuntimeError("Picamera2 ist nicht installiert. Installiere 'picamera2' (nur Raspberry Pi / libcamera).") from e

# ---------- Konfiguration ----------
OUT_DIR = Path("./calibration")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "cam_calib_charuco.npz"

CAPTURE_W, CAPTURE_H = 1280, 720   # Kalibrierauflösung (z.B. 1280x720)
NUM_SNAPSHOTS_TARGET = 20          # 15-25 Bilder aus leicht variierenden Lagen/Winkeln
MIN_DETECTED_MARKERS = 6           # Mindestens so viele Marker pro Snapshot
# Headless-Betrieb: keine Fenster, Snapshots automatisch sammeln
AUTO_SNAPSHOT = False  # Interaktiver Modus: Bilder nur auf SPACE aufnehmen

# Charuco-Board Parameter (A4-tauglich, robust)
SQUARES_X = 5
SQUARES_Y = 7
SQUARE_MM = 40.0
MARKER_MM = 30.0
DICT_NAME = "DICT_4X4_50"

# ---------- Hilfsfunktionen für ArUco/Charuco API-Kompatibilität ----------
def ensure_aruco_support():
    ar = cv2.aruco
    has_detect = hasattr(ar, "ArucoDetector") or hasattr(ar, "detectMarkers")
    if not has_detect:
        raise RuntimeError(
            "Deine OpenCV-Installation hat kein ArUco-Detect (contrib). Installiere opencv-contrib-python oder das passende Debian-Paket mit contrib-Modulen."
        )

def get_aruco_dict():
    ar = cv2.aruco
    d = getattr(ar, DICT_NAME)
    return ar.getPredefinedDictionary(d)

def make_charuco_board(aruco_dict):
    # versucht neue & alte API
    ar = cv2.aruco
    if hasattr(ar, "CharucoBoard_create"):
        return ar.CharucoBoard_create(SQUARES_X, SQUARES_Y, SQUARE_MM, MARKER_MM, aruco_dict)
    try:
        return ar.CharucoBoard((SQUARES_X, SQUARES_Y), SQUARE_MM, MARKER_MM, aruco_dict)
    except Exception as e:
        raise RuntimeError("Deine OpenCV-Installation unterstützt CharucoBoard nicht. Installiere opencv-contrib-python >= 4.5") from e

def detect_charuco(gray, aruco_dict, board):
    ar = cv2.aruco
    # Detector-Parameter (API-kompatibel erzeugen)
    if hasattr(ar, "DetectorParameters"):  # neuere API
        params = ar.DetectorParameters()
    else:
        params = ar.DetectorParameters_create()

    # Erkennen mit neuer oder alter API
    if hasattr(ar, "ArucoDetector"):
        detector = ar.ArucoDetector(aruco_dict, params)
        corners, ids, _ = detector.detectMarkers(gray)
    elif hasattr(ar, "detectMarkers"):
        corners, ids, _ = ar.detectMarkers(gray, aruco_dict, parameters=params)
    else:
        raise RuntimeError("ArUco-Erkennung nicht verfügbar. Bitte OpenCV mit contrib-ArUco installieren.")
    if ids is None or len(ids) == 0:
        return None, None, corners, ids

    # Charuco-Ecken interpolieren
    if hasattr(ar, "interpolateCornersCharuco"):
        retval, ch_corners, ch_ids = ar.interpolateCornersCharuco(corners, ids, gray, board)
        # retval = Anzahl interpolierter Ecken; ch_corners: Nx1x2, ch_ids: Nx1
        return ch_corners, ch_ids, corners, ids
    else:
        # Fallback: Nur Marker (weniger genau)
        return None, None, corners, ids

def calibrate_from_accum(marker_snaps, all_ch_corners, all_ch_ids, img_size, board):
    """Kalibriere bevorzugt mit Charuco; ansonsten mit Marker-basiertem calibrateCameraAruco.
    """
    ar = cv2.aruco
    # 1) Charuco bevorzugt, wenn API vorhanden und genügend Daten
    if hasattr(ar, "calibrateCameraCharuco"):
        # Filtere nur Views mit >=4 Charuco-Punkten
        filtered = [(c,i) for c,i in zip(all_ch_corners, all_ch_ids) if i is not None and len(i) >= 4]
        if len(filtered) >= 4:
            f_corners, f_ids = zip(*filtered)
            print(f"[Info] Verwende Charuco-Kalibrierung mit {len(filtered)} gültigen Views (>=4 Ecken)")
            ret, K, D, rvecs, tvecs = ar.calibrateCameraCharuco(list(f_corners), list(f_ids), board, img_size, None, None)
            return ret, K, D
    # 2) Marker-basiert als Fallback (benötigt calibrateCameraAruco)
    if hasattr(ar, "calibrateCameraAruco"):
        all_corners = []  # alle Marker-Corner-Listen hintereinander
        all_ids = []      # alle IDs hintereinander
        counter = []      # Anzahl Marker je Bild
        for (mk_corners, mk_ids, _board) in marker_snaps:
            if mk_ids is None or len(mk_ids) == 0:
                continue
            all_corners.extend(mk_corners)
            all_ids.append(mk_ids)
            counter.append(int(len(mk_ids)))
        if not all_corners:
            raise RuntimeError("Keine Marker-Daten für calibrateCameraAruco vorhanden.")
        ids_concat = np.concatenate(all_ids, axis=0)
        print("[Warnung] Nutze Marker-basierte Kalibrierung (kein Charuco verfügbar).")
        ret, K, D, rvecs, tvecs = ar.calibrateCameraAruco(all_corners, ids_concat, counter, board, img_size, None, None)
        return ret, K, D
    # 3) Kein brauchbarer Kalibrierpfad verfügbar
    raise RuntimeError(
        "ArUco-Kalibrierfunktionen fehlen (weder calibrateCameraCharuco noch calibrateCameraAruco). Bitte opencv-contrib-python installieren."
    )

# ---------- Aufnahme & Kalibrierung (Picamera2, headless) ----------
print("[Info] Interaktiver Modus – Anzahl Marker wird laufend angezeigt.")
print("[Tipp] Drücke SPACE für einen Snapshot (Taste muss losgelassen werden, bevor der nächste zählt).")
picam2 = Picamera2()
if CAPTURE_W and CAPTURE_H:
    config = picam2.create_preview_configuration(main={"size": (int(CAPTURE_W), int(CAPTURE_H)), "format": "RGB888"})
else:
    config = picam2.create_preview_configuration(main={"format": "RGB888"})
picam2.configure(config)
picam2.start()
time.sleep(0.2)  # kurze Aufwärmzeit

ensure_aruco_support()
aruco_dict = get_aruco_dict()
board = make_charuco_board(aruco_dict)

snapshots = 0
all_ch_corners, all_ch_ids = [], []
marker_snapshots = []  # für Fallback (Einfangen von (corners, ids, board))
start = time.time()

print(f"[Info] Ziel: {NUM_SNAPSHOTS_TARGET} Snapshots (mind. {MIN_DETECTED_MARKERS} Marker je Bild empfohlen).")

# --- Tastatureingabe (plattformabhängig, non-blocking) ---
is_tty = sys.stdin.isatty()
need_restore_tty = False
last_status_print = 0.0
wait_for_space_release = False
last_space_seen = 0.0

def _poll_space_pressed():
    """Gibt True zurück, wenn in diesem Loop SPACE gedrückt wurde (non-blocking)."""
    global last_space_seen
    try:
        if os.name == 'nt':
            import msvcrt  # type: ignore
            pressed = False
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == ' ':
                    pressed = True
            if pressed:
                last_space_seen = time.time()
            return pressed
        else:
            import select, termios, tty
            pressed = False
            if is_tty:
                # Terminal in cbreak setzen (nur einmal)
                global need_restore_tty, _old_term
                if not need_restore_tty:
                    _old_term = termios.tcgetattr(sys.stdin)
                    tty.setcbreak(sys.stdin.fileno())
                    need_restore_tty = True
                r, _, _ = select.select([sys.stdin], [], [], 0)
                while r:
                    ch = sys.stdin.read(1)
                    if ch == ' ':
                        pressed = True
                    r, _, _ = select.select([sys.stdin], [], [], 0)
            if pressed:
                last_space_seen = time.time()
            return pressed
    except Exception:
        return False

try:
    while True:
        try:
            frame_rgb = picam2.capture_array()
        except Exception as _:
            print("Kein Kamerabild von Picamera2, beende…")
            break
        # Picamera2 liefert RGB → für OpenCV nach BGR wandeln
        frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ch_corners, ch_ids, mk_corners, mk_ids = detect_charuco(gray, aruco_dict, board)

        # Live-Status anzeigen (gedrosselt)
        n_mk = 0 if mk_ids is None else len(mk_ids)
        n_ch = 0 if ch_ids is None else len(ch_ids)
        now = time.time()
        if now - last_status_print > 0.25:
            print(f"\r[Live] Marker: {n_mk:2d} | Charuco: {n_ch:2d} | Snapshots: {snapshots}/{NUM_SNAPSHOTS_TARGET} | SPACE=Foto", end='', flush=True)
            last_status_print = now

        # SPACE-Logik: nur auf frisches Drücken ein Snapshot, danach erst nach Loslassen wieder
        space_now = _poll_space_pressed()
        if wait_for_space_release:
            # warten, bis SPACE losgelassen (keine SPACE-Events) für mind. 0.3s
            if not space_now and (now - last_space_seen) > 0.3:
                wait_for_space_release = False
        else:
            if space_now:
                # Warnen, wenn wenige Marker erkannt wurden, aber trotzdem aufnehmen
                if (n_ch < MIN_DETECTED_MARKERS) and (n_mk < MIN_DETECTED_MARKERS):
                    print(f"\n[Hinweis] Wenig Features gefunden: Marker={n_mk}, Charuco={n_ch}. Diese Aufnahme könnte weniger beitragen.")
                if ch_corners is not None and ch_ids is not None:
                    all_ch_corners.append(ch_corners)
                    all_ch_ids.append(ch_ids)
                    marker_snapshots.append((mk_corners, mk_ids, board))
                else:
                    marker_snapshots.append((mk_corners, mk_ids, board))
                snapshots += 1
                print(f"\n[OK] Snapshot {snapshots}/{NUM_SNAPSHOTS_TARGET} gespeichert (Marker: {n_mk}, Charuco: {n_ch})")
                wait_for_space_release = True
                # kurze Pause, damit Terminal lesbar bleibt
                time.sleep(0.05)

        if snapshots >= NUM_SNAPSHOTS_TARGET:
            print("\n[Info] Zielanzahl erreicht – starte Kalibrierung…")
            break
finally:
    # Terminal wiederherstellen
    if os.name != 'nt':
        try:
            import termios
            if need_restore_tty:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _old_term)
        except Exception:
            pass

picam2.stop()
try:
    picam2.close()
except Exception:
    pass

# Sammlung zusammenfassen
valid_charuco_views = sum(1 for ids in all_ch_ids if ids is not None and len(ids) >= 4)
print(f"\n[Info] Gesammelt: Snapshots={snapshots}, Charuco-Views(>=4)={valid_charuco_views}, Marker-Views={len(marker_snapshots)}")

if snapshots < max(8, MIN_DETECTED_MARKERS):
    raise RuntimeError("Zu wenige Snapshots für eine stabile Kalibrierung. Sammle mehr Ansichten.")

# Bildgröße
H, W = gray.shape[:2]
img_size = (W, H)

# Kalibrieren
print("[Info] Kalibriere Kamera…")
ret, K, D = calibrate_from_accum(marker_snapshots, all_ch_corners, all_ch_ids, img_size, board)

print(f"[Ergebnis] Reprojektion-Fehler (px): {ret:.4f}")

# Optimale neue Kamera-Matrix (alpha=0 -> kein schwarzer Rand, auto-crop)
newK, roi = cv2.getOptimalNewCameraMatrix(K, D, img_size, alpha=0)
x,y,w,h = roi

# Remap-Tabellen (schnell im Livebetrieb)
map1, map2 = cv2.initUndistortRectifyMap(K, D, None, newK, img_size, cv2.CV_16SC2)

# Speichern
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

print(f"[Gespeichert] {OUT_FILE.resolve()}")
print("[Tipp] Im Code später laden mit:")
print(f"    d = np.load(r'{OUT_FILE.resolve()}', allow_pickle=True)")
print("    K, D, newK, roi, map1, map2 = d['K'], d['D'], d['newK'], d['roi'], d['map1'], d['map2']")
