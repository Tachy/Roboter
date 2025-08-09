"""
Modul für die serielle Kommunikation des Unkrautroboters.
"""

import serial
import time
from . import config

class SerialManager:
    def __init__(self):
        if config.USE_SIMULATED_SERIAL:
            self.port = config.SIMULATED_SERIAL_PORT
        else:
            self.port = config.SERIAL_PORT
            
        self.serial = serial.Serial(
            port=self.port,
            baudrate=config.BAUDRATE,
            timeout=1
        )
        time.sleep(2)  # Zeit für Verbindungsaufbau
        print(f"Serielle Verbindung hergestellt auf {self.port}")

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.write(f"{command}\n".encode())

    def read_line(self):
        """Liest eine Zeile von der seriellen Schnittstelle."""
        if self.serial.in_waiting:
            return self.serial.readline().decode().strip()
        return None

    def close(self):
        """Schließt die serielle Verbindung."""
        self.serial.close()
