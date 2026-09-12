/**
 * Secure Multi-Bot Device Verification Frontend Script
 * Filename: script.js
 * High Security Fingerprinting & Telegram WebApp Integration
 */

// 1. Particle Background Animation Engine
function createParticles() {
    const container = document.getElementById('particles');
    if (!container) return;
    
    container.innerHTML = '';
    const particleCount = 20;

    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.classList.add('particle');

        const size = Math.random() * 20 + 5;
        particle.style.width = size + 'px';
        particle.style.height = size + 'px';

        particle.style.left = Math.random() * 100 + '%';
        particle.style.top = Math.random() * 100 + '%';

        particle.style.animationDelay = Math.random() * 10 + 's';
        particle.style.animationDuration = Math.random() * 10 + 10 + 's';

        container.appendChild(particle);
    }
}

// 2. Status Badge Update Helper
function updateHeaderBadge(type) {
    const badge = document.getElementById('headerBadge');
    const text = document.getElementById('headerStatusText');
    if (!badge || !text) return;

    badge.className = 'status-pill';

    if (type === 'scanning') {
        badge.classList.add('pill-blue');
        text.textContent = 'SCANNING';
    } else if (type === 'error') {
        badge.classList.add('pill-red');
        text.textContent = 'ERROR';
    } else if (type === 'active') {
        badge.classList.add('pill-green');
        text.textContent = 'ACTIVE';
    } else if (type === 'failed') {
        badge.classList.add('pill-red');
        text.textContent = 'FAILED';
    }
}

// 3. View Switcher Helper
function showView(viewId) {
    const allViews = document.querySelectorAll('.view-section');
    allViews.forEach(v => v.classList.remove('active'));

    const target = document.getElementById(viewId);
    if (target) {
        target.classList.add('active');
    }
}

// 4. Close WebApp Helper
function closeTelegramWebApp() {
    try {
        if (window.Telegram && window.Telegram.WebApp && typeof window.Telegram.WebApp.close === 'function') {
            window.Telegram.WebApp.close();
        } else {
            window.close();
        }
    } catch (e) {
        window.close();
    }
}

