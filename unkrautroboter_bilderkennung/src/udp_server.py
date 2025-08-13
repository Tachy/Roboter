"""
Modul für die UDP-Server-Funktionalität des Unkrautroboters.
"""

import socket
import ipaddress
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
def _is_source_allowed(src_ip_str: str) -> bool:
    """Prüft, ob eine Absender-IP gemäß ALLOWED_UDP_SOURCES zugelassen ist.
    Erlaubt einzelne IPs und CIDR-Netze. Bei leerer Liste: alles erlaubt.
    Fehler beim Parsen führen nicht zur Ablehnung (fail-open, wie zuvor)."""
    try:
        if not getattr(config, 'ALLOWED_UDP_SOURCES', None):
            return True
        src_ip = ipaddress.ip_address(src_ip_str)
        for entry in config.ALLOWED_UDP_SOURCES:
            try:
                if '/' in entry:
                    net = ipaddress.ip_network(entry, strict=False)
                    if src_ip in net:
                        return True
                else:
                    if src_ip == ipaddress.ip_address(entry):
                        return True
            except Exception:
                continue
        return False
    except Exception:
        return True



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
        # Quell-IP prüfen
        if not _is_source_allowed(addr[0]):
            logger.warning(f"Verwerfe Steuer-Befehl von nicht erlaubter Quelle: {addr[0]}")
            continue
        if command in ["AUTO", "MANUAL", "DISTORTION", "EXTRINSIK"]:
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
        # Quell-IP prüfen
        if not _is_source_allowed(addr[0]):
            logger.warning(f"Verwerfe Joystick-Befehl von nicht erlaubter Quelle: {addr[0]}")
            continue
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
                    elif mode == "EXTRINSIK":
                        robot_control.robot.extrinsic_button_pressed()
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


