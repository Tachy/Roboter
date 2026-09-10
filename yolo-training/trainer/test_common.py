#!/usr/bin/env python3
"""Tests for common.inbox_basenames() — inbox image discovery.

`common` imports `fcntl`, so run on the .17 box, not on Windows:

    ~/lightly/venv-trainer/bin/python -m pytest trainer/test_common.py   # if pytest
    ~/lightly/venv-trainer/bin/python trainer/test_common.py             # standalone
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import common


def _touch(d: Path, *names: str) -> None:
    for n in names:
        (d / n).write_bytes(b"x")


def test_inbox_basenames_png_only(tmp_path, monkeypatch):
    _touch(tmp_path, "a.png", "b.PNG")
    _touch(tmp_path, "c.jpg", "d.JPG", "e.jpeg", "notes.txt", ".hidden")  # ignored
    (tmp_path / "sub").mkdir()
    _touch(tmp_path / "sub", "nested.png")  # glob("*.png") is non-recursive
    monkeypatch.setattr(common, "INBOX", tmp_path)
    assert common.inbox_basenames() == {"a.png", "b.PNG"}


def test_inbox_basenames_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "INBOX", tmp_path / "nope")
    assert common.inbox_basenames() == set()


# --- standalone runner (no pytest) ---------------------------------------- #
class _MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, value in reversed(self._undo):
            setattr(obj, name, value)
        self._undo.clear()


def _main() -> int:
    tests = [test_inbox_basenames_png_only, test_inbox_basenames_missing_dir]
    fails = 0
    for fn in tests:
        mp = _MonkeyPatch()
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td), mp)
                print(f"ok   {fn.__name__}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {fn.__name__}: {e}")
            finally:
                mp.undo()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_main())
