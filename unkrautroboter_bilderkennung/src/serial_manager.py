"""
Modul für die serielle Kommunikation des Unkrautroboters.
"""

import serial
import serial.tools.list_ports
import time
import threading
import queue
import logging
import json
from . import config, status_bus

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
        self.serial = None
        self.port_open = False

        try:
            self.serial = serial.Serial(
                port=self.port, baudrate=config.BAUDRATE, timeout=1
            )
            self.port_open = True
        except serial.SerialException:
            try:
                self.port = config.SERIAL_PORT
                self.serial = serial.Serial(
                    port=self.port, baudrate=config.BAUDRATE, timeout=1
                )
                self.port_open = True
            except serial.SerialException as e:
                logger.error(f"Konnte serielle Verbindung nicht herstellen: {e}")
                self.serial = None
                self.port_open = False

        if self.port_open:
            logger.info(f"Serielle Verbindung hergestellt auf {self.port}")
        else:
            logger.warning(
                "Keine serielle Verbindung verfügbar - läuft im Offline-Modus"
            )

        self.buffer = ""  # Puffer für eingehende Zeichen
        self.received_lines = (
            queue.Queue()
        )  # Thread-sichere Queue für empfangene Zeilen
        self.running = True
        # Starte den Lese-Thread nur wenn Port offen ist
        if self.port_open:
            self.read_thread = threading.Thread(target=self._read_serial, daemon=True)
            self.read_thread.start()
            time.sleep(2)  # Zeit für Verbindungsaufbau

    def _read_serial(self):
        """Thread-Funktion zum kontinuierlichen Lesen der seriellen Schnittstelle."""
        import os

        while self.running:
            try:
                if not self.port_open or not self.serial or not self.serial.is_open:
                    time.sleep(0.1)
                    continue

                if self.serial.in_waiting:
                    # Lese ein Roh-Byte und prüfe, ob überhaupt etwas gelesen wurde
                    b = self.serial.read(1)
                    if not b:
                        # Kein Byte empfangen (Timeout) — kurz schlafen und weiter
                        time.sleep(0.01)
                        continue
                    val = b[0]
                    # Versuche die Darstellung als String (für Puffer/Zeilenbau)
                    try:
                        char = b.decode(errors="ignore")
                    except Exception:
                        char = ""
                    logger.debug(f"Empfangenes Byte: 0x{val:02x}")

                    # Zeilenende erfasst?
                    if char == "\n":
                        complete_command = self.buffer.strip()
                        if complete_command:
                            logger.info(f"Kompletter Befehl: {complete_command}")
                            # sichere Byte-Darstellung der kompletten Zeile
                            logger.debug(
                                "Als Bytes: %s",
                                " ".join(
                                    f"0x{bb:02x}"
                                    for bb in complete_command.encode(
                                        "utf-8", errors="replace"
                                    )
                                ),
                            )
                            # Prüfe ob STATUS-JSON vom Arduino
                            if complete_command.startswith("STATUS:"):
                                try:
                                    json_str = complete_command[7:]  # Nach "STATUS:"
                                    arduino_data = json.loads(json_str)
                                    status_bus.set_arduino_status(arduino_data)
                                    logger.debug(f"Arduino-Status empfangen: {arduino_data}")
                                except json.JSONDecodeError as e:
                                    logger.warning(f"Ungültiger STATUS-JSON vom Arduino: {e}")
                            
                            self.received_lines.put(complete_command)
                        # Puffer zurücksetzen (auch beim leeren String)
                        self.buffer = ""
                    else:
                        # Normales Zeichen an Puffer anhängen
                        self.buffer += char
                else:
                    # Keine Daten verfügbar -> kurz schlafen, um CPU-Load zu reduzieren
                    time.sleep(0.01)
            except Exception as e:
                logger.error(
                    f"Schwerwiegender Fehler in der seriellen Schnittstelle: {e}"
                )
                os._exit(1)

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino."""
        if not self.port_open or not self.serial or not self.serial.is_open:
            logger.warning(f"Kann Befehl nicht senden (Port nicht offen): {command}")
            return False
        try:
            self.serial.write(f"{command}\n".encode())
            return True
        except serial.SerialException as e:
            logger.error(f"Fehler beim Senden des Befehls '{command}': {e}")
            self.port_open = False
            return False

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
        if hasattr(self, "read_thread") and self.read_thread.is_alive():
            self.read_thread.join(timeout=1.0)  # Warte max. 1 Sekunde auf Thread-Ende
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.port_open = False
