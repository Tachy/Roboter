"""
Modul für die serielle Kommunikation des Unkrautroboters.
"""

import serial
import serial.tools.list_ports
import time
import threading
import queue
from . import config

class SerialManager:
    def __init__(self):
        if config.USE_SIMULATED_SERIAL:
            self.port = config.SIMULATED_SERIAL_PORT
        else:
            self.port = config.SERIAL_PORT
            
        # Warte auf serielle Schnittstelle
        while True:
            try:
                # Prüfe ob Port verfügbar ist
                import glob
                
                # Suche nach allen seriellen Ports (hardware und pts)
                hardware_ports = list(serial.tools.list_ports.comports())
                pts_ports = glob.glob('/dev/pts/[0-9]*')
                
                print("\nVerfügbare Hardware-Ports:")
                for port in hardware_ports:
                    print(f"  - {port.device}: {port.description}")
                    
                print("\nVerfügbare PTS-Ports:")
                for port in pts_ports:
                    print(f"  - {port}")
                
                # Kombiniere alle gefundenen Ports
                all_ports = [p.device for p in hardware_ports] + pts_ports
                
                if self.port not in all_ports:
                    print(f"\nWarte auf Port {self.port}...")
                    time.sleep(2)
                    continue
                    
                self.serial = serial.Serial(
                    port=self.port,
                    baudrate=config.BAUDRATE,
                    timeout=1
                )
                break
                
            except serial.SerialException as e:
                print(f"Port nicht verfügbar: {str(e)}")
                time.sleep(2)
                
        print(f"Serielle Verbindung hergestellt auf {self.port}")
        
        self.buffer = ""  # Puffer für eingehende Zeichen
        self.received_lines = queue.Queue()  # Thread-sichere Queue für empfangene Zeilen
        self.running = True
        
        # Starte den Lese-Thread
        self.read_thread = threading.Thread(target=self._read_serial, daemon=True)
        self.read_thread.start()
        
        time.sleep(2)  # Zeit für Verbindungsaufbau

    def _read_serial(self):
        """Thread-Funktion zum kontinuierlichen Lesen der seriellen Schnittstelle."""
        while self.running:
            if self.serial.in_waiting:
                char = self.serial.read().decode(errors='ignore')
                print(f"Empfangenes Byte: 0x{ord(char):02x}")
                
                if char == '\n':  # Zeilenende gefunden
                    complete_command = self.buffer.strip()  # Entferne Whitespace und CR
                    if complete_command:  # Ignoriere leere Zeilen
                        print(f"Kompletter Befehl: {complete_command}")
                        print("Als Bytes:", ' '.join(f'0x{ord(c):02x}' for c in complete_command))
                        self.received_lines.put(complete_command)
                    self.buffer = ""  # Puffer zurücksetzen
                else:
                    self.buffer += char
            else:
                # Kurze Pause wenn keine Daten verfügbar
                time.sleep(0.01)

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
