#!/usr/bin/env bash
#
# Safely update LightlyStudio in the isolated venv on the .17 box.
#
# "Safe" here means:
#   - holds the same flock the trainer scripts use ($LIGHTLY_DIR/.studio_ctl.lock),
#     so a timer-driven reindex/export/train cannot collide with the upgrade
#   - stops lightly-studio.service first (DuckDB is a single-writer store)
#   - snapshots the WHOLE venv (cp -a, restored in place -> shebangs stay valid)
#     and the DuckDB file before touching anything
#   - upgrades via the venv's own pip, to a pinned version
#   - verifies: pip check, core imports, and - if the service can come up -
#     the trainer REST glue (common.LS().list_images(), the exact calls
#     reindex.py / preannotate.py depend on)
#   - on ANY failure -> automatic rollback to the snapshot
#   - only (re)starts the GUI service when the dataset has >=1 image;
#     LightlyStudio refuses to start on an empty dataset
#
# Usage:
#   update-lightly-studio.sh                 # check -> confirm -> upgrade -> verify
#   update-lightly-studio.sh --check         # report installed vs latest, do nothing
#   update-lightly-studio.sh --yes           # skip the confirmation prompt
#   update-lightly-studio.sh --to=1.2.0      # target a specific version (allows downgrade)
#   update-lightly-studio.sh --keep-stopped  # never start the service at the end
#
# Env overrides: LIGHTLY_DIR (~/lightly)   STUDIO_UNIT (lightly-studio.service)

set -euo pipefail

LIGHTLY_DIR="${LIGHTLY_DIR:-$HOME/lightly}"
STUDIO_UNIT="${STUDIO_UNIT:-lightly-studio.service}"
PKG="lightly-studio"

VENV="$LIGHTLY_DIR/venv"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"
TRAINER_PY="$LIGHTLY_DIR/venv-trainer/bin/python"
TRAINER_DIR="$LIGHTLY_DIR/trainer"
INBOX="$LIGHTLY_DIR/inbox/unkraut"
DB="$LIGHTLY_DIR/studio/lightly_studio.db"
LOCK="$LIGHTLY_DIR/.studio_ctl.lock"
STUDIO_URL="http://127.0.0.1:8001"

CHECK_ONLY=0; ASSUME_YES=0; KEEP_STOPPED=0; TARGET=""
for a in "$@"; do
  case "$a" in
    --check)        CHECK_ONLY=1 ;;
    --yes|-y)       ASSUME_YES=1 ;;
    --keep-stopped) KEEP_STOPPED=1 ;;
    --to=*)         TARGET="${a#--to=}" ;;
    -h|--help)      sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "update-lightly-studio: unknown option '$a'" >&2; exit 2 ;;
  esac
done

log()  { printf '>> %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { warn "$*"; exit 1; }
sc()   { systemctl --user "$@"; }

for c in flock curl systemctl du df; do
  command -v "$c" >/dev/null || die "required command not found: $c"
done

pkg_version() { "$PIP" show "$PKG" 2>/dev/null | awk '/^Version:/{print $2}'; }

latest_version() {
  local v
  v="$("$PIP" index versions "$PKG" 2>/dev/null | awk '/^ *LATEST:/{print $2}')"
  if [[ -z "$v" ]]; then
    v="$("$PIP" install "$PKG==_nope_" 2>&1 \
          | grep -oE 'from versions:[^)]*' \
          | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | tail -1)"
  fi
  printf '%s' "$v"
}

# Count PNGs waiting in the inbox (LightlyStudio needs >=1 sample to start).
inbox_count() { find "$INBOX" -maxdepth 1 -type f -name '*.png' 2>/dev/null | wc -l; }

# --------------------------------------------------------------------------- #
[[ -x "$PIP" ]] || die "no venv pip at $PIP"

CUR="$(pkg_version)"; [[ -n "$CUR" ]] || die "$PKG is not installed in $VENV"
if [[ -n "$TARGET" ]]; then WANT="$TARGET"; else WANT="$(latest_version)"; fi
[[ -n "$WANT" ]] || die "could not determine the target version (PyPI unreachable?)"

log "venv        : $VENV  (python $("$PY" -V 2>&1 | awk '{print $2}'))"
log "installed   : $CUR"
log "target      : $WANT${TARGET:+  (pinned via --to)}"

if [[ "$CUR" == "$WANT" && -z "$TARGET" ]]; then
  log "already up to date - nothing to do."
  exit 0
fi
if [[ $CHECK_ONLY -eq 1 ]]; then
  log "--check: an update is available ($CUR -> $WANT). Stopping here."
  exit 0
fi
if [[ $ASSUME_YES -eq 0 ]]; then
  read -rp ">> upgrade $PKG  $CUR -> $WANT  ? [y/N] " ans || ans=""
  [[ "$ans" == [yY] || "$ans" == [yY][eE][sS] ]] || die "aborted by user."
fi

# --------------------------------------------------------------------------- #
# critical section: nobody else may touch the studio DB while we work
exec 9>"$LOCK"
flock -n 9 || die "another studio-control job holds $LOCK - try again later"

SNAP="$VENV.bak-$CUR"
SNAP_OK=0
DBSNAP=""
cleanup_partial() { [[ $SNAP_OK -eq 0 && -d "$SNAP" ]] && rm -rf "$SNAP"; }
trap cleanup_partial EXIT

WAS_ACTIVE=0
sc is-active --quiet "$STUDIO_UNIT" && WAS_ACTIVE=1
log "stopping $STUDIO_UNIT (was: $([[ $WAS_ACTIVE -eq 1 ]] && echo active || echo inactive))"
sc stop "$STUDIO_UNIT" || true
sc reset-failed "$STUDIO_UNIT" 2>/dev/null || true
sleep 2

# disk check before the (multi-GB) venv copy
need_mb="$(du -sm "$VENV" | cut -f1)"
avail_mb="$(df -Pm "$LIGHTLY_DIR" | awk 'NR==2{print $4}')"
(( avail_mb > need_mb + 1024 )) \
  || die "not enough free disk for a venv snapshot (need ~${need_mb}M + slack, have ${avail_mb}M)"

if [[ -e "$SNAP" ]]; then
  log "reusing existing snapshot $SNAP as the rollback point"
  SNAP_OK=1
else
  log "snapshot venv -> $SNAP  (${need_mb}M)"
  cp -a "$VENV" "$SNAP"
  SNAP_OK=1
fi
if [[ -f "$DB" ]]; then
  DBSNAP="$DB.bak-$(date +%Y%m%d%H%M%S)"
  cp -a "$DB" "$DBSNAP"
  log "snapshot DB  -> $DBSNAP"
fi

restore_snapshot() {
  warn "restoring $PKG $CUR from snapshot"
  rm -rf "$VENV"
  mv "$SNAP" "$VENV"
  SNAP_OK=0
  [[ -n "$DBSNAP" && -f "$DBSNAP" ]] && cp -a "$DBSNAP" "$DB"
}

verify_offline() {
  log "verify: pip check"
  "$PIP" check || return 1
  log "verify: installed version == $WANT"
  [[ "$(pkg_version)" == "$WANT" ]] || return 1
  log "verify: core imports (lightly_studio, db_manager, ImageDataset, start_gui)"
  "$PY" - <<'PYEOF' || return 1
import lightly_studio                       # noqa: F401
from lightly_studio import ImageDataset     # noqa: F401
from lightly_studio.database import db_manager  # noqa: F401
import lightly_studio.core.start_gui        # noqa: F401
print("   imports ok")
PYEOF
  return 0
}

# ---- upgrade + offline verification, still under the lock ----
UPGRADE_OK=1
log "pip install $PKG==$WANT"
if "$PIP" install --no-input "$PKG==$WANT" && verify_offline; then
  log "offline verification passed"
else
  warn "upgrade or offline verification FAILED"
  restore_snapshot
  UPGRADE_OK=0
fi

flock -u 9
exec 9>&-
# --------------------------------------------------------------------------- #

# Bring the GUI back. lightly-reindex.sh re-takes the lock itself, so this
# runs only AFTER we released it above.
bring_up_service() {
  if [[ $KEEP_STOPPED -eq 1 ]]; then
    log "--keep-stopped: leaving $STUDIO_UNIT down."
    return 0
  fi
  local n; n="$(inbox_count)"
  if [[ "$n" -eq 0 ]]; then
    log "inbox has 0 images - LightlyStudio can't start on an empty dataset."
    log "   it comes up automatically once lightly-pull brings in a PNG, or run:"
    log "   $LIGHTLY_DIR/bin/lightly-reindex.sh"
    return 0
  fi
  log "reindex + start service ($n image(s) in inbox)"
  "$LIGHTLY_DIR/bin/lightly-reindex.sh" || warn "lightly-reindex.sh exited nonzero"
}

verify_online() {
  sc is-active --quiet "$STUDIO_UNIT" || { log "service not running - skipping online check"; return 0; }
  log "verify: REST /api/version"
  curl -sf "$STUDIO_URL/api/version" >/dev/null || return 1
  log "verify: trainer REST client (common.LS().list_images())"
  "$TRAINER_PY" - <<PYEOF || return 1
import sys
sys.path.insert(0, "$TRAINER_DIR")
import common
ls = common.LS()
ls.wait_ready(30)
ls.collection_id()
rows = ls.list_images()
print(f"   REST ok - {len(rows)} sample(s) visible")
PYEOF
  return 0
}

if [[ $UPGRADE_OK -eq 1 ]]; then
  bring_up_service
  if verify_online; then
    log "online verification passed"
  else
    warn "ONLINE verification failed after upgrade - rolling back"
    exec 9>"$LOCK"
    flock -w 120 9 || warn "proceeding with rollback without the lock (timed out)"
    sc stop "$STUDIO_UNIT" || true
    SNAP="$VENV.bak-$CUR"   # the rollback point kept from the success path
    if [[ -d "$SNAP" ]]; then
      restore_snapshot
    else
      warn "snapshot $SNAP is gone - falling back to: pip install $PKG==$CUR"
      "$PIP" install --no-input "$PKG==$CUR" || warn "pip downgrade failed too"
    fi
    flock -u 9; exec 9>&-
    UPGRADE_OK=0
    bring_up_service
  fi
else
  # offline verification already rolled the venv back; just bring the GUI up
  bring_up_service
fi

# --------------------------------------------------------------------------- #
NOW="$(pkg_version)"
echo
if [[ "$NOW" == "$WANT" && $UPGRADE_OK -eq 1 ]]; then
  log "DONE - $PKG upgraded $CUR -> $NOW"
  # keep this run's rollback point; list any older ones as prunable
  mapfile -t olds < <(find "$(dirname "$VENV")" -maxdepth 1 -type d -name "$(basename "$VENV").bak-*" ! -name "*.bak-$CUR" 2>/dev/null)
  if (( ${#olds[@]} )); then
    log "old rollback snapshots (safe to delete once $NOW is proven):"
    printf '     rm -rf %s\n' "${olds[@]}"
  fi
  log "rollback point for this run: $VENV.bak-$CUR"
  # best-effort: line the reference clone up with the new version
  if [[ -d "$LIGHTLY_DIR/lightly-studio/.git" ]]; then
    git -C "$LIGHTLY_DIR/lightly-studio" fetch --tags -q 2>/dev/null \
      && git -C "$LIGHTLY_DIR/lightly-studio" checkout -q "v$NOW" 2>/dev/null \
      && log "reference clone checked out at v$NOW" || true
  fi
  exit 0
else
  die "$PKG is back on $NOW (upgrade to $WANT did not stick - see messages above)"
fi
