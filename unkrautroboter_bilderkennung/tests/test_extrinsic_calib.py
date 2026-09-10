"""Unit-Tests für die überarbeitete EXTRINSIK-Kalibrierung.

Getestet werden die reinen Geometrie-Helfer (keine Kamera/Serial-Hardware):
- estimate_board_homography  (Pixel -> Board-mm)
- similarity_from_point_pairs / similarity_through_origin (generische Helfer)
- compose_affine_homography  (S ∘ H)
- homography_from_pose        (Kamerapose + Intrinsik -> Pixel -> Boden-mm)
"""

import numpy as np
import pytest

from src import geometry


# --------------------------------------------------------------------------- #
# Hilfen                                                                       #
# --------------------------------------------------------------------------- #
class _FakeBoard:
    """Dupliziert nur das, was die Helfer brauchen: getChessboardCorners()."""

    def __init__(self, corners_xyz):
        self._c = np.asarray(corners_xyz, dtype=np.float32)

    def getChessboardCorners(self):
        return self._c


def _grid_corners(nx=9, ny=14, step=50.0, x0=50.0, y0=50.0):
    pts = []
    for r in range(ny):
        for c in range(nx):
            pts.append([x0 + c * step, y0 + r * step, 0.0])
    return np.asarray(pts, dtype=np.float32)


def _apply_h(H, xy):
    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    hom = np.hstack([xy, np.ones((len(xy), 1))])
    out = (H @ hom.T).T
    return out[:, :2] / out[:, 2:3]


# --------------------------------------------------------------------------- #
# estimate_board_homography                                                    #
# --------------------------------------------------------------------------- #
def test_estimate_board_homography_recovers_known_transform():
    board = _FakeBoard(_grid_corners())
    obj_mm = board.getChessboardCorners()[:, :2]

    # wahre Abbildung Board-mm -> Pixel (Affin + leichte Perspektive)
    H_mm_to_px = np.array(
        [[2.0, 0.10, 40.0], [0.05, 1.95, 30.0], [1e-4, 5e-5, 1.0]]
    )
    px = _apply_h(H_mm_to_px, obj_mm)

    ch_ids = np.arange(len(obj_mm)).reshape(-1, 1)
    ch_corners = px.reshape(-1, 1, 2).astype(np.float32)

    H = geometry.estimate_board_homography(ch_corners, ch_ids, board)
    assert H is not None

    # H bildet Pixel -> Board-mm ab: an den Ecken muss obj_mm herauskommen
    got = _apply_h(H, px)
    assert np.allclose(got, obj_mm, atol=1e-3)


def test_estimate_board_homography_needs_four_points():
    board = _FakeBoard(_grid_corners())
    ch_ids = np.arange(3).reshape(-1, 1)
    ch_corners = np.zeros((3, 1, 2), dtype=np.float32)
    assert geometry.estimate_board_homography(ch_corners, ch_ids, board) is None


# --------------------------------------------------------------------------- #
# similarity_from_point_pairs                                                  #
# --------------------------------------------------------------------------- #
def test_similarity_pure_translation_single_point():
    src = np.array([[10.0, 5.0]])
    dst = np.array([[0.0, 0.0]])
    S, scale, resid = geometry.similarity_from_point_pairs(src, dst)
    assert scale == pytest.approx(1.0)
    assert resid == pytest.approx(0.0)
    got = _apply_h(np.vstack([S, [0, 0, 1]]), src)
    assert np.allclose(got, dst, atol=1e-9)


def test_similarity_recovers_rotation_scale_translation():
    # wahre Transformation: 3° Drehung, Maßstab 1.0, Versatz (12, -7)
    th = np.deg2rad(3.0)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    t = np.array([12.0, -7.0])
    src = np.array([[0.0, 0.0], [220.0, 0.0], [440.0, 0.0], [100.0, 60.0]])
    dst = (src @ R.T) + t

    S, scale, resid = geometry.similarity_from_point_pairs(src, dst)
    assert scale == pytest.approx(1.0, abs=1e-3)
    assert resid == pytest.approx(0.0, abs=1e-3)
    got = _apply_h(np.vstack([S, [0, 0, 1]]), src)
    assert np.allclose(got, dst, atol=1e-3)


def test_similarity_reports_residual_on_noisy_pairs():
    src = np.array([[0.0, 0.0], [220.0, 0.0], [440.0, 0.0]])
    dst = np.array([[0.0, 0.0], [220.0, 3.0], [440.0, -3.0]])  # nicht kollinear konsistent
    S, scale, resid = geometry.similarity_from_point_pairs(src, dst)
    assert resid > 1.0


# --------------------------------------------------------------------------- #
# similarity_through_origin                                                    #
# --------------------------------------------------------------------------- #
def test_similarity_through_origin_cancels_constant_bias():
    th = np.deg2rad(4.0)
    s = 1.01
    A = s * np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    Bt = np.array([[150.0, 10.0], [290.0, -5.0], [430.0, 8.0]])  # wahre Board-Lagen
    M = (A @ Bt.T).T  # mech = A · board  (Translation 0)
    bias = np.array([7.0, 55.0])
    P_meas = Bt + bias  # gemessen (Schwerpunkt verdeckter Ecken)

    A_hat, scale, theta_deg, resid = geometry.similarity_through_origin(P_meas, M)
    assert scale == pytest.approx(s, abs=1e-4)
    assert theta_deg == pytest.approx(4.0, abs=1e-3)
    assert resid == pytest.approx(0.0, abs=1e-6)
    assert np.allclose((A_hat @ Bt.T).T, M, atol=1e-6)


