#!/usr/bin/env python3
"""Freeze a held-out evaluation set — run ONCE (or with --add later).

Tags `n` reviewed, not-yet-holdout samples with `holdout` (via REST) and
exports just those into ``<LIGHTLY_DIR>/holdout/`` as a canonical YOLO tree
plus ``filenames.txt``. The `holdout` tag makes `export_confirmed.py` and
`train_now.py` skip these samples forever.

Run in the STUDIO venv:
  ~/lightly/venv/bin/python make_holdout.py --n 200 [--add]

Guard: refuses if ``holdout/`` already has images unless ``--add``.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import common
from export_confirmed import export_tag

HOLDOUT_DIR = common.LIGHTLY_DIR / "holdout"
HOLDOUT_TAG = "holdout"
REVIEW_TAG = "reviewed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--add", action="store_true",
                    help="allow adding to an existing holdout")
    a = ap.parse_args()

    existing = list((HOLDOUT_DIR / "images").glob("*")) if HOLDOUT_DIR.exists() else []
    if existing and not a.add:
        print(f"make_holdout: {HOLDOUT_DIR}/images already has {len(existing)} files. "
              f"Use --add to extend, or bump to a fresh holdout_v2/ dir.")
        return 1

    ls = common.LS()
    ls.wait_ready(timeout=30)
    samples = ls.list_images()
    pool = [s for s in samples
            if REVIEW_TAG in {t["name"] if isinstance(t, dict) else t for t in s["tags"]}
            and HOLDOUT_TAG not in {t["name"] if isinstance(t, dict) else t for t in s["tags"]}]
    if len(pool) < a.n:
        print(f"make_holdout: only {len(pool)} reviewed samples available "
              f"(< requested {a.n}); taking all of them.")
    random.seed(a.seed)
    random.shuffle(pool)
    pick = pool[: a.n]
    if not pick:
        print("make_holdout: nothing to do (no eligible reviewed samples).")
        return 1

    cid = ls.collection_id()
    tags = {t["name"]: t["tag_id"] for t in ls.get(f"/api/collections/{cid}/tags")}
    tid = tags.get(HOLDOUT_TAG) or ls.post(
        f"/api/collections/{cid}/tags", {"name": HOLDOUT_TAG})["tag_id"]
    ls.post(f"/api/collections/{cid}/tags/{tid}/add/samples",
            {"sample_ids": [s["sample_id"] for s in pick]})
    print(f"make_holdout: tagged {len(pick)} sample(s) as {HOLDOUT_TAG!r}")

    # export ONLY the holdout tag, no exclusions
    r = export_tag(HOLDOUT_TAG, HOLDOUT_DIR, exclude_tags=())
    (HOLDOUT_DIR / "filenames.txt").write_text("\n".join(r["files"]) + "\n")
    print(f"make_holdout: frozen {r['images']} images at {HOLDOUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
