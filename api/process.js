import { put } from "@vercel/blob";

export default async function handler(req, res) {

    // ==============================
    // CORS
    // ==============================

    res.setHeader(
        "Access-Control-Allow-Origin",
        "*"
    );

    res.setHeader(
        "Access-Control-Allow-Methods",
        "POST, OPTIONS"
    );

    res.setHeader(
        "Access-Control-Allow-Headers",
        "Content-Type"
    );


    // ==============================
    // OPTIONS
    // ==============================

    if (req.method === "OPTIONS") {

        return res.status(200).json({
            status: "success"
        });

    }


    // ==============================
    // ONLY POST
    // ==============================

    if (req.method !== "POST") {

        return res.status(405).json({

            status: "failed",

            message:
                "Only POST requests are allowed"

        });

    }


    try {

        // ==========================
        // REQUEST DATA
        // ==========================

        let data = req.body;


        if (typeof data === "string") {

            try {

                data = JSON.parse(data);

            } catch (error) {

                return res.status(400).json({

                    status: "failed",

                    message: "Invalid JSON"

                });

            }

        }


        if (!data || typeof data !== "object") {

            return res.status(400).json({

                status: "failed",

                message:
                    "Invalid request body"

            });

        }


        // ==========================
        // FIRE.HTML DATA
        // ==========================

        const user_id =
            data.user_id !== undefined
                ? String(data.user_id).trim()
                : "";


        const bot_hash =
            data.bot_hash !== undefined
                ? String(data.bot_hash).trim()
                : "";


        const visitorId =
            data.visitorId !== undefined
                ? String(data.visitorId)
                : "";


        const device_id =
            data.device_id !== undefined
                ? String(data.device_id)
                : "";


        const user_agent =
            data.user_agent !== undefined
                ? String(data.user_agent)
                : "";


        const platform =
            data.platform !== undefined
                ? String(data.platform)
                : "";


        const language =
            data.language !== undefined
                ? String(data.language)
                : "";


        const timezone =
            data.timezone !== undefined
                ? String(data.timezone)
                : "";


        const hardware_concurrency =
            data.hardware_concurrency !== undefined
                ? String(data.hardware_concurrency)
                : "";


        const device_memory =
            data.device_memory !== undefined
                ? String(data.device_memory)
                : "";


        const screen_resolution =
            data.screen_resolution !== undefined
                ? String(data.screen_resolution)
                : "";


        // ==========================
        // REQUIRED
        // ==========================

        if (!user_id || !bot_hash) {

            return res.status(400).json({

                status: "failed",

                message:
                    "Missing user_id or bot_hash"

            });

        }


        // ==========================
        // IP
        // ==========================

        let ip = "unknown";


        if (req.headers["x-forwarded-for"]) {

            ip =
                String(
                    req.headers["x-forwarded-for"]
                )
                .split(",")[0]
                .trim();

        } else if (
            req.headers["x-real-ip"]
        ) {

            ip =
                String(
                    req.headers["x-real-ip"]
                ).trim();

        }


        // ==========================
        // UNIQUE RECORD ID
        // ==========================

        const recordId =
            Date.now().toString() +
            "_" +
            Math.random()
                .toString(36)
                .substring(2, 10);


        // ==========================
        // RECORD
        // ==========================

        const record = {

            id: recordId,

            user_id: user_id,

            bot_hash: bot_hash,

            visitorId: visitorId,

            device_id: device_id,

            user_agent: user_agent,

            platform: platform,

            language: language,

            timezone: timezone,

            hardware_concurrency:
                hardware_concurrency,

            device_memory:
                device_memory,

            screen_resolution:
                screen_resolution,

            ip: ip,

            status: "success",

            created_at:
                new Date().toISOString()

        };


        // ==========================
        // SAVE DIRECTLY TO BLOB
        // ==========================

        await put(

            "verification/" +
            bot_hash +
            "/" +
            user_id +
            "/" +
            recordId +
            ".json",

            JSON.stringify(
                record,
                null,
                2
            ),

            {
                access: "private"
            }

        );


        // ==========================
        // RESPONSE
        // ==========================

        return res.status(200).json({

            status: "success",

            message:
                "Verification successful"

        });


    } catch (error) {

        console.error(
            "PROCESS ERROR:",
            error
        );


        return res.status(500).json({

            status: "failed",

            message:
                "Storage error"

        });

    }

    }
