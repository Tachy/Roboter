"""
Modul für die UDP-Server-Funktionalität des Unkrautroboters.
"""

import socket
import threading
from . import config, camera, training

# Callback-Funktionen, die von außen gesetzt werden
on_mode_change = None
on_command = None

def start_control_server():
    """Startet den UDP-Server für die Modussteuerung."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_CONTROL_PORT))
    print(f"UDP-Steuerkanal läuft auf Port {config.UDP_CONTROL_PORT}...")
    
    while True:
        data, addr = sock.recvfrom(1024)
        command = data.decode().strip().upper()
        if command in ["AUTO", "MANUAL"]:
            if on_mode_change:
                on_mode_change(command)
                print(f"Modus auf {command} geändert (von {addr})")
                if command == "MANUAL":
                    camera.start_stream()
                else:
                    camera.stop_stream()
        else:
            print(f"Unbekannter Befehl: {command} (von {addr})")

def start_joystick_server():
    """Startet den UDP-Server für Joystick-Kommandos."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_JOYSTICK_PORT))
    print(f"UDP-Joystick-Server läuft auf Port {config.UDP_JOYSTICK_PORT}...")
    
    while True:
        data, addr = sock.recvfrom(1024)
        command = data.decode().strip()
        if on_command:
            handled = on_command(command)
            if handled:
                print(f"Joystick-Befehl empfangen und verarbeitet: {command} (von {addr})")
                # BUTTON:1 auswerten für Bildaufnahme
                if ",BUTTON:1" in command:
                    training.save_training_image()
            else:
                print(f"Joystick-Befehl ignoriert (von {addr})")
