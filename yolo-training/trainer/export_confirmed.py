#!/usr/bin/env python3
"""Export tagged annotations to a canonical YOLO dataset directory.

Runs in the STUDIO venv (needs `lightly_studio`), briefly stopping the service
to open the DuckDB file. Class ids are remapped to exactly ``common.CLASSES``
order (LightlyStudio orders labels alphabetically), and a canonical
``data.yaml`` is written. A selected sample with no boxes becomes an empty
label file (true negative — valuable against pavement false positives).

Reusable entry point: ``export_tag(tag, out_dir, exclude_tags=(...))``.

CLI (defaults = the training set):
  ~/lightly/venv/bin/python export_confirmed.py [--tag reviewed] [--out DIR]
      [--exclude holdout] [--all]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

import common

OUT_DEFAULT = common.LIGHTLY_DIR / "datasets" / "unkraut_confirmed"


def _read_ls_class_order(data_yaml: Path) -> dict[int, str]:
    order: dict[int, str] = {}
    in_names = False
    for line in data_yaml.read_text().splitlines():
        if line.strip().startswith("names:"):
            in_names = True
            continue
        if in_names:
            m = re.match(r"\s+(\d+):\s*(\S+)", line)
            if m:
                order[int(m.group(1))] = m.group(2)
            elif line and not line[0].isspace():
                break
    return order


def export_tag(tag: str | None, out: Path, exclude_tags=("holdout",)) -> dict:
    """Export samples carrying `tag` (or all samples if tag is None) into `out`.

    Returns {"images", "boxes", "negatives", "files": [file_name, ...]}.
    Caller is responsible for nothing else — this stops/starts the service.
    """
    canon = {name: i for i, name in enumerate(common.CLASSES)}
    exclude = set(exclude_tags)

    tmp = Path(tempfile.mkdtemp(prefix="ls_export_"))
    with common.studio_stopped():
        from lightly_studio.database import db_manager
        db_manager.connect(db_file=str(common.DB_FILE), must_exist=False)
        import lightly_studio as ls

        ds = ls.ImageDataset.load_or_create(name=common.DATASET_NAME)
        selected = []
        for s in ds.query():
            tags = set(s.tags)
            if tags & exclude:
                continue
            if tag is None or tag in tags:
                selected.append(s.file_name)

        ds.export(ds.query()).to_yolo_object_detections(output_folder=str(tmp))

    ls_order = _read_ls_class_order(tmp / "data.yaml")
    remap = {i: canon[n] for i, n in ls_order.items() if n in canon}

    if out.exists():
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True)
    (out / "labels").mkdir(parents=True)

    ls_labels = tmp / "labels"
    n_img = n_box = n_neg = 0
    kept = []
    for fname in sorted(selected):
        src_img = common.INBOX / fname
        if not src_img.exists():
            print(f"export: WARN image missing: {fname}")
            continue
        shutil.copy2(src_img, out / "images" / fname)
        kept.append(fname)
        n_img += 1
        src_txt = ls_labels / (Path(fname).stem + ".txt")
        rows = []
        if src_txt.exists():
            for ln in src_txt.read_text().splitlines():
                p = ln.split()
                if len(p) == 5 and int(p[0]) in remap:
                    rows.append(f"{remap[int(p[0])]} {p[1]} {p[2]} {p[3]} {p[4]}")
        (out / "labels" / (Path(fname).stem + ".txt")).write_text(
            "\n".join(rows) + ("\n" if rows else ""))
        n_box += len(rows)
        n_neg += not rows

    (out / "data.yaml").write_text(
        "path: .\ntrain: images\nval: images\n"
        f"nc: {len(common.CLASSES)}\nnames:\n"
        + "".join(f"  {i}: {n}\n" for i, n in enumerate(common.CLASSES))
    )
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"export[{tag or 'ALL'}]: {out}  ({n_img} img, {n_box} box, {n_neg} neg)"
          f"  remap {ls_order}->{common.CLASSES}")
    return {"images": n_img, "boxes": n_box, "negatives": n_neg, "files": kept}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="reviewed")
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--exclude", nargs="*", default=["holdout"])
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    r = export_tag(None if a.all else a.tag, a.out, a.exclude)
    return 0 if r["images"] else 1


if __name__ == "__main__":
    sys.exit(main())
