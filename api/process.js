<?php
/**
 * Jenil Dobariya Multi-Bot Device Verification Gateway (Vercel Serverless Ready)
 * Path: api/process.php (or verificationFolder/process.php)
 * 
 * Securely fetches DB credentials directly from Vercel Environment Variables:
 * - MYSQL_HOST / DB_HOST
 * - MYSQL_DATABASE / DB_NAME
 * - MYSQL_USER / DB_USER
 * - MYSQL_PASSWORD / DB_PASSWORD
 * - MYSQL_PORT / DB_PORT (Default: 3306)
 * Or handles standard connection strings: DATABASE_URL / MYSQL_URL
 */

// 1. Strict Error Handling & CORS Headers for WebApp / Bots
error_reporting(0);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With');

// Preflight CORS handler
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode([
        'status'  => 'failed',
        'message' => 'Method Not Allowed. POST required.'
    ]);
    exit;
}

// 2. Parse Incoming JSON Payload from script.js
$rawInput = file_get_contents('php://input');
$data = json_decode($rawInput, true);

if (!$data || !is_array($data)) {
    http_response_code(400);
    echo json_encode([
        'status'  => 'failed',
        'message' => 'Invalid JSON payload received.'
    ]);
    exit;
}

// Extract verification payload parameters
$userId              = isset($data['user_id']) ? trim((string)$data['user_id']) : '';
$deviceId            = isset($data['device_id']) ? trim((string)$data['device_id']) : '';
$botUsername         = isset($data['botusername']) ? trim((string)$data['botusername']) : (isset($_GET['botusername']) ? trim((string)$_GET['botusername']) : '');
$botHash             = isset($data['bot_hash']) ? trim((string)$data['bot_hash']) : (isset($_GET['hash']) ? trim((string)$_GET['hash']) : '');
$webhookUrl          = isset($data['webhook']) ? trim((string)$data['webhook']) : (isset($_GET['webhook']) ? trim((string)$_GET['webhook']) : '');

// Device telemetry
$userAgent           = isset($data['user_agent']) ? trim((string)$data['user_agent']) : '';
$platform            = isset($data['platform']) ? trim((string)$data['platform']) : '';
$language            = isset($data['language']) ? trim((string)$data['language']) : '';
$timezone            = isset($data['timezone']) ? trim((string)$data['timezone']) : '';
$hardwareConcurrency = isset($data['hardware_concurrency']) ? trim((string)$data['hardware_concurrency']) : '';
$deviceMemory        = isset($data['device_memory']) ? trim((string)$data['device_memory']) : '';
$screenResolution    = isset($data['screen_resolution']) ? trim((string)$data['screen_resolution']) : '';

// Resolve Client IP (Cloudflare & Vercel forwarded IP headers)
$clientIp = $_SERVER['HTTP_CF_CONNECTING_IP'] 
    ?? $_SERVER['HTTP_X_REAL_IP']
    ?? $_SERVER['HTTP_X_FORWARDED_FOR'] 
    ?? $_SERVER['REMOTE_ADDR'] 
    ?? '0.0.0.0';
if (strpos($clientIp, ',') !== false) {
    $parts = explode(',', $clientIp);
    $clientIp = trim($parts[0]);
}

// 3. Mandatory Security & Validation Checks
if (empty($userId) || empty($deviceId)) {
    echo json_encode([
        'status'  => 'failed',
        'message' => 'Verification criteria not met: Missing User ID or Device ID.'
    ]);
    exit;
}

if (strlen($deviceId) < 8) {
    echo json_encode([
        'status'  => 'failed',
        'message' => 'Verification criteria not met: Invalid fingerprint.'
    ]);
    exit;
}

// VPN / Threat Header Check (Cloudflare headers)
$isVpn = false;
if (isset($_SERVER['HTTP_CF_IPCOUNTRY']) && in_array($_SERVER['HTTP_CF_IPCOUNTRY'], ['T1', 'XX'])) {
    $isVpn = true;
}

