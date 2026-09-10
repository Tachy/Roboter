"""Tests für calibration.ExtrinsicSession (EXTRINSIK v3, reine Board-Pose).

`calibration` zieht über `camera` `picamera2` herein (nur auf dem Pi vorhanden);
conftest.py hängt dafür bei Bedarf einen Stub ein.
"""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from src import calibration, camera, geometry  # noqa: E402


def _apply_h(H, xy):
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    hom = np.hstack([xy, np.ones((len(xy), 1))])
    out = (H @ hom.T).T
    return out[:, :2] / out[:, 2:3]


def _synthetic_pose():
    K = np.array([[950.0, 0.0, 640.0], [0.0, 950.0, 360.0], [0.0, 0.0, 1.0]])
    th = np.deg2rad(22.0)  # nach vorn geneigt
    R = np.array(
        [[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]]
    )
    t = np.array([-40.0, 30.0, 430.0])
    return K, R, t


def _project(objp_xy, K, R, t):
    rvec, _ = cv2.Rodrigues(R)
    obj = np.hstack([np.asarray(objp_xy, float), np.zeros((len(objp_xy), 1))])
    px, _ = cv2.projectPoints(obj.astype(np.float64), rvec, t.reshape(3, 1), K, np.zeros(5))
    return px.reshape(-1, 2)


def test_finalize_from_injected_corners(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "ground_homography.npz")
    monkeypatch.setattr(calibration, "EXTR_FILE", tmp_path / "extrinsics.npz")
    monkeypatch.setattr(geometry, "_H", None, raising=False)

    sess = calibration.ExtrinsicSession()
    obj_all = sess._obj_all_mm  # (126, 2) Board-mm der inneren Ecken
    K, R, t = _synthetic_pose()

    # 3 "Bilder" derselben Pose, je eine Teilmenge der Ecken
    rng = np.random.default_rng(0)
    for _ in range(3):
        ids = np.sort(rng.choice(len(obj_all), size=60, replace=False))
        objp2 = obj_all[ids]
        px = _project(objp2, K, R, t)
        sess.img_pts.append(px)
        sess.obj_pts.append(np.hstack([objp2, np.zeros((len(ids), 1))]))
        sess.n_frames += 1
    sess.newK = K

    path, reproj = sess.finalize()
    assert path.exists()
    assert (tmp_path / "extrinsics.npz").exists()
    assert reproj < 0.5  # exakte synthetische Daten

    d = np.load(str(path))
    assert d["H"].shape == (3, 3)
    assert float(d["scale"]) == 1.0
    assert float(d["theta_deg"]) == 0.0

    # finalize lädt die Homographie sofort scharf: Pixel einer bekannten Board-mm
    # -> zurück auf genau diese Board-mm (== mechanische mm).
    assert geometry.is_world_transform_ready() is True
    test_mm = np.array([[0.0, 150.0], [220.0, 300.0], [440.0, 450.0]])
    px = _project(test_mm, K, R, t)
    for (u, v), (X, Y) in zip(px, test_mm):
        gx, gy = geometry.pixel_to_world(float(u), float(v))
        assert gx == pytest.approx(X, abs=0.5)
        assert gy == pytest.approx(Y, abs=0.5)


def test_finalize_needs_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "g.npz")
    sess = calibration.ExtrinsicSession()
    with pytest.raises(RuntimeError):
        sess.finalize()


def test_add_frame_on_rendered_board_then_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "ground_homography.npz")
    monkeypatch.setattr(calibration, "EXTR_FILE", tmp_path / "extrinsics.npz")
    monkeypatch.setattr(geometry, "_H", None, raising=False)

    sess = calibration.ExtrinsicSession()

    # Board-Bild: 1 px == 1 mm, Rand `m` -> Board-mm (x,y) == Pixel (x+m, y+m).
    # Passende Lochkamera: fx=fy=1, Hauptpunkt (m,m), Kamera bei Z=1, R=I.
    m = 60
    img = sess.board.generateImage((500 + 2 * m, 750 + 2 * m), marginSize=m)
    bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    K = np.array([[1.0, 0.0, float(m)], [0.0, 1.0, float(m)], [0.0, 0.0, 1.0]])

    # undistort_bgr durch Passthrough ersetzen (keine echte Kamera-Kalibrierung)
    monkeypatch.setattr(camera, "undistort_bgr", lambda b: (b, K))

    for _ in range(3):
        ok, msg, _ = sess.add_frame(bgr)
        assert ok, msg
    assert sess.n_frames == 3

    path, reproj = sess.finalize()
    assert path.exists()
    assert reproj < 1.0

    geometry.load_homography(str(path))
    # Pixel der Board-mm (400, 500) == (460, 560) -> zurück auf ~(400, 500)
    gx, gy = geometry.pixel_to_world(460.0, 560.0)
    assert gx == pytest.approx(400.0, abs=2.0)
    assert gy == pytest.approx(500.0, abs=2.0)
