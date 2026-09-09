#!/usr/bin/env bash
# LightlyStudio GUI for the Unkrautroboter labeling loop.
# Isolated venv; does NOT touch ~/ComfyUI. Bound to the LAN.
set -euo pipefail
cd /home/tachy/lightly
exec venv/bin/lightly-studio gui \
  --host 0.0.0.0 --port 8001 \
  --db-file /home/tachy/lightly/studio/lightly_studio.db
