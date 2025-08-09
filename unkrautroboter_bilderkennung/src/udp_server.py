"""
Modul für die UDP-Server-Funktionalität des Unkrautroboters.
"""

import socket
import threading
from . import config, camera, training
from .robot_control import robot  # Importiere die globale Roboter-Instanz

def start_control_server():
    """Startet den UDP-Server für die Modussteuerung."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_CONTROL_PORT))
    print(f"UDP-Steuerkanal läuft auf Port {config.UDP_CONTROL_PORT}...")
    
    while True:
        data, addr = sock.recvfrom(1024)
        command = data.decode().strip().upper()
        if command in ["AUTO", "MANUAL"]:
            robot.set_mode(command)
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
        if robot.get_mode() == "MANUAL":
            print(f"Joystick-Befehl empfangen: {command} (von {addr})")
            # BUTTON:1 auswerten für Bildaufnahme
            if ",BUTTON:1" in command:
                training.save_training_image()
                command = command.replace(",BUTTON:1", "")
            # Nur X und Y an Arduino weiterleiten
            robot.send_command(command)
            print(f"Joystick-Befehl an Arduino gesendet: {command}")
        else:
            print(f"Joystick-Befehl ignoriert, da Modus {robot.get_mode()} aktiv ist.")
