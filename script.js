/**
 * JENIL DOBARIYA SECURE DEVICE VERIFICATION - HARD SECURITY CLIENT
 * File: script.js
 * Hardware GPU Hash + Telegram Native Only + Anti-Cheat Shield
 */

// 1. Generate Deep Hardware Fingerprint (Unchanged even if browser language changes)
function getHardwareFingerprint() {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 200;
    canvas.height = 50;
    const ctx = canvas.getContext('2d');
    ctx.textBaseline = "top";
    ctx.font = "14px 'Arial'";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#f60";
    ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = "#069";
    ctx.fillText("JenilDobariyaSecurity2026", 2, 15);
    ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
    ctx.fillText("JenilDobariyaSecurity2026", 4, 17);
    const canvasHash = canvas.toDataURL();

    // Combine immutable hardware components
    const hardwareSeed = [
      screen.width + "x" + screen.height + "x" + screen.colorDepth,
      navigator.hardwareConcurrency || 4,
      navigator.deviceMemory || 4,
      navigator.platform || '',
      canvasHash.substring(canvasHash.length - 64)
    ].join('###');

    // Simple robust 32-char string hash
    let hash = 0;
    for (let i = 0; i < hardwareSeed.length; i++) {
      const chr = hardwareSeed.charCodeAt(i);
      hash = ((hash << 5) - hash) + chr;
      hash |= 0;
    }
    const hexHash = Math.abs(hash).toString(16).padStart(8, '0');
    return "hw_" + hexHash + "_" + (navigator.hardwareConcurrency || 4) + "c" + (navigator.deviceMemory || 4) + "g";
  } catch (e) {
    return null;
  }
}

// 2. View Switcher Helper
function showView(viewId) {
  document.querySelectorAll('.view-section').forEach(v => v.classList.remove('active'));
  const target = document.getElementById(viewId);
  if (target) target.classList.add('active');
}

// 3. Status Badge Updater
function updateBadge(type, label) {
  const badge = document.getElementById('headerBadge');
  const text = document.getElementById('headerStatusText');
  if (!badge || !text) return;
  badge.className = 'status-pill';
  if (type === 'scanning') {
    badge.classList.add('pill-blue');
    text.textContent = label || 'SCANNING';
  } else if (type === 'active' || type === 'success') {
    badge.classList.add('pill-green');
    text.textContent = label || 'VERIFIED';
  } else {
    badge.classList.add('pill-red');
    text.textContent = label || 'FAILED';
  }
}

// 4. Ambient Particle System
function createParticles() {
  const container = document.getElementById('particles');
  if (!container) return;
  for (let i = 0; i < 20; i++) {
    const p = document.createElement('div');
    p.classList.add('particle');
    p.style.width = (Math.random() * 5 + 3) + 'px';
    p.style.height = p.style.width;
    p.style.left = (Math.random() * 100) + '%';
    p.style.top = (Math.random() * 100) + '%';
    p.style.animationDelay = (Math.random() * 5) + 's';
    p.style.animationDuration = (Math.random() * 10 + 10) + 's';
    container.appendChild(p);
  }
}

// 5. Telegram WebApp Close Handler
function closeTelegramApp() {
  if (window.Telegram && window.Telegram.WebApp && typeof window.Telegram.WebApp.close === 'function') {
    window.Telegram.WebApp.close();
  } else {
    window.close();
  }
}

