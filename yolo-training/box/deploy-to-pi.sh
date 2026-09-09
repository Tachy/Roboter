#!/usr/bin/env bash
#
# Ship the last promoted model from .17 straight to the Pi's model-OTA inbox.
# Runs ON .17 (the pull key already reaches the Pi). The dashboard's
# "Trainieren & deployen" button calls this after train_now.py promotes;
# bin/deploy-model.sh stays as the manual dev-PC path.
#
#   Source : ~/lightly/models/pi_inbox_staging/  (best.pt [+ .onnx] [+ ncnn/] + metrics.json)
#   Target : admin@192.168.179.252:/home/admin/model_upload/model_<ts>.tar
#
# Env: LIGHTLY_DIR (~/lightly)  PI (admin@192.168.179.252)  PI_PORT (22)
#      PI_DEST (/home/admin/model_upload)  PI_KEY (~/.ssh/id_ed25519)

set -euo pipefail

LIGHTLY_DIR="${LIGHTLY_DIR:-$HOME/lightly}"
PI="${PI:-admin@192.168.179.252}"
PI_PORT="${PI_PORT:-22}"
PI_DEST="${PI_DEST:-/home/admin/model_upload}"
PI_KEY="${PI_KEY:-$HOME/.ssh/id_ed25519}"
STAGING="$LIGHTLY_DIR/models/pi_inbox_staging"

[[ -f "$STAGING/best.pt" ]] || { echo "deploy-to-pi: no best.pt in $STAGING" >&2; exit 1; }
[[ -f "$STAGING/metrics.json" ]] || { echo "deploy-to-pi: no metrics.json in $STAGING" >&2; exit 1; }

read -r GATE IMGSZ CLASSES TS < <("$LIGHTLY_DIR/venv/bin/python" - "$STAGING/metrics.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("gate", "?"), d.get("imgsz", ""), ",".join(d.get("classes", [])), d.get("ts", ""))
PY
)
[[ "$GATE" == "pass" ]] || { echo "deploy-to-pi: metrics gate='$GATE' (not pass), refusing" >&2; exit 1; }
[[ -n "$IMGSZ" && -n "$CLASSES" ]] || { echo "deploy-to-pi: metrics.json missing imgsz/classes" >&2; exit 1; }
TS="${TS:-$(date +%Y%m%d%H%M%S)}"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cp -r "$STAGING" "$WORK/src"
find "$WORK/src" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -f "$WORK/src/metrics.json"

"$LIGHTLY_DIR/venv/bin/python" - "$WORK/src" "$TS" "$IMGSZ" "$CLASSES" <<'PY'
import hashlib, json, sys
from pathlib import Path
src, ts, imgsz, classes = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4].split(",")
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
files = {r: sha(src / r) for r in (
    "best.pt", "best.onnx",
    "best_ncnn_model/model.ncnn.param", "best_ncnn_model/model.ncnn.bin",
) if (src / r).is_file()}
(src / "manifest.json").write_text(json.dumps(
    {"trained_ts": ts, "imgsz": imgsz, "classes": classes, "sha256": files}, indent=2))
print("manifest:", ", ".join(files))
PY

TAR="$WORK/model_${TS}.tar"
( cd "$WORK/src" && tar -cf "$TAR" manifest.json best.pt \
    $([[ -f best.onnx ]] && echo best.onnx) \
    $([[ -d best_ncnn_model ]] && echo best_ncnn_model) )
base="$(basename "$TAR")"
echo "deploy-to-pi: $base ($(du -h "$TAR" | cut -f1)) -> $PI:$PI_DEST/"

SSH="ssh -p $PI_PORT -i $PI_KEY -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
$SSH "$PI" "mkdir -p '$PI_DEST'"
scp -q -P "$PI_PORT" -i "$PI_KEY" -o BatchMode=yes "$TAR" "$PI:$PI_DEST/.$base.part"
$SSH "$PI" "mv '$PI_DEST/.$base.part' '$PI_DEST/$base'"
echo "deploy-to-pi: done — Pi swaps it in MANUAL mode (-> .uploaded / .failed)"
