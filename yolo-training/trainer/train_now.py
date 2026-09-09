#!/usr/bin/env python3
"""Fine-tune the weed detector on confirmed labels and promote it if it holds.

Runs in the TRAINER venv (needs `ultralytics`). Calls export_confirmed.py in
the STUDIO venv as a subprocess (separate venvs).

Pipeline:
  1. export reviewed labels           -> datasets/unkraut_confirmed/
  2. assemble train/val (90/10 by filename sha, holdout excluded)
  3. YOLO(base).train(imgsz=1280, ...) from the last promoted weights
  4. evaluate candidate + current on the frozen holdout (if one exists)
  5. promotion gate (global + per-class, no regression, absolute floor)
  6. pass -> registry/<ts>/ {best.pt, best_ncnn_model/, best.onnx, metrics.json,
             MODEL_CARD.md}; flip models/current; copy to models/pi_inbox_staging/
     fail -> registry/<ts>/REJECTED_metrics.json, exit 1

Usage:  ~/lightly/venv-trainer/bin/python train_now.py
        [--epochs 120] [--imgsz 1280] [--floor 0.25] [--skip-export]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import common

DATASETS = common.LIGHTLY_DIR / "datasets"
CONFIRMED = DATASETS / "unkraut_confirmed"
CURRENT_DS = DATASETS / "unkraut_current"
HOLDOUT = common.LIGHTLY_DIR / "holdout"
REGISTRY = common.LIGHTLY_DIR / "models" / "registry"
CURRENT_LINK = common.LIGHTLY_DIR / "models" / "current"
PI_STAGING = common.LIGHTLY_DIR / "models" / "pi_inbox_staging"
GPU_LOCK = common.LIGHTLY_DIR / "gpu.lock"
RUNS = common.LIGHTLY_DIR / "runs"
STUDIO_PY = common.LIGHTLY_DIR / "venv" / "bin" / "python"

VAL_FRACTION = 10        # 1 in N -> val
GATE_GLOBAL_SLACK = 0.005
GATE_PERCLASS_SLACK = 0.02


def sha_bucket(name: str) -> int:
    return int(hashlib.sha1(name.encode()).hexdigest(), 16)


@contextlib.contextmanager
def gpu_lock():
    import fcntl
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(GPU_LOCK, "w")
    print("train_now: waiting for GPU lock ...")
    fcntl.flock(f, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def assemble_dataset(imgsz: int) -> tuple[Path, int, int]:
    holdout_names = set()
    fn = HOLDOUT / "filenames.txt"
    if fn.exists():
        holdout_names = {ln.strip() for ln in fn.read_text().splitlines() if ln.strip()}

    if CURRENT_DS.exists():
        shutil.rmtree(CURRENT_DS)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (CURRENT_DS / sub).mkdir(parents=True)

    imgs = sorted(p for p in (CONFIRMED / "images").glob("*") if p.name not in holdout_names)
    n_train = n_val = 0
    for img in imgs:
        split = "val" if sha_bucket(img.name) % VAL_FRACTION == 0 else "train"
        lbl = CONFIRMED / "labels" / (img.stem + ".txt")
        shutil.copy2(img, CURRENT_DS / "images" / split / img.name)
        (CURRENT_DS / "labels" / split / (img.stem + ".txt")).write_text(
            lbl.read_text() if lbl.exists() else "")
        n_train += split == "train"
        n_val += split == "val"

    if n_val == 0 and n_train > 1:      # tiny dataset: borrow one for val
        one = next((CURRENT_DS / "images/train").glob("*"))
        for d in ("images", "labels"):
            src = CURRENT_DS / d / "train" / (one.stem + (".txt" if d == "labels" else one.suffix))
            src.rename(CURRENT_DS / d / "val" / src.name)
        n_train -= 1
        n_val += 1

    (CURRENT_DS / "data.yaml").write_text(
        f"path: {CURRENT_DS}\ntrain: images/train\nval: images/val\n"
        f"nc: {len(common.CLASSES)}\nnames:\n"
        + "".join(f"  {i}: {n}\n" for i, n in enumerate(common.CLASSES))
    )
    return CURRENT_DS / "data.yaml", n_train, n_val


def val_metrics(weights: Path, data_yaml: Path, imgsz: int) -> dict:
    from ultralytics import YOLO
    m = YOLO(str(weights))
    r = m.val(data=str(data_yaml), imgsz=imgsz, iou=0.6, conf=0.001,
              device=0, verbose=False, plots=False)
    per = {}
    for i, ci in enumerate(r.box.ap_class_index):
        per[m.names[int(ci)]] = {"ap50": float(r.box.ap50[i]),
                                 "ap": float(r.box.ap[i])}
    return {"map": float(r.box.map), "map50": float(r.box.map50),
            "per_class": per, "names": {int(k): v for k, v in m.names.items()}}


def gate(cand: dict, cur: dict | None, floor: float) -> tuple[bool, list[str]]:
    reasons = []
    if cand["map"] < floor:
        reasons.append(f"map {cand['map']:.3f} < floor {floor}")
    if set(cand["names"].values()) != set(common.CLASSES) or \
            [cand["names"][i] for i in sorted(cand["names"])] != common.CLASSES:
        reasons.append(f"class names/order wrong: {cand['names']}")
    if cur:
        if cand["map"] < cur["map"] - GATE_GLOBAL_SLACK:
            reasons.append(f"map regression {cand['map']:.3f} < {cur['map']:.3f}-{GATE_GLOBAL_SLACK}")
        for cls in common.CLASSES:
            a = cand["per_class"].get(cls, {}).get("ap50", 0.0)
            b = cur["per_class"].get(cls, {}).get("ap50", 0.0)
            if a < b - GATE_PERCLASS_SLACK:
                reasons.append(f"{cls} ap50 regression {a:.3f} < {b:.3f}-{GATE_PERCLASS_SLACK}")
    return (not reasons), reasons


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short", "HEAD"],
            text=True).strip()
    except Exception:
        return "unknown"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--floor", type=float, default=0.25)
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--no-gate", action="store_true",
                    help="promote regardless of the holdout gate (still records reasons)")
    a = ap.parse_args()
    ts = dt.datetime.now().strftime("%Y%m%d%H%M%S")

    if not a.skip_export:
        print("train_now: exporting confirmed labels (studio venv) ...")
        rc = subprocess.run([str(STUDIO_PY),
                             str(Path(__file__).with_name("export_confirmed.py"))]).returncode
        if rc != 0:
            print("train_now: export_confirmed.py failed / nothing confirmed — abort")
            return 1

    data_yaml, n_train, n_val = assemble_dataset(a.imgsz)
    print(f"train_now: dataset {n_train} train / {n_val} val")
    if n_train == 0:
        print("train_now: no training images — abort")
        return 1

    base = common.CURRENT_MODEL if common.CURRENT_MODEL.exists() else Path("yolo26s.pt")
    print(f"train_now: base weights = {base}")

    from ultralytics import YOLO
    import ultralytics

    with gpu_lock():
        model = YOLO(str(base))
        model.train(data=str(data_yaml), imgsz=a.imgsz, epochs=a.epochs, batch=-1,
                    rect=False, mosaic=1.0, close_mosaic=15, patience=30,
                    device=0, workers=8, lr0=0.005, seed=0,
                    project=str(RUNS), name=f"y26s_{ts}", exist_ok=False, verbose=True)
        run_dir = RUNS / f"y26s_{ts}"
        cand = run_dir / "weights" / "best.pt"
        if not cand.exists():
            print("train_now: training produced no best.pt — abort")
            return 1

        holdout_yaml = HOLDOUT / "data.yaml"
        eval_data = holdout_yaml if holdout_yaml.exists() else data_yaml
        print(f"train_now: evaluating on {'frozen holdout' if holdout_yaml.exists() else 'val split'}")
        cand_m = val_metrics(cand, eval_data, a.imgsz)
        cur_m = None
        if common.CURRENT_MODEL.exists() and holdout_yaml.exists():
            cur_m = val_metrics(common.CURRENT_MODEL, eval_data, a.imgsz)

        ok, reasons = gate(cand_m, cur_m, a.floor)
        if a.no_gate and not ok:
            print(f"train_now: --no-gate: promoting despite {reasons}")
            ok = True

        metrics = {
            "ts": ts, "gate": "pass" if ok else "reject",
            "gate_bypassed": bool(a.no_gate), "reasons": reasons,
            "candidate": cand_m, "current": cur_m,
            "eval_on": "holdout" if holdout_yaml.exists() else "val_split",
            "dataset": {"train": n_train, "val": n_val,
                        "confirmed_images": len(list((CONFIRMED / "images").glob("*")))},
            "base_weights": str(base), "base_sha256": sha256(base) if base.exists() else None,
            "ultralytics": ultralytics.__version__, "imgsz": a.imgsz,
            "classes": common.CLASSES, "trainer_git": git_commit(),
        }

        reg = REGISTRY / ts
        reg.mkdir(parents=True, exist_ok=True)
        if not ok:
            (reg / "REJECTED_metrics.json").write_text(json.dumps(metrics, indent=2))
            print(f"train_now: REJECTED — {reasons}\n  {reg}/REJECTED_metrics.json")
            return 1

        # --- promote ---
        shutil.copy2(cand, reg / "best.pt")
        pt = reg / "best.pt"
        # ultralytics writes exports next to the source .pt
        YOLO(str(pt)).export(format="ncnn", quantize=16, imgsz=a.imgsz)
        YOLO(str(pt)).export(format="onnx", opset=19, imgsz=a.imgsz)
        ncnn_src = reg / "best_ncnn_model"

        metrics["artifacts_sha256"] = {
            "best.pt": sha256(reg / "best.pt"),
            "best.onnx": sha256(reg / "best.onnx") if (reg / "best.onnx").exists() else None,
            "model.ncnn.param": sha256(ncnn_src / "model.ncnn.param") if (ncnn_src / "model.ncnn.param").exists() else None,
            "model.ncnn.bin": sha256(ncnn_src / "model.ncnn.bin") if (ncnn_src / "model.ncnn.bin").exists() else None,
        }
        (reg / "metrics.json").write_text(json.dumps(metrics, indent=2))
        card = [f"# {ts}", "",
                f"YOLO26s @ imgsz {a.imgsz}, classes {common.CLASSES}", ""]
        if cur_m:
            card.append(f"- holdout mAP50-95: {cand_m['map']:.4f}  (current {cur_m['map']:.4f})")
        else:
            card.append(f"- eval mAP50-95: {cand_m['map']:.4f}  ({metrics['eval_on']}, no frozen holdout yet)")
        for cls in common.CLASSES:
            card.append(f"- {cls} AP50: {cand_m['per_class'].get(cls, {}).get('ap50', float('nan')):.4f}")
        card += [f"- base: {base.name}", f"- ultralytics: {ultralytics.__version__}",
                 f"- trainer git: {metrics['trainer_git']}"]
        (reg / "MODEL_CARD.md").write_text("\n".join(card) + "\n")

        if CURRENT_LINK.is_symlink() or CURRENT_LINK.exists():
            CURRENT_LINK.unlink()
        CURRENT_LINK.symlink_to(reg)

        if PI_STAGING.exists():
            shutil.rmtree(PI_STAGING)
        PI_STAGING.mkdir(parents=True)
        shutil.copy2(reg / "best.pt", PI_STAGING / "best.pt")
        if (reg / "best.onnx").exists():
            shutil.copy2(reg / "best.onnx", PI_STAGING / "best.onnx")
        if ncnn_src.exists():
            shutil.copytree(ncnn_src, PI_STAGING / "best_ncnn_model")
        shutil.copy2(reg / "metrics.json", PI_STAGING / "metrics.json")

    print(f"train_now: PROMOTED -> {reg}\n  current -> {os.readlink(CURRENT_LINK)}\n"
          f"  staged for the Pi in {PI_STAGING}\n  next: bin/deploy-model.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
