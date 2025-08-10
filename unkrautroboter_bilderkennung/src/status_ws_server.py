import asyncio
import datetime
import subprocess
import json
import websockets
from websockets.exceptions import ConnectionClosedOK
from . import robot_control, camera

WS_STATUS_PORT = 8765  # WebSocket-Port für Statusdaten

# Funktion zum Sammeln der Statusdaten (wie im udp_server)
def get_status_data():
    # Aktuelle Uhrzeit im ISO-Format
    now = datetime.datetime.now().isoformat(sep=' ', timespec='seconds')
    # Uptime des robot.service (systemd)
    try:
        # systemctl show -p ActiveEnterTimestamp roboter.service
        result = subprocess.run([
            'systemctl', 'show', '-p', 'ActiveEnterTimestamp', 'roboter.service'
        ], capture_output=True, text=True, check=True)
        line = result.stdout.strip()
        # Beispiel: ActiveEnterTimestamp=Sun 2025-08-10 17:00:00 CEST
        if '=' in line:
            _, start_str = line.split('=', 1)
            # Entferne ggf. Wochentag und Zeitzone
            parts = start_str.strip().split()
            if len(parts) >= 3:
                date_str = parts[1] + ' ' + parts[2]
                try:
                    start_dt = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    uptime = datetime.datetime.now() - start_dt
                    uptime_str = str(uptime).split('.')[0]  # Nur hh:mm:ss
                except Exception:
                    uptime_str = '-'
            else:
                uptime_str = '-'
        else:
            uptime_str = '-'
    except Exception:
        uptime_str = '-'
    return {
        "mode": robot_control.robot.get_mode() if hasattr(robot_control, 'robot') else None,
        "stream": camera.is_streaming(),
        "cpu_temp": camera.get_cpu_temperature(),
        "time": now,
        "uptime": uptime_str
    }

async def status_broadcast(websocket):
    try:
        while True:
            status = get_status_data()
            #print(f"[WebSocket-Status] Sende: {status}")
            await websocket.send(json.dumps(status))
            await asyncio.sleep(1)
    except ConnectionClosedOK:
        # Verbindung wurde sauber vom Client geschlossen – kein Fehler, kein Log nötig
        pass

def start_status_ws_server():
    async def run_server():
        async with websockets.serve(status_broadcast, "0.0.0.0", WS_STATUS_PORT):
            print(f"WebSocket-Status-Server läuft auf Port {WS_STATUS_PORT}")
            await asyncio.Future()  # läuft für immer

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())
