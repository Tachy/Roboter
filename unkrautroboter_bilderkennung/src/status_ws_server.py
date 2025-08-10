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
    # CPU-Last berechnen (Prozent, 1 Sekunde Mittelwert)
    def get_cpu_load():
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
            parts = line.split()
            if parts[0] != "cpu":
                return None
            total_1 = sum(map(int, parts[1:]))
            idle_1 = int(parts[4])
            import time
            time.sleep(0.2)
            with open("/proc/stat", "r") as f:
                line = f.readline()
            parts = line.split()
            total_2 = sum(map(int, parts[1:]))
            idle_2 = int(parts[4])
            total_diff = total_2 - total_1
            idle_diff = idle_2 - idle_1
            if total_diff == 0:
                return None
            usage = 100.0 * (total_diff - idle_diff) / total_diff
            return round(usage, 1)
        except Exception:
            return None

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
    # CPU-Frequenz auslesen (MHz)
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r") as f:
            freq_khz = int(f.read().strip())
            cpu_freq = round(freq_khz / 1000)  # MHz
    except Exception:
        cpu_freq = None

    cpu_load = get_cpu_load()
    status = {
        "mode": robot_control.robot.get_mode() if hasattr(robot_control, 'robot') else None,
        "stream": camera.is_streaming(),
        "cpu_temp": camera.get_cpu_temperature(),
        "cpu_freq": cpu_freq,
        "cpu_load": cpu_load,
        "time": now,
        "uptime": uptime_str
    }
    # Joystick-Daten nur im Modus MANUAL mitsenden
    if status["mode"] == "MANUAL":
        if hasattr(robot_control.robot, "get_joystick_status"):
            joy = robot_control.robot.get_joystick_status()
            status["joystick"] = joy
        else:
            status["joystick"] = {"x": 0, "y": 0}
    return status

async def status_broadcast(websocket):
    try:
        while True:
            status = get_status_data()
            print(f"[WebSocket-Status] Sende: {status}")
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
