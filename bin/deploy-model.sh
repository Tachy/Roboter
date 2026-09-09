#!/usr/bin/env bash
#
# Bundle a promoted detector model and drop it in the Pi's model-OTA inbox.
#
#   Source : tachy@192.168.179.17:29876:~/lightly/models/pi_inbox_staging/
#            (best.pt [+ best.onnx] [+ best_ncnn_model/] + metrics.json)
#            or a local dir via --from
#   Target : admin@192.168.179.252:/home/admin/model_upload/model_<ts>.tar
#
# The Pi (robot_control._check_model_upload) validates the tar in MANUAL mode
# — manifest classes/imgsz, sha256 per file, subprocess load-test — then
# atomically swaps model/best* and hot-reloads. roboter.service is NOT
# restarted. Mirrors the .hex firmware OTA.
#
# Usage:
#   bin/deploy-model.sh [--from DIR] [--dry-run] [--force]
#                       [--imgsz N] [--classes a,b]
#
#   --from DIR   local staging dir instead of pulling from .17
#   --dry-run    build model_<ts>.tar locally, do not copy to the Pi
#   --force      ignore a non-"pass" gate in metrics.json
#   --imgsz N / --classes a,b   override / supply when metrics.json is absent

set -euo pipefail

BOX_USER=tachy;  BOX_HOST=192.168.179.17;  BOX_PORT=29876
BOX_STAGING="lightly/models/pi_inbox_staging"
PI_USER=admin;   PI_HOST=192.168.179.252;  PI_PORT=22
PI_DEST=/home/admin/model_upload

FROM=""; DRY_RUN=0; FORCE=0; OV_IMGSZ=""; OV_CLASSES=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)     FROM="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    --force)    FORCE=1; shift ;;
    --imgsz)    OV_IMGSZ="$2"; shift 2 ;;
    --classes)  OV_CLASSES="$2"; shift 2 ;;
    -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "deploy-model: unknown option '$1'" >&2; exit 2 ;;
  esac
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SRC="$TMP/src"

if [[ -n "$FROM" ]]; then
  [[ -d "$FROM" ]] || { echo "deploy-model: --from '$FROM' not a dir" >&2; exit 1; }
  cp -r "$FROM" "$SRC"
else
  echo ">> pulling staging from $BOX_USER@$BOX_HOST ..."
  scp -q -P "$BOX_PORT" -o BatchMode=yes -r \
    "$BOX_USER@$BOX_HOST:$BOX_STAGING" "$SRC"
fi

[[ -f "$SRC/best.pt" ]] || { echo "deploy-model: no best.pt in source" >&2; exit 1; }

# --- imgsz + classes: metrics.json, else overrides ---------------------------
IMGSZ="$OV_IMGSZ"; CLASSES="$OV_CLASSES"
if [[ -f "$SRC/metrics.json" ]]; then
  read -r M_GATE M_BYP M_IMGSZ M_CLASSES M_TS < <(python - "$SRC/metrics.json" <<'PY' | tr -d '\r'
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("gate", "?"), str(d.get("gate_bypassed", False)).lower(),
      d.get("imgsz", ""), ",".join(d.get("classes", [])), d.get("ts", ""))
PY
)
  [[ -z "$IMGSZ"   ]] && IMGSZ="$M_IMGSZ"
  [[ -z "$CLASSES" ]] && CLASSES="$M_CLASSES"
  if [[ "$M_GATE" != "pass" && $FORCE -eq 0 ]]; then
    echo "deploy-model: metrics.json gate='$M_GATE' (not pass). Use --force." >&2
    exit 1
  fi
  [[ "$M_BYP" == "true" ]] && echo ">> note: promotion gate was bypassed (--no-gate)"
  TS="${M_TS:-$(date +%Y%m%d%H%M%S)}"
else
  TS="$(date +%Y%m%d%H%M%S)"
fi
[[ -n "$IMGSZ" && -n "$CLASSES" ]] || {
  echo "deploy-model: need imgsz+classes (no metrics.json) — pass --imgsz / --classes" >&2
  exit 1; }

# --- drop junk, build manifest --------------------------------------------
find "$SRC" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
rm -f "$SRC/metrics.json"

python - "$SRC" "$TS" "$IMGSZ" "$CLASSES" <<'PY'
import hashlib, json, sys
from pathlib import Path
src, ts, imgsz, classes = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4].split(",")
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
files = {}
for rel in ("best.pt", "best.onnx",
            "best_ncnn_model/model.ncnn.param", "best_ncnn_model/model.ncnn.bin"):
    p = src / rel
    if p.is_file():
        files[rel] = sha(p)
(src / "manifest.json").write_text(json.dumps(
    {"trained_ts": ts, "imgsz": imgsz, "classes": classes, "sha256": files}, indent=2),
    newline="\n")
print(">> manifest:", ", ".join(files))
PY

TAR="$TMP/model_${TS}.tar"
( cd "$SRC" && tar -cf "$TAR" manifest.json best.pt \
    $([[ -f best.onnx ]] && echo best.onnx) \
    $([[ -d best_ncnn_model ]] && echo best_ncnn_model) )
echo ">> built $(basename "$TAR")  ($(du -h "$TAR" | cut -f1))"
tar -tf "$TAR" | sed 's/^/     /'

if [[ $DRY_RUN -eq 1 ]]; then
  cp "$TAR" "./$(basename "$TAR")"
  echo ">> --dry-run: kept ./$(basename "$TAR"), not copied to the Pi"
  exit 0
fi

echo ">> copying to $PI_USER@$PI_HOST:$PI_DEST/ ..."
base="$(basename "$TAR")"
ssh -p "$PI_PORT" -o BatchMode=yes "$PI_USER@$PI_HOST" "mkdir -p '$PI_DEST'"
# scp to a .part name, then atomic rename — the Pi's 0.1 s scan must never see
# a half-written *.tar
scp -q -P "$PI_PORT" -o BatchMode=yes "$TAR" "$PI_USER@$PI_HOST:$PI_DEST/.$base.part"
ssh -p "$PI_PORT" -o BatchMode=yes "$PI_USER@$PI_HOST" \
  "mv '$PI_DEST/.$base.part' '$PI_DEST/$base'"
echo ">> done. Put the robot in MANUAL — it validates, swaps and renames the tar"
echo "   to .uploaded / .failed (see roboter.service logs)."
