// Statusbox aktualisieren (inkl. letzte Aufnahme und Stream-Steuerung)
function updateStatusBox(data) {
    const el = document.getElementById('status-content');
    if (!el) return;
    if (!data) {
        el.textContent = 'Keine Statusdaten verfügbar.';
        return;
    }
    let html = '';
    html += `Modus: <b>${data.mode ?? '-'}</b><br>`;
    html += `Stream: <b>${data.stream ? 'aktiv' : 'inaktiv'}</b><br>`;
    let cpuTempColor = '';
    let cpuTempVal = null;
    if (typeof data.cpu_temp === 'number') cpuTempVal = data.cpu_temp;
    else if (typeof data.cpu_temp === 'string' && data.cpu_temp.match(/^\d+(\.\d+)?/)) cpuTempVal = parseFloat(data.cpu_temp);
    if (cpuTempVal !== null) {
        if (cpuTempVal >= 70) cpuTempColor = 'color:#ff3333;font-weight:bold;';
        else if (cpuTempVal >= 60) cpuTempColor = 'color:#ffd600;font-weight:bold;';
    }
    html += `CPU-Temp: <b style="${cpuTempColor}">${cpuTempVal !== null ? cpuTempVal.toFixed(1) + '°C' : (data.cpu_temp ?? '-')}</b><br>`;
    html += `CPU Takt: <b>${typeof data.cpu_freq === 'number' ? data.cpu_freq + ' MHz' : (data.cpu_freq ?? '-')}</b><br>`;
    html += `CPU-Last: <b>${typeof data.cpu_load === 'number' ? data.cpu_load + ' %' : (data.cpu_load ?? '-')}</b><br>`;
    html += `Zeit: <b>${data.time ?? '-'}</b><br>`;
    html += `Uptime: <b>${data.uptime ?? '-'}</b>`;
    if (data.wifi) {
        const pct = (typeof data.wifi.signal_pct === 'number') ? `${data.wifi.signal_pct}%` : '-';
        html += `<br>WLAN: <b>${pct}</b>`;
    }
    if (typeof data.world_transform_ready !== 'undefined') {
        html += `<br>Extrinsik: <b>${data.world_transform_ready ? 'bereit' : 'nicht bereit'}</b>`;
    }
    if (data.mode === 'EXTRINSIK') {
        html += `<br><span style="color:#9cf;">Hinweis: Im EXTRINSIK‑Modus den Joystick‑Button drücken, um die Pose zu bestimmen.</span>`;
    }
    if (data.mode === 'MANUAL' && data.joystick && typeof data.joystick.x === 'number' && typeof data.joystick.y === 'number') {
        html += `<br><br>Joystick X: <b>${data.joystick.x}</b> &nbsp; Y: <b>${data.joystick.y}</b>`;
    }
    el.innerHTML = html;

    if (data && data.mode) {
        const modeSelect = document.getElementById('mode');
        if (modeSelect && modeSelect.value !== data.mode) modeSelect.value = data.mode;
    }

    const tsRaw = data.last_capture_ts;
    const timeDiv = document.getElementById('last-capture-time');
    if (tsRaw !== null && tsRaw !== undefined) {
        const img = document.getElementById('last-capture-img');
        const empty = document.getElementById('last-capture-empty');
        const ts = Number(tsRaw);
        if (!Number.isNaN(ts)) {
            const newSrc = lastCaptureUrl(ts);
            if (img.getAttribute('data-ts') !== String(ts)) {
                img.onload = () => { img.style.display = 'block'; if (empty) empty.style.display = 'none'; };
                img.onerror = () => { img.style.display = 'none'; if (empty) empty.style.display = 'block'; };
                img.setAttribute('data-ts', String(ts));
                img.src = newSrc;
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
}
