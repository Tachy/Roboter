#!/usr/bin/env bash
#
# Chain the labeling-loop ingest steps. Run on .17 by lightly-reindex.timer.
#
#   1. reindex.py     (studio venv)   — index new inbox images into LightlyStudio
#                                        (briefly stops the service, only if new)
#   2. preannotate.py (trainer venv)  — run best.pt over not-yet-seen samples,
#                                        POST predictions via REST (no downtime)
set -uo pipefail

LIGHTLY_DIR="${LIGHTLY_DIR:-$HOME/lightly}"
STUDIO_PY="$LIGHTLY_DIR/venv/bin/python"
TRAINER_PY="$LIGHTLY_DIR/venv-trainer/bin/python"
TRAINER_DIR="$LIGHTLY_DIR/trainer"

"$STUDIO_PY"  "$TRAINER_DIR/reindex.py"     || echo "reindex.sh: reindex.py exit $?"
"$TRAINER_PY" "$TRAINER_DIR/preannotate.py" || echo "reindex.sh: preannotate.py exit $?"
