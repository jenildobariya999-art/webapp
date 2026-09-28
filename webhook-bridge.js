/**
 * webhook-bridge.js
 *
 * script.js never includes the page's ?webhook=... URL in its request to
 * api/process.js, so the server has no way to notify the bot. This script
 * must be loaded BEFORE script.js. It transparently wraps window.fetch:
 * every call behaves exactly as before, EXCEPT the call to api/process.js
 * has the page's webhook URL injected into its JSON body, so process.js
 * (server-side, no CORS restrictions) can POST the result to the bot itself.
 *
 * It also plays a short tone based on the result (success / already /
 * failed), generated with the Web Audio API - no audio files needed.
 *
 * It also fixes the header status pill (#headerBadge/#headerStatusText):
 * script.js only ever sets it to an "active" state regardless of the real
 * outcome, so this overrides it with the true result - FAILED on a blocked
 * device, ALREADY on a repeat device, ACTIVE on a fresh pass.
 *
 * No other changes to script.js are required.
 */
(function () {
  const originalFetch = window.fetch;
  const params = new URLSearchParams(window.location.search);
  const webhookUrl = params.get('webhook');

  // ---- Sound ----
  function playTone(freqs, durationMs) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx();
      let t = ctx.currentTime;
      freqs.forEach(function (f) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = f;
        gain.gain.setValueAtTime(0.001, t);
        gain.gain.exponentialRampToValueAtTime(0.5, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, t + durationMs / 1000);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t);
        osc.stop(t + durationMs / 1000);
        t += durationMs / 1000;
      });
    } catch (err) {
      // Audio may be blocked until a user gesture on some browsers - safe to ignore.
    }
  }

  function playResultSound(data) {
    if (data && data.status === 'success') {
      playTone([523.25, 659.25, 783.99], 140); // ascending major triad - success
    } else if (data && data.attempt) {
      playTone([440, 440], 120); // neutral double-blip - already verified
    } else {
      playTone([349.23, 261.63], 220); // descending tone - failed
    }
  }

  // ---- Header status pill fix ----
  // script.js only ever marks this "active" regardless of true outcome.
  function fixHeaderStatus(data) {
    const badge = document.getElementById('headerBadge');
    const text = document.getElementById('headerStatusText');
    if (!badge || !text) return;

    badge.classList.remove('pill-blue', 'pill-green', 'pill-red');

    if (data && data.status === 'success') {
      text.textContent = 'ACTIVE';
      badge.classList.add('pill-green');
    } else if (data && data.attempt) {
      text.textContent = 'ALREADY';
      badge.classList.add('pill-blue');
    } else {
      text.textContent = 'FAILED';
      badge.classList.add('pill-red');
    }
  }

  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const isProcessCall = url.indexOf('api/process.js') !== -1;

    if (isProcessCall && webhookUrl && init && typeof init.body === 'string') {
      try {
        const bodyObj = JSON.parse(init.body);
        bodyObj.webhook = webhookUrl;
        init = Object.assign({}, init, { body: JSON.stringify(bodyObj) });
      } catch (err) {
        console.error('webhook-bridge: could not inject webhook URL into request', err);
      }
    }

    const promise = originalFetch(input, init);

    if (isProcessCall) {
      promise.then(function (response) {
        response.clone().json().then(function (data) {
          playResultSound(data);
          fixHeaderStatus(data);
        }).catch(function () {});
      }).catch(function () {});
    }

    return promise;
  };
})();
