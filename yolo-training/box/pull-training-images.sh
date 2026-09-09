#!/usr/bin/env bash
#
# Pull robot training images into the LightlyStudio inbox.
#
# Runs on the .17 GPU box (systemd --user, lightly-pull.timer, ~every 3 min).
# The Raspberry Pi is NOT modified: it keeps writing bild_NNNN.jpg into
# /home/admin/training/, this script rsyncs them over read-only and
# content-addresses new ones into inbox/unkraut/<sha12>.jpg.
#
# Env overrides:
#   LIGHTLY_DIR   ($HOME/lightly)
#   PI_SRC        rsync source; "admin@192.168.179.252:training/" or a local
#                 directory (with trailing slash) for a Pi-less test
#   PI_SSH_KEY    explicit identity file; empty => ssh picks its default key
#                 (~/.ssh/id_ed25519). Ignored when PI_SRC is local.
#   PI_SSH_PORT   (22)

set -uo pipefail

LIGHTLY_DIR="${LIGHTLY_DIR:-$HOME/lightly}"
PI_SRC="${PI_SRC:-admin@192.168.179.252:training/}"
PI_SSH_KEY="${PI_SSH_KEY-}"          # empty => ssh default key resolution
PI_SSH_PORT="${PI_SSH_PORT:-22}"

MIRROR="$LIGHTLY_DIR/inbox/_mirror"
UNKRAUT="$LIGHTLY_DIR/inbox/unkraut"
MANIFEST="$LIGHTLY_DIR/inbox/manifest.jsonl"
LOCK="$LIGHTLY_DIR/inbox/.pull.lock"

mkdir -p "$MIRROR" "$UNKRAUT"

exec 9>"$LOCK"
flock -n 9 || { echo "$(date -Is) pull: another run holds the lock, skipping"; exit 0; }

log() { echo "$(date -Is) pull: $*"; }

# --- 1. mirror the Pi's training/ dir --------------------------------------
# No --delete and no --ignore-existing: a bild_0001.jpg that changed after a
# Pi re-image is re-fetched (different size/mtime), then step 2 gives it a new
# sha and a new inbox file. The mirror is the durable .17-side archive.
if [[ "$PI_SRC" == *:* ]]; then
  RSH="ssh -p $PI_SSH_PORT -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
  [[ -n "$PI_SSH_KEY" ]] && RSH="$RSH -i $PI_SSH_KEY -o IdentitiesOnly=yes"
  if ! rsync -a --timeout=25 -e "$RSH" "$PI_SRC" "$MIRROR/"; then
    log "rsync failed (Pi offline / unreachable) — will retry next run"
    exit 0
  fi
else
  if ! rsync -a --timeout=25 "$PI_SRC" "$MIRROR/"; then
    log "rsync from local source '$PI_SRC' failed"
    exit 0
  fi
fi

# --- 2. content-address new images into inbox/unkraut/ --------------------
new=0
shopt -s nullglob
for f in "$MIRROR"/bild_*.jpg "$MIRROR"/bild_*.JPG; do
  [[ -f "$f" ]] || continue
  sha="$(sha1sum "$f" | cut -c1-12)"
  dst="$UNKRAUT/$sha.jpg"
  [[ -e "$dst" ]] && continue
  # hardlink when possible (same fs, never edited); copy as fallback
  if ! ln "$f" "$dst" 2>/dev/null; then
    cp -p "$f" "$dst.tmp.$$" && mv "$dst.tmp.$$" "$dst"
  fi
  printf '{"sha":"%s","orig":"%s","first_seen":"%s","src":"%s"}\n' \
    "$sha" "$(basename "$f")" "$(date -Is)" "$PI_SRC" >> "$MANIFEST"
  new=$((new + 1))
done

log "ok — $new new image(s) into $UNKRAUT (mirror: $(find "$MIRROR" -maxdepth 1 -name 'bild_*' | wc -l) files)"
