const API_URL =
    "https://verificationtest.byethost31.com/verificationFolder/process.php";
const $ = (id) => document.getElementById(id);
function showView(id) {
    document.querySelectorAll(".view-section").forEach((view) => {
        view.classList.remove("active");
    });
    const view = $(id);
    if (view) view.classList.add("active");
}
function setHeader(state, text) {
    const badge = $("headerBadge");
    const statusText = $("headerStatusText");
    if (badge) {
        badge.classList.remove("pill-blue", "pill-green", "pill-red");
        badge.classList.add(
            state === "success"
                ? "pill-green"
                : state === "failed"
                  ? "pill-red"
                  : "pill-blue",
        );
    }
    if (statusText) statusText.textContent = text;
}
function showFailure(message) {
    setHeader("failed", "FAILED");
    const failedMessage = $("failedMsg");
    if (failedMessage) failedMessage.textContent = message;
    showView("view-failed");
}
function createParticles() {
    const container = $("particles");
    if (!container) return;
    for (let i = 0; i < 18; i += 1) {
        const particle = document.createElement("div");
        particle.className = "particle";
        particle.style.left = `${Math.random() * 100}%`;
        particle.style.top = `${Math.random() * 100}%`;
        particle.style.animationDelay = `${Math.random() * 5}s`;
        particle.style.animationDuration = `${8 + Math.random() * 8}s`;
        container.appendChild(particle);
    }
}
function getTelegramUser() {
    const telegram = window.Telegram && window.Telegram.WebApp;
    if (!telegram) {
        return { telegram: null, user: null };
    }
    telegram.ready();
    const user =
        telegram.initDataUnsafe && telegram.initDataUnsafe.user
            ? telegram.initDataUnsafe.user
            : null;
    return { telegram, user };
}
function renderUser(user) {
    const fullName = [user.first_name, user.last_name]
        .filter(Boolean)
        .join(" ")
        .trim();
    const nameElement = $("userName");
    const idElement = $("userIdDisplay");
    const avatar = $("avatarBox");
    if (nameElement) nameElement.textContent = fullName || "USER";
    if (idElement) idElement.textContent = String(user.id);
    if (avatar && user.photo_url) {
        avatar.textContent = "";
        avatar.style.backgroundImage = `url("${user.photo_url}")`;
        avatar.style.backgroundSize = "cover";
        avatar.style.backgroundPosition = "center";
    } else if (avatar) {
        avatar.textContent = (fullName || "U").charAt(0).toUpperCase();
    }
}
function closeTelegramPage(telegram) {
    if (telegram && typeof telegram.close === "function") {
        telegram.close();
        return;
    }
    window.close();
}
async function getFingerprint() {
    if (!window.FingerprintJS) {
        throw new Error("FingerprintJS library was not loaded.");
    }
    const agent = await window.FingerprintJS.load();
    const result = await agent.get();
    if (!result || !result.visitorId) {
        throw new Error("Fingerprint was not returned.");
    }
    return result.visitorId;
}
async function sendAuditRequest(payload) {
    const response = await fetch(API_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
    });
    const responseText = await response.text();
    let data;
    try {
        data = JSON.parse(responseText);
    } catch {
        throw new Error(
            `Server returned non-JSON response (${response.status}).`,
        );
    }
    if (!response.ok) {
        throw new Error(data.message || `Server returned HTTP ${response.status}.`);
    }
    return data;
}
async function startVerification() {
    createParticles();
    showView("view-scanning");
    setHeader("scanning", "SCANNING");
    const progress = $("scanBar");
    if (progress) progress.style.width = "20%";
    const { telegram, user } = getTelegramUser();
    if (!telegram || !user || !user.id) {
        showFailure(
            "Open this page from the Verify button inside Telegram. A Telegram user ID is unavailable in a normal browser.",
        );
        return;
    }
    renderUser(user);
    try {
        if (progress) progress.style.width = "45%";
        const fingerprint = await getFingerprint();
        if (progress) progress.style.width = "70%";
        const params = new URLSearchParams(window.location.search);
        const botHash = params.get("bothash") || params.get("bot_hash") || "";
        const botUsername =
            params.get("botusername") || params.get("bot") || "";
        const payload = {
            user_id: user.id,
            bot: botUsername,
            bot_hash: botHash,
            botusername: botUsername,
            hash: botHash,
            fingerprint: fingerprint,
            device_id: fingerprint,
            user_agent: navigator.userAgent,
            platform: navigator.platform || "unknown",
            language: navigator.language || "unknown",
            timezone:
                Intl.DateTimeFormat().resolvedOptions().timeZone || "unknown",
            hardware_concurrency: navigator.hardwareConcurrency || null,
            device_memory: navigator.deviceMemory || null,
            screen_resolution: `${window.screen.width}x${window.screen.height}`,
        };
        const data = await sendAuditRequest(payload);
        if (progress) progress.style.width = "100%";
        /*
         * The supplied PHP endpoint intentionally returns audit_only.
         * Do not turn a browser response into Telegram authentication.
         */
        if (data.status === "audit_only") {
            showFailure(
                "Device information was received for local audit. No Telegram verification was granted.",
            );
            return;
        }
        showFailure(data.message || "Verification was not completed.");
    } catch (error) {
        console.error("Verification request failed:", error);
        showFailure(
            "Connection failed. Check the PHP URL, HTTPS, CORS, and server response.",
        );
    }
}
document.addEventListener("DOMContentLoaded", () => {
    $("btnSuccessClose")?.addEventListener("click", () => {
        closeTelegramPage(window.Telegram?.WebApp);
    });
    $("btnFailClose")?.addEventListener("click", () => {
        closeTelegramPage(window.Telegram?.WebApp);
    });
    $("btnAlreadyClose")?.addEventListener("click", () => {
        closeTelegramPage(window.Telegram?.WebApp);
    });
    startVerification();
});
