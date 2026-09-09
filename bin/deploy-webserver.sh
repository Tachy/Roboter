#!/usr/bin/env bash
#
# Deploy the monitoring web dashboard to the Apache document root.
# Plain scp copy -- no delete, no chmod, no extra privileges.
#
#   Source : unkrautroboter_bilderkennung/monitoring_webserver/
#   Target : apache@192.168.179.4:/var/www/html   (SSH/scp on port 29876)
#
# The doc root is shared with other home-automation pages; only the files
# listed in DEPLOY_ITEMS are overwritten, nothing else is touched.
#
# Usage: bin/deploy-webserver.sh [--dry-run]
#
# Overridable via environment:
#   DEPLOY_USER (apache)  DEPLOY_HOST (192.168.179.4)
#   DEPLOY_PORT (29876)   DEPLOY_DEST (/var/www/html)

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-apache}"
DEPLOY_HOST="${DEPLOY_HOST:-192.168.179.4}"
DEPLOY_PORT="${DEPLOY_PORT:-29876}"
DEPLOY_DEST="${DEPLOY_DEST:-/var/www/html}"

# Top-level files and asset dirs copied into the doc root.
DEPLOY_FILES=(unkrautroboter.html send_udp.php)
DEPLOY_DIRS=(css js)

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "deploy-webserver: unknown option '$arg'" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd -- "$SCRIPT_DIR/../unkrautroboter_bilderkennung/monitoring_webserver" && pwd)"

for item in "${DEPLOY_FILES[@]}" "${DEPLOY_DIRS[@]}"; do
  [[ -e "$SRC_DIR/$item" ]] || { echo "deploy-webserver: missing '$item' in $SRC_DIR" >&2; exit 1; }
done

TARGET="$DEPLOY_USER@$DEPLOY_HOST"
SCP=(scp -P "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=10)
SSH=(ssh -p "$DEPLOY_PORT" -o BatchMode=yes -o ConnectTimeout=10)

echo ">> source : $SRC_DIR"
echo ">> target : $TARGET:$DEPLOY_DEST  (port $DEPLOY_PORT)"
echo ">> files  : ${DEPLOY_FILES[*]}   dirs: ${DEPLOY_DIRS[*]}"

if [[ $DRY_RUN -eq 1 ]]; then
  echo ">> --dry-run, would copy:"
  for f in "${DEPLOY_FILES[@]}"; do echo "   $SRC_DIR/$f  ->  $TARGET:$DEPLOY_DEST/$f"; done
  for d in "${DEPLOY_DIRS[@]}"; do echo "   $SRC_DIR/$d/.  ->  $TARGET:$DEPLOY_DEST/$d/"; done
  exit 0
fi

echo ">> copying files ..."
files=(); for f in "${DEPLOY_FILES[@]}"; do files+=("$SRC_DIR/$f"); done
"${SCP[@]}" -- "${files[@]}" "$TARGET:$DEPLOY_DEST/"

echo ">> copying asset dirs ..."
mkdirs=""
for d in "${DEPLOY_DIRS[@]}"; do mkdirs+=" '$DEPLOY_DEST/$d'"; done
"${SSH[@]}" "$TARGET" "mkdir -p --$mkdirs"
for d in "${DEPLOY_DIRS[@]}"; do
  # "/." copies the directory contents in, so re-runs never nest css/css.
  "${SCP[@]}" -r -- "$SRC_DIR/$d/." "$TARGET:$DEPLOY_DEST/$d/"
done

echo ">> deployed files:"
"${SSH[@]}" "$TARGET" "cd '$DEPLOY_DEST' && ls -l ${DEPLOY_FILES[*]} ${DEPLOY_DIRS[*]}" | sed 's/^/     /'

echo ">> done."
