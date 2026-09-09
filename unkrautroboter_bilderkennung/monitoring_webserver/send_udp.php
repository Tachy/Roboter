<?php
const PI_HOST        = '192.168.179.252';
const PORT_CONTROL   = 5005;
const PORT_JOYSTICK  = 5006;
const PORT_HEARTBEAT = 5007;

// Gemeinsames Geheimnis für steuernde Aktionen (RESET, Moduswechsel).
// ==> UNBEDINGT ändern und identisch in js/config.js (CONFIG.CONTROL_TOKEN) setzen.
// Reines LAN-Schutzmittel gegen versehentliche/fremde Requests (kein starker Schutz,
// da der Token im ausgelieferten JS sichtbar ist).
const CONTROL_TOKEN  = 'da0fab74ab9e6556b15063bf07168541b29f7c2877547293';

function controlAuthorized(): bool {
    $supplied = $_POST['token'] ?? $_GET['token'] ?? '';
    return is_string($supplied) && $supplied !== '' && hash_equals(CONTROL_TOKEN, $supplied);
}

function sendUdp(string $host, int $port, string $msg): bool {
    $sock = socket_create(AF_INET, SOCK_DGRAM, SOL_UDP);
    if (!$sock) return false;
    try {
        return socket_sendto($sock, $msg, strlen($msg), 0, $host, $port) !== false;
    } finally {
        socket_close($sock);
    }
}

// Heartbeat: ?heartbeat=1
if (isset($_GET['heartbeat'])) {
    if (sendUdp(PI_HOST, PORT_HEARTBEAT, 'HEARTBEAT')) {
        echo 'OK';
    } else {
        http_response_code(500);
        echo 'Fehler beim Senden';
    }
    exit;
}

// Neustart: POST reset=1 (+ token). Bewusst nur POST + Token (M2).
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['reset'])) {
    if (!controlAuthorized()) {
        http_response_code(403);
        echo 'Nicht autorisiert';
        exit;
    }
    if (sendUdp(PI_HOST, PORT_CONTROL, 'RESET')) {
        echo 'OK';
    } else {
        http_response_code(500);
        echo 'Fehler beim Senden';
    }
    exit;
}

// Joystick (POST joy): x,y in -100..100, optional button=1
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['joy'])) {
    $x = max(-100, min(100, intval($_POST['x'] ?? 0)));
    $y = max(-100, min(100, intval($_POST['y'] ?? 0)));
    $button = intval($_POST['button'] ?? 0) === 1;
    $msg = "JOYSTICK:X={$x},Y={$y}" . ($button ? ',B=1' : '');
    if (sendUdp(PI_HOST, PORT_JOYSTICK, $msg)) {
        echo 'OK';
    } else {
        http_response_code(500);
        echo 'Fehler beim Senden';
    }
    exit;
}

// Modus setzen (POST mode): AUTO, MANUAL, DISTORTION, EXTRINSIK
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['mode'])) {
    if (!controlAuthorized()) {
        http_response_code(403);
        echo 'Nicht autorisiert';
        exit;
    }
    $mode = strtoupper(trim($_POST['mode'] ?? ''));
    if (!in_array($mode, ['AUTO', 'MANUAL', 'DISTORTION', 'EXTRINSIK'])) {
        http_response_code(400);
        echo 'Ungültiger Modus';
        exit;
    }
    if (sendUdp(PI_HOST, PORT_CONTROL, $mode)) {
        echo 'Modus erfolgreich gesendet';
    } else {
        http_response_code(500);
        echo 'Fehler beim Senden';
    }
    exit;
}

http_response_code(400);
echo 'Ungültige Anfrage';
