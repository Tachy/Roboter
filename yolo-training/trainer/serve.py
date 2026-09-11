#!/usr/bin/env python3
"""Train + deploy control server for the weed detector (runs on .17, port 8090).

Endpoints
  GET  /            HTML console (button + live log)
  GET  /status      JSON: {running, phase, model_ts, gate, cand_map, cur_map,
                           deployed, rc, log}
  POST /train       token + optional deploy=1 / skip_export=1 → run train_now.py,
                    then (if deploy and it promoted) deploy-to-pi.sh
  POST /deploy      token → deploy-to-pi.sh only (ship the last promoted model)

Actions need ?token= / form token matching env TRAINER_TOKEN (fail-closed).
The .4 dashboard proxies here via train.php with the shared CONTROL_TOKEN.
Started by lightly-trainer-ui.service.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

LIGHTLY_DIR = Path(os.environ.get("LIGHTLY_DIR", Path.home() / "lightly"))
TRAINER_PY = LIGHTLY_DIR / "venv-trainer" / "bin" / "python"
TRAIN_NOW = Path(__file__).with_name("train_now.py")
DEPLOY_SH = LIGHTLY_DIR / "bin" / "deploy-to-pi.sh"
LOG = LIGHTLY_DIR / "logs" / "train_now.log"
REGISTRY = LIGHTLY_DIR / "models" / "registry"
CURRENT = LIGHTLY_DIR / "models" / "current"

HOST = os.environ.get("TRAINER_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("TRAINER_UI_PORT", "8090"))
TOKEN = os.environ.get("TRAINER_TOKEN", "")

# --base is passed straight to train_now.py as a subprocess arg (no shell), but keep
# it to an obvious shape anyway: a bare ultralytics id or a relative path fragment.
_BASE_RE = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")

_lock = threading.Lock()
_state = {"running": False, "phase": "idle", "model_ts": None, "gate": None,
          "cand_map": None, "cur_map": None, "deployed": False, "rc": None}


def _log_tail(n=8000) -> str:
    try:
        return LOG.read_text()[-n:]
    except OSError:
        return ""


def _current_ts() -> str:
    try:
        return os.path.basename(os.readlink(CURRENT))
    except OSError:
        return "-"


def _newest_metrics() -> dict:
    try:
        d = max(REGISTRY.glob("*/"), key=lambda p: p.stat().st_mtime)
        for name in ("metrics.json", "REJECTED_metrics.json"):
            if (d / name).is_file():
                return json.loads((d / name).read_text())
    except (ValueError, OSError, json.JSONDecodeError):
        pass
    return {}


def _worker(deploy: bool, skip_export: bool, base: str = ""):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(f"=== {time.strftime('%F %T')}  train (deploy={deploy}"
                   f"{', base=' + base if base else ''}) ===\n")
    with _lock:
        _state.update(running=True, phase="train", deployed=False, rc=None,
                      gate=None, cand_map=None, cur_map=None, model_ts=None)
    args = [str(TRAINER_PY), str(TRAIN_NOW)]
    if skip_export:
        args.append("--skip-export")
    if base:
        args += ["--base", base]
    with open(LOG, "a", buffering=1) as f:
        rc = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT,
                            cwd=str(LIGHTLY_DIR)).returncode

    m = _newest_metrics()
    with _lock:
        _state.update(rc=rc, gate=m.get("gate"),
                      cand_map=(m.get("candidate") or {}).get("map"),
                      cur_map=(m.get("current") or {}).get("map"),
                      model_ts=m.get("ts"))

    if deploy and rc == 0 and m.get("gate") == "pass":
        with _lock:
            _state["phase"] = "deploy"
        with open(LOG, "a", buffering=1) as f:
            f.write("\n=== deploy-to-pi ===\n")
            drc = subprocess.run(["bash", str(DEPLOY_SH)], stdout=f,
                                 stderr=subprocess.STDOUT,
                                 cwd=str(LIGHTLY_DIR)).returncode
        with _lock:
            _state["deployed"] = (drc == 0)
    elif deploy:
        with open(LOG, "a", buffering=1) as f:
            f.write(f"\n(kein Deploy: rc={rc}, gate={m.get('gate')})\n")

    with _lock:
        _state.update(running=False, phase="idle")


def _deploy_only():
    with _lock:
        _state.update(running=True, phase="deploy", deployed=False)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", buffering=1) as f:
        f.write(f"\n=== {time.strftime('%F %T')}  deploy-only ===\n")
        drc = subprocess.run(["bash", str(DEPLOY_SH)], stdout=f,
                             stderr=subprocess.STDOUT, cwd=str(LIGHTLY_DIR)).returncode
    with _lock:
        _state.update(running=False, phase="idle", deployed=(drc == 0))


def start(kind: str, deploy: bool, skip_export: bool, base: str = "") -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True   # claim immediately
    if kind == "deploy":
        threading.Thread(target=_deploy_only, daemon=True).start()
    else:
        threading.Thread(target=_worker, args=(deploy, skip_export, base),
                         daemon=True).start()
    return True


PAGE = """<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5>
<title>Unkraut – Train &amp; Deploy</title>
<style>body{{font:15px/1.5 Arial, sans-serif;margin:2rem;max-width:1100px}}
button{{font-size:1.05rem;padding:.55rem 1.3rem}}
pre{{background:#111;color:#ddd;padding:1rem;overflow:auto;max-height:65vh;white-space:pre-wrap}}
.r{{color:#c60}}.i{{color:#080}}</style>
<h1>Unkraut-Detektor</h1>
<p>Status <b class="{cls}">{status}</b> &nbsp;|&nbsp; aktuelles Modell <code>{cur}</code>
 &nbsp;|&nbsp; letztes Gate <b>{gate}</b> (cand {cand} / current {curmap}) {dep}</p>
<form method=post action=/train>
 <input type=hidden name=token value="">
 <input type=hidden name=deploy value=1>
 <button {dis}>Trainieren &amp; deployen</button>
 <label style="margin-left:1rem"><input type=checkbox name=skip_export> skip export</label>
 <label style="margin-left:1rem"><input type=checkbox name=base value=yolo26s.pt> Basis: YOLO26s (Erstumstieg)</label>
 <span style="margin-left:1rem;color:#888">Token nötig – Aufruf normalerweise über das .4-Dashboard</span>
</form>
<h3>Log</h3><pre>{log}</pre>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code=200, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, params) -> bool:
        if not TOKEN:
            return False
        supplied = (params.get("token", [""])[0]
                    or self.headers.get("x-token", ""))
        return bool(supplied) and supplied == TOKEN

    def do_GET(self):
        if self.path.startswith("/status"):
            with _lock:
                s = dict(_state)
            s["log"] = _log_tail()
            s["current_model"] = _current_ts()
            return self._send(body=json.dumps(s).encode(),
                              ctype="application/json")
        with _lock:
            s = dict(_state)
        page = PAGE.format(
            status="läuft (%s) …" % s["phase"] if s["running"] else "bereit",
            cls="r" if s["running"] else "i",
            dis="disabled" if s["running"] else "",
            cur=html.escape(_current_ts()),
            gate=s["gate"] or "–",
            cand="%.3f" % s["cand_map"] if s["cand_map"] is not None else "–",
            curmap="%.3f" % s["cur_map"] if s["cur_map"] is not None else "–",
            dep="· deployed ✅" if s["deployed"] else "",
            log=html.escape(_log_tail()) or "(noch nichts)",
        )
        self._send(body=page.encode())

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        params = parse_qs(self.rfile.read(n).decode())
        if self.path not in ("/train", "/deploy"):
            return self._send(404, b"nope")
        if not self._authed(params):
            return self._send(403, b"forbidden")
        if self.path == "/deploy":
            ok = start("deploy", True, False)
        else:
            base = params.get("base", [""])[0].strip()
            if base and not _BASE_RE.match(base):
                return self._send(400, b"bad base")
            ok = start("train",
                       params.get("deploy", ["0"])[0] in ("1", "true", "on"),
                       params.get("skip_export", [""])[0] in ("1", "true", "on"),
                       base)
        body = b"ok" if ok else b"busy"
        wants_html = "text/html" in self.headers.get("accept", "")
        self.send_response(303 if wants_html else 200)
        if wants_html:
            self.send_header("location", "/")
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"train/deploy server on http://{HOST}:{PORT}  (token {'set' if TOKEN else 'MISSING'})")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
