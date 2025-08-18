"""
Thread-safe status message bus to surface status texts to the web UI.
"""

import threading
import time
from typing import Optional

_lock = threading.Lock()
_message: Optional[str] = None
_ts: float = 0.0


def set_message(text: Optional[str]) -> None:
    global _message, _ts
    with _lock:
        _message = (text or "").strip() if text is not None else None
        _ts = time.time()


def get_message() -> Optional[str]:
    with _lock:
        return _message


def get_message_info():
    with _lock:
        return {"message": _message, "ts": _ts}
