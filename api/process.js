let records = globalThis.__verification_records;

if (!records) {
    records = [];
    globalThis.__verification_records = records;
}

export default function handler(req, res) {

    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    res.setHeader(
        "Access-Control-Allow-Headers",
        "Content-Type"
    );

    if (req.method === "OPTIONS") {
        return res.status(200).json({
            status: "success",
            message: "OPTIONS OK"
        });
    }

    if (req.method !== "POST") {
        return res.status(405).json({
            status: "failed",
            message: "Only POST requests are allowed"
        });
    }

    try {

        const data = req.body || {};

        const user_id =
            data.user_id !== undefined
                ? String(data.user_id).trim()
                : "";

        const bot_hash =
            data.bot_hash !== undefined
                ? String(data.bot_hash).trim()
                : "";

        if (!user_id || !bot_hash) {

            return res.status(400).json({
                status: "failed",
                message: "Missing user_id or bot_hash"
            });
        }


        /*
        ========================================
        CHECK EXISTING USER
        ========================================
        */

        const existing = records.find(function(record) {

            return (
                String(record.user_id) === user_id &&
                String(record.bot_hash) === bot_hash
            );

        });


        if (existing) {

            return res.status(200).json({
                status: "active",
                message: "Already verified"
            });
        }


        /*
        ========================================
        GET IP
        ========================================
        */

        let ip = "unknown";

        if (req.headers["x-forwarded-for"]) {

            ip = String(
                req.headers["x-forwarded-for"]
            ).split(",")[0].trim();

        } else if (req.socket && req.socket.remoteAddress) {

            ip = String(
                req.socket.remoteAddress
            );

        }


        /*
        ========================================
        CREATE RECORD
        ========================================
        */

        const record = {

            id:
                "verify_" +
                Date.now() +
                "_" +
                Math.random()
                    .toString(36)
                    .substring(2, 10),

            user_id: user_id,

            bot_hash: bot_hash,

            visitorId:
                data.visitorId !== undefined
                    ? String(data.visitorId)
                    : "",

            device_id:
                data.device_id !== undefined
                    ? String(data.device_id)
                    : "",

            user_agent:
                data.user_agent !== undefined
                    ? String(data.user_agent)
                    : "",

            platform:
                data.platform !== undefined
                    ? String(data.platform)
                    : "",

            language:
                data.language !== undefined
                    ? String(data.language)
                    : "",

            timezone:
                data.timezone !== undefined
                    ? String(data.timezone)
                    : "",

            hardware_concurrency:
                data.hardware_concurrency !== undefined
                    ? String(data.hardware_concurrency)
                    : "",

            device_memory:
                data.device_memory !== undefined
                    ? String(data.device_memory)
                    : "",

            screen_resolution:
                data.screen_resolution !== undefined
                    ? String(data.screen_resolution)
                    : "",

            ip: ip,

            status: "success",

            created_at:
                new Date().toISOString()
        };


        records.push(record);


        /*
        ========================================
        RESPONSE
        ========================================
        */

        return res.status(200).json({

            status: "success",

            message: "Verification successful"

        });

    } catch (error) {

        return res.status(500).json({

            status: "failed",

            message: "Server error",

            error: String(error.message || error)

        });

    }

          }
