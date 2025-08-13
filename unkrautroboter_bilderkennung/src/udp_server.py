"""
Modul für die UDP-Server-Funktionalität des Unkrautroboters.
"""

import socket
import threading
import time
import logging
from . import config
from . import config, camera, training, robot_control

# Logger einrichten
logger = logging.getLogger("udp_server")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )

# Callback-Funktionen, die von außen gesetzt werden
on_mode_change = None
on_command = None


_last_heartbeat = 0
_stream_running = False



# Funktion zum Sammeln der Statusdaten

def start_control_server():
    """Startet den UDP-Server für die Modussteuerung."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_CONTROL_PORT))
    logger.info(f"UDP-Steuerkanal läuft auf Port {config.UDP_CONTROL_PORT}...")
    
    while True:
        data, addr = sock.recvfrom(1024)
        command = data.decode().strip().upper()
        if command in ["AUTO", "MANUAL", "DISTORTION"]:
            if on_mode_change:
                on_mode_change(command)
                logger.info(f"Modus auf {command} geändert (von {addr})")
        else:
            logger.warning(f"Unbekannter Befehl: {command} (von {addr})")

def start_joystick_server():
    """Startet den UDP-Server für Joystick-Kommandos."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_JOYSTICK_PORT))
    logger.info(f"UDP-Joystick-Server läuft auf Port {config.UDP_JOYSTICK_PORT}...")
    
    while True:
        data, addr = sock.recvfrom(1024)
        command = data.decode().strip()
        if on_command:
            handled = on_command(command)
            if handled:
                logger.debug(f"Joystick-Befehl empfangen und verarbeitet: {command} (von {addr})")
                # BUTTON:1: je nach Modus
                if ",BUTTON:1" in command:
                    mode = robot_control.robot.get_mode() if hasattr(robot_control, 'robot') else None
                    if mode == "MANUAL":
                        training.save_training_image()
                    elif mode == "DISTORTION":
                        robot_control.robot.calibration_button_pressed()
            else:
                logger.debug(f"Joystick-Befehl ignoriert (von {addr})")

# Heartbeat-Listener für den Videostream
def _heartbeat_listener():
    global _last_heartbeat
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.UDP_IP, config.UDP_HEARTBEAT_PORT))
    logger.info(f"UDP-Heartbeat-Server läuft auf Port {config.UDP_HEARTBEAT_PORT}...")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            _last_heartbeat = time.time()
            logger.debug(f"Heartbeat empfangen von {addr}")
        except Exception as e:
            logger.error(f"Fehler im Heartbeat-Listener: {e}")
            time.sleep(1)

# Watchdog für den Videostream basierend auf Heartbeat
def _stream_watchdog():
    global _last_heartbeat, _stream_running
    while True:
        now = time.time()
        if now - _last_heartbeat < config.HEARTBEAT_TIMEOUT:
            if not _stream_running:
                logger.info("Starte Videostream (Heartbeat aktiv)")
                camera.start_stream()
                _stream_running = True
        else:
            if _stream_running:
                logger.warning("Stoppe Videostream (kein Heartbeat)")
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


