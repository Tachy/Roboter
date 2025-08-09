<?php
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['mode'])) {
    $mode = strtoupper(trim($_POST['mode']));

    // Überprüfen, ob der Modus gültig ist
    if (!in_array($mode, ['AUTO', 'MANUAL'])) {
        http_response_code(400);
        echo "Ungültiger Modus";
        exit;
    }

    // Raspberry Pi UDP-Konfiguration
    $udpHost = "192.168.179.252"; // IP-Adresse des Raspberry Pi
    $udpPort = 5005; // UDP-Port des Raspberry Pi

    // UDP-Socket erstellen und Nachricht senden
    $socket = socket_create(AF_INET, SOCK_DGRAM, SOL_UDP);
    if (!$socket) {
        http_response_code(500);
        echo "Fehler beim Erstellen des Sockets";
        exit;
    }

    $sent = socket_sendto($socket, $mode, strlen($mode), 0, $udpHost, $udpPort);
    socket_close($socket);

    if ($sent === false) {
        http_response_code(500);
        echo "Fehler beim Senden der Nachricht";
    } else {
        echo "Modus erfolgreich gesendet";
    }
} else {
    http_response_code(400);
    echo "Ungültige Anfrage";
}