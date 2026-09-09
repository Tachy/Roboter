# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

Always respond to the user in German. Code, identifiers, and code comments stay in English.

## Project Overview

A solar-powered weed-removal robot (Unkrautroboter) that detects weeds growing in pavement cracks using AI (YOLOv8), drives to their location, and removes them with a rotating wire brush. Three distinct hardware controllers each have their own codebase:

| Subsystem | Hardware | Language | Directory |
|---|---|---|---|
| Image recognition & orchestration | Raspberry Pi 4B | Python | `unkrautroboter_bilderkennung/` |
| Motor control | Arduino Mega 2560 | C++ (Arduino) | `unkrautroboter_motorsteuerung/` |
| Energy management | Arduino Pro Mini 5V | C++ (Arduino) | `unkrautroboter_energiemanagement/` |

## Robot Access (SSH)

The Raspberry Pi is reachable via SSH with a pre-installed key (no password prompt):

```bash
ssh admin@192.168.179.252
```

- Host: `192.168.179.252` (LAN), hostname `unkrautroboter`, Raspberry Pi OS Bookworm (aarch64, kernel 6.12).
- User: `admin`, passwordless `sudo`.
- The `unkrautroboter_bilderkennung` code is deployed directly in `/home/admin` (`main.py`, `src/`, `calibration/`, `model/`, `state/`, `upload/`, ...).
- Runs as systemd service `roboter.service` (`sudo systemctl status|restart roboter.service`).
- Deploy the Pi software with `bin/deploy-pi4.sh` — scp of the git-tracked `main.py` + `src/` only (nothing else in `unkrautroboter_bilderkennung/` goes on the Pi); systemd unit is managed by hand, not touched. `--dry-run` lists the files. Inside Claude Code: `/deploy-pi4 [--dry-run]` runs it token-free via the same hook as `/deploy-webserver`.

### Web Dashboard Server

The operator UI `unkrautroboter.html` is served by Apache on a separate host (home server, CentOS 7):

```bash
ssh -p 29876 apache@192.168.179.4     # pre-installed key, no password; no passwordless sudo
```

- Document root: `/var/www/html` (shared with many other home-automation pages). Relevant files: `unkrautroboter.html`, `css/style.css`, `js/*.js` (`config.js`, `mode.js`, `stream.js`, `status.js`, `ws.js`, `heartbeat.js`, `joypad.js`, `reset.js`).
- Reachable at `http://192.168.179.4/unkrautroboter.html` (HTTP 200 verified).
- `js/config.js` points the browser back at the Pi: `HOST=192.168.179.252`, `HTTP_PORT=8080` (MJPEG `/stream`, `/last_capture.jpg`), `WS_PORT=8765` (WebSocket). Mode/joystick control still goes over the Pi's UDP ports (5005/5006/5007).
- Source of truth for the dashboard is `unkrautroboter_bilderkennung/monitoring_webserver/` in this repo. Deploy with `bin/deploy-webserver.sh` — a plain scp copy of `unkrautroboter.html`, `send_udp.php`, `css/`, `js/` into the doc root (no delete, no chmod; other doc-root files untouched). `--dry-run` shows what it would copy.
- Inside Claude Code, typing `/deploy-webserver [flags]` runs that script directly and spends no tokens: a `UserPromptSubmit` hook (`.claude/settings.json` → `bin/deploy-slash-hook.sh`) intercepts the command, runs the deploy, and blocks the prompt from reaching the model. The same generic hook handles every `/deploy-<name>` on its allow-list — currently `webserver arduino pi4` (and `model` once Phase 5 lands). The `.claude/commands/deploy-*.md` files only provide autocomplete + a fallback.

### Training Platform (RTX 4090) — `.17`

GPU box that runs the labeling + training loop (plan: `~/.claude/plans/adaptive-orbiting-fountain.md`).

```bash
ssh -p 29876 tachy@192.168.179.17     # pre-installed key, no password; SSH on port 29876 (not 22); no passwordless sudo
```

