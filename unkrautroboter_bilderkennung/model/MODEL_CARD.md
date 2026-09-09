# Model Card – Unkrautroboter Detektionsmodell

Provenienz-Datei für das auf dem Pi laufende YOLO-Modell. Das eigentliche Gewicht
(`best.pt`, `best_ncnn_model/`, `best.onnx`) ist **nicht** in Git – es lebt auf der
Trainingsmaschine `.17` unter `~/lightly/models/registry/<ts>/` und kommt per
`bin/deploy-model.sh` auf den Pi. Diese Datei wird vom Trainer (`yolo-training/trainer/
train_now.py`) geschrieben und bei einer Promotion vom Menschen committet.

## Aktuell ausgeliefert

| Feld | Wert |
|---|---|
| Datei | `best.pt` |
| sha256 | `c2c24c64032312307ecca2319e23d4efceee61240d28078ec1ba54de0f61a244` |
| Architektur | YOLOv8m (Alt-Stand, vor Umstellung auf YOLO26s) |
| Klassen | `0: unkraut`, `1: moos` |
| Trainings-imgsz | 640 |
| Basisgewichte | `yolov8m.pt` |
| Holdout-mAP | – (kein eingefrorener Holdout im alten Workflow) |
| Datum | 2025-08-13 (Datei-mtime) |
| Quelle | manuelles Training via `yolo-training/prepare_and_train.py` |

## Historie

- 2025-08-13 – YOLOv8m, imgsz 640, Klassen `unkraut`/`moos`. Erster in Git eingecheckter
  Stand; ab Plan *adaptive-orbiting-fountain* aus Git entfernt (`git rm --cached`) und
  auf `.17` als „home of record" abgelegt.
