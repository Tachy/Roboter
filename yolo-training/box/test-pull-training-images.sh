#!/usr/bin/env bash
#
# Pi-less test for pull-training-images.sh. Uses a local directory as PI_SRC
# (the script's built-in test mode) and asserts that .png frames content-address
# into inbox/unkraut/, that non-PNG files (incl. legacy .jpg) are ignored, and
# that a second run is idempotent.
#
# Run on .17 (needs flock/rsync/sha1sum):  bash box/test-pull-training-images.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/pull-training-images.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

SRC="$WORK/pi_training"
LIGHTLY="$WORK/lightly"
UNK="$LIGHTLY/inbox/unkraut"
MANIFEST="$LIGHTLY/inbox/manifest.jsonl"
mkdir -p "$SRC"
printf 'fake-png-1'   > "$SRC/bild_0009.png"
printf 'fake-PNG-1'   > "$SRC/bild_0010.PNG"
printf 'legacy-jpg'   > "$SRC/bild_0011.jpg"   # ignored — PNG only
printf 'not-a-frame'  > "$SRC/readme.txt"

fail() { echo "FAIL: $*"; exit 1; }
run()  { PI_SRC="$SRC/" LIGHTLY_DIR="$LIGHTLY" bash "$SCRIPT" >/dev/null; }

run

n_png=$(find "$UNK" -maxdepth 1 -name '*.png' | wc -l)
n_all=$(find "$UNK" -maxdepth 1 -type f | wc -l)
[[ "$n_png" -eq 2 ]] || fail "expected 2 .png in inbox (.png + .PNG), got $n_png"
[[ "$n_all" -eq 2 ]] || fail "expected 2 files in inbox (.jpg ignored), got $n_all"
[[ "$(wc -l < "$MANIFEST")" -eq 2 ]] || fail "expected 2 manifest lines, got $(wc -l < "$MANIFEST")"
grep -q '"orig":"bild_0009.png"' "$MANIFEST" || fail "manifest missing the .png entry"

# second run: nothing new, no duplicate files, no extra manifest lines
run
[[ "$(find "$UNK" -maxdepth 1 -type f | wc -l)" -eq 2 ]] || fail "second run not idempotent"
[[ "$(wc -l < "$MANIFEST")" -eq 2 ]] || fail "second run appended manifest lines"

echo "ok — pull-training-images.sh is PNG-only, ignores legacy .jpg, idempotent"
