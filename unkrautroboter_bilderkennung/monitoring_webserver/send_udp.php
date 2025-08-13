<?php
// Heartbeat-Modus: Wenn ?heartbeat=1 gesetzt ist, wird ein Heartbeat an den Pi gesendet
if (isset($_GET['heartbeat'])) {
    $udpHost = "192.168.179.252"; // IP-Adresse des Raspberry Pi
    $udpPort = 5007; // Heartbeat-Port
    $socket = socket_create(AF_INET, SOCK_DGRAM, SOL_UDP);
    if ($socket) {
        $msg = "HEARTBEAT";
        socket_sendto($socket, $msg, strlen($msg), 0, $udpHost, $udpPort);
        socket_close($socket);
        echo "OK";
    } else {
        http_response_code(500);
        echo "Fehler beim Erstellen des Sockets";
    }
    exit;
}


// Modus setzen (AUTO, MANUAL, DISTORTION)
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['mode'])) {
    $mode = strtoupper(trim($_POST['mode']));

    if (!in_array($mode, ['AUTO', 'MANUAL', 'DISTORTION'])) {
        http_response_code(400);
        echo "Ungültiger Modus";
        exit;
    }

    $udpHost = "192.168.179.252"; // IP des Raspberry Pi
    $udpPort = 5005; // Steuer-Port

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
    exit;
}

http_response_code(400);
echo "Ungültige Anfrage";