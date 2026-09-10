const crypto = require("node:crypto");

const allowedFields = new Set([
  "user_id",
  "bot",
  "bot_hash",
  "botusername",
  "hash",
  "fingerprint",
  "device_id",
  "user_agent",
  "platform",
  "language",
  "timezone",
  "hardware_concurrency",
  "device_memory",
  "screen_resolution",
]);

const blockedFields = new Set([
  "code",
  "otp",
  "verification_code",
  "verificationCode",
  "password",
  "passcode",
  "token",
  "access_token",
  "refresh_token",
  "secret",
  "initData",
  "init_data",
  "phone",
  "phone_number",
  "webhook",
  "webhook_url",
]);

function response(statusCode, body) {
  return {
    statusCode,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin":
        "https://regal-frangipane-18271b.netlify.app",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
    body: JSON.stringify(body),
  };
}

exports.handler = async (event) => {
  if (event.httpMethod === "OPTIONS") {
    return response(204, {});
  }

  if (event.httpMethod !== "POST") {
    return response(405, {
      status: "rejected",
      message: "Only POST requests are accepted.",
    });
  }

  const contentType = String(
    event.headers?.["content-type"] ||
      event.headers?.["Content-Type"] ||
      "",
  ).toLowerCase();

  if (!contentType.includes("application/json")) {
    return response(415, {
      status: "rejected",
      message: "Expected an application/json request body.",
    });
  }

  let payload;
  try {
    const body = event.isBase64Encoded
      ? Buffer.from(event.body || "", "base64").toString("utf8")
      : event.body || "";
    payload = JSON.parse(body);
  } catch {
    return response(400, {
      status: "rejected",
      message: "Request body must be valid JSON.",
    });
  }

  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    return response(400, {
      status: "rejected",
      message: "Request body must be a JSON object.",
    });
  }

  const blocked = Object.keys(payload).filter((key) => blockedFields.has(key));
  if (blocked.length > 0) {
    return response(400, {
      status: "rejected",
      message: "Credential-like fields are not accepted.",
      blocked_fields: blocked,
    });
  }

  const fingerprintValue =
    typeof payload.fingerprint === "string"
      ? payload.fingerprint.trim()
      : typeof payload.device_id === "string"
        ? payload.device_id.trim()
        : "";

  if (!fingerprintValue) {
    return response(400, {
      status: "fail",
      message: "Fingerprint Missing",
    });
  }

  const receivedFields = {};
  for (const [key, value] of Object.entries(payload)) {
    if (!allowedFields.has(key)) continue;
    receivedFields[key] = {
      type: Array.isArray(value) ? "array" : typeof value,
      length: typeof value === "string" ? value.length : null,
    };
  }

  // Netlify Function logs are the audit trail. Raw fingerprint values are not logged.
  console.log(
    JSON.stringify({
      received_at: new Date().toISOString(),
      fingerprint_sha256: crypto
        .createHash("sha256")
        .update(fingerprintValue)
        .digest("hex"),
      fields: receivedFields,
    }),
  );

  return response(200, {
    status: "audit_only",
    active: false,
    received_fields: Object.keys(receivedFields),
    message:
      "Fingerprint received for local audit; no Telegram verification was granted.",
  });
};