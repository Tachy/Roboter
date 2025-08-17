// Heartbeat an den PHP-Proxy senden, solange die Seite offen ist
function sendHeartbeat() {
    fetch('send_udp.php?heartbeat=1').catch(() => { });
    setTimeout(sendHeartbeat, 2000);
}
window.addEventListener('load', sendHeartbeat);
