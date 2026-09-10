const ADMIN_PASSWORD = "CHANGE_THIS_PASSWORD";


let records = globalThis.__verification_records;

if (!records) {

    records = [];

    globalThis.__verification_records =
        records;
}


export default function handler(req, res) {

    res.setHeader(
        "Access-Control-Allow-Origin",
        "*"
    );

    res.setHeader(
        "Access-Control-Allow-Methods",
        "GET, OPTIONS"
    );

    res.setHeader(
        "Access-Control-Allow-Headers",
        "Content-Type, X-Admin-Password"
    );


    /*
    ========================================
    OPTIONS
    ========================================
    */

    if (req.method === "OPTIONS") {

        return res.status(200).json({

            status: "success",

            message: "OPTIONS OK"

        });

    }


    /*
    ========================================
    METHOD
    ========================================
    */

    if (req.method !== "GET") {

        return res.status(405).json({

            status: "failed",

            message: "Only GET requests are allowed"

        });

    }


    /*
    ========================================
    PASSWORD
    ========================================
    */

    const password =
        req.headers["x-admin-password"] || "";


    if (
        !password ||
        String(password) !== ADMIN_PASSWORD
    ) {

        return res.status(401).json({

            status: "failed",

            error: "UNAUTHORIZED",

            message: "Invalid admin password"

        });

    }


    /*
    ========================================
    STATS
    ========================================
    */

    let success = 0;

    let failed = 0;

    let active = 0;


    const botStats = {};


    records.forEach(function(record) {

        const status =
            String(
                record.status || "unknown"
            ).toLowerCase();


        const bot =
            record.bot_hash
                ? String(record.bot_hash)
                : "unknown";


        if (status === "success") {
            success++;
        }

        if (status === "failed") {
            failed++;
        }

        if (status === "active") {
            active++;
        }


        if (!botStats[bot]) {

            botStats[bot] = {

                bot_hash: bot,

                total: 0,

                success: 0,

                failed: 0,

                active: 0

            };

        }


        botStats[bot].total++;


        if (status === "success") {

            botStats[bot].success++;

        }


        if (status === "failed") {

            botStats[bot].failed++;

        }


        if (status === "active") {

            botStats[bot].active++;

        }

    });


    /*
    ========================================
    NEWEST FIRST
    ========================================
    */

    const sortedRecords =
        records.slice().sort(function(a, b) {

            return String(
                b.created_at || ""
            ).localeCompare(
                String(a.created_at || "")
            );

        });


    /*
    ========================================
    RESPONSE
    ========================================
    */

    return res.status(200).json({

        status: "success",

        message: "Admin API working",

        stats: {

            total: records.length,

            success: success,

            failed: failed,

            active: active

        },

        bots:
            Object.values(botStats),

        records:
            sortedRecords

    });

}
