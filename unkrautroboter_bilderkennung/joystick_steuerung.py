import pygame
import socket
import time
import threading

# Raspberry Pi UDP-Konfiguration
UDP_IP = "192.168.179.252"  # IP-Adresse des Raspberry Pi
UDP_PORT = 5006             # Neuer UDP-Port für Joystick-Daten

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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        pygame.event.pump()  # Joystick-Events verarbeiten
        x_axis = joystick.get_axis(0)  # X-Achse
        y_axis = joystick.get_axis(1)  # Y-Achse

        # Werte skalieren und formatieren
        x_value = int(x_axis * 100)  # Wertebereich -100 bis 100
        y_value = int(y_axis * 100)  # Wertebereich -100 bis 100
        message = f"JOYSTICK:X={x_value},Y={y_value}"

        # Nachricht senden
        sock.sendto(message.encode(), (UDP_IP, UDP_PORT))
        print(f"Gesendet: {message}")

        time.sleep(0.5)  # 500 ms warten

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