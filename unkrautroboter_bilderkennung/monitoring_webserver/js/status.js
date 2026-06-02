function htmlRow(label, value) {
    return `${label}: <b>${value}</b><br>`;
}

function updateArduinoBox(arduino) {
    const el = document.getElementById('arduino-content');
    if (!el) return;
    if (!arduino || typeof arduino !== 'object') { el.innerHTML = '–'; return; }
    let html = '';
    if (typeof arduino.mode  === 'string')  html += htmlRow('Modus',     arduino.mode);
    if (typeof arduino.encL  === 'number')  html += htmlRow('Encoder L', arduino.encL);
    if (typeof arduino.encR  === 'number')  html += htmlRow('Encoder R', arduino.encR);
    if (typeof arduino.encX  === 'number')  html += htmlRow('Encoder X', arduino.encX);
    if (typeof arduino.encZ  === 'number')  html += htmlRow('Encoder Z', arduino.encZ);
    if (arduino.ina_present === true) {
        if (typeof arduino.ina_current_mA === 'number') html += htmlRow('Strom',    (arduino.ina_current_mA / 1000).toFixed(2) + ' A');
        if (typeof arduino.ina_voltage_mV === 'number') html += htmlRow('Spannung', (arduino.ina_voltage_mV / 1000).toFixed(2) + ' V');
        if (typeof arduino.ina_power_mW   === 'number') html += htmlRow('Leistung', (arduino.ina_power_mW   / 1000).toFixed(2) + ' W');
    }
    el.innerHTML = html || '–';
}

function updateStatusBox(data) {
    const el = document.getElementById('status-content');
    if (!el) return;
    if (!data) {
        el.textContent = 'Keine Statusdaten verfügbar.';
        return;
    }

    let cpuTempVal = null;
    if (typeof data.cpu_temp === 'number') cpuTempVal = data.cpu_temp;
    else if (typeof data.cpu_temp === 'string' && data.cpu_temp.match(/^\d+(\.\d+)?/)) cpuTempVal = parseFloat(data.cpu_temp);
    let cpuTempColor = '';
    if (cpuTempVal !== null) {
        if (cpuTempVal >= 70) cpuTempColor = 'color:#ff3333;font-weight:bold;';
        else if (cpuTempVal >= 60) cpuTempColor = 'color:#ffd600;font-weight:bold;';
    }
    const cpuTempStr = cpuTempVal !== null ? cpuTempVal.toFixed(1) + '°C' : (data.cpu_temp ?? '-');

    let html = '';
    html += htmlRow('Modus',    data.mode ?? '-');
    html += htmlRow('Stream',   data.stream ? 'aktiv' : 'inaktiv');
    html += `CPU-Temp: <b style="${cpuTempColor}">${cpuTempStr}</b><br>`;
    html += htmlRow('CPU Takt', typeof data.cpu_freq === 'number' ? data.cpu_freq + ' MHz' : (data.cpu_freq ?? '-'));
    html += htmlRow('CPU-Last', typeof data.cpu_load === 'number' ? data.cpu_load + ' %'   : (data.cpu_load ?? '-'));
    html += htmlRow('Zeit',     data.time   ?? '-');
    html += htmlRow('Uptime',   data.uptime ?? '-');
    if (data.wifi) {
        const pct = typeof data.wifi.signal_pct === 'number' ? `${data.wifi.signal_pct}%` : '-';
        html += htmlRow('WLAN', pct);
    }
    if (typeof data.world_transform_ready !== 'undefined') {
        html += htmlRow('Extrinsik', data.world_transform_ready ? 'bereit' : 'nicht bereit');
        if (data.message && (data.mode === 'EXTRINSIK' || data.mode === 'DISTORTION')) {
            html += `<span style="color:#9cf;">${data.message}</span><br>`;
        }
    }
    if (data.mode === 'MANUAL' && data.joystick && typeof data.joystick.x === 'number' && typeof data.joystick.y === 'number') {
        html += `<br>Joystick X: <b>${data.joystick.x}</b> &nbsp; Y: <b>${data.joystick.y}</b>`;
    }
    el.innerHTML = html;

    if (data.mode) {
        const modeSelect = document.getElementById('mode');
        if (modeSelect && modeSelect.value !== data.mode) modeSelect.value = data.mode;
        if (typeof window.__setJoypadMode === 'function') window.__setJoypadMode(data.mode);
        const resetBox = document.getElementById('reset-box');
        if (resetBox) resetBox.style.display = (data.mode === 'MANUAL') ? 'block' : 'none';
    }

    const tsRaw = data.last_capture_ts;
    const timeDiv = document.getElementById('last-capture-time');
    if (tsRaw !== null && tsRaw !== undefined) {
        const ts = Number(tsRaw);
        if (!Number.isNaN(ts)) {
            const img = document.getElementById('last-capture-img');
            const empty = document.getElementById('last-capture-empty');
            if (img && img.getAttribute('data-ts') !== String(ts)) {
                img.onload  = () => { img.style.display = 'block'; if (empty) empty.style.display = 'none'; };
                img.onerror = () => { img.style.display = 'none';  if (empty) empty.style.display = 'block'; };
                img.setAttribute('data-ts', String(ts));
                img.src = lastCaptureUrl(ts);
            }
            if (timeDiv) {
                const d = new Date(ts * 1000);
                const hh = d.getHours().toString().padStart(2, '0');
                const mm = d.getMinutes().toString().padStart(2, '0');
                const ss = d.getSeconds().toString().padStart(2, '0');
                timeDiv.textContent = `Uhrzeit: ${hh}:${mm}:${ss}`;
            }
        } else if (timeDiv) {
            timeDiv.textContent = '–';
        }
    } else if (timeDiv) {
        timeDiv.textContent = '–';
    }

    if (typeof data.stream !== 'undefined') {
        const active = !!data.stream;
        ensureStream(active);
        if (active) startStreamWatchdog(); else stopStreamWatchdog();
    }

    if (data.arduino) updateArduinoBox(data.arduino);
}
