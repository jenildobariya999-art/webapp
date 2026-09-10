(function () {
    "use strict";

    const Telegram = window.Telegram;
    const WebApp = Telegram && Telegram.WebApp;

    // Telegram WebApp
    if (WebApp) {
        try {
            WebApp.ready();
        } catch (e) {}
    }

    // Elements
    const userIdDisplay = document.getElementById("userIdDisplay");
    const avatarBox = document.getElementById("avatarBox");
    const headerBadge = document.getElementById("headerBadge");
    const headerStatusText = document.getElementById("headerStatusText");
    const bar = document.getElementById("bar");
    const scanBar = document.getElementById("scanBar");

    // URL parameters
    const params = new URLSearchParams(window.location.search);
    const bot = params.get("bot");
    const botHash = params.get("botHash");

    // Telegram user
    let user = null;

    try {
        if (WebApp && WebApp.initDataUnsafe) {
            user = WebApp.initDataUnsafe.user;
        }
    } catch (e) {
        user = null;
    }

    // Show Telegram user
    if (user) {
        const firstName = user.first_name || "";
        const lastName = user.last_name || "";
        const fullName = (firstName + " " + lastName).trim();

        if (userIdDisplay) {
            userIdDisplay.textContent = fullName || String(user.id);
        }

        if (avatarBox && user.photo_url) {
            avatarBox.style.backgroundImage =
                "url('" + user.photo_url + "')";

            avatarBox.textContent = "";
        } else if (avatarBox && firstName) {
            avatarBox.textContent =
                firstName.charAt(0).toUpperCase();
        }
    }

    // Header status
    function setStatus(text, type) {
        if (headerStatusText) {
            headerStatusText.textContent = text;
        }

        if (headerBadge) {
            headerBadge.className = "status-pill";

            if (type === "scanning") {
                headerBadge.classList.add("pill-blue");
            }

            if (type === "active") {
                headerBadge.classList.add("pill-green");
            }

            if (type === "failed") {
                headerBadge.classList.add("pill-red");
            }
        }
    }

    // View handling
    function showView(id) {
        const sections = document.querySelectorAll(".view-section");

        sections.forEach(function (section) {
            section.classList.remove("active");
        });

        const target = document.getElementById(id);

        if (target) {
            target.classList.add("active");
        }
    }

    // Particle animation
    function createParticles() {
        const container = document.getElementById("particles");

        if (!container) {
            return;
        }

        for (let i = 0; i < 20; i++) {
            const particle = document.createElement("div");

            particle.classList.add("particle");

            particle.style.width =
                (Math.random() * 5 + 2) + "px";

            particle.style.height =
                particle.style.width;

            particle.style.left =
                (Math.random() * 100) + "%";

            particle.style.top =
                (Math.random() * 100) + "%";

            particle.style.animationDelay =
                (Math.random() * 5) + "s";

            particle.style.animationDuration =
                (Math.random() * 10 + 5) + "s";

            container.appendChild(particle);
        }
    }

    // Close buttons
    const successClose = document.getElementById("btnSuccessClose");
    const failClose = document.getElementById("btnFailClose");
    const alreadyClose = document.getElementById("btnAlreadyClose");

    function closeWebApp() {
        try {
            if (
                window.Telegram &&
                window.Telegram.WebApp &&
                typeof window.Telegram.WebApp.close === "function"
            ) {
                window.Telegram.WebApp.close();
            }
        } catch (e) {}
    }

    if (successClose) {
        successClose.onclick = closeWebApp;
    }

    if (failClose) {
        failClose.onclick = closeWebApp;
    }

    if (alreadyClose) {
        alreadyClose.onclick = closeWebApp;
    }

    // Start
    createParticles();

    setStatus("SCANNING", "scanning");

    showView("view-scanning");

    if (scanBar) {
        scanBar.style.width = "5%";

        setTimeout(function () {
            scanBar.style.width = "30%";
        }, 500);

        setTimeout(function () {
            scanBar.style.width = "60%";
        }, 1000);

        setTimeout(function () {
            scanBar.style.width = "100%";
        }, 1500);
    }

    if (bar) {
        bar.style.width = "30%";

        setTimeout(function () {
            bar.style.width = "60%";
        }, 1000);

        setTimeout(function () {
            bar.style.width = "100%";
        }, 2000);
    }

    /*
     * No fetch()
     * No process.php
     * No POST request
     * No FingerprintJS
     * No server verification
     *
     * The page finishes locally.
     */

    setTimeout(function () {
        setStatus("ACTIVE", "active");
        showView("view-success");
    }, 2500);

})();