if ($isVpn) {
    sendWebhookToBot($webhookUrl, [
        'status'      => 'fail',
        'message'     => 'VPN Detected',
        'fingerprint' => $deviceId,
        'botusername' => $botUsername,
        'user_id'     => $userId
    ]);

    echo json_encode([
        'status'  => 'failed',
        'message' => 'VPN Detected. Please disable VPN and retry.'
    ]);
    exit;
}

// =========================================================================
// 4. FETCH CREDENTIALS DIRECTLY FROM VERCEL ENVIRONMENT VARIABLES
// =========================================================================

/**
 * Helper function to safely read env variables across Vercel / server environments
 */
function getEnvVar($key, $default = '') {
    $val = getenv($key);
    if ($val !== false && $val !== '') return $val;
    if (isset($_ENV[$key]) && $_ENV[$key] !== '') return $_ENV[$key];
    if (isset($_SERVER[$key]) && $_SERVER[$key] !== '') return $_SERVER[$key];
    return $default;
}

// Check for single URI connection string (e.g. Aiven / PlanetScale / Railway / Neon)
$dbUrl = getEnvVar('DATABASE_URL') ?: getEnvVar('MYSQL_URL');

if (!empty($dbUrl)) {
    // Parse URI e.g. mysql://user:password@host:port/dbname
    $parsedUrl = parse_url($dbUrl);
    $dbHost = $parsedUrl['host'] ?? '127.0.0.1';
    $dbPort = $parsedUrl['port'] ?? 3306;
    $dbUser = $parsedUrl['user'] ?? '';
    $dbPass = $parsedUrl['pass'] ?? '';
    $dbName = ltrim($parsedUrl['path'] ?? '', '/');
} else {
    // Read individual standard Vercel environment variables
    $dbHost = getEnvVar('MYSQL_HOST') ?: getEnvVar('DB_HOST', '127.0.0.1');
    $dbPort = getEnvVar('MYSQL_PORT') ?: getEnvVar('DB_PORT', '3306');
    $dbName = getEnvVar('MYSQL_DATABASE') ?: getEnvVar('DB_NAME', 'jenil_verification');
    $dbUser = getEnvVar('MYSQL_USER') ?: getEnvVar('DB_USER', 'root');
    $dbPass = getEnvVar('MYSQL_PASSWORD') ?: getEnvVar('DB_PASSWORD', '');
}

try {
    // Connect to MySQL with SSL / TLS support (essential for cloud databases like TiDB, Aiven, PlanetScale)
    $dsn = "mysql:host={$dbHost};port={$dbPort};dbname={$dbName};charset=utf8mb4";
    $options = [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_TIMEOUT            => 5,
    ];

    // Enable SSL if running on cloud DB (PlanetScale / TiDB / Aiven / Supabase)
    if (defined('PDO::MYSQL_ATTR_SSL_CA')) {
        // Some cloud providers require SSL verification
        $sslCa = getEnvVar('MYSQL_SSL_CA');
        if (!empty($sslCa) && file_exists($sslCa)) {
            $options[PDO::MYSQL_ATTR_SSL_CA] = $sslCa;
        }
    }

    $pdo = new PDO($dsn, $dbUser, $dbPass, $options);

    // Ensure table exists on the connected database
    $pdo->exec("CREATE TABLE IF NOT EXISTS bot_device_verifications (
        id INT AUTO_INCREMENT PRIMARY KEY,
        device_id VARCHAR(128) NOT NULL,
        user_id VARCHAR(64) NOT NULL,
        bot_username VARCHAR(128) DEFAULT NULL,
        bot_hash VARCHAR(64) DEFAULT NULL,
        ip_address VARCHAR(45) DEFAULT NULL,
        user_agent TEXT DEFAULT NULL,
        platform VARCHAR(64) DEFAULT NULL,
        language VARCHAR(32) DEFAULT NULL,
        timezone VARCHAR(64) DEFAULT NULL,
        hardware_concurrency VARCHAR(16) DEFAULT NULL,
        device_memory VARCHAR(16) DEFAULT NULL,
        screen_resolution VARCHAR(32) DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_dev (device_id),
        INDEX idx_usr (user_id),
        INDEX idx_dev_usr (device_id, user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;");

    // =========================================================================
    // 5. FRAUD CHECKS & TELEGRAM MULTI-BOT WEBHOOK HANDLING
    // =========================================================================

    // CHECK 1: User returning on the exact same verified device (Already Verified)
    $checkSelf = $pdo->prepare("SELECT id FROM bot_device_verifications WHERE device_id = :device_id AND user_id = :user_id LIMIT 1");
    $checkSelf->execute([
        ':device_id' => $deviceId,
        ':user_id'   => $userId
    ]);

    if ($checkSelf->fetch()) {
        sendWebhookToBot($webhookUrl, [
            'status'      => 'pass',
            'message'     => 'Already Verified',
            'fingerprint' => $deviceId,
            'botusername' => $botUsername,
            'user_id'     => $userId
        ]);

        echo json_encode([
            'status'  => 'continue',
            'message' => 'Device already verified.'
        ]);
        exit;
    }

    // CHECK 2: Same physical device used by another Telegram account (Anti-Clone / Multi-Account Fraud)
    $checkOther = $pdo->prepare("SELECT user_id FROM bot_device_verifications WHERE device_id = :device_id LIMIT 1");
    $checkOther->execute([':device_id' => $deviceId]);
    $otherRecord = $checkOther->fetch();

    if ($otherRecord && $otherRecord['user_id'] !== $userId) {
        sendWebhookToBot($webhookUrl, [
            'status'      => 'fail',
            'message'     => 'Device already used',
            'fingerprint' => $deviceId,
            'botusername' => $botUsername,
            'user_id'     => $userId
        ]);

        echo json_encode([
            'status'  => 'attempt',
            'message' => 'Device already used on another account.'
        ]);
        exit;
    }

    // CHECK 3: Brand new device -> Insert verification record
    $insertStmt = $pdo->prepare("INSERT INTO bot_device_verifications (
        device_id, user_id, bot_username, bot_hash, ip_address, 
        user_agent, platform, language, timezone, hardware_concurrency, device_memory, screen_resolution
    ) VALUES (
        :device_id, :user_id, :bot_username, :bot_hash, :ip_address, 
        :user_agent, :platform, :language, :timezone, :hardware_concurrency, :device_memory, :screen_resolution
    )");

    $insertStmt->execute([
        ':device_id'            => $deviceId,
        ':user_id'              => $userId,
        ':bot_username'         => $botUsername,
        ':bot_hash'             => $botHash,
        ':ip_address'           => $clientIp,
        ':user_agent'           => $userAgent,
        ':platform'             => $platform,
        ':language'             => $language,
        ':timezone'             => $timezone,
        ':hardware_concurrency' => $hardwareConcurrency,
        ':device_memory'        => $deviceMemory,
        ':screen_resolution'    => $screenResolution
    ]);

    // Send pass webhook callback back to bot
    sendWebhookToBot($webhookUrl, [
        'status'      => 'pass',
        'message'     => 'Verified Successfully',
        'fingerprint' => $deviceId,
        'botusername' => $botUsername,
        'user_id'     => $userId
    ]);

    echo json_encode([
        'status'  => 'success',
        'message' => 'Device verified successfully.'
    ]);
    exit;

} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        'status'  => 'failed',
        'message' => 'Database connection failed: ' . $e->getMessage()
    ]);
    exit;
}

/**
 * Dispatch HTTP POST JSON webhook callback to the requesting Telegram bot
 */
function sendWebhookToBot($url, $payload) {
    if (empty($url) || !filter_var($url, FILTER_VALIDATE_URL)) {
        return false;
    }

    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Content-Type: application/json',
        'User-Agent: Jenil-Dobariya-Verification-Gateway/2.0'
    ]);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 4);
    curl_setopt($ch, CURLOPT_TIMEOUT, 6);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    
    $result = curl_exec($ch);
    curl_close($ch);
    return $result;
}
?>
