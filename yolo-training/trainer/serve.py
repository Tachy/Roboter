#!/usr/bin/env python3
"""Tiny "Train now" web UI for the weed detector.

One button that runs train_now.py (under the GPU lock, inside train_now) and a
live log tail. stdlib only; bind on the LAN. Started by lightly-trainer-ui.service.

    http://192.168.179.17:8090
"""
from __future__ import annotations

import html
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LIGHTLY_DIR = Path(os.environ.get("LIGHTLY_DIR", Path.home() / "lightly"))
TRAINER_PY = LIGHTLY_DIR / "venv-trainer" / "bin" / "python"
TRAIN_NOW = Path(__file__).with_name("train_now.py")
LOG = LIGHTLY_DIR / "logs" / "train_now.log"
HOST = os.environ.get("TRAINER_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("TRAINER_UI_PORT", "8090"))

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def start_training(extra_args: list[str]) -> bool:
    global _proc
    with _lock:
        if running():
            return False
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "w") as f:
            f.write(f"=== train_now start ===\n")
        logf = open(LOG, "a", buffering=1)
        _proc = subprocess.Popen(
            [str(TRAINER_PY), str(TRAIN_NOW), *extra_args],
            stdout=logf, stderr=subprocess.STDOUT, cwd=str(LIGHTLY_DIR),
        )
        return True


PAGE = """<!doctype html><meta charset=utf-8>
<meta http-equiv=refresh content=4>
<title>Unkraut – Train now</title>
<style>
 body{{font:15px/1.5 system-ui;margin:2rem;max-width:1100px}}
 button{{font-size:1.1rem;padding:.6rem 1.4rem}}
 pre{{background:#111;color:#ddd;padding:1rem;overflow:auto;max-height:70vh;white-space:pre-wrap}}
 .r{{color:#c60}} .i{{color:#080}}
</style>
<h1>Unkraut-Detektor – Training</h1>
<p>Status: <b class="{cls}">{status}</b>
 &nbsp;|&nbsp; Modell: <code>{current}</code></p>
<form method=post action=/train>
 <button {disabled}>Jetzt trainieren</button>
 &nbsp;<label><input type=checkbox name=skip_export> skip export</label>
</form>
<h3>Log</h3><pre>{log}</pre>
"""


class H(BaseHTTPRequestHandler):
    def _send(self, code=200, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/log"):
            txt = LOG.read_text()[-20000:] if LOG.exists() else "(kein Log)"
            return self._send(body=txt.encode(), ctype="text/plain; charset=utf-8")
        cur = "-"
        link = LIGHTLY_DIR / "models" / "current"
        if link.is_symlink():
            cur = os.path.basename(os.readlink(link))
        run = running()
        page = PAGE.format(
            status="läuft …" if run else "bereit",
            cls="r" if run else "i",
            disabled="disabled" if run else "",
            current=html.escape(cur),
            log=html.escape(LOG.read_text()[-20000:]) if LOG.exists() else "(noch nichts)",
        )
        self._send(body=page.encode())

    def do_POST(self):
        if self.path != "/train":
            return self._send(404, b"nope")
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n).decode()
        extra = ["--skip-export"] if "skip_export" in body else []
        start_training(extra)
        self.send_response(303)
        self.send_header("location", "/")
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"trainer UI on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
