"""Shared config + tiny REST client for the LightlyStudio labeling loop on .17.

LightlyStudio 1.1.0 uses DuckDB with a single exclusive writer, so only the
running `lightly-studio.service` may hold the DB open. Two access paths:

* REST  (this module's `LS` client) — works while the service runs. Used by
  `preannotate.py` (create predictions) and for reading samples/labels/tags.
* Python DB API — needs the service stopped. Used only by `reindex.py`
  (index new files) and `export_confirmed.py` (YOLO export), both infrequent.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

LIGHTLY_DIR = Path(os.environ.get("LIGHTLY_DIR", Path.home() / "lightly"))
INBOX = LIGHTLY_DIR / "inbox" / "unkraut"
STATE_DIR = LIGHTLY_DIR / "state"
DB_FILE = LIGHTLY_DIR / "studio" / "lightly_studio.db"

DATASET_NAME = "unkraut"
CLASSES = ["unkraut", "moos"]          # class id 0, 1 — order is authoritative

LS_BASE = os.environ.get("LS_BASE", "http://127.0.0.1:8001")
STUDIO_UNIT = "lightly-studio.service"

CURRENT_MODEL = LIGHTLY_DIR / "models" / "current" / "best.pt"


# --------------------------------------------------------------------------- #
# REST client
# --------------------------------------------------------------------------- #
class LS:
    """Minimal LightlyStudio REST client (stdlib only)."""

    def __init__(self, base: str = LS_BASE):
        self.base = base.rstrip("/")
        self._cid: str | None = None

    def _req(self, method: str, path: str, body=None, timeout=60):
        url = f"{self.base}{path}"
        data = None
        headers = {"accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def get(self, path, **kw):
        return self._req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self._req("POST", path, body=body, **kw)

    # -- domain helpers ---------------------------------------------------- #
    def wait_ready(self, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.get("/api/version", timeout=5)
                return True
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(2)
        raise RuntimeError("LightlyStudio did not come up in time")

    def collection_id(self) -> str:
        if self._cid:
            return self._cid
        cols = self.get("/api/collections")
        for c in cols:
            if c.get("name") == DATASET_NAME:
                self._cid = c["collection_id"]
                return self._cid
        raise RuntimeError(f"collection {DATASET_NAME!r} not found")

    def list_images(self):
        """All samples in the collection (REST caps `limit` at 100 → paginate)."""
        cid = self.collection_id()
        rows, offset, page = [], 0, 100
        while True:
            out = self.post(
                f"/api/collections/{cid}/images/list",
                {"pagination": {"limit": page, "offset": offset}},
            )
            batch = out.get("data", [])
            rows.extend(batch)
            if len(batch) < page:
                return rows
            offset += page

    def ensure_labels(self) -> dict[str, str]:
        """Return {class_name: annotation_label_id}, creating any missing."""
        cid = self.collection_id()
        have = {l["annotation_label_name"]: l["annotation_label_id"]
                for l in self.get(f"/api/collections/{cid}/annotation_labels")}
        for name in CLASSES:
            if name not in have:
                r = self.post(f"/api/collections/{cid}/annotation_labels",
                              {"annotation_label_name": name})
                have[name] = r["annotation_label_id"]
        return have

    def add_box(self, sample_id, label_id, x, y, w, h, source):
        cid = self.collection_id()
        return self.post(
            f"/api/collections/{cid}/annotations",
            {
                "parent_sample_id": sample_id,
                "annotation_label_id": label_id,
                "annotation_type": "object_detection",
                "annotation_collection_name": source,
                "x": int(round(x)), "y": int(round(y)),
                "width": int(round(w)), "height": int(round(h)),
            },
        )


# --------------------------------------------------------------------------- #
# systemctl --user helpers (for the DB-API scripts)
# --------------------------------------------------------------------------- #
def _sc(*args):
    return subprocess.run(["systemctl", "--user", *args], check=True)


def studio_stop():
    _sc("stop", STUDIO_UNIT)
    time.sleep(2)


def studio_start():
    _sc("start", STUDIO_UNIT)
    LS().wait_ready()


# --------------------------------------------------------------------------- #
# small local-state helpers (gate work without touching the DB)
# --------------------------------------------------------------------------- #
def _state_file(name: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / name


def load_seen(name: str) -> set[str]:
    p = _state_file(name)
    return set(p.read_text().split()) if p.exists() else set()


def add_seen(name: str, items) -> None:
    p = _state_file(name)
    with p.open("a") as f:
        for it in items:
            f.write(f"{it}\n")


def inbox_basenames() -> set[str]:
    return {p.name for p in INBOX.glob("*.jpg")} | {p.name for p in INBOX.glob("*.JPG")}
