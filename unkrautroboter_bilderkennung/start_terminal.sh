#!/bin/bash

# Beispiel: virtuellen seriellen Port verbinden
# Passe die Parameter nach Bedarf an
SOCAT_CMD="socat -d -d PTY,link=/tmp/ttyV8,raw,echo=0 PTY,link=/tmp/ttyV9,raw,echo=0"

# socat im Hintergrund starten und PID merken
$SOCAT_CMD &
SOCAT_PID=$!
echo "socat gestartet (PID $SOCAT_PID)"

sleep 2

# Terminal starten (hier: LXTerminal, kann angepasst werden)
# Das Terminal läuft im Vordergrund; Script wartet, bis es geschlossen wird
picocom --imap lfcrlf --omap crcrlf -b 115200 /dev/serial0

# Nach Beenden des Terminals socat stoppen
echo "Beende socat..."
kill $SOCAT_PID 2>/dev/null
wait $SOCAT_PID 2>/dev/null

echo "Fertig."


