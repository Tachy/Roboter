# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A solar-powered weed-removal robot (Unkrautroboter) that detects weeds growing in pavement cracks using AI (YOLOv8), drives to their location, and removes them with a rotating wire brush. Three distinct hardware controllers each have their own codebase:

| Subsystem | Hardware | Language | Directory |
|---|---|---|---|
| Image recognition & orchestration | Raspberry Pi 4B | Python | `unkrautroboter_bilderkennung/` |
| Motor control | Arduino Mega 2560 | C++ (Arduino) | `unkrautroboter_motorsteuerung/` |
| Energy management | Arduino Pro Mini 5V | C++ (Arduino) | `unkrautroboter_energiemanagement/` |

## Running the Raspberry Pi App

```bash
cd unkrautroboter_bilderkennung
python main.py          # run directly
# or as systemd service: sudo systemctl start roboter.service
```

**Dependencies** (managed via Poetry):
```bash
cd unkrautroboter_bilderkennung
poetry install
```

**Joystick client** (run on PC with USB gamepad):
```bash
# Update UDP_IP in joystick_steuerung.py to match Pi's IP first
cd unkrautroboter_bilderkennung/joysticksteuerung_pc
python joystick_steuerung.py
```

## Arduino Development

The `.vscode/arduino.json` configures the workspace for the Arduino extension in VS Code. Board: `arduino.avr.mega`. Build output goes to `unkrautroboter_motorsteuerung/build/arduino.avr.mega/`.

**OTA firmware upload** (while Pi runs, robot in MANUAL mode): place a `.hex` file in `unkrautroboter_bilderkennung/upload/` — the Pi auto-flashes it to the Mega via avrdude and moves it to `.uploaded` or `.failed`.

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

The Arduino's `anfrageUndAbarbeiten()` function is the AUTO mode cycle: send `GETXY`, collect `XY:` lines until `DONE`, sort by Y ascending, drive to each weed, lower brush, then advance for the next frame.

## Architecture: Coordinate Pipeline

```
Camera (picamera2, 1280×720)
  → YOLO inference (model/best.pt, YOLOv8)
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
