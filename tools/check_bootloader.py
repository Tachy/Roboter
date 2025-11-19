#!/usr/bin/env python3
"""
Kleines Hilfswerkzeug zum Testen, ob der Bootloader nach Reset aktiv ist.

Benutzung:
  - Standardmäßig erwartet das Tool, dass du den Reset-Taster drückst
    und sofort ENTER in diesem Terminal drückst. Das Tool führt dann
        eine kurze aktive Prüfung durch (keine avrdude-Aufrufe; nur serielle Analyse).

  - Alternativ kannst du im passiven Modus (`--mode passive`) nur die
    seriellen Bytes für ein paar Sekunden beobachten (Hex + ASCII).

Beispiel:
  python3 tools/check_bootloader.py --port /dev/serial0 --mode active

Die Ausgabe ist auf Deutsch gehalten.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import binascii

try:
    import serial  # pyserial
except Exception:
    serial = None


def passive_read(port: str, baud: int, duration: float) -> bytes:
    """Liest für `duration` Sekunden von `port` und liefert die gelesenen Bytes."""
    if serial is None:
        print(
            "pyserial ist nicht installiert. Installiere mit: python3 -m pip install --user pyserial"
        )
        sys.exit(1)
    print(f"Öffne {port} mit {baud} Baud, lese für {duration:.1f} s...")
    try:
        s = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:
        print(f"Fehler beim Öffnen des Ports {port}: {e}")
        sys.exit(1)
    try:
        # Eingangsbuffer leeren
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        t0 = time.time()
        buf = bytearray()
        while time.time() - t0 < duration:
            data = s.read(256)
            if data:
                buf.extend(data)
        return bytes(buf)
    finally:
        try:
            s.close()
        except Exception:
            pass


# Hinweis: avrdude-Aufrufe wurden entfernt. Dieses Tool führt nur serielle
# Beobachtungen und heuristische Analysen durch.


def hexdump(data: bytes) -> None:
    """Einfacher Hexdump (ähnlich `hexdump -C`)."""
    off = 0
    width = 16
    while off < len(data):
        chunk = data[off : off + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join((chr(b) if 32 <= b < 127 else ".") for b in chunk)
        print(f"{off:08x}  {hexpart:<48}  |{asciipart}|")
        off += width


def analyze_serial_data(data: bytes) -> None:
    """Einfache Heuristik zur Einschätzung, ob die Bytes eher "binary" (Bootloader/Protokoll)
    oder "ascii" (Sketch-Ausgabe) sind.

    Die Methode ist nicht perfekt — viele Bootloader senden nichts ohne Anfrage —
    aber sie hilft, offensichtliche Fälle zu erkennen (z.B. reiner ASCII-Text vs. Binärdaten).
    """
    if not data:
        print("Keine Daten zum Analysieren.")
        return

    total = len(data)
    printable = sum(1 for b in data if 32 <= b < 127)
    printable_pct = printable / total
    unique = len(set(data))

    print(f"\nAnalyse: {total} Bytes, {unique} verschiedene Bytes")
    print(f"Printable ASCII: {printable} bytes ({printable_pct*100:.1f}%)")

    # Häufige Zeichen prüfen
    from collections import Counter

    ctr = Counter(data)
    most = ctr.most_common(5)
    print("Top 5 Bytes (hex:count): ", ", ".join(f"{b:02x}:{c}" for b, c in most))

    # Heuristische Entscheidung
    if printable_pct > 0.9:
        print(
            "Einschätzung: Sehr wahrscheinlich ASCII-Sketch-Ausgabe (kein Bootloader-Handshake)."
        )
    elif printable_pct < 0.6 and total > 16:
        print(
            "Einschätzung: Enthält viele nicht-printable Bytes — mögliches Bootloader/Binärprotokoll oder Rauschdaten."
        )
    else:
        print(
            "Einschätzung: Gemischte Ausgabe — eventuell Bootloader oder frühe Sketch-Ausgabe. Weitere Tests (active) empfohlen."
        )


def is_printable(data: bytes) -> bool:
    """Return True if data contains only printable ASCII (allow CR/LF/Tab)."""
    if not data:
        return True
    for b in data:
        # allow tab, newline, carriage return
        if b in (9, 10, 13):
            continue
        if not (32 <= b < 127):
            return False
    return True


def format_data_for_print(data: bytes) -> str:
    """Format data for human-readable output.

    - If all bytes are printable (incl. CR/LF/Tab) returns decoded text.
    - Otherwise returns a hex-coded representation (space-separated hex bytes).
    """
    if is_printable(data):
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return str(data)
    # Non-printable: show as hex codes
    return " ".join(f"{b:02x}" for b in data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Test-Modus: gibt serielle Ausgaben aus, wartet 5s und löst dann einen GPIO-Reset aus"
        )
    )
    parser.add_argument(
        "--port", default="/dev/serial0", help="Serieller Port (z.B. /dev/serial0)"
    )
    parser.add_argument("--baud", default=115200, type=int, help="Baudrate")
    parser.add_argument(
        "--pre-reset-secs",
        default=10,
        type=float,
        help="Sekunden, die vor dem Auslösen des GPIO-Resets die serielle Ausgabe gezeigt werden",
    )
    parser.add_argument(
        "--post-reset-secs",
        default=10,
        type=float,
        help="Sekunden, die nach dem GPIO-Reset noch serielle Ausgabe gezeigt werden",
    )
    parser.add_argument(
        "--reset-pin",
        default=23,
        type=int,
        help="BCM-Pin für Reset (Default 23)",
    )
    parser.add_argument(
        "--reset-pulse",
        default=0.2,
        type=float,
        help="Pulsdauer in Sekunden (GPIO) für Reset (Default 0.2)",
    )
    parser.add_argument(
        "--reset-active",
        choices=["high", "low"],
        default="high",
        help=(
            "Welche Pegelrichtung den Reset auslöst: 'high' = GPIO HIGH schaltet Optokoppler-LED ein "
            "(häufig bei Anode an GPIO), 'low' = GPIO LOW schaltet Optokoppler-LED ein "
            "(häufig bei Anode an +3.3V). Default: high"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["active", "passive"],
        default="active",
        help="Modus: 'active' löst GPIO-Reset aus und führt serielle Nachanalyse durch; 'passive' liest nur serielle Bytes.",
    )
    parser.add_argument(
        "--read-secs",
        default=3.0,
        type=float,
        help="Sekunden, die im passiven Modus vom seriellen Port gelesen werden (default 3s)",
    )
    parser.add_argument(
        "--timeout",
        default=10,
        type=int,
        help="(Ignored) früher für avrdude-Timeout; jetzt ungenutzt, bleibt aus Kompatibilität.",
    )

    args = parser.parse_args()

    if serial is None:
        print(
            "pyserial ist nicht installiert. Installiere mit: python3 -m pip install --user pyserial"
        )
        sys.exit(1)

    port = args.port
    baud = int(args.baud)
    pre_secs = float(args.pre_reset_secs)
    post_secs = float(args.post_reset_secs)
    pin = int(args.reset_pin)
    pulse = float(args.reset_pulse)
    active = args.reset_active
    mode = args.mode
    read_secs = float(args.read_secs)
    timeout = int(args.timeout)

    # Passive Mode: nur seriell lesen und beenden (kein Reset)
    if mode == "passive":
        data = passive_read(port, baud, read_secs)
        if not data:
            print(
                "Keine Daten empfangen (leere Ausgabe). Das kann normal sein — Bootloader sendet meist nichts."
            )
        else:
            # Wenn nur druckbare Zeichen, Plain-Text ausgeben, sonst Hexcodes
            if is_printable(data):
                try:
                    print(data.decode("utf-8", errors="replace"))
                except Exception:
                    print(str(data))
            else:
                print("Nicht-druckbare Bytes empfangen — Ausgabe als Hexcodes:")
                print(format_data_for_print(data))
                print("\nHexdump:")
                hexdump(data)
            # Heuristische Analyse durchführen
            analyze_serial_data(data)
        return

    print(
        f"Öffne {port} mit {baud} Baud. Zeige serielle Ausgabe für {pre_secs:.1f}s vor Reset..."
    )
    try:
        s = serial.Serial(port, baud, timeout=0.1)
    except Exception as e:
        print(f"Fehler beim Öffnen des Ports {port}: {e}")
        sys.exit(1)

    def print_serial_for(duration: float) -> None:
        t0 = time.time()
        try:
            while time.time() - t0 < duration:
                data = s.read(512)
                if data:
                    out = format_data_for_print(data)
                    # If non-printable bytes, prefix with HEX label for clarity
                    if not is_printable(data):
                        print("HEX:", out, end="\n", flush=True)
                    else:
                        print(out, end="", flush=True)
        except KeyboardInterrupt:
            pass

    # Vor Reset: Serienausgabe zeigen
    try:
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        print_serial_for(pre_secs)

        # GPIO-Reset auslösen
        print(f"\nFühre GPIO-Reset auf BCM {pin} aus (Puls {pulse}s) ...")

        def gpio_reset(pin: int, pulse: float, active: str) -> None:
            """Führe einen Reset durch.

            active: 'high' bedeutet, dass ein HIGH am GPIO die Optokoppler-LED einschaltet.
                    'low' bedeutet, dass ein LOW die LED einschaltet.
            """
            # RPi.GPIO bevorzugt
            try:
                import RPi.GPIO as GPIO  # type: ignore

                GPIO.setmode(GPIO.BCM)
                if active == "high":
                    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                    try:
                        GPIO.output(pin, GPIO.HIGH)
                        time.sleep(pulse)
                        GPIO.output(pin, GPIO.LOW)
                    finally:
                        try:
                            GPIO.cleanup(pin)
                        except Exception:
                            pass
                else:
                    GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
                    try:
                        GPIO.output(pin, GPIO.LOW)
                        time.sleep(pulse)
                        GPIO.output(pin, GPIO.HIGH)
                    finally:
                        try:
                            GPIO.cleanup(pin)
                        except Exception:
                            pass
                return
            except Exception:
                pass

            # Fallback: gpiozero
            try:
                from gpiozero import LED  # type: ignore

                led = LED(pin)
                try:
                    if active == "high":
                        led.on()
                        time.sleep(pulse)
                        led.off()
                    else:
                        led.off()
                        time.sleep(pulse)
                        led.on()
                finally:
                    try:
                        led.close()
                    except Exception:
                        pass
                return
            except Exception:
                pass

            print(
                "Keine passende GPIO-Bibliothek gefunden (RPi.GPIO oder gpiozero). Kann Reset nicht per GPIO ausführen."
            )
            sys.exit(1)

        gpio_reset(pin, pulse, active)

        # Nach Reset: noch kurz weiter ausgeben
        print(f"Zeige serielle Ausgabe für {post_secs:.1f}s nach Reset...")
        print_serial_for(post_secs)
    finally:
        try:
            s.close()
        except Exception:
            pass

    # active mode: avrdude-Aufrufe wurden entfernt. Stattdessen führen wir
    # eine serielle Nachbeobachtung durch und analysieren die empfangenen Bytes.
    print("avrdude-Aufrufe sind deaktiviert. Führe serielle Nachbeobachtung durch...")
    data = passive_read(port, baud, read_secs)
    if not data:
        print("Keine Daten empfangen nach Reset.")
    else:
        if is_printable(data):
            try:
                print(data.decode("utf-8", errors="replace"))
            except Exception:
                print(str(data))
        else:
            print("Nicht-druckbare Bytes empfangen — Ausgabe als Hexcodes:")
            print(format_data_for_print(data))
            print("\nHexdump:")
            hexdump(data)
        analyze_serial_data(data)


if __name__ == "__main__":
    main()
