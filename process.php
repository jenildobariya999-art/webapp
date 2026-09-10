<?php
declare(strict_types=1);

/**
 * Local audit endpoint for the attached verification page.
 *
 * This endpoint deliberately does not:
 *   - send data to Telegram, email, webhooks, or any other external service;
 *   - accept or store passwords, OTPs, verification codes, tokens, or initData;
 *   - make an authentication/verification decision.
 *
 * It records only the names, types, and lengths of received fields in a local
 * JSONL file. The fingerprint itself is represented by a one-way hash.
 */

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function respond(int $statusCode, array $body): never
{
    http_response_code($statusCode);
    echo json_encode($body, JSON_UNESCAPED_SLASHES);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    respond(405, [
        'status' => 'rejected',
        'message' => 'Only POST requests are accepted.',
    ]);
}

$contentType = strtolower((string) ($_SERVER['CONTENT_TYPE'] ?? ''));
if (!str_contains($contentType, 'application/json')) {
    respond(415, [
        'status' => 'rejected',
        'message' => 'Expected an application/json request body.',
    ]);
}

$rawBody = file_get_contents('php://input');
if ($rawBody === false || strlen($rawBody) > 32 * 1024) {
    respond(413, [
        'status' => 'rejected',
        'message' => 'Request body is missing or too large.',
    ]);
}

$payload = json_decode($rawBody, true);
if (!is_array($payload)) {
    respond(400, [
        'status' => 'rejected',
        'message' => 'Request body must be a JSON object.',
    ]);
}

// Never process credential-like data through this audit endpoint.
$blockedKeys = [
    'code',
    'otp',
    'verification_code',
    'verificationCode',
    'password',
    'passcode',
    'token',
    'access_token',
    'refresh_token',
    'secret',
    'initData',
    'init_data',
    'phone',
    'phone_number',
    'webhook',
    'webhook_url',
];

$blocked = array_values(array_intersect(array_keys($payload), $blockedKeys));
if ($blocked !== []) {
    respond(400, [
        'status' => 'rejected',
        'message' => 'Credential-like fields are not accepted.',
        'blocked_fields' => $blocked,
    ]);
}

// These cover both the attached browser page and the attached bot export.
$allowedKeys = [
    'user_id',
    'bot',
    'bot_hash',
    'botusername',
    'hash',
    'fingerprint',
    'device_id',
    'user_agent',
    'platform',
    'language',
    'timezone',
    'hardware_concurrency',
    'device_memory',
    'screen_resolution',
];

$fingerprint = '';
if (isset($payload['fingerprint']) && is_scalar($payload['fingerprint'])) {
    $fingerprint = trim((string) $payload['fingerprint']);
} elseif (isset($payload['device_id']) && is_scalar($payload['device_id'])) {
    $fingerprint = trim((string) $payload['device_id']);
}

if ($fingerprint === '') {
    respond(400, [
        'status' => 'fail',
        'message' => 'Fingerprint Missing',
    ]);
}

$received = [];
foreach ($payload as $key => $value) {
    if (!in_array($key, $allowedKeys, true)) {
        continue;
    }

    $received[$key] = [
        'type' => get_debug_type($value),
        'length' => is_string($value) ? strlen($value) : null,
    ];
}

$record = [
    'received_at' => gmdate('c'),
    'method' => $_SERVER['REQUEST_METHOD'],
    'content_type' => $contentType,
    'fingerprint_sha256' => hash('sha256', $fingerprint),
    'fields' => $received,
];

$auditPath = __DIR__ . DIRECTORY_SEPARATOR . 'process-audit.jsonl';
$written = file_put_contents(
    $auditPath,
    json_encode($record, JSON_UNESCAPED_SLASHES) . PHP_EOL,
    FILE_APPEND | LOCK_EX
);

if ($written === false) {
    respond(500, [
        'status' => 'error',
        'message' => 'Could not write the local audit record.',
    ]);
}

// Do not return "Verified Successfully": the client cannot authenticate itself.
respond(200, [
    'status' => 'audit_only',
    'active' => false,
    'received_fields' => array_keys($received),
    'message' => 'Fingerprint received for local audit; no Telegram verification was granted.',
]);