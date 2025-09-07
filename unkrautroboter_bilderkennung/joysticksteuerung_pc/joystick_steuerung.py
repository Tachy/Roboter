# Start in Powershell!
# C:\Users\johan\OneDrive\Documents\KI-Projekte\Roboter\unkrautroboter_bilderkennung>  python3 joystick_steuerung.py

import pygame
import socket
import time
import threading

# Raspberry Pi UDP-Konfiguration
UDP_IP = "192.168.179.252"  # IP-Adresse des Raspberry Pi
UDP_PORT = 5006  # Neuer UDP-Port für Joystick-Daten


# Joystick-Initialisierung
def init_joystick():
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("Kein Joystick gefunden!")
        exit(1)
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    print(f"Joystick erkannt: {joystick.get_name()}")
    return joystick


# Joystick-Daten auslesen und über UDP senden
def joystick_to_udp(joystick):
    """
    Liest den Joystick in hoher Frequenz aus (ca. 50 Hz), damit kurze Klicks nicht verloren gehen,
    sendet aber nur alle 500 ms eine UDP-Nachricht mit den aktuellen Achsenwerten und einem Button-Flag.
    Das Button-Flag ist gesetzt, wenn der Button aktuell gedrückt ist ODER innerhalb des letzten 500-ms-Intervalls gedrückt wurde.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Configure two physical buttons. Each maps to a button code sent as B=<code>.
    # Defaults: physical button at index 0 -> sends B=1; physical button at index 1 -> sends B=3.
    # These are 0-based joystick button indices and are user-configurable.
    BUTTON_INDEX_1 = 1  # physical button index that should send B=1
    BUTTON_INDEX_3 = 2  # physical button index that should send B=3
    SEND_INTERVAL_MS = 500

    # Per-button edge detection / latch state
    last_button_state_1 = 0
    last_button_state_3 = 0
    button_pending_1 = (
        False  # wurde seit der letzten Sendung ein Klick für B=1 erkannt?
    )
    button_pending_3 = (
        False  # wurde seit der letzten Sendung ein Klick für B=3 erkannt?
    )
    last_send_ms = 0
    last_x_value = 0
    last_y_value = 0

    while True:
        # Joystick-Events verarbeiten und aktuelle Werte lesen
        pygame.event.pump()
        x_axis = joystick.get_axis(0)  # X-Achse
        y_axis = joystick.get_axis(1)  # Y-Achse

        # Read both physical buttons
        button_1 = joystick.get_button(BUTTON_INDEX_1)
        button_3 = joystick.get_button(BUTTON_INDEX_3)

        # Skalieren auf -100..100 und puffern
        last_x_value = int(max(-1.0, min(1.0, x_axis)) * 100)
        last_y_value = int(max(-1.0, min(1.0, y_axis)) * 100)

        now_ms = int(time.time() * 1000)

        # Button steigende Flanken merken (separat für beide Buttons)
        if button_1 and not last_button_state_1:
            button_pending_1 = True
        if button_3 and not last_button_state_3:
            button_pending_3 = True

        # Alle 500 ms senden: Achsen + optional ein Button-Code.
        # Präferenz: B=3 hat Vorrang vor B=1 wenn beide gedrückt/pending sind.
        if now_ms - last_send_ms >= SEND_INTERVAL_MS:
            include_button = False
            send_code = None
            if button_3 or button_pending_3:
                include_button = True
                send_code = 3
            elif button_1 or button_pending_1:
                include_button = True
                send_code = 1

            message = f"JOYSTICK:X={last_x_value},Y={last_y_value}"
            if include_button and send_code is not None:
                message += f",B={send_code}"
            sock.sendto(message.encode(), (UDP_IP, UDP_PORT))
            print(f"Gesendet: {message}")
            last_send_ms = now_ms
            # Button-Latches zurücksetzen (aktueller Haltezustand wird weiterhin berücksichtigt)
            button_pending_1 = False
            button_pending_3 = False

        # Update last states for edge detection
        last_button_state_1 = 1 if button_1 else 0
        last_button_state_3 = 1 if button_3 else 0

        # Hohe Pollingfrequenz (ca. 50 Hz)
        time.sleep(0.02)


# Hauptprogramm
if __name__ == "__main__":
    joystick = init_joystick()

    # Joystick-Thread starten
    joystick_thread = threading.Thread(target=joystick_to_udp, args=(joystick,))
    joystick_thread.daemon = True
    joystick_thread.start()

    print("Joystick-Auswertung läuft. Drücke STRG+C zum Beenden.")
    try:
        while True:
            time.sleep(1)  # Hauptthread bleibt aktiv
    except KeyboardInterrupt:
        print("Programm beendet.")