def test_similarity_through_origin_needs_two_points():
    with pytest.raises(ValueError):
        geometry.similarity_through_origin([[1.0, 2.0]], [[0.0, 0.0]])


# --------------------------------------------------------------------------- #
# compose_affine_homography                                                    #
# --------------------------------------------------------------------------- #
def test_compose_affine_homography_chains_maps():
    H_px_to_board = np.array([[0.5, 0.0, 10.0], [0.0, 0.5, 20.0], [0.0, 0.0, 1.0]])
    # S: Board-mm -> mechanisch (Versatz -10, -20 hebt H genau auf)
    S = np.array([[1.0, 0.0, -10.0], [0.0, 1.0, -20.0]])
    H_mech = geometry.compose_affine_homography(S, H_px_to_board)
    # Pixel (100, 200) -> Board (60, 120) -> mechanisch (50, 100)
    got = _apply_h(H_mech, [[100.0, 200.0]])
    assert np.allclose(got, [[50.0, 100.0]], atol=1e-9)


# --------------------------------------------------------------------------- #
# homography_from_pose                                                         #
# --------------------------------------------------------------------------- #
def test_homography_from_pose_maps_pixels_back_to_ground_mm():
    cv2 = pytest.importorskip("cv2")
    K = np.array([[900.0, 0.0, 640.0], [0.0, 900.0, 360.0], [0.0, 0.0, 1.0]])
    # Kamera ~400 mm über dem Boden, 25° nach vorn geneigt
    th = np.deg2rad(25.0)
    R = np.array(
        [[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]]
    )
    t = np.array([-30.0, 20.0, 420.0])

    H = geometry.homography_from_pose(R, t, K)

    world = np.array([[0.0, 150.0], [220.0, 150.0], [440.0, 300.0], [100.0, 400.0]])
    rvec, _ = cv2.Rodrigues(R)
    px, _ = cv2.projectPoints(
        np.hstack([world, np.zeros((len(world), 1))]).astype(np.float64),
        rvec, t.reshape(3, 1), K, np.zeros(5),
    )
    px = px.reshape(-1, 2)

    got = _apply_h(H, px)
    assert np.allclose(got, world, atol=1e-6)


# --------------------------------------------------------------------------- #
# Polynom "Kurvenmatrix" (Rohpixel -> Boden-mm)                                #
# --------------------------------------------------------------------------- #
def test_poly_basis_shapes_and_terms():
    # Grad d -> (d+1)(d+2)/2 Terme
    for d, n in [(1, 3), (2, 6), (3, 10), (4, 15)]:
        assert geometry.poly_basis(1.0, 1.0, d).shape == (n,)
    Phi = geometry.poly_basis(np.array([1.0, 2.0]), np.array([3.0, 4.0]), 2)
    assert Phi.shape == (2, 6)
    # Term-Reihenfolge: [1, u, v, u², uv, v²]  bei u=2, v=3
    assert np.allclose(geometry.poly_basis(2.0, 3.0, 2), [1, 2, 3, 4, 6, 9])


def test_fit_and_eval_poly_recovers_distorted_map():
    cv2 = pytest.importorskip("cv2")
    K = np.array([[900.0, 0.0, 640.0], [0.0, 900.0, 360.0], [0.0, 0.0, 1.0]])
    D = np.array([-0.16, 0.04, 0.001, -0.001, 0.0])
    th = np.deg2rad(22.0)
    R = np.array(
        [[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]]
    )
    t = np.array([-25.0, 30.0, 450.0])
    rvec, _ = cv2.Rodrigues(R)

    # Welt-mm-Gitter -> verzeichnete Rohpixel (Vorwärtsmodell)
    gx, gy = np.meshgrid(np.linspace(0, 450, 25), np.linspace(80, 500, 25))
    world = np.column_stack([gx.ravel(), gy.ravel()])
    px, _ = cv2.projectPoints(
        np.hstack([world, np.zeros((len(world), 1))]).astype(np.float64),
        rvec, t.reshape(3, 1), K, D,
    )
    px = px.reshape(-1, 2)

    C, rms, mx = geometry.fit_pixel_to_world_poly(px, world, degree=3)
    assert rms < 0.5  # mm
    # Auswertung an neuen Punkten (verzeichnet projiziert)
    test_world = np.array([[100.0, 150.0], [300.0, 250.0], [420.0, 400.0]])
    tpx, _ = cv2.projectPoints(
        np.hstack([test_world, np.zeros((3, 1))]).astype(np.float64),
        rvec, t.reshape(3, 1), K, D,
    )
    for (u, v), (X, Y) in zip(tpx.reshape(-1, 2), test_world):
        gxv, gyv = geometry.eval_pixel_to_world_poly(C, 3, float(u), float(v))
        assert gxv == pytest.approx(X, abs=1.0)
        assert gyv == pytest.approx(Y, abs=1.0)