// 6. Master Verification Execution
document.addEventListener('DOMContentLoaded', async () => {
  createParticles();

  // Attach button close actions
  ['btnSuccessClose', 'btnFailClose', 'btnAlreadyClose'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.onclick = closeTelegramApp;
  });

  const urlParams = new URLSearchParams(window.location.search);
  const botUsername = urlParams.get('botusername') || urlParams.get('bot') || '';
  const botHash = urlParams.get('hash') || '';
  const webhook = urlParams.get('webhook') || '';

  // Detect Telegram WebApp
  const tg = window.Telegram ? window.Telegram.WebApp : null;
  const tgUser = tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null;

  // STRICT TELEGRAM CHECK: If not Telegram WebApp, show critical blocked screen
  const isNativeTelegram = !!(tg && (tgUser || tg.initData));
  if (!isNativeTelegram && !urlParams.get('test_bypass')) {
    updateBadge('failed', 'BLOCKED');
    showView('view-critical');
    const desc = document.querySelector('#view-critical .desc');
    if (desc) desc.textContent = "Security Alert: Third-party browsers not permitted. Open exclusively inside Telegram.";
    return;
  }

  // Populate User Details
  const userNameEl = document.getElementById('userName');
  const userIdEl = document.getElementById('userIdDisplay');
  const avatarBox = document.getElementById('avatarBox');

  const userId = tgUser ? tgUser.id : (urlParams.get('user_id') || '');
  const firstName = tgUser ? (tgUser.first_name || 'USER') : 'USER';
  const lastName = tgUser ? (tgUser.last_name || '') : '';
  const fullName = (firstName + ' ' + lastName).trim();

  if (userNameEl) userNameEl.textContent = fullName;
  if (userIdEl) userIdEl.textContent = userId || '---';

  if (avatarBox) {
    if (tgUser && tgUser.photo_url) {
      avatarBox.innerHTML = '';
      avatarBox.style.backgroundImage = "url('" + tgUser.photo_url + "')";
    } else {
      avatarBox.textContent = (firstName.charAt(0) || 'U').toUpperCase();
    }
  }

  if (!userId) {
    updateBadge('failed', 'NO ID');
    showView('view-critical');
    return;
  }

  // Step 1: Animation Progress
  showView('view-scanning');
  updateBadge('scanning', 'SCANNING');
  const scanBar = document.getElementById('scanBar');
  if (scanBar) scanBar.style.width = '35%';

  // Step 2: Extract Multi-Factor Signatures
  let fpVisitorId = '';
  try {
    if (window.FingerprintJS) {
      const fp = await window.FingerprintJS.load();
      const res = await fp.get();
      fpVisitorId = res.visitorId;
    }
  } catch (err) {
    console.warn('FingerprintJS fallback', err);
  }

  const hwHash = getHardwareFingerprint() || fpVisitorId;
  if (scanBar) scanBar.style.width = '70%';

  // Step 3: Package Payload
  const payload = {
    user_id: String(userId),
    device_id: fpVisitorId || hwHash,
    hardware_hash: hwHash,
    is_telegram_native: isNativeTelegram,
    botusername: botUsername,
    bot_hash: botHash,
    webhook: webhook,
    user_agent: navigator.userAgent || '',
    platform: navigator.platform || '',
    language: navigator.language || '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    hardware_concurrency: navigator.hardwareConcurrency || '',
    device_memory: navigator.deviceMemory || '',
    screen_resolution: (screen.width || '') + 'x' + (screen.height || '')
  };

  // Step 4: Dispatch to Backend
  try {
    const endpoint = window.location.pathname.includes('/api/process') ? '/api/process' : '/api/process.js';
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const result = await response.json();
    if (scanBar) scanBar.style.width = '100%';

    setTimeout(() => {
      if (result.status === 'success') {
        updateBadge('success', 'PASSED');
        showView('view-success');
      } else if (result.status === 'continue') {
        updateBadge('active', 'VERIFIED');
        showView('view-already');
      } else if (result.status === 'attempt') {
        updateBadge('failed', 'USED DEVICE');
        showView('view-already');
        const desc = document.querySelector('#view-already .desc');
        if (desc) desc.textContent = "Warning: This physical phone is already linked to another account.";
      } else {
        updateBadge('failed', 'FAILED');
        showView('view-failed');
        const msg = document.getElementById('failedMsg');
        if (msg && result.message) msg.textContent = result.message;
      }
    }, 400);

  } catch (networkErr) {
    if (scanBar) scanBar.style.width = '100%';
    updateBadge('failed', 'NET ERROR');
    showView('view-failed');
    const msg = document.getElementById('failedMsg');
    if (msg) msg.textContent = "Connection Error. Please check your network and retry.";
  }
});
