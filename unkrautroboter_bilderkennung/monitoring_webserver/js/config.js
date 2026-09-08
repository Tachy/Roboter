// Zentrale Konfiguration für IP/Ports und URL-Helfer
// Hinweis: Bei leerem HOST wird automatisch der Hostname der Seite verwendet
const CONFIG = {
    HOST: '192.168.179.252',
    HTTP_PORT: 8080,
    WS_PORT: 8765,
    // Muss identisch zu CONTROL_TOKEN in send_udp.php sein (für RESET / Moduswechsel).
    CONTROL_TOKEN: 'r0b0t-4b7f9c1e2d6a8054-change-me',
};

function effectiveHost() {
    return CONFIG.HOST && CONFIG.HOST.trim() !== '' ? CONFIG.HOST : window.location.hostname;
}

function streamUrl() {
    return `http://${effectiveHost()}:${CONFIG.HTTP_PORT}/stream`;
}

function lastCaptureUrl(ts) {
    return `http://${effectiveHost()}:${CONFIG.HTTP_PORT}/last_capture.jpg?ts=${ts}`;
}

function wsUrl() {
    const proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    return `${proto}://${effectiveHost()}:${CONFIG.WS_PORT}`;
}
