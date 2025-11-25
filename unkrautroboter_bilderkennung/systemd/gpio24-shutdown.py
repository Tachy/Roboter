#!/usr/bin/env python3
from gpiozero import Button  # type: ignore
from signal import pause
import os

# GPIO24 (Pin 18) wird als Shutdown-Taster verwendet
shutdown_btn = Button(24, pull_up=True, hold_time=1)  # 1 Sekunde halten für Shutdown


def shutdown():
    os.system("sudo shutdown -h now")


shutdown_btn.when_held = shutdown

pause()
