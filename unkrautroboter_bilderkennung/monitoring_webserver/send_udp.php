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

// Neustart anstoßen: ?reset=1 per GET – sendet UDP "RESET" an den Pi
if (isset($_GET['reset'])) {
    $udpHost = "192.168.179.252"; // IP-Adresse des Raspberry Pi
    $udpPort = 5005; // Steuer-Port
    $socket = socket_create(AF_INET, SOCK_DGRAM, SOL_UDP);
    if ($socket) {
        $msg = "RESET"; // einheitlicher Steuerbefehl
        socket_sendto($socket, $msg, strlen($msg), 0, $udpHost, $udpPort);
        socket_close($socket);
        echo "OK";
    } else {
        http_response_code(500);
        echo "Fehler beim Erstellen des Sockets";
    }
    exit;
}

    // Virtuelles Joystick-Forwarding (POST): x,y in -100..100, optional button=1 (sent as B=1)
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['joy'])) {
    $x = isset($_POST['x']) ? intval($_POST['x']) : 0;
    $y = isset($_POST['y']) ? intval($_POST['y']) : 0;
    $button = (isset($_POST['button']) && intval($_POST['button']) === 1) ? true : false;

    // Clamp to -100..100
    $x = max(-100, min(100, $x));
    $y = max(-100, min(100, $y));

    $udpHost = "192.168.179.252"; // IP des Raspberry Pi
    $udpPort = 5006; // Joystick-Port

    $socket = socket_create(AF_INET, SOCK_DGRAM, SOL_UDP);
    if (!$socket) {
        http_response_code(500);
        echo "Fehler beim Erstellen des Sockets";
        exit;
    }

    $msg = "JOYSTICK:X={$x},Y={$y}";
    if ($button) {
        // Use the compact token format expected by the Pi/Arduino: B=1
        $msg .= ",B=1";
    }

    $sent = socket_sendto($socket, $msg, strlen($msg), 0, $udpHost, $udpPort);
    socket_close($socket);

    if ($sent === false) {
        http_response_code(500);
        echo "Fehler beim Senden der Nachricht";
    } else {
        echo "OK";
    }
    exit;
}


// Modus setzen (AUTO, MANUAL, DISTORTION, EXTRINSIK)
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['mode'])) {
    $mode = strtoupper(trim($_POST['mode']));

    if (!in_array($mode, ['AUTO', 'MANUAL', 'DISTORTION', 'EXTRINSIK'])) {
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
