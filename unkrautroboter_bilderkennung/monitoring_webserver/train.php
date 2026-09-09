<?php
// Proxy zwischen Dashboard-Browser und dem Train/Deploy-Server auf .17 (:8090).
// GET  ?status=1        -> .17 /status (JSON, ohne Auth, read-only)
// POST train=1  + token -> .17 /train?deploy=1   (trainieren, bei Promote deployen)
// POST deploy=1 + token -> .17 /deploy           (nur letztes Modell rausschicken)

const TRAINER_URL   = 'http://192.168.179.17:8090';
// MUSS identisch zu CONTROL_TOKEN in send_udp.php / CONFIG.CONTROL_TOKEN sein
// und zu TRAINER_TOKEN in ~/lightly/trainer.env auf .17.
const CONTROL_TOKEN = 'da0fab74ab9e6556b15063bf07168541b29f7c2877547293';

header('Content-Type: application/json; charset=utf-8');

function forward(string $path, ?array $post, int $timeout): array {
    $ch = curl_init(TRAINER_URL . $path);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 4,
        CURLOPT_TIMEOUT        => $timeout,
    ]);
    if ($post !== null) {
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($post));
    }
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err  = curl_error($ch);
    curl_close($ch);
    return [$code, $body, $err];
}

$authorized = function (): bool {
    $t = $_POST['token'] ?? $_GET['token'] ?? '';
    return is_string($t) && $t !== '' && hash_equals(CONTROL_TOKEN, $t);
};

// --- status (GET) ---
if ($_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['status'])) {
    [$code, $body, $err] = forward('/status', null, 6);
    if ($code === 200 && $body !== false) { echo $body; exit; }
    http_response_code(502);
    echo json_encode(['error' => 'trainer .17 nicht erreichbar', 'detail' => $err ?: "HTTP $code"]);
    exit;
}

// --- train / deploy (POST + Token) ---
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!$authorized()) { http_response_code(403); echo json_encode(['error' => 'Nicht autorisiert']); exit; }

    if (isset($_POST['deploy']) && !isset($_POST['train'])) {
        [$code, $body, $err] = forward('/deploy', ['token' => CONTROL_TOKEN], 15);
    } else {
        $post = ['token' => CONTROL_TOKEN, 'deploy' => '1'];
        if (!empty($_POST['skip_export'])) $post['skip_export'] = '1';
        // one-off model-family switch (e.g. "yolo26s.pt"); shape-checked, .17 re-checks
        if (!empty($_POST['base']) && preg_match('/^[A-Za-z0-9._\/-]{1,80}$/', $_POST['base'])) {
            $post['base'] = $_POST['base'];
        }
        [$code, $body, $err] = forward('/train', $post, 15);
    }
    if ($code === 200) { echo json_encode(['ok' => true, 'reply' => $body]); exit; }
    if ($code === 403) { http_response_code(403); echo json_encode(['error' => 'Token von .17 abgelehnt']); exit; }
    http_response_code(502);
    echo json_encode(['error' => 'trainer .17 Fehler', 'detail' => $err ?: "HTTP $code", 'body' => $body]);
    exit;
}

http_response_code(400);
echo json_encode(['error' => 'Ungültige Anfrage']);
