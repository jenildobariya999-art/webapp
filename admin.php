<?php

header("Content-Type: application/json; charset=UTF-8");

$ADMIN_PASSWORD = "JENILBRO";

$password = isset($_SERVER["HTTP_X_ADMIN_PASSWORD"])
    ? $_SERVER["HTTP_X_ADMIN_PASSWORD"]
    : "";

if (!hash_equals($ADMIN_PASSWORD, $password)) {
    http_response_code(401);

    echo json_encode([
        "status" => "failed",
        "message" => "Unauthorized"
    ]);

    exit;
}

$file = __DIR__ . "/data/verifications.json";

if (!file_exists($file)) {
    echo json_encode([
        "status" => "success",
        "stats" => [
            "total" => 0,
            "success" => 0,
            "failed" => 0,
            "active" => 0
        ],
        "bots" => [],
        "records" => []
    ]);

    exit;
}

$records = json_decode(file_get_contents($file), true);

if (!is_array($records)) {
    $records = [];
}

$total = count($records);
$success = 0;
$failed = 0;
$active = 0;

$botStats = [];

foreach ($records as $record) {

    $status = isset($record["status"])
        ? strtolower((string)$record["status"])
        : "unknown";

    if ($status === "success") {
        $success++;
    }

    if ($status === "failed") {
        $failed++;
    }

    if ($status === "active") {
        $active++;
    }

    $bot = isset($record["bot_hash"])
        ? (string)$record["bot_hash"]
        : "unknown";

    if (!isset($botStats[$bot])) {
        $botStats[$bot] = [
            "bot_hash" => $bot,
            "success" => 0,
            "failed" => 0,
            "active" => 0,
            "total" => 0
        ];
    }

    $botStats[$bot]["total"]++;

    if ($status === "success") {
        $botStats[$bot]["success"]++;
    }

    if ($status === "failed") {
        $botStats[$bot]["failed"]++;
    }

    if ($status === "active") {
        $botStats[$bot]["active"]++;
    }
}

$bots = array_values($botStats);

usort($records, function($a, $b) {

    $aTime = isset($a["created_at"])
        ? $a["created_at"]
        : "";

    $bTime = isset($b["created_at"])
        ? $b["created_at"]
        : "";

    return strcmp($bTime, $aTime);
});

echo json_encode([
    "status" => "success",

    "stats" => [
        "total" => $total,
        "success" => $success,
        "failed" => $failed,
        "active" => $active
    ],

    "bots" => $bots,

    "records" => $records
], JSON_UNESCAPED_SLASHES);
