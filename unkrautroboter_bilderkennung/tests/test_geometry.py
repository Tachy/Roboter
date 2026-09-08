"""Unit-Tests für die Pixel->Welt-Umrechnung (geometry.pixel_to_world)."""

import numpy as np
import pytest

from src import geometry


def _write_h(tmp_path, H):
    p = tmp_path / "H.npz"
    np.savez(str(p), H=np.asarray(H, dtype=float))
    return str(p)


def test_load_homography_affine(tmp_path):
    # X_mm = 0.5*px + 10 ; Y_mm = 0.5*py + 20
    H = [[0.5, 0.0, 10.0], [0.0, 0.5, 20.0], [0.0, 0.0, 1.0]]
    assert geometry.load_homography(_write_h(tmp_path, H)) is True
    assert geometry.is_world_transform_ready() is True

    x, y = geometry.pixel_to_world(100.0, 200.0)
    assert x == pytest.approx(60.0)
    assert y == pytest.approx(120.0)


def test_homography_perspective_divide(tmp_path):
    # W = 2 -> Ergebnis wird durch W geteilt
    H = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 2.0]]
    assert geometry.load_homography(_write_h(tmp_path, H)) is True

    x, y = geometry.pixel_to_world(100.0, 200.0)
    assert x == pytest.approx(50.0)
    assert y == pytest.approx(100.0)


def test_load_homography_rejects_bad_shape(tmp_path):
    p = tmp_path / "bad.npz"
    np.savez(str(p), H=np.eye(4))
    assert geometry.load_homography(str(p)) is False


def test_load_homography_missing_file():
    assert geometry.load_homography(str("does-not-exist.npz")) is False
