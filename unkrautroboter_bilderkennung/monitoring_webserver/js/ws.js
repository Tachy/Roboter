// WebSocket für Statusdaten mit Reconnect-Backoff
let ws = null;
let wsReconnectDelayMs = 1000;
let wsReconnectTimer = null;

function scheduleWsReconnect() {
    if (wsReconnectTimer) return;
    wsReconnectTimer = setTimeout(() => {
        wsReconnectTimer = null;
        startStatusWebSocket();
    }, wsReconnectDelayMs);
    wsReconnectDelayMs = Math.min(Math.floor(wsReconnectDelayMs * 1.7), 15000);
}

function startStatusWebSocket() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    const url = wsUrl();
    try {
        ws = new WebSocket(url);
    } catch (e) {
        updateStatusBox(null);
        scheduleWsReconnect();
        return;
    }
    ws.onopen = () => {
        wsReconnectDelayMs = 1000;
        if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
        updateStatusBox({ mode: '-', stream: false, cpu_temp: null, timestamp: null });
    };
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateStatusBox(data);
        } catch (_) {
            updateStatusBox(null);
        }
    };
    ws.onerror = () => {
        updateStatusBox(null);
        scheduleWsReconnect();
    };
    ws.onclose = () => {
        updateStatusBox(null);
        scheduleWsReconnect();
    };
}

window.addEventListener('beforeunload', () => {
    if (ws) { try { ws.close(); } catch (_) { } }
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
});

window.addEventListener('load', () => {
    // Platzhalter-Status initial, WebSocket starten
    lastPlaceholderVisible = false;
    startStatusWebSocket();
});
