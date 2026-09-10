<?php

header("Content-Type: application/json; charset=UTF-8");
header("Access-Control-Allow-Origin: *");
header("Access-Control-Allow-Methods: GET, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type, X-Admin-Password");

if ($_SERVER["REQUEST_METHOD"] === "OPTIONS") {
    http_response_code(200);
    echo json_encode([
        "status" => "success",
        "message" => "OPTIONS OK"
    ]);
    exit;
}


/*
========================================
ADMIN PASSWORD
========================================
CHANGE THIS PASSWORD
========================================
*/

$ADMIN_PASSWORD = "ADMIN";


/*
========================================
DEBUG MODE
========================================
*/

$debug = true;


/*
========================================
CHECK PASSWORD
========================================
*/

$password = "";

if (isset($_SERVER["HTTP_X_ADMIN_PASSWORD"])) {
    $password = (string)$_SERVER["HTTP_X_ADMIN_PASSWORD"];
}

if ($password === "") {

    http_response_code(401);

    echo json_encode([
        "status" => "failed",
        "error" => "MISSING_PASSWORD",
        "message" => "Admin password was not received."
    ]);

    exit;
}


if (!hash_equals($ADMIN_PASSWORD, $password)) {

    http_response_code(401);

    echo json_encode([
        "status" => "failed",
        "error" => "WRONG_PASSWORD",
        "message" => "Invalid admin password."
    ]);

    exit;
}


/*
========================================
DATA FILE
========================================
*/

$dataDir = __DIR__ . "/data";
$file = $dataDir . "/verifications.json";


/*
========================================
CHECK DATA DIRECTORY
========================================
*/

if (!is_dir($dataDir)) {

    $created = @mkdir($dataDir, 0755, true);

    if (!$created && !is_dir($dataDir)) {

        http_response_code(500);

        echo json_encode([
            "status" => "failed",
            "error" => "DATA_DIRECTORY_ERROR",
            "message" => "Cannot create data directory.",
            "path" => $dataDir
        ]);

        exit;
    }
}


/*
========================================
CREATE DATA FILE
========================================
*/

if (!file_exists($file)) {

    $created = @file_put_contents(
        $file,
        "[]",
        LOCK_EX
    );

    if ($created === false) {

        http_response_code(500);

        echo json_encode([
            "status" => "failed",
            "error" => "DATA_FILE_CREATE_ERROR",
            "message" => "Cannot create verifications.json.",
            "path" => $file
        ]);

        exit;
    }
}


/*
========================================
CHECK FILE READABLE
========================================
*/

if (!is_readable($file)) {

    http_response_code(500);

    echo json_encode([
        "status" => "failed",
        "error" => "DATA_FILE_NOT_READABLE",
        "message" => "verifications.json is not readable."
    ]);

    exit;
}


/*
========================================
READ DATA
========================================
*/

$raw = @file_get_contents($file);

if ($raw === false) {

    http_response_code(500);

    echo json_encode([
        "status" => "failed",
        "error" => "DATA_READ_ERROR",
        "message" => "Could not read verifications.json."
    ]);

    exit;
}


$records = json_decode($raw, true);


/*
========================================
CHECK JSON
========================================
*/

if (!is_array($records)) {

    http_response_code(500);

    echo json_encode([
        "status" => "failed",
        "error" => "INVALID_JSON",
        "message" => "verifications.json contains invalid JSON.",
        "json_error" => json_last_error_msg()
    ]);

    exit;
}


/*
========================================
STATISTICS
========================================
*/

$total = count($records);

$success = 0;
$failed = 0;
$active = 0;

$botStats = [];


foreach ($records as $record) {

    if (!is_array($record)) {
        continue;
    }


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
            "total" => 0,
            "success" => 0,
            "failed" => 0,
            "active" => 0
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


/*
========================================
SORT NEWEST FIRST
========================================
*/

usort($records, function($a, $b) {

    $aTime = isset($a["created_at"])
        ? (string)$a["created_at"]
        : "";

    $bTime = isset($b["created_at"])
        ? (string)$b["created_at"]
        : "";

    return strcmp($bTime, $aTime);
});


/*
========================================
FINAL RESPONSE
========================================
*/

$response = [
    "status" => "success",

    "message" => "Admin API working.",

    "stats" => [
        "total" => $total,
        "success" => $success,
        "failed" => $failed,
        "active" => $active
    ],

    "bots" => array_values($botStats),

    "records" => $records
];


if ($debug) {

    $response["debug"] = [
        "php_version" => PHP_VERSION,
        "data_directory_exists" => is_dir($dataDir),
        "data_file_exists" => file_exists($file),
        "data_file_readable" => is_readable($file),
        "data_file_size" => file_exists($file)
            ? filesize($file)
            : 0
    ];
}


echo json_encode(
    $response,
    JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
);
?>
