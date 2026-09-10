"""Tests für calibration.ExtrinsicSession.

`calibration` zieht über `camera` `picamera2` herein (nur auf dem Pi vorhanden);
conftest.py hängt dafür bei Bedarf einen Stub ein.
"""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from src import calibration, geometry  # noqa: E402


def _cap(mech_x, board_xy_meas, is_origin, n_ch=50, H_board=None):
    return {
        "mech_xy": np.array([float(mech_x), 0.0]),
        "board_xy_meas": np.asarray(board_xy_meas, dtype=float),
        "is_origin": bool(is_origin),
        "H_board": np.eye(3) if H_board is None else np.asarray(H_board, dtype=float),
        "n_ch": int(n_ch),
    }


def test_finalize_through_origin_cancels_bias_and_composes(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "ground_homography.npz")
    monkeypatch.setattr(geometry, "_H", None, raising=False)

    sess = calibration.ExtrinsicSession(targets_mm=(0.0, 150.0, 290.0, 430.0))

    # Wahre Board-Lagen der Bürste (leicht gegierte Schiene), Mechanik = A·board.
    bias = np.array([9.0, 52.0])
    sess.captures = [
        _cap(0.0, bias, is_origin=True, n_ch=40),          # Nullpunkt: board (0,0)
        _cap(150.0, np.array([150.0, 2.0]) + bias, False, 55),
        _cap(290.0, np.array([290.0, -1.0]) + bias, False, 60),
        _cap(430.0, np.array([430.0, 3.0]) + bias, False, 45),
    ]
    sess.count = 4

    path, resid = sess.finalize()
    assert path.exists()
    assert sess.last_method == "through_origin"
    assert sess.last_scale == pytest.approx(1.0, abs=0.02)
    assert resid < 5.0

    d = np.load(str(path))
    assert d["H"].shape == (3, 3)
    assert bool(d["has_origin"]) is True
    assert int(d["n_interior"]) == 3

    # H_mech ist scharf geladen; Board-Ursprung -> Mechanik-Ursprung.
    assert geometry.is_world_transform_ready() is True
    x, y = geometry.pixel_to_world(0.0, 0.0)
    assert (x, y) == pytest.approx((0.0, 0.0), abs=1.0)
    # Punkt weiter außen bleibt maßstabsgetreu.
    x2, _ = geometry.pixel_to_world(400.0, 0.0)
    assert x2 == pytest.approx(400.0, abs=6.0)


def test_finalize_fallback_without_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "gh.npz")
    monkeypatch.setattr(geometry, "_H", None, raising=False)
    sess = calibration.ExtrinsicSession()
    sess.captures = [
        _cap(0.0, [0.0, 0.0], is_origin=False),
        _cap(220.0, [220.0, 0.0], is_origin=False),
        _cap(440.0, [440.0, 0.0], is_origin=False),
    ]
    sess.count = 3
    path, resid = sess.finalize()
    assert sess.last_method == "full_similarity_fallback"
    assert path.exists()


def test_finalize_needs_at_least_one_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "gh.npz")
    sess = calibration.ExtrinsicSession()
    with pytest.raises(RuntimeError):
        sess.finalize()


def test_capture_on_synthetic_board_end_to_end(tmp_path, monkeypatch):
    """Rendert das echte ChArUco-Board, verdeckt an jeder Position ein Ecken-Nest
    und prüft die komplette Kette capture()->finalize()->pixel_to_world()."""
    monkeypatch.setattr(geometry, "_H", None, raising=False)
    monkeypatch.setattr(calibration, "GROUND_H_FILE", tmp_path / "ground_homography.npz")

    # Messpositionen bewusst mit Abstand zur fernen Board-Kante (x=450), damit
    # die (unrealistisch große) Test-Verdeckung nicht an der Kante abgeschnitten
    # wird – sonst driftet der Schwerpunkt-Versatz mit x.
    targets = (0.0, 120.0, 250.0, 380.0)
    sess = calibration.ExtrinsicSession(targets_mm=targets)

    # Board-Bild: S px == 1 mm, Rand `margin` px -> Board-mm (x,y) == Pixel
    # (x*S + margin, y*S + margin). capture() bestimmt die Abbildung selbst.
    S, margin = 6, 60
    img = sess.board.generateImage((500 * S + 2 * margin, 750 * S + 2 * margin),
                                   marginSize=margin)
    base = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    def _occlude(bx, by, half_mm=55):
        out = base.copy()
        cx, cy = int(bx * S + margin), int(by * S + margin)
        cv2.rectangle(out, (cx - half_mm * S, cy - half_mm * S),
                      (cx + half_mm * S, cy + half_mm * S), (0, 0, 0), -1)
        return out

    # Die Bürste fährt entlang der Board-Nahkante (board-y ~ 0); verdeckt wird
    # jeweils das Nest der ersten inneren Eckenreihe über der Zielposition.
    for i, tx in enumerate(targets):
        frame = _occlude(tx if tx > 0 else 20, 50)
        ok, msg, _ = sess.capture(frame, tx, is_origin=(i == 0))
        assert ok, msg

    path, resid = sess.finalize()
    assert path.exists()
    assert sess.last_method == "through_origin"
    assert sess.last_scale == pytest.approx(1.0, abs=0.05)
    # achsparalleles, deckungsgleiches Board -> kleiner Rest
    assert resid < 25.0

    # Board-Ursprungspixel -> Mechanik-Ursprung
    x, y = geometry.pixel_to_world(float(margin), float(margin))
    assert (x, y) == pytest.approx((0.0, 0.0), abs=8.0)
    # Pixel der Board-mm (400, 0) -> Mechanik ~ (400, 0). Toleranz großzügig:
    # die künstliche Rechteck-Verdeckung im Test erzeugt einen leicht
    # x-abhängigen Schwerpunkt-Versatz (reale, starre Bürste tut das nicht).
    x2, y2 = geometry.pixel_to_world(float(400 * S + margin), float(margin))
    assert x2 == pytest.approx(400.0, abs=25.0)
    assert abs(y2) < 20.0
