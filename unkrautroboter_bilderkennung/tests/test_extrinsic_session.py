"""Tests für calibration.ExtrinsicSession (EXTRINSIK v3.1, Rohbild + Polynom).

`calibration` zieht über `camera` `picamera2` herein (nur auf dem Pi vorhanden);
conftest.py hängt dafür bei Bedarf einen Stub ein.
"""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from src import calibration, geometry  # noqa: E402


def _synthetic_pose():
    # Kamera weit genug zurück, damit das ganze Board (500x750 mm) im 1280x720
    # Bild liegt; Board-Mitte (~225, 375) landet nahe Bildmitte.
    K = np.array([[950.0, 0.0, 640.0], [0.0, 950.0, 360.0], [0.0, 0.0, 1.0]])
    D = np.array([-0.14, 0.03, 0.0008, -0.0006, 0.0])  # milde Verzeichnung
    th = np.deg2rad(12.0)
    R = np.array(
        [[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]]
    )
    t = np.array([-225.0, -330.0, 1050.0])
    return K, D, R, t


def _project(objp_xy, K, D, R, t):
    rvec, _ = cv2.Rodrigues(R)
    obj = np.hstack([np.asarray(objp_xy, float), np.zeros((len(objp_xy), 1))])
    px, _ = cv2.projectPoints(obj.astype(np.float64), rvec, t.reshape(3, 1), K, D)
    return px.reshape(-1, 2)


def test_finalize_from_injected_corners(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "POLY_FILE", tmp_path / "ground_poly.npz")
    monkeypatch.setattr(calibration, "EXTR_FILE", tmp_path / "extrinsics.npz")
    monkeypatch.setattr(geometry, "_C", None, raising=False)

    sess = calibration.ExtrinsicSession()
    K, D, R, t = _synthetic_pose()
    sess.K, sess.D = K, D
    sess.img_shape = (720, 1280)

    obj_all = sess._obj_all_mm  # (126, 2) Board-mm
    # nur Ecken nehmen, die (verzeichnet) im Bild landen
    px_all = _project(obj_all, K, D, R, t)
    inb = (
        (px_all[:, 0] > 0) & (px_all[:, 0] < 1280)
        & (px_all[:, 1] > 0) & (px_all[:, 1] < 720)
    )
    vis = np.where(inb)[0]
    assert len(vis) > 40, "Testpose zeigt zu wenig Board"

    rng = np.random.default_rng(0)
    for _ in range(3):
        ids = np.sort(rng.choice(vis, size=min(50, len(vis)), replace=False))
        objp2 = obj_all[ids]
        sess.img_pts.append(_project(objp2, K, D, R, t))
        sess.obj_pts.append(np.hstack([objp2, np.zeros((len(ids), 1))]))
        sess.n_frames += 1

    path, reproj, fit_rms = sess.finalize()
    assert path.exists()
    assert (tmp_path / "extrinsics.npz").exists()
    assert reproj < 0.5
    assert fit_rms < 0.5

    d = np.load(str(path))
    assert d["C"].shape[1] == 2
    assert int(d["degree"]) in (3, 4)

    # finalize lädt das Polynom scharf: verzeichnetes Pixel einer bekannten
    # Board-mm -> zurück auf genau diese Board-mm (== mechanische mm).
    assert geometry.is_world_transform_ready() is True
    check_ids = vis[:: max(1, len(vis) // 6)][:6]
    for i in check_ids:
        u, v = _project(obj_all[i : i + 1], K, D, R, t)[0]
        gx, gy = geometry.pixel_to_world(float(u), float(v))
        assert gx == pytest.approx(obj_all[i, 0], abs=1.0)
        assert gy == pytest.approx(obj_all[i, 1], abs=1.0)


def test_distortion_uses_small_board_extrinsik_uses_big():
    # DISTORTION: kleines A4-Board (7x5 -> 6x4 = 24 innere Ecken)
    cs = calibration.CalibrationSession(target_snapshots=5)
    assert cs.board.getChessboardCorners().shape[0] == 24
    # EXTRINSIK: großes Boden-Board (10x15 -> 9x14 = 126)
    es = calibration.ExtrinsicSession()
    assert es.board.getChessboardCorners().shape[0] == 126


def test_finalize_needs_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "POLY_FILE", tmp_path / "g.npz")
    sess = calibration.ExtrinsicSession()
    sess.K = np.eye(3)
    with pytest.raises(RuntimeError):
        sess.finalize()


def test_finalize_rejects_poor_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "POLY_FILE", tmp_path / "g.npz")
    sess = calibration.ExtrinsicSession()
    K, D, R, t = _synthetic_pose()
    sess.K, sess.D = K, D
    sess.img_shape = (720, 1280)
    # nur ein winziger Board-Fleck (x 50..120, y 300..380)
    obj = sess._obj_all_mm
    small = obj[(obj[:, 0] <= 120) & (obj[:, 1] >= 300) & (obj[:, 1] <= 380)]
    sess.img_pts.append(_project(small, K, D, R, t))
    sess.obj_pts.append(np.hstack([small, np.zeros((len(small), 1))]))
    sess.n_frames = 1
    with pytest.raises(RuntimeError, match="Board zu wenig im Bild"):
        sess.finalize()


def test_add_frame_on_rendered_board_then_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "POLY_FILE", tmp_path / "ground_poly.npz")
    monkeypatch.setattr(calibration, "EXTR_FILE", tmp_path / "extrinsics.npz")
    monkeypatch.setattr(geometry, "_C", None, raising=False)

    sess = calibration.ExtrinsicSession()
    # Board-Bild: 1 px == 1 mm, Rand m -> Board-mm (x,y) == Pixel (x+m, y+m).
    # Passende Lochkamera: fx=fy=1, Hauptpunkt (m,m), R=I, Kamera bei Z=1.
    m = 60
    img = sess.board.generateImage((500 + 2 * m, 750 + 2 * m), marginSize=m)
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    sess.K = np.array([[1.0, 0.0, float(m)], [0.0, 1.0, float(m)], [0.0, 0.0, 1.0]])
    sess.D = np.zeros(5)

    for _ in range(3):
        ok, msg, _ = sess.add_frame(bgr)
        assert ok, msg
    assert sess.n_frames == 3

    path, reproj, fit_rms = sess.finalize()
    assert path.exists()
    assert reproj < 1.0
    assert fit_rms < 1.0

    geometry.load_ground_poly(str(path))
    # Pixel der Board-mm (400, 500) == (460, 560) -> zurück auf ~(400, 500)
    gx, gy = geometry.pixel_to_world(460.0, 560.0)
    assert gx == pytest.approx(400.0, abs=2.0)
    assert gy == pytest.approx(500.0, abs=2.0)
