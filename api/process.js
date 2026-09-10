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
    // POST ONLY
    // ==============================

    if (req.method !== "POST") {

        return res.status(405).json({

            status: "fail",

            message:
                "Only POST requests are allowed"

        });

    }


    try {

        // ==========================
        // GET BODY
        // ==========================

        let data = req.body;


        if (typeof data === "string") {

            try {

                data = JSON.parse(data);

            } catch (error) {

                return res.status(400).json({

                    status: "fail",

                    message:
                        "Invalid JSON"

                });

            }

        }


        if (!data || typeof data !== "object") {

            return res.status(400).json({

                status: "fail",

                message:
                    "Invalid request body"

            });

        }


        // ==========================
        // USER ID
        // ==========================

        const user_id =
            data.user_id !== undefined
                ? String(data.user_id).trim()
                : "";


        if (!user_id) {

            return res.status(400).json({

                status: "fail",

                message:
                    "Missing field: user_id"

            });

        }


        // ==========================
        // BOT HASH
        // ==========================

        const bot_hash =
            data.bot_hash !== undefined
                ? String(data.bot_hash).trim()
                : "";


        if (!bot_hash) {

            return res.status(400).json({

                status: "fail",

                message:
                    "Missing field: bot_hash"

            });

        }


        // ==========================
        // RESPONSE
        // ==========================

        return res.status(200).json({

            status: "success",

            message:
                "Verification data received",

            user_id:
                user_id,

            bot_hash:
                bot_hash

        });


    } catch (error) {

        console.error(
            "API ERROR:",
            error
        );


        return res.status(500).json({

            status: "fail",

            message:
                "Internal server error"

        });

    }

}
