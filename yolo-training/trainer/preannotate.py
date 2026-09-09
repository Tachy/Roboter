#!/usr/bin/env python3
"""Pre-annotate not-yet-reviewed inbox images with the current detector.

Pure REST — the LightlyStudio service keeps running. For every sample without
a prediction yet (tracked in a local marker), run the current `best.pt` and
POST one bounding box per detection into annotation source "yolo26s_v<N>".
Low confidence on purpose: deleting a false box in the GUI is cheaper than
drawing a missed one.

Run from the TRAINER venv:  ~/lightly/venv-trainer/bin/python preannotate.py
Driven by  lightly-reindex.timer  (staged right after reindex.py).
"""
from __future__ import annotations

import os
import sys

import common

SEEN = "preannotated.txt"
SOURCE_VERSION = os.environ.get("PREANNOT_SOURCE", "yolo26s_v0")
CONF = float(os.environ.get("PREANNOT_CONF", "0.15"))
IMGSZ = int(os.environ.get("PREANNOT_IMGSZ", "1280"))


def main() -> int:
    if not common.CURRENT_MODEL.exists():
        print(f"preannotate: no model at {common.CURRENT_MODEL} — skip")
        return 0

    ls = common.LS()
    ls.wait_ready(timeout=30)
    samples = ls.list_images()
    seen = common.load_seen(SEEN)
    todo = [s for s in samples if s["file_name"] not in seen]
    if not todo:
        print(f"preannotate: nothing to do ({len(samples)} samples, all pre-annotated)")
        return 0

    from ultralytics import YOLO

    model = YOLO(str(common.CURRENT_MODEL))
    names = model.names  # {0: 'unkraut', 1: 'moos'}
    labels = ls.ensure_labels()

    done, boxes = [], 0
    for s in todo:
        path = s["file_path_abs"]
        try:
            res = model.predict(path, imgsz=IMGSZ, conf=CONF, iou=0.5,
                                device=0, verbose=False)
        except Exception as e:  # noqa: BLE001
            print(f"preannotate: predict failed for {s['file_name']}: {e}")
            continue
        r = res[0]
        for b in r.boxes:
            cls = names.get(int(b.cls), str(int(b.cls)))
            if cls not in labels:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            ls.add_box(s["sample_id"], labels[cls],
                       x1, y1, x2 - x1, y2 - y1, SOURCE_VERSION)
            boxes += 1
        done.append(s["file_name"])

    common.add_seen(SEEN, done)
    print(f"preannotate: {len(done)} image(s), {boxes} box(es) → source {SOURCE_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
