import asyncio
import datetime
import subprocess
import json
import websockets
import logging
from . import config
from websockets.exceptions import ConnectionClosedOK
from . import robot_control, camera, geometry, status_bus

# Logger einrichten
logger = logging.getLogger("status_ws_server")
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=config.LOGLEVEL,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

WS_STATUS_PORT = 8765  # WebSocket-Port für Statusdaten


# Funktion zum Sammeln der Statusdaten (wie im udp_server)
def get_status_data():
    # WLAN-Status (RSSI) aus /proc/net/wireless lesen
    def get_wifi_status():
        try:
            with open("/proc/net/wireless", "r") as f:
                lines = f.readlines()
            # Überspringe die ersten 2 Header-Zeilen, suche erste Interface-Zeile
            for line in lines[2:]:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                iface = iface.strip()
                parts = rest.split()
                # Erwartetes Format (Beispiel):
                # Inter-| sta ...
                #  wlan0: 0000   64.  -45.  -256        ...
                # parts[0]=status, parts[1]=link, parts[2]=level(dBm), parts[3]=noise
                if len(parts) >= 3:
                    rssi_dbm = float(parts[2])
                    pct = max(0, min(100, round(2 * (rssi_dbm + 90))))
                    return {
                        "signal_pct": pct,
                    }
            # Keine Interface-Zeile gefunden
            return {"signal_pct": None}
        except Exception:
            return {"signal_pct": None}

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
    now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    # Uptime des robot.service (systemd)
    try:
        # systemctl show -p ActiveEnterTimestamp roboter.service
        result = subprocess.run(
            ["systemctl", "show", "-p", "ActiveEnterTimestamp", "roboter.service"],
            capture_output=True,
            text=True,
            check=True,
        )
        line = result.stdout.strip()
        # Beispiel: ActiveEnterTimestamp=Sun 2025-08-10 17:00:00 CEST
        if "=" in line:
            _, start_str = line.split("=", 1)
            # Entferne ggf. Wochentag und Zeitzone
            parts = start_str.strip().split()
            if len(parts) >= 3:
                date_str = parts[1] + " " + parts[2]
                try:
                    start_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    uptime = datetime.datetime.now() - start_dt
                    uptime_str = str(uptime).split(".")[0]  # Nur hh:mm:ss
                except Exception:
                    uptime_str = "-"
            else:
                uptime_str = "-"
        else:
            uptime_str = "-"
    except Exception:
        uptime_str = "-"
    # CPU-Frequenz auslesen (MHz)
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r") as f:
            freq_khz = int(f.read().strip())
            cpu_freq = round(freq_khz / 1000)  # MHz
    except Exception:
        cpu_freq = None

    cpu_load = get_cpu_load()
    status = {
        "mode": (
            robot_control.robot.get_mode() if hasattr(robot_control, "robot") else None
        ),
        "stream": camera.is_streaming(),
        "cpu_temp": camera.get_cpu_temperature(),
        "cpu_freq": cpu_freq,
        "cpu_load": cpu_load,
        "time": now,
        "last_capture_ts": camera.get_last_capture_timestamp(),
        "uptime": uptime_str,
        "world_transform_ready": geometry.is_world_transform_ready(),
        "wifi": get_wifi_status(),
        "message": status_bus.get_message(),
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
        # Reagiere schnell auf neue Aufnahmen oder Statusmeldungen und sende sofort
        last_ts = camera.get_last_capture_timestamp()
        last_msg_ts = 0.0
        heartbeat_interval = 1.0
        quick_checks = 10  # 10 * 0.1s = 1s
        while True:
            sent_on_change = False
            for _ in range(quick_checks):
                curr_ts = camera.get_last_capture_timestamp()
                msg_info = status_bus.get_message_info()
                curr_msg_ts = (
                    msg_info.get("ts", 0.0) if isinstance(msg_info, dict) else 0.0
                )
                if curr_ts != last_ts and curr_ts is not None:
                    status = get_status_data()
                    await websocket.send(json.dumps(status))
                    last_ts = curr_ts
                    sent_on_change = True
                    break
                if curr_msg_ts and curr_msg_ts != last_msg_ts:
                    status = get_status_data()
                    await websocket.send(json.dumps(status))
                    last_msg_ts = curr_msg_ts
                    sent_on_change = True
                    break
                await asyncio.sleep(heartbeat_interval / quick_checks)

            if sent_on_change:
                continue

            # Periodischer Heartbeat-Status
            status = get_status_data()
            logger.debug(f"[WebSocket-Status] Sende (Heartbeat): {status}")
            await websocket.send(json.dumps(status))
    except ConnectionClosedOK:
        # Verbindung wurde sauber vom Client geschlossen – kein Fehler, kein Log nötig
        pass


def start_status_ws_server():
    async def run_server():
        async with websockets.serve(status_broadcast, "0.0.0.0", WS_STATUS_PORT):
            logger.info(f"WebSocket-Status-Server läuft auf Port {WS_STATUS_PORT}")
            await asyncio.Future()  # läuft für immer

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_server())
