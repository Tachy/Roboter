"""Test-Setup: Projektwurzel (mit dem `src`-Paket) auf sys.path legen.

Zusätzlich wird – nur wenn das echte `picamera2` fehlt (also nicht auf dem Pi) –
ein minimaler Stub eingehängt, damit `src.camera` / `src.calibration` importierbar
sind. Die Stubs werden von keinem Test funktional genutzt.
"""

import os
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _install_picamera2_stub():
    try:
        import picamera2  # noqa: F401

        return
    except Exception:
        pass

    pic = types.ModuleType("picamera2")
    pic.__path__ = []  # als Package markieren

    class _Noop:
        """Permissiver Platzhalter: jeder Attributzugriff -> No-op-Callable."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, _name):
            return lambda *a, **k: None

    pic.Picamera2 = _Noop

    enc = types.ModuleType("picamera2.encoders")
    enc.MJPEGEncoder = object
    out = types.ModuleType("picamera2.outputs")
    out.FileOutput = object

    pic.encoders = enc
    pic.outputs = out
    sys.modules["picamera2"] = pic
    sys.modules["picamera2.encoders"] = enc
    sys.modules["picamera2.outputs"] = out


_install_picamera2_stub()
