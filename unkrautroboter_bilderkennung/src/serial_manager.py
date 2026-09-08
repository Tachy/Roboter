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
        self._write_lock = threading.Lock()  # send_command aus mehreren Threads
        self.running = True
        # Lese-Thread immer starten: er baut den Port bei Bedarf mit Backoff neu auf
        # (auch wenn der erste Verbindungsversuch fehlgeschlagen ist).
        self.read_thread = threading.Thread(target=self._read_serial, daemon=True)
        self.read_thread.start()
        if self.port_open:
            time.sleep(2)  # Zeit für Verbindungsaufbau

    def _safe_close_port(self):
        """Schließt den Port ohne zu werfen und verwirft den Zeilenpuffer."""
        self.port_open = False
        try:
            if self.serial and self.serial.is_open:
                self.serial.close()
        except Exception:
            pass
        self.buffer = ""

    def _try_reopen(self) -> bool:
        """Versucht, den seriellen Port neu zu öffnen (erst simuliert, dann echt).
        Gibt True zurück, wenn eine Verbindung besteht."""
        for port in (config.SIMULATED_SERIAL_PORT, config.SERIAL_PORT):
            try:
                self.serial = serial.Serial(
                    port=port, baudrate=config.BAUDRATE, timeout=1
                )
                self.port = port
                self.port_open = True
                return True
            except serial.SerialException:
                continue
        self.serial = None
        self.port_open = False
        return False

    def _dispatch_line(self, line: str) -> None:
        """Eine vollständige empfangene Zeile verarbeiten (STATUS-JSON + Queue)."""
        logger.info(f"Kompletter Befehl: {line}")
        if line.startswith("STATUS:"):
            try:
                arduino_data = json.loads(line[7:])
                status_bus.set_arduino_status(arduino_data)
                logger.debug(f"Arduino-Status empfangen: {arduino_data}")
            except json.JSONDecodeError as e:
                logger.warning(f"Ungültiger STATUS-JSON vom Arduino: {e}")
        self.received_lines.put(line)

    def _read_serial(self):
        """Thread-Funktion zum kontinuierlichen Lesen der seriellen Schnittstelle.

        Bei Fehlern wird der Port geschlossen und mit Backoff neu verbunden – der
        Prozess wird NICHT mehr beendet."""
        reconnect_delay = 0.5
        while self.running:
            try:
                if not self.port_open or not self.serial or not self.serial.is_open:
                    if self._try_reopen():
                        logger.info(
                            f"Serielle Verbindung (wieder) hergestellt auf {self.port}"
                        )
                        reconnect_delay = 0.5
                    else:
                        time.sleep(reconnect_delay)
                        reconnect_delay = min(reconnect_delay * 2, 10.0)
                    continue

                n = self.serial.in_waiting
                if n:
                    # Alles verfügbare am Stück lesen statt Byte für Byte (L2).
                    chunk = self.serial.read(n)
                    if not chunk:
                        time.sleep(0.01)
                        continue
                    for ch in chunk.decode(errors="ignore"):
                        if ch == "\r":
                            continue
                        if ch == "\n":
                            line = self.buffer.strip()
                            self.buffer = ""
                            if line:
                                self._dispatch_line(line)
                        else:
                            self.buffer += ch
                            if len(self.buffer) > 4096:
                                logger.warning(
                                    "Serieller Zeilenpuffer zu groß – verworfen."
                                )
                                self.buffer = ""
                else:
                    # Keine Daten verfügbar -> kurz schlafen, um CPU-Last zu reduzieren
                    time.sleep(0.01)
            except Exception as e:
                logger.error(
                    f"Fehler in der seriellen Schnittstelle: {e} – schließe Port, "
                    f"Reconnect folgt."
                )
                self._safe_close_port()
                time.sleep(0.5)

    def send_command(self, command):
        """Sendet ein Kommando an den Arduino. Thread-sicher (Schreib-Lock)."""
        if not self.port_open or not self.serial or not self.serial.is_open:
            logger.warning(f"Kann Befehl nicht senden (Port nicht offen): {command}")
            return False
        try:
            with self._write_lock:
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