// 5. Main Execution on DOM Loaded
document.addEventListener('DOMContentLoaded', async function () {
    // Generate background ambient particles
    createParticles();

    // Bind Close / Continue buttons
    const btnSuccess = document.getElementById('btnSuccessClose');
    const btnFail = document.getElementById('btnFailClose');
    const btnAlready = document.getElementById('btnAlreadyClose');

    if (btnSuccess) btnSuccess.onclick = closeTelegramWebApp;
    if (btnFail) btnFail.onclick = closeTelegramWebApp;
    if (btnAlready) btnAlready.onclick = closeTelegramWebApp;

    // Parse Query Parameters
    const urlParams = new URLSearchParams(window.location.search);
    const botParam = urlParams.get('botusername') || urlParams.get('bot') || '';
    const hashParam = urlParams.get('hash') || urlParams.get('bot_hash') || '';
    const webhookParam = urlParams.get('webhook') || '';

    // Read Telegram WebApp User Context
    let tgUser = null;
    let userId = null;
    let userName = 'USER';
    let photoUrl = null;

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe) {
        tgUser = window.Telegram.WebApp.initDataUnsafe.user;
    }

    const userNameEl = document.getElementById('userName');
    const userIdDisplayEl = document.getElementById('userIdDisplay');
    const avatarBox = document.getElementById('avatarBox');

    if (tgUser && tgUser.id) {
        userId = String(tgUser.id);
        const firstName = tgUser.first_name || '';
        const lastName = tgUser.last_name || '';
        userName = (firstName + ' ' + lastName).trim() || 'USER';
        photoUrl = tgUser.photo_url || null;
    } else {
        // Fallback to URL query parameter if testing in browser
        userId = urlParams.get('user_id') || null;
        userName = urlParams.get('username') || 'USER';
    }

    if (userNameEl) userNameEl.textContent = userName;
    if (userIdDisplayEl) userIdDisplayEl.textContent = userId ? userId : '---';

    if (avatarBox) {
        if (photoUrl) {
            avatarBox.style.backgroundImage = "url('" + photoUrl + "')";
            avatarBox.innerHTML = '';
        } else {
            avatarBox.textContent = (userName.charAt(0) || 'U').toUpperCase();
        }
    }

    // Critical Security Check: User ID must exist
    if (!userId) {
        updateHeaderBadge('error');
        showView('view-critical');
        return;
    }

    // Start Scanning State
    updateHeaderBadge('scanning');
    showView('view-scanning');

    const scanBar = document.getElementById('scanBar');
    if (scanBar) {
        scanBar.style.width = '30%';
        setTimeout(() => {
            if (scanBar) scanBar.style.width = '60%';
        }, 800);
    }

    try {
        // High-Security Hardware & Browser Fingerprint Extraction
        let visitorId = '';
        try {
            if (typeof FingerprintJS !== 'undefined' && FingerprintJS.load) {
                const fp = await FingerprintJS.load();
                const result = await fp.get();
                visitorId = result.visitorId || '';
            }
        } catch (fpErr) {
            console.warn('FingerprintJS load error:', fpErr);
        }

        // Secondary fallback fingerprint if FingerprintJS blocked or CDN issue
        if (!visitorId || visitorId.length < 8) {
            const screenInfo = (screen.width || '') + 'x' + (screen.height || '') + 'x' + (screen.colorDepth || '');
            const rawHash = (navigator.userAgent || '') + (navigator.language || '') + screenInfo + (navigator.hardwareConcurrency || '');
            let hashVal = 0;
            for (let i = 0; i < rawHash.length; i++) {
                hashVal = ((hashVal << 5) - hashVal) + rawHash.charCodeAt(i);
                hashVal |= 0;
            }
            visitorId = 'fp_' + Math.abs(hashVal).toString(16) + Date.now().toString(16).slice(-4);
        }

        if (scanBar) scanBar.style.width = '100%';

        // Collect Complete Client System Telemetry
        const payload = {
            user_id: userId,
            device_id: visitorId,
            botusername: botParam,
            bot_hash: hashParam,
            webhook: webhookParam,
            user_agent: navigator.userAgent || '',
            platform: navigator.platform || '',
            language: navigator.language || '',
            timezone: (typeof Intl !== 'undefined' && Intl.DateTimeFormat) 
                        ? Intl.DateTimeFormat().resolvedOptions().timeZone 
                        : '',
            hardware_concurrency: navigator.hardwareConcurrency ? String(navigator.hardwareConcurrency) : '',
            device_memory: navigator.deviceMemory ? String(navigator.deviceMemory) : 'unknown',
            screen_resolution: (window.screen ? window.screen.width : '') + 'x' + (window.screen.height ? window.screen.height : '')
        };

        // Send to backend (Vercel rewrite handles /process.php -> /api/process.js smoothly)
        const response = await fetch('process.php', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        // Evaluate Backend Response According to Same Logic
        if (data.status === 'success') {
            updateHeaderBadge('active');
            showView('view-success');
        } else if (data.status === 'continue') {
            updateHeaderBadge('active');
            showView('view-already');
        } else if (data.status === 'attempt') {
            // Anti-Clone: Same physical device used on another account
            updateHeaderBadge('failed');
            const failedMsg = document.getElementById('failedMsg');
            if (failedMsg) {
                failedMsg.textContent = data.message || 'Device already used on another account.';
            }
            showView('view-failed');
        } else {
            // Validation failed or criteria not met
            updateHeaderBadge('failed');
            const failedMsg = document.getElementById('failedMsg');
            if (failedMsg) {
                failedMsg.textContent = data.message || 'Verification criteria not met.';
            }
            showView('view-failed');
        }

    } catch (err) {
        console.error('Verification request error:', err);
        updateHeaderBadge('failed');
        const failedMsg = document.getElementById('failedMsg');
        if (failedMsg) {
            failedMsg.textContent = 'Connection Error. Please try again.';
        }
        showView('view-failed');
    }
});
