"""
Modul für die serielle Kommunikation des Unkrautroboters.
"""

import serial
import serial.tools.list_ports
import time
import threading
import queue
import logging
from . import config
from . import config

# Logger einrichten
logger = logging.getLogger("serial_manager")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


class SerialManager:
    def __init__(self):
        self.port = config.SIMULATED_SERIAL_PORT
        try:
            self.serial = serial.Serial(
                port=self.port, baudrate=config.BAUDRATE, timeout=1
            )
        except serial.SerialException:
            self.port = config.SERIAL_PORT
            self.serial = serial.Serial(
                port=self.port, baudrate=config.BAUDRATE, timeout=1
            )

        logger.info(f"Serielle Verbindung hergestellt auf {self.port}")
        self.buffer = ""  # Puffer für eingehende Zeichen
        self.received_lines = (
            queue.Queue()
        )  # Thread-sichere Queue für empfangene Zeilen
        self.running = True
        # Starte den Lese-Thread
        self.read_thread = threading.Thread(target=self._read_serial, daemon=True)
        self.read_thread.start()
        time.sleep(2)  # Zeit für Verbindungsaufbau

    def _read_serial(self):
        """Thread-Funktion zum kontinuierlichen Lesen der seriellen Schnittstelle."""
        import os

        while self.running:
            try:
                if self.serial.in_waiting:
                    char = self.serial.read().decode(errors="ignore")
                    logger.debug(f"Empfangenes Byte: 0x{ord(char):02x}")

                    if char == "\n":  # Zeilenende gefunden
                        complete_command = (
                            self.buffer.strip()
                        )  # Entferne Whitespace und CR
                        if complete_command:  # Ignoriere leere Zeilen
                            logger.info(f"Kompletter Befehl: {complete_command}")
                            logger.debug(
                                "Als Bytes: %s",
                                " ".join(f"0x{ord(c):02x}" for c in complete_command),
                            )
                            self.received_lines.put(complete_command)
                        self.buffer = ""  # Puffer zurücksetzen
                    else:
                        self.buffer += char
                else:
                    # Kurze Pause wenn keine Daten verfügbar
                    time.sleep(0.01)
            except Exception as e:
                logger.error(
                    f"Schwerwiegender Fehler in der seriellen Schnittstelle: {e}"
                )
                os._exit(1)

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        self.serial.write(f"{command}\n".encode())

    def read_line(self):
        """Liest eine Zeile aus der Queue der empfangenen Befehle.
        Nicht-blockierend, gibt None zurück wenn keine Zeile verfügbar."""
        try:
            return self.received_lines.get_nowait()
        except queue.Empty:
            return None

    def close(self):
        """Beendet den Lese-Thread und schließt die serielle Verbindung."""
        self.running = False
        if self.read_thread.is_alive():
            self.read_thread.join(timeout=1.0)  # Warte max. 1 Sekunde auf Thread-Ende
        self.serial.close()
