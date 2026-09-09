#!/usr/bin/env bash
#
# Deploy the Pi 4 robot software to the Raspberry Pi -- a plain scp copy.
#
#   Source : unkrautroboter_bilderkennung/  ->  main.py, pyproject.toml,
#            poetry.lock (if tracked) + src/
#   Target : admin@192.168.179.252:/home/admin/
#
# Only the git-tracked top-level files and src/ files are copied, so
# __pycache__, *.pyc and other local junk never ship. Everything else under
# unkrautroboter_bilderkennung/ stays off the Pi (model/, calibration/,
# monitoring_webserver/, systemd/, tests/, joysticksteuerung_pc/, ...).
# After copying, roboter.service is restarted (admin has passwordless sudo).
# The systemd *unit file* itself is managed by hand and is NOT touched.
#
# Usage: bin/deploy-pi4.sh [--dry-run]
#   --dry-run   list what would be copied, connect to nothing
#
# Overridable via environment:
#   DEPLOY_USER (admin)  DEPLOY_HOST (192.168.179.252)
#   DEPLOY_PORT (22)     DEPLOY_DEST (/home/admin)

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-admin}"
DEPLOY_HOST="${DEPLOY_HOST:-192.168.179.252}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_DEST="${DEPLOY_DEST:-/home/admin}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "deploy-pi4: unknown option '$arg'" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SRC_ROOT="$REPO/unkrautroboter_bilderkennung"

[[ -f "$SRC_ROOT/main.py" && -d "$SRC_ROOT/src" ]] \
  || { echo "deploy-pi4: main.py / src not found under $SRC_ROOT" >&2; exit 1; }

# git-tracked file list = the deployable source of truth.
# (poetry.lock is optional — silently skipped by git ls-files until committed.)
TRACKED=()
while IFS= read -r f; do TRACKED+=("$f"); done \
  < <(git -C "$SRC_ROOT" ls-files main.py pyproject.toml poetry.lock src)
[[ ${#TRACKED[@]} -gt 0 ]] \
  || { echo "deploy-pi4: no tracked files found (need a git checkout)" >&2; exit 1; }

# This simple scp deploy assumes src/ is flat (no sub-directories).
for f in "${TRACKED[@]}"; do
  case "$f" in
    src/*/*) echo "deploy-pi4: '$f' -- src/ has sub-dirs now, update this script" >&2; exit 1 ;;
  esac
done

TARGET="$DEPLOY_USER@$DEPLOY_HOST"
SCP=(scp -P "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=10)
SSH=(ssh -p "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=10)

echo ">> source : $SRC_ROOT  (main.py + src/, git-tracked only)"
echo ">> target : $TARGET:$DEPLOY_DEST/  (port $DEPLOY_PORT)"
echo ">> files  : ${#TRACKED[@]}"

if [[ $DRY_RUN -eq 1 ]]; then
  echo ">> --dry-run, would copy:"
  printf '     %s\n' "${TRACKED[@]}"
  exit 0
fi

topfiles=(); srcfiles=()
for f in "${TRACKED[@]}"; do
  if [[ $f == src/* ]]; then srcfiles+=("$SRC_ROOT/$f"); else topfiles+=("$SRC_ROOT/$f"); fi
done

echo ">> copying top-level files (${#topfiles[@]}) ..."
"${SCP[@]}" -- "${topfiles[@]}" "$TARGET:$DEPLOY_DEST/"

echo ">> copying src/ ..."
"${SSH[@]}" "$TARGET" "mkdir -p '$DEPLOY_DEST/src'"
"${SCP[@]}" -- "${srcfiles[@]}" "$TARGET:$DEPLOY_DEST/src/"

echo ">> deployed:"
"${SSH[@]}" "$TARGET" "cd '$DEPLOY_DEST' && ls -l main.py pyproject.toml poetry.lock 2>/dev/null; echo; ls -l src/" | sed 's/^/     /'

echo ">> restarting roboter.service ..."
"${SSH[@]}" "$TARGET" \
  "sudo -n systemctl restart roboter.service; rc=\$?; sudo -n systemctl --no-pager --lines=0 status roboter.service || true; exit \$rc" \
  | sed 's/^/     /'

echo ">> done."
