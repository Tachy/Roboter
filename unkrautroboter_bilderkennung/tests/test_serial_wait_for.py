"""Test für SerialManager.wait_for (ohne echte serielle Hardware)."""

import queue
import threading
import time

import pytest

pytest.importorskip("serial", reason="pyserial nur auf dem Pi installiert")

from src.serial_manager import SerialManager  # noqa: E402


def _bare_manager():
    """SerialManager-Instanz ohne __init__ (kein Port), nur mit Queue."""
    sm = SerialManager.__new__(SerialManager)
    sm.received_lines = queue.Queue()
    return sm


def test_wait_for_returns_matching_line_and_skips_others():
    sm = _bare_manager()
    sm.received_lines.put("STATUS:{\"encX\": 5}")
    sm.received_lines.put("WAITING")
    sm.received_lines.put("XREACHED:219.7")
    line = sm.wait_for(("XREACHED:", "FAULT:"), timeout=1.0)
    assert line == "XREACHED:219.7"


def test_wait_for_times_out_to_none():
    sm = _bare_manager()
    sm.received_lines.put("STATUS:{}")
    t0 = time.monotonic()
    assert sm.wait_for("XREACHED:", timeout=0.3) is None
    assert time.monotonic() - t0 < 2.0


def test_wait_for_sees_line_arriving_late():
    sm = _bare_manager()

    def _delayed():
        time.sleep(0.2)
        sm.received_lines.put("FAULT:MOVE")

    threading.Thread(target=_delayed, daemon=True).start()
    assert sm.wait_for(("XREACHED:", "FAULT:"), timeout=2.0) == "FAULT:MOVE"
