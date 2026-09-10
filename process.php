<?php

header("Content-Type: application/json; charset=UTF-8");
header("Access-Control-Allow-Origin: *");
header("Access-Control-Allow-Methods: POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type");

if ($_SERVER["REQUEST_METHOD"] === "OPTIONS") {
    http_response_code(200);
    exit;
}

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    echo json_encode([
        "status" => "failed",
        "message" => "Only POST requests are allowed"
    ]);
    exit;
}

$raw = file_get_contents("php://input");
$data = json_decode($raw, true);

if (!is_array($data)) {
    echo json_encode([
        "status" => "failed",
        "message" => "Invalid JSON"
    ]);
    exit;
}

$user_id = isset($data["user_id"]) ? trim((string)$data["user_id"]) : "";
$bot_hash = isset($data["bot_hash"]) ? trim((string)$data["bot_hash"]) : "";

if ($user_id === "" || $bot_hash === "") {
    echo json_encode([
        "status" => "failed",
        "message" => "Missing user_id or bot_hash"
    ]);
    exit;
}

$dataDir = __DIR__ . "/data";
$file = $dataDir . "/verifications.json";

if (!is_dir($dataDir)) {
    mkdir($dataDir, 0755, true);
}

if (!file_exists($file)) {
    file_put_contents($file, "[]");
}

$records = json_decode(file_get_contents($file), true);

if (!is_array($records)) {
    $records = [];
}

$device_id = isset($data["device_id"]) ? (string)$data["device_id"] : "";
$visitor_id = isset($data["visitorId"]) ? (string)$data["visitorId"] : "";

$existing = false;

foreach ($records as $record) {
    if (
        isset($record["user_id"]) &&
        isset($record["bot_hash"]) &&
        (string)$record["user_id"] === $user_id &&
        (string)$record["bot_hash"] === $bot_hash
    ) {
        $existing = true;
        break;
    }
}

if ($existing) {
    echo json_encode([
        "status" => "active",
        "message" => "Already verified"
    ]);
    exit;
}

$ip = isset($_SERVER["REMOTE_ADDR"])
    ? $_SERVER["REMOTE_ADDR"]
    : "unknown";

$newRecord = [
    "id" => uniqid("verify_", true),
    "user_id" => $user_id,
    "bot_hash" => $bot_hash,

    "visitorId" => $visitor_id,
    "device_id" => $device_id,

    "user_agent" => isset($data["user_agent"]) ? (string)$data["user_agent"] : "",
    "platform" => isset($data["platform"]) ? (string)$data["platform"] : "",
    "language" => isset($data["language"]) ? (string)$data["language"] : "",
    "timezone" => isset($data["timezone"]) ? (string)$data["timezone"] : "",
    "hardware_concurrency" => isset($data["hardware_concurrency"]) ? (string)$data["hardware_concurrency"] : "",
    "device_memory" => isset($data["device_memory"]) ? (string)$data["device_memory"] : "",
    "screen_resolution" => isset($data["screen_resolution"]) ? (string)$data["screen_resolution"] : "",

    "ip" => $ip,
    "status" => "success",
    "created_at" => date("Y-m-d H:i:s")
];

$records[] = $newRecord;

file_put_contents(
    $file,
    json_encode($records, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES),
    LOCK_EX
);

echo json_encode([
    "status" => "success",
    "message" => "Verification successful"
]);
