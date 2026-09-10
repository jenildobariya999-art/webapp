export default async function handler(req, res) {

    // ==========================================
    // CORS
    // ==========================================

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


    if (req.method === "OPTIONS") {

        return res.status(200).json({
            status: "success"
        });

    }


    // ==========================================
    // METHOD
    // ==========================================

    if (req.method !== "POST") {

        return res.status(405).json({
            status: "fail",
            message: "Only POST requests are allowed"
        });

    }


    try {

        // ======================================
        // BODY
        // ======================================

        let data = req.body;


        if (typeof data === "string") {

            try {

                data = JSON.parse(data);

            } catch (error) {

                return res.status(400).json({
                    status: "fail",
                    message: "Invalid JSON"
                });

            }

        }


        if (
            !data ||
            typeof data !== "object"
        ) {

            return res.status(400).json({
                status: "fail",
                message: "Invalid request body"
            });

        }


        // ======================================
        // INPUTS
        // ======================================

        const user_id =
            data.user_id !== undefined
                ? String(data.user_id).trim()
                : "";


        const botusername =
            data.botusername !== undefined
                ? String(data.botusername).trim()
                : "";


        const hash =
            data.hash !== undefined
                ? String(data.hash).trim()
                : "";


        const webhook =
            data.webhook !== undefined
                ? String(data.webhook).trim()
                : "";


        const fingerprint =
            data.fingerprint !== undefined
                ? String(data.fingerprint).trim()
                : "";


        // ======================================
        // VALIDATION
        // ======================================

        if (!user_id) {

            return res.status(400).json({
                status: "fail",
                message: "Missing field: user_id"
            });

        }


        if (!botusername) {

            return res.status(400).json({
                status: "fail",
                message: "Missing field: botusername"
            });

        }


        if (!hash) {

            return res.status(400).json({
                status: "fail",
                message: "Missing field: hash"
            });

        }


        if (!webhook) {

            return res.status(400).json({
                status: "fail",
                message: "Missing field: webhook"
            });

        }


        if (!fingerprint) {

            return res.status(400).json({
                status: "fail",
                message: "Missing field: fingerprint"
            });

        }


        // ======================================
        // WEBHOOK URL VALIDATION
        // ======================================

        let webhookUrl;

        try {

            webhookUrl =
                new URL(webhook);

        } catch (error) {

            return res.status(400).json({
                status: "fail",
                message: "Invalid webhook URL"
            });

        }


        if (
            webhookUrl.protocol !== "https:"
        ) {

            return res.status(400).json({
                status: "fail",
                message: "Webhook must use HTTPS"
            });

        }


        // ======================================
        // RESULT
        // ======================================
        //
        // Your TBC code performs the actual
        // same-device check using:
        //
        // Bot.getData("FP_" + fp)
        //
        // Therefore the first response is PASS.
        //
        // TBC decides whether it is:
        //
        // New device
        // OR
        // Same device
        //
        // ======================================

        const verificationResult = {

            status: "pass",

            message:
                "Verified Successfully",

            fingerprint:
                fingerprint,

            botusername:
                botusername

        };


        // ======================================
        // SEND RESULT TO TBC WEBHOOK
        // ======================================

        let webhookResponse;


        try {

            webhookResponse =
                await fetch(
                    webhookUrl.toString(),
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body:
                            JSON.stringify(
                                verificationResult
                            )
                    }
                );

        } catch (error) {

            console.error(
                "TBC WEBHOOK ERROR:",
                error
            );

            return res.status(502).json({
                status: "fail",
                message:
                    "Unable to contact verification callback"
            });

        }


        // ======================================
        // CHECK WEBHOOK
        // ======================================

        if (!webhookResponse.ok) {

            console.error(
                "TBC WEBHOOK STATUS:",
                webhookResponse.status
            );

            return res.status(502).json({
                status: "fail",
                message:
                    "Verification callback failed"
            });

        }


        // ======================================
        // SUCCESS
        // ======================================

        return res.status(200).json(
            verificationResult
        );


    } catch (error) {

        console.error(
            "PROCESS API ERROR:",
            error
        );

        return res.status(500).json({
            status: "fail",
            message:
                "Internal server error"
        });

    }

            }
