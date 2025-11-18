#!/usr/bin/env python3
"""Einfaches Script: 10s Countdown, dann harter Aufruf von avrdude.

Aufruf (hart kodiert):
  avrdude -v -p m2560 -c stk500v2 -P /dev/serial0 -b 115200 -U flash:w:unkrautroboter.hex:i

Das Script nimmt keine Parameter entgegen; ändere den Code, wenn andere Werte benötigt werden.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time


def main() -> int:
    # Hart kodierte Werte
    hexfile = "unkrautroboter.hex"
    port = "/dev/serial0"
    baud = 115200
    mcu = "m2560"
    programmer = "wiring"
    countdown = 10

    avrdude = shutil.which("avrdude")
    if not avrdude:
        print("avrdude nicht im PATH gefunden. Bitte avrdude installieren.")
        return 2

    print(
        f"Werde in {countdown}s flashen: {hexfile} -> {mcu} via {programmer} on {port} @ {baud}"
    )
    try:
        for i in range(countdown, 0, -1):
            print(f"{i} ", end="\r", flush=True)
            time.sleep(1)
        print("\nStarte avrdude...")
    except KeyboardInterrupt:
        print("Abgebrochen durch Benutzer.")
        return 1

    cmd = [
        avrdude,
        "-v",
        "-p",
        mcu,
        "-c",
        programmer,
        "-P",
        port,
        "-b",
        str(baud),
        "-D",
        "-U",
        f"flash:w:{hexfile}:i",
    ]

    print("Befehl:", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        print(f"Fehler beim Starten von avrdude: {e}")
        return 3

    if proc.stdout:
        print("--- avrdude STDOUT ---")
        print(proc.stdout)
    if proc.stderr:
        print("--- avrdude STDERR ---")
        print(proc.stderr)

    if proc.returncode == 0:
        print("avrdude erfolgreich beendet (returncode 0)")
        return 0
    else:
        print(f"avrdude beendet mit returncode {proc.returncode}")
        return proc.returncode


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
