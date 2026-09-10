<?php

header("Content-Type: application/json; charset=UTF-8");
header("Content-Disposition: inline");
header("X-Content-Type-Options: nosniff");

header("Access-Control-Allow-Origin: *");
header("Access-Control-Allow-Methods: POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type");


// ==============================
// OPTIONS
// ==============================

if ($_SERVER["REQUEST_METHOD"] === "OPTIONS") {

    http_response_code(200);

    echo json_encode([
        "status" => "success"
    ]);

    exit;
}


// ==============================
// POST ONLY
// ==============================

if ($_SERVER["REQUEST_METHOD"] !== "POST") {

    http_response_code(405);

    echo json_encode([
        "status" => "fail",
        "message" => "Only POST requests are allowed"
    ]);

    exit;
}


// ==============================
// READ JSON
// ==============================

$body = file_get_contents("php://input");

$data = json_decode($body, true);


// ==============================
// INVALID JSON
// ==============================

if (!is_array($data)) {

    http_response_code(400);

    echo json_encode([
        "status" => "fail",
        "message" => "Invalid JSON"
    ]);

    exit;
}


// ==============================
// USER ID
// ==============================

$user_id = isset($data["user_id"])
    ? trim((string)$data["user_id"])
    : "";


// ==============================
// BOT HASH
// ==============================

$bot_hash = isset($data["bot_hash"])
    ? trim((string)$data["bot_hash"])
    : "";


// ==============================
// CHECK USER ID
// ==============================

if ($user_id === "") {

    http_response_code(400);

    echo json_encode([
        "status" => "fail",
        "message" => "Missing field: user_id"
    ]);

    exit;
}


// ==============================
// CHECK BOT HASH
// ==============================

if ($bot_hash === "") {

    http_response_code(400);

    echo json_encode([
        "status" => "fail",
        "message" => "Missing field: bot_hash"
    ]);

    exit;
}


// ==============================
// SUCCESS TEST
// ==============================

echo json_encode([

    "status" => "success",

    "message" => "Data received successfully"

]);

exit;

?>