- Host: `gamepc-4090-linux`, Ubuntu 26.04 LTS, kernel 7.0, user `tachy` (home `/home/tachy`).
- GPU: NVIDIA GeForce RTX 4090, 24 GB, driver 595.84, CUDA 13.2 (driver runtime only — no `nvcc`). Shared with `comfyui.service` (port 8188) and `ollama.service` (port 11434) — **do not touch `~/tachy/ComfyUI`**.
- System `python3` is 3.14 **without pip**; `pixi` is installed but unused; **no Docker / conda / uv**. All pipeline work lives in isolated venvs under `~/lightly/`.
- Disk: ~1.9 TB root, ~1.4 TB free.
- **`~/lightly/` layout:** `venv/` (LightlyStudio 1.1.0, py3.14), `venv-trainer/` (ultralytics 8.4 + torch cu130 + onnx/ncnn), `lightly-studio/` (git clone, reference), `inbox/{_mirror,unkraut}/`, `studio/lightly_studio.db`, `datasets/`, `holdout/`, `models/{registry,current,pi_inbox_staging}/`, `bin/`, `logs/`.
- **LightlyStudio** runs as `systemd --user` unit `lightly-studio.service` (`Linger=yes`) → browser UI at **`http://192.168.179.17:8001`**. Manage: `systemctl --user status|restart lightly-studio`.
- **Image ingest:** `.17` pulls read-only from the Pi — `yolo-training/box/pull-training-images.sh` on a `lightly-pull.timer` rsyncs `admin@192.168.179.252:training/` into `inbox/_mirror/`, then content-addresses new files into `inbox/unkraut/<sha12>.jpg` (+ `inbox/manifest.jsonl`). The Pi runs unchanged. Requires the `.17` key `~/.ssh/id_ed25519_pi` in the Pi's `authorized_keys` with `command="rrsync -ro /home/admin/training"`.
- Repo mirror of the `.17` setup: `yolo-training/box/` (systemd units, launcher scripts, bootstrap README); the loop scripts: `yolo-training/trainer/`.

## Running the Raspberry Pi App

```bash
cd unkrautroboter_bilderkennung
python main.py          # run directly
# or as systemd service: sudo systemctl start roboter.service
```

**Dependencies.** `pyproject.toml` is the manifest of intent; there is **no `poetry.lock` in git** and `poetry install` is deliberately **not** the install path on the Pi.

