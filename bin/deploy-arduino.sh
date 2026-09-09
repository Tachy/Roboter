#!/usr/bin/env bash
#
# Build the motor-control firmware and drop it in the Pi's flash inbox.
#
#   Sketch : unkrautroboter_motorsteuerung/unkrautroboter_motorsteuerung.ino
#   Build  : arduino-cli compile; board/CPU taken from .vscode/arduino.json
#            -> unkrautroboter_motorsteuerung/build/arduino.avr.mega/
#   Deploy : scp the APPLICATION-ONLY  unkrautroboter_motorsteuerung.ino.hex
#            to  admin@192.168.179.252:/home/admin/upload/
#
# Why the plain .ino.hex and NOT .ino.with_bootloader.hex:
# the Pi (robot_control.py::_flash_hex_to_mega) flashes any *.hex it finds in
# that directory while in MANUAL mode with
#     avrdude -p m2560 -c wiring -P /dev/serial0 -b 115200 -D -U flash:w:<hex>:i
# The "wiring" programmer talks to the ATmega2560's resident STK500v2
# bootloader over the serial line, so the bootloader must stay intact --
# we ship the application image only. Verified: the last successfully
# flashed /home/admin/upload/*.ino.hex.uploaded is byte-for-byte the
# arduino-cli app-only build; highest flash address ~0x8960, far below the
# 0x3E000 boot section, and it contains no >64 KB (:02000004) records.
#
# Usage: bin/deploy-arduino.sh [--dry-run]
#   --dry-run   compile only, do not copy to the Pi
#
# Overridable via environment:
#   ARDUINO_CLI   path to arduino-cli (else PATH, else bundled Arduino IDE)
#   FW_FQBN       full FQBN (else built from .vscode/arduino.json)
#   DEPLOY_USER (admin)  DEPLOY_HOST (192.168.179.252)
#   DEPLOY_PORT (22)     DEPLOY_DEST (/home/admin/upload)

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-admin}"
DEPLOY_HOST="${DEPLOY_HOST:-192.168.179.252}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_DEST="${DEPLOY_DEST:-/home/admin/upload}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "deploy-arduino: unknown option '$arg'" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SKETCH_DIR="$REPO/unkrautroboter_motorsteuerung"
BUILD_DIR="$SKETCH_DIR/build/arduino.avr.mega"
ARDUINO_JSON="$REPO/.vscode/arduino.json"
HEX_NAME="unkrautroboter_motorsteuerung.ino.hex"

[[ -f "$SKETCH_DIR/unkrautroboter_motorsteuerung.ino" ]] \
  || { echo "deploy-arduino: sketch not found in $SKETCH_DIR" >&2; exit 1; }

# --- locate arduino-cli -------------------------------------------------------
acli="${ARDUINO_CLI:-}"
if [[ -z "$acli" ]] && command -v arduino-cli >/dev/null 2>&1; then
  acli="$(command -v arduino-cli)"
fi
if [[ -z "$acli" ]]; then
  for c in \
    "/c/Program Files/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe" \
    "${LOCALAPPDATA:-$HOME/AppData/Local}/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe" \
    "$HOME/AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"; do
    [[ -x "$c" ]] && { acli="$c"; break; }
  done
fi
[[ -n "$acli" ]] || { echo "deploy-arduino: arduino-cli not found (set ARDUINO_CLI=)" >&2; exit 1; }

# --- FQBN from .vscode/arduino.json -----------------------------------------
FQBN="${FW_FQBN:-}"
if [[ -z "$FQBN" ]]; then
  FQBN="$(python - "$ARDUINO_JSON" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
board = d.get("board") or "arduino:avr:mega"
cpu = "atmega2560"
for kv in (d.get("configuration") or "").split(","):
    k, _, v = kv.partition("=")
    if k.strip() == "cpu" and v.strip():      # keep only the real board menu option
        cpu = v.strip()
print(f"{board}:cpu={cpu}")
PY
)"
fi

echo ">> arduino-cli : $acli"
echo ">> sketch      : $SKETCH_DIR"
echo ">> fqbn        : $FQBN"
echo ">> build dir   : $BUILD_DIR"

# --- compile ---------------------------------------------------------------
mkdir -p "$BUILD_DIR"
"$acli" compile --fqbn "$FQBN" --output-dir "$BUILD_DIR" "$SKETCH_DIR"

HEX="$BUILD_DIR/$HEX_NAME"
[[ -f "$HEX" ]] || { echo "deploy-arduino: no $HEX_NAME produced" >&2; exit 1; }

# Guard: application image only, no bootloader / no >64 KB addressing.
if grep -q '^:02000004' "$HEX"; then
  echo "deploy-arduino: REFUSING -- $HEX_NAME has >64 KB (:02000004) records," >&2
  echo "                 this does not look like a plain application image." >&2
  exit 1
fi
bytes=$(wc -c < "$HEX")
echo ">> built       : $HEX_NAME ($bytes bytes, application-only, no bootloader)"

if [[ $DRY_RUN -eq 1 ]]; then
  echo ">> --dry-run: not copying to the Pi."
  exit 0
fi

# --- deploy --------------------------------------------------------------
echo ">> copying to  : $DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_DEST/  (port $DEPLOY_PORT)"
scp -P "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=10 -- \
  "$HEX" "$DEPLOY_USER@$DEPLOY_HOST:$DEPLOY_DEST/"

echo ">> upload dir now:"
ssh -p "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=10 \
  "$DEPLOY_USER@$DEPLOY_HOST" "ls -la '$DEPLOY_DEST'" | sed 's/^/     /'

cat <<EOF
>> done. The robot flashes it automatically:
   - it must be in MANUAL mode (the upload scan only runs there)
   - avrdude -c wiring over /dev/serial0; on success the file is renamed
     to $HEX_NAME.uploaded, on failure to .failed
EOF
