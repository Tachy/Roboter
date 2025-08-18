# file: prepare_and_train.py
# All‑in‑one: YAML erzeugen → 80/20 splitten (mit Shuffle) → YOLOv8m trainieren

import os, shutil, random
from pathlib import Path

# ===== CONFIG =====
DATASET_DIR = Path("dataset")
RAW_DIR = DATASET_DIR / "images_raw"  # Bilder + gleichnamige .txt daneben
NAMES = ["unkraut", "moos"]  # <- Klassenreihenfolge passend zu IDs (0,1,...)
MODEL = "yolov8m.pt"
IMGSZ = 640
EPOCHS = 100
BATCH = 8
VAL_SPLIT = 0.20
SEED = 42
SHUFFLE = True
CLEAN_SPLIT = True  # train/val vorab leeren
PROJECT = "runs"
RUN_NAME = "train"

# ==================


def clear_all_workdirs():
    # Trainings- und Validierungsdaten leeren
    clear_dir(DATASET_DIR / "images" / "train")
    clear_dir(DATASET_DIR / "images" / "val")
    clear_dir(DATASET_DIR / "labels" / "train")
    clear_dir(DATASET_DIR / "labels" / "val")
    # Optional: YOLO-Ausgaben löschen (nur, wenn du wirklich alles neu willst)
    # clear_dir(Path(PROJECT))
    print("Train/Val-Verzeichnisse geleert.")
    print("Inhalt nach clear_dir:")
    print("train:", list((DATASET_DIR / "images" / "train").glob("*")))
    print("val:", list((DATASET_DIR / "images" / "val").glob("*")))


def write_dataset_yaml(path: Path, names):
    lines = ["path: dataset", "train: images/train", "val: images/val", "", "names:"]
    for i, n in enumerate(names):
        lines.append(f"  {i}: {n}")
    path.write_text("\n".join(lines), encoding="utf-8")


def ensure_dirs():
    (DATASET_DIR / "images" / "train").mkdir(parents=True, exist_ok=True)
    (DATASET_DIR / "images" / "val").mkdir(parents=True, exist_ok=True)
    (DATASET_DIR / "labels" / "train").mkdir(parents=True, exist_ok=True)
    (DATASET_DIR / "labels" / "val").mkdir(parents=True, exist_ok=True)


def clear_dir(p: Path):
    if not p.exists():
        return
    for x in p.iterdir():
        if x.is_file() or x.is_symlink():
            x.unlink(missing_ok=True)
        elif x.is_dir():
            shutil.rmtree(x, ignore_errors=True)


def collect_pairs():
    # Sammle Bilder eindeutig (case-insensitive), vermeide Duplikate durch mehrfach passende Globs
    exts = {".jpg", ".jpeg", ".png"}
    imgs_set = set()
    for p in RAW_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            # nutze das aufgelöste Pfad-String in lower() als Eindeutigkeits-Key
            imgs_set.add(p.resolve())
    imgs = sorted(imgs_set)
    pairs = []
    for img in imgs:
        lbl = img.with_suffix(".txt")
        pairs.append((img, lbl))
    return pairs


def split_copy(pairs):
    if SHUFFLE:
        random.seed(SEED)
        random.shuffle(pairs)
    split_idx = int(len(pairs) * (1.0 - VAL_SPLIT))
    train_pairs = pairs[:split_idx]
    val_pairs = pairs[split_idx:]

    dst_im_train = DATASET_DIR / "images" / "train"
    dst_im_val = DATASET_DIR / "images" / "val"
    dst_lb_train = DATASET_DIR / "labels" / "train"
    dst_lb_val = DATASET_DIR / "labels" / "val"

    # Nur in train kopieren
    for img, lbl in train_pairs:
        shutil.copy2(img, dst_im_train / img.name)
        out = dst_lb_train / (img.stem + ".txt")
        if lbl.exists():
            shutil.copy2(lbl, out)
        else:
            out.write_text("", encoding="utf-8")

    # Nur in val kopieren
    for img, lbl in val_pairs:
        shutil.copy2(img, dst_im_val / img.name)
        out = dst_lb_val / (img.stem + ".txt")
        if lbl.exists():
            shutil.copy2(lbl, out)
        else:
            out.write_text("", encoding="utf-8")

    # Nach dem Kopieren:
    print("Nach split_copy:")
    train_list = list(dst_im_train.glob("*"))
    val_list = list(dst_im_val.glob("*"))
    print(
        f"Split: train {len(train_pairs)} | val {len(val_pairs)} | unique files -> train {len(train_list)} | val {len(val_list)}"
    )
    # Überschneidungen prüfen (sollten 0 sein) – keine nachträgliche Bereinigung für deterministisches Verhalten
    overlap = set(p.name for p in train_list) & set(p.name for p in val_list)
    if overlap:
        print(f"WARN: Überschneidung zwischen train und val: {sorted(list(overlap))}")


def main():

    assert RAW_DIR.exists(), f"RAW_DIR nicht gefunden: {RAW_DIR}"
    ensure_dirs()
    clear_all_workdirs()  # Immer aufräumen, unabhängig von CLEAN_SPLIT

    pairs = collect_pairs()
    assert pairs, f"Keine Bilder in {RAW_DIR} gefunden."
    write_dataset_yaml(Path("dataset.yaml"), NAMES)
    split_copy(pairs)

    # Train starten (Python-API vermeidet CLI-Abhängigkeiten)
    from ultralytics import YOLO

    model = YOLO(MODEL)
    results = model.train(
        data="dataset.yaml",
        imgsz=IMGSZ,
        epochs=EPOCHS,
        batch=BATCH,
        rect=True,
        workers=0,
        project=PROJECT,
        name=RUN_NAME,
    )
    # Pfad zur best.pt ausgeben
    out_dir = Path(PROJECT) / "detect" / RUN_NAME / "weights" / "best.pt"
    print("\n=== Training fertig ===")
    print(
        "best.pt:",
        out_dir if out_dir.exists() else "(noch nicht gefunden – siehe runs/...)",
    )


if __name__ == "__main__":
    main()
