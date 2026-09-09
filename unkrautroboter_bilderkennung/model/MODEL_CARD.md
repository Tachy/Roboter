# Model Card – Unkrautroboter Detektionsmodell

Provenienz-Datei für das auf dem Pi laufende YOLO-Modell. Das eigentliche Gewicht
(`best.pt`, `best_ncnn_model/`, `best.onnx`) ist **nicht** in Git – es lebt auf der
Trainingsmaschine `.17` unter `~/lightly/models/registry/<ts>/` und kommt per
`bin/deploy-model.sh` auf den Pi. Diese Datei wird vom Trainer (`yolo-training/trainer/
train_now.py`) geschrieben und bei einer Promotion vom Menschen committet.

## Aktuell ausgeliefert

| Feld | Wert |
|---|---|
| Registry-ID | `20260909213903` (`.17:~/lightly/models/registry/20260909213903/`) |
| Dateien | `best.pt`, `best_ncnn_model/`, `best.onnx` |
| best.pt sha256 | `2101b0efd090f16339e3cd45afe058cd4d989780ad05c420fc0ecb69ca061c5d` |
| Architektur | **YOLO26s** |
| Laufzeit auf dem Pi | NCNN FP16 (`YOLO_RUNTIME="ncnn"`) |
| Klassen | `0: unkraut`, `1: moos` |
| Trainings-/Inferenz-imgsz | 1280 |
| Basisgewichte | `yolo26s.pt` |
| ultralytics | 8.4.146 |
| Holdout-mAP50-95 | 0.955 — **auf Fake-Bildern, nicht aussagekräftig** |
| Gate | bypassed (`--no-gate`, Bootstrap) |
| Datum | 2026-09-09 |
| Quelle | `train_now.py --base yolo26s.pt` auf ~44 **synthetischen** Bildern |

> ⚠️ **Bootstrap-Modell.** Nur der Architektur-/Pipeline-Umstieg v8m → YOLO26s NCNN@1280.
> Auf Fake-Bildern trainiert, erkennt kein echtes Unkraut. Wird beim ersten Echtdaten-Lauf
> über die Modell-OTA ersetzt (Dashboard-Button, Checkbox „Basis: YOLO26s" dann NICHT mehr
> nötig). v8m-Fallback liegt auf dem Pi unter `model/best.pt.v8m`.

## Historie

- 2026-09-09 – **YOLO26s**, imgsz 1280, NCNN FP16, Klassen `unkraut`/`moos`. Bootstrap
  auf synthetischen Bildern, um Trainings-Pipeline + Pi-Laufzeit auf YOLO26 umzustellen.
  Registry `20260909213903` auf `.17`, manuell in `/home/admin/model/` gestaged +
  `config.py` auf `ncnn`/1280 geflippt.
- 2025-08-13 – YOLOv8m, imgsz 640, sha256 `c2c24c64…f61a244`, Basis `yolov8m.pt`. Erster
  in Git eingecheckter Stand; ab Plan *adaptive-orbiting-fountain* aus Git entfernt
  (`git rm --cached`) und auf `.17` als „home of record" abgelegt. Liegt auf dem Pi noch
  als `model/best.pt.v8m` (Notfall-Rückfall).
