"""Test-Setup: Projektwurzel (mit dem `src`-Paket) auf sys.path legen."""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
