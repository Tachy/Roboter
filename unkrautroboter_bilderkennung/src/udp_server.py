"""
Modul für die UDP-Server-Funktionalität des Unkrautroboters.
"""

import socket
import threading
import time
from . import config, camera, training

# Callback-Funktionen, die von außen gesetzt werden
on_mode_change = None
on_command = None


_last_heartbeat = 0
_stream_running = False

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

# Heartbeat-Listener für den Videostream
def _heartbeat_listener():
    global _last_heartbeat
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_HEARTBEAT_PORT))
    print(f"UDP-Heartbeat-Server läuft auf Port {config.UDP_HEARTBEAT_PORT}...")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            _last_heartbeat = time.time()
            print(f"Heartbeat empfangen von {addr}")
        except Exception as e:
            print(f"Fehler im Heartbeat-Listener: {e}")
            time.sleep(1)

# Watchdog für den Videostream basierend auf Heartbeat
def _stream_watchdog():
    global _last_heartbeat, _stream_running
    while True:
        now = time.time()
        if now - _last_heartbeat < config.HEARTBEAT_TIMEOUT:
            if not _stream_running:
                print("Starte Videostream (Heartbeat aktiv)")
                camera.start_stream()
                _stream_running = True
        else:
            if _stream_running:
                print("Stoppe Videostream (kein Heartbeat)")
                camera.stop_stream()
                _stream_running = False
        time.sleep(1)

def start_heartbeat_monitor():
    """Startet Heartbeat-UDP-Listener und Watchdog für den Videostream."""
    global _last_heartbeat, _stream_running
    _last_heartbeat = 0
    _stream_running = False
    threading.Thread(target=_heartbeat_listener, daemon=True).start()
    threading.Thread(target=_stream_watchdog, daemon=True).start()