- The Pi venv `/home/admin/pyvenv` is created `--system-site-packages` and its packages were assembled by hand: `pip` (with the piwheels ARM mirror from `/etc/pip.conf`) + apt (`picamera2`). Verified working set (Phase 6): `ultralytics 8.4.146`, `torch 2.8.0+cpu`, `torchvision 0.23.0`, `ncnn`, `onnx`/`onnxruntime`, `numpy 2.2.6`, `opencv-python 4.12`, `matplotlib 3.10`, `picamera2 0.3.31` (apt).
- `poetry` on the Pi is only the `poetry run python main.py` launcher used by `roboter.service` (it runs inside the already-populated `pyvenv`; it installs nothing).
- Why no lock: poetry's resolver can't model the piwheels/apt/system-site-packages mix. A fresh `poetry lock` resolves `numpy` down to `2.0.2` (a macOS-only `ultralytics` marker conflicts with `opencv-python`'s `numpy<2.3.0` cap and drags the whole tree to the last Python-3.9-era releases) and wants to pip-install `picamera2` over the apt build — i.e. it would *downgrade* the verified robot. Reproducibility target is the pinned direct versions in `pyproject.toml` installed via `pip` from piwheels, not a lockfile.
- `bin/deploy-pi4.sh` ships `pyproject.toml` alongside `main.py`/`src/` so the Pi's copy stays in sync with the repo.

```bash
# reference only — normal deploy is bin/deploy-pi4.sh
cd unkrautroboter_bilderkennung && poetry install
```

**Joystick client** (run on PC with USB gamepad):
```bash
# Update UDP_IP in joystick_steuerung.py to match Pi's IP first
cd unkrautroboter_bilderkennung/joysticksteuerung_pc
python joystick_steuerung.py
```

## Arduino Development

The `.vscode/arduino.json` configures the workspace for the Arduino extension in VS Code. Board: `arduino.avr.mega`. Build output goes to `unkrautroboter_motorsteuerung/build/arduino.avr.mega/`.

**Build + deploy firmware:** `bin/deploy-arduino.sh` compiles with `arduino-cli` (FQBN `arduino:avr:mega:cpu=atmega2560`, read from `.vscode/arduino.json`) into `unkrautroboter_motorsteuerung/build/arduino.avr.mega/`, then scp's the **application-only** `unkrautroboter_motorsteuerung.ino.hex` to `admin@192.168.179.252:/home/admin/upload/`. `--dry-run` = compile only. Needs `arduino-cli` (auto-found in the bundled Arduino IDE) and the `arduino:avr` core. Inside Claude Code: `/deploy-arduino [--dry-run]` runs it token-free via the same hook as `/deploy-webserver`.

**No bootloader in the image:** the Pi flashes with `avrdude -c wiring` over `/dev/serial0` (`robot_control.py::_flash_hex_to_mega`), which drives the ATmega2560's resident STK500v2 bootloader — so ship `*.ino.hex`, never `*.ino.with_bootloader.hex`. Confirmed against the last good `/home/admin/upload/*.ino.hex.uploaded`: byte-identical to the app-only build, highest flash address ~0x8960, no `:02000004` records.

**OTA firmware upload** (while Pi runs, robot in MANUAL mode): a `.hex` in `/home/admin/upload/` on the Pi — the Pi auto-flashes it to the Mega via avrdude and renames it to `.uploaded` or `.failed`. The scan only runs in MANUAL mode.

**Model OTA** (same pattern, planned in the labeling-pipeline plan): a promoted detection model is bundled on `.17` into `model_<ts>.tar` (`best.pt` + `best_ncnn_model/` + `best.onnx` + `manifest.json`) and `bin/deploy-model.sh` scp's it to `/home/admin/model_upload/` on the Pi. In MANUAL mode `robot_control._check_model_upload()` validates it (sha256, class names, imgsz, subprocess load-test), atomically swaps `model/best*`, hot-reloads the YOLO model, and renames the tar to `.uploaded`/`.failed`. One `.old` generation is kept for manual rollback. `roboter.service` is **not** restarted. Inside Claude Code: `/deploy-model`.

## Architecture: Raspberry Pi ↔ Arduino MEGA Serial Protocol

The entire autonomous operation flows over a single serial link (`/dev/serial0`, 115200 baud). `SerialManager` (`src/serial_manager.py`) reads in a background thread into a `queue.Queue`.

**Arduino → Pi messages:**
- `WAITING` — Arduino idle, waiting for mode command
- `GETXY` — Arduino requests weed coordinates for current camera frame
- `STATUS:{...}` — JSON with encoder values, mode, and optional INA260 power readings (every 5 s)

**Pi → Arduino messages:**
- `MODE:AUTO` / `MODE:MANUAL` — switch operating mode
- `XY:x_mm,y_mm` — one weed coordinate in mm (multiple sent sequentially)
- `DONE` — all coordinates for current frame sent
- `JOYSTICK:X=...,Y=...[,B=...]` — manual drive command (-100..100 range)
- `NOGO:x1,y1,x2,y2` + `NOGO:DONE` — pavement-edge segments the robot must not cross (**Stage 2, not yet implemented** — see plan `adaptive-orbiting-fountain.md` Phase 7)

The Arduino's `anfrageUndAbarbeiten()` function is the AUTO mode cycle: send `GETXY`, collect `XY:` lines until `DONE`, sort by Y ascending, drive to each weed, lower brush, then advance for the next frame.

## Architecture: Coordinate Pipeline

```
Camera (picamera2, 1280×720)
  → YOLO inference (model/best.pt)     ← YOLOv8m @640 today (config.YOLO_RUNTIME="pt"); Pi env is on
                                        ultralytics 8.4.146 + ncnn, ready for YOLO26s NCNN FP16 @1280
                                        (~4.5 s on the Pi 4B, verified) — flip YOLO_RUNTIME/YOLO_IMG_SIZE
                                        once a real YOLO26 model lands via the model OTA
  → pixel (x,y)
  → geometry.pixel_to_world()     ← prefers ground_homography.npz; falls back to extrinsics.npz + ray-plane
  → (x_mm, y_mm) in robot frame   ← offset by WORLD_OFFSET_XY_MM from config
  → sent as XY: to Arduino MEGA
```

Calibration files live in `unkrautroboter_bilderkennung/calibration/`:
- `cam_calib_charuco.npz` — camera intrinsics (K, D, newK) from DISTORTION mode
- `extrinsics.npz` — camera pose (R, t, plane) from EXTRINSIK mode
- `ground_homography.npz` — direct pixel→mm homography (preferred if present)

## Operating Modes

Modes are controlled via UDP port 5005 and persisted across restarts in `state/mode.txt`.

| Mode | Behavior |
|---|---|
| `AUTO` | Main loop: capture → YOLO → send coords → Arduino drives and brushes |
| `MANUAL` | Joystick UDP commands forwarded directly to Arduino |
| `DISTORTION` | Camera intrinsic calibration; joystick B=1 captures a ChArUco snapshot (20 needed) |
| `EXTRINSIK` | One-shot extrinsic calibration; joystick B=1 estimates pose from ChArUco on ground |

## Arduino MEGA: Motor Layout

All motors use 18 kHz PWM via hardware timers 1–5. The custom `motorAnalogWrite()` maps 0–255 onto `ICR = 110` (hardware top).

| Axis | Pins | Encoder | End Switches |
|---|---|---|---|
| Left wheel | RPWM=5, LPWM=6 | INT pin 2 | — |
| Right wheel | RPWM=7, LPWM=8 | INT pin 3 | — |
| X carriage | RPWM=44, LPWM=45 | INT pin 18 | END_X_L=27, END_X_R=28 |
| Z brush axis | RPWM=11, LPWM=12 | INT pin 19 | END_Z_O=29, END_Z_U=30 |
| Brush | PWM=46 | poll pin 26 | — |

End switches are wired **normally closed (NC)**: `pressed == HIGH`. All movement functions (`setzeXPosition`, `setzeZPosition`, `fahreStrecke`) use impulse-based trapezoidal ramps. A Timer2 ISR polls serial every 5 ms to prevent the 64-byte hardware RX buffer from overflowing during long blocking moves.

## Energy Management (Arduino Pro Mini)

Fully independent from the Mega. Controls 4 bistable relays (via ULN2003) based on PV and battery voltage only:

- **PV < 15 V** for 2 s → disconnect Victron MPPT (R1 PV first, then R2 battery)
- **PV > 19 V** for 2 s → reconnect Victron (R2 battery first, then R1 PV)
- **Battery ≤ 12.40 V** → signal Pi shutdown (PI_SHDN_PIN HIGH) then cut main power (R4)
- **Battery ≥ 13.00 V** → enable main power path via precharge sequence (R3 → R4 → R3 off)

All voltage thresholds are calibrated constants at the top of the `.ino` file. `SERIAL_DEBUG = false` in production (no serial output).

## Key Configuration (`src/config.py`)

| Setting | Value | Notes |
|---|---|---|
| `SERIAL_PORT` | `/dev/serial0` | Falls back to `SIMULATED_SERIAL_PORT=/tmp/ttyV8` |
| `BAUDRATE` | 115200 | |
| `UDP_CONTROL_PORT` | 5005 | Mode switches |
| `UDP_JOYSTICK_PORT` | 5006 | Joystick commands |
| `UDP_HEARTBEAT_PORT` | 5007 | Stream on/off watchdog |
| `HTTP_PORT` | 8080 | MJPEG stream |
| `ALLOWED_UDP_SOURCES` | IP whitelist | Edit to add new control clients |
| `YOLO_MODEL_PATH` | `./model/best.pt` | |
| `FW_RESET_GPIO` | 23 (BCM) | GPIO to reset Mega before flashing |
