(function () {

    "use strict";


    // ==========================================
    // TELEGRAM
    // ==========================================

    const tg = window.Telegram && window.Telegram.WebApp
        ? window.Telegram.WebApp
        : null;


    if (tg) {
        try {
            tg.ready();
            tg.expand();
        } catch (e) {
        }
    }


    // ==========================================
    // ELEMENTS
    // ==========================================

    const scanningView = document.getElementById("view-scanning");
    const criticalView = document.getElementById("view-critical");
    const failedView = document.getElementById("view-failed");
    const successView = document.getElementById("view-success");
    const alreadyView = document.getElementById("view-already");

    const scanBar = document.getElementById("scanBar");
    const scanStatus = document.getElementById("scanStatus");

    const failedMsg = document.getElementById("failedMsg");
    const criticalMsg = document.getElementById("criticalMsg");

    const userName = document.getElementById("userName");
    const userIdDisplay = document.getElementById("userIdDisplay");
    const avatarBox = document.getElementById("avatarBox");

    const headerBadge = document.getElementById("headerBadge");
    const headerStatusText = document.getElementById("headerStatusText");

    const btnFailClose = document.getElementById("btnFailClose");
    const btnSuccessClose = document.getElementById("btnSuccessClose");
    const btnAlreadyClose = document.getElementById("btnAlreadyClose");


    // ==========================================
    // URL PARAMETERS
    // ==========================================

    const params = new URLSearchParams(
        window.location.search
    );

    const botusername = (
        params.get("botusername") || ""
    ).trim();

    const webhook = (
        params.get("webhook") || ""
    ).trim();

    const hash = (
        params.get("hash") || ""
    ).trim();


    // ==========================================
    // TELEGRAM USER
    // ==========================================

    let telegramUser = null;

    if (
        tg &&
        tg.initDataUnsafe &&
        tg.initDataUnsafe.user
    ) {
        telegramUser = tg.initDataUnsafe.user;
    }


    // ==========================================
    // SHOW USER
    // ==========================================

    if (telegramUser) {

        const firstName =
            telegramUser.first_name || "";

        const lastName =
            telegramUser.last_name || "";

        const fullName =
            (firstName + " " + lastName).trim();

        userName.textContent =
            fullName || "USER";

        userIdDisplay.textContent =
            telegramUser.id
                ? String(telegramUser.id)
                : "---";


        if (telegramUser.photo_url) {

            const img =
                document.createElement("img");

            img.src =
                telegramUser.photo_url;

            img.alt = "User";

            avatarBox.innerHTML = "";

            avatarBox.appendChild(img);

        } else {

            avatarBox.textContent =
                (
                    firstName ||
                    "U"
                ).charAt(0).toUpperCase();

        }

    }


    // ==========================================
    // VIEW HANDLER
    // ==========================================

    function showView(view) {

        const views = [
            scanningView,
            criticalView,
            failedView,
            successView,
            alreadyView
        ];

        views.forEach(function (item) {

            if (item) {
                item.classList.remove("active");
            }

        });

        if (view) {
            view.classList.add("active");
        }

    }


    // ==========================================
    // HEADER STATUS
    // ==========================================

    function setHeaderStatus(
        text,
        type
    ) {

        headerStatusText.textContent =
            text;

        headerBadge.classList.remove(
            "pill-blue",
            "pill-green",
            "pill-red"
        );

        if (type === "success") {

            headerBadge.classList.add(
                "pill-green"
            );

        } else if (type === "failed") {

            headerBadge.classList.add(
                "pill-red"
            );

        } else {

            headerBadge.classList.add(
                "pill-blue"
            );

        }

    }


    // ==========================================
    // PROGRESS
    // ==========================================

    function setProgress(
        value,
        text
    ) {

        const safeValue =
            Math.max(
                0,
                Math.min(100, value)
            );

        scanBar.style.width =
            safeValue + "%";

        if (text) {
            scanStatus.textContent =
                text;
        }

    }


    // ==========================================
    // CLOSE TELEGRAM
    // ==========================================

    function closeWebApp() {

        try {

            if (tg) {
                tg.close();
                return;
            }

        } catch (e) {
        }

        window.close();

    }


    btnFailClose.addEventListener(
        "click",
        closeWebApp
    );

    btnSuccessClose.addEventListener(
        "click",
        closeWebApp
    );

    btnAlreadyClose.addEventListener(
        "click",
        closeWebApp
    );


    // ==========================================
    // PARTICLES
    // ==========================================

    const particles =
        document.getElementById("particles");

    if (particles) {

        for (
            let i = 0;
            i < 25;
            i++
        ) {

            const p =
                document.createElement("div");

            p.className =
                "particle";

            p.style.left =
                Math.random() * 100 + "%";

            p.style.animationDuration =
                (5 + Math.random() * 8) + "s";

            p.style.animationDelay =
                (Math.random() * 7) + "s";

            particles.appendChild(p);

        }

    }


    // ==========================================
    // BASIC VALIDATION
    // ==========================================

    if (!telegramUser) {

        setHeaderStatus(
            "ERROR",
            "failed"
        );

        criticalMsg.textContent =
            "Telegram user session was not found.";

        showView(
            criticalView
        );

        return;

    }


    if (!botusername) {

        setHeaderStatus(
            "ERROR",
            "failed"
        );

        criticalMsg.textContent =
            "Bot information is missing.";

        showView(
            criticalView
        );

        return;

    }


    if (!webhook) {

        setHeaderStatus(
            "ERROR",
            "failed"
        );

        criticalMsg.textContent =
            "Verification callback is missing.";

        showView(
            criticalView
        );

        return;

    }


    if (!hash) {

        setHeaderStatus(
            "ERROR",
            "failed"
        );

        criticalMsg.textContent =
            "Verification hash is missing.";

        showView(
            criticalView
        );

        return;

    }


    // ==========================================
    // COLLECT DEVICE DATA
    // ==========================================

    async function collectFingerprint() {

        let fingerprint = "";

        try {

            const fp =
                await FingerprintJS.load();

            const result =
                await fp.get();

            fingerprint =
                String(
                    result.visitorId || ""
                ).trim();

        } catch (e) {

            fingerprint = "";

        }

        return fingerprint;

    }


    // ==========================================
    // DEVICE ID
    // ==========================================

    function getDeviceId() {

        try {

            let deviceId =
                localStorage.getItem(
                    "verification_device_id"
                );

            if (!deviceId) {

                deviceId =
                    crypto.randomUUID();

                localStorage.setItem(
                    "verification_device_id",
                    deviceId
                );

            }

            return deviceId;

        } catch (e) {

            return "";

        }

    }


    // ==========================================
    // SEND VERIFICATION
    // ==========================================

    async function startVerification() {

        try {

            setProgress(
                10,
                "Initializing secure scanner..."
            );


            const fingerprint =
                await collectFingerprint();


            if (!fingerprint) {

                setHeaderStatus(
                    "FAILED",
                    "failed"
                );

                failedMsg.textContent =
                    "Unable to generate device fingerprint.";

                showView(
                    failedView
                );

                return;

            }


            setProgress(
                35,
                "Fingerprint generated..."
            );


            const deviceId =
                getDeviceId();


            const screenResolution =
                String(
                    window.screen.width +
                    "x" +
                    window.screen.height
                );


            const payload = {

                user_id:
                    String(
                        telegramUser.id
                    ),

                botusername:
                    botusername,

                hash:
                    hash,

                webhook:
                    webhook,

                fingerprint:
                    fingerprint,

                device_id:
                    deviceId,

                user_agent:
                    navigator.userAgent || "",

                platform:
                    navigator.platform || "",

                language:
                    navigator.language || "",

                timezone:
                    (
                        Intl.DateTimeFormat()
                    ).resolvedOptions().timeZone || "",

                hardware_concurrency:
                    navigator.hardwareConcurrency || 0,

                device_memory:
                    navigator.deviceMemory || 0,

                screen_resolution:
                    screenResolution

            };


            setProgress(
                55,
                "Checking verification server..."
            );


            const response =
                await fetch(
                    "/api/process",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body:
                            JSON.stringify(
                                payload
                            )
                    }
                );


            setProgress(
                75,
                "Processing verification..."
            );


            let result = null;


            try {

                result =
                    await response.json();

            } catch (e) {

                result = null;

            }


            if (
                !response.ok ||
                !result
            ) {

                throw new Error(
                    result &&
                    result.message
                        ? result.message
                        : "Server request failed"
                );

            }


            setProgress(
                100,
                "Verification completed."
            );


            // ==================================
            // RESULT
            // ==================================

            const status =
                String(
                    result.status || ""
                ).toLowerCase().trim();

            const message =
                String(
                    result.message || ""
                ).trim();


            if (
                status === "pass" &&
                message ===
                    "Verified Successfully"
            ) {

                setHeaderStatus(
                    "VERIFIED",
                    "success"
                );

                showView(
                    successView
                );

                return;

            }


            if (
                status === "pass" &&
                message ===
                    "Already Verified"
            ) {

                setHeaderStatus(
                    "VERIFIED",
                    "success"
                );

                showView(
                    alreadyView
                );

                return;

            }


            setHeaderStatus(
                "FAILED",
                "failed"
            );

            failedMsg.textContent =
                message ||
                "Verification failed.";

            showView(
                failedView
            );


        } catch (error) {

            setHeaderStatus(
                "FAILED",
                "failed"
            );

            failedMsg.textContent =
                error &&
                error.message
                    ? error.message
                    : "Unable to connect to verification server.";

            showView(
                failedView
            );

        }

    }


    // ==========================================
    // START
    // ==========================================

    setTimeout(
        startVerification,
        500
    );


})();
