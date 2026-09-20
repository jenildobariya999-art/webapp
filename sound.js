/* ============================================================
   sound.js — tiny UI sound layer for the status console
   No audio files needed: tones are synthesized with the Web
   Audio API. Drop this in after your markup:
     <script src="sound.js"></script>
   Sounds only start after the first user interaction, per
   browser autoplay rules.
   ============================================================ */

(() => {
  let ctx;
  function getCtx() {
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  // Plays a short, soft tone. type: 'click' | 'hover' | 'success' | 'error'
  function blip(type = 'click') {
    const audioCtx = getCtx();
    const now = audioCtx.currentTime;

    const presets = {
      hover:   { freq: 740,  duration: 0.06, gain: 0.05, wave: 'sine'     },
      click:   { freq: 520,  duration: 0.12, gain: 0.09, wave: 'triangle' },
      success: { freq: 660,  duration: 0.22, gain: 0.10, wave: 'sine', glideTo: 990 },
      error:   { freq: 220,  duration: 0.24, gain: 0.10, wave: 'sawtooth', glideTo: 140 },
    };
    const p = presets[type] || presets.click;

    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = p.wave;
    osc.frequency.setValueAtTime(p.freq, now);
    if (p.glideTo) osc.frequency.exponentialRampToValueAtTime(p.glideTo, now + p.duration);

    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(p.gain, now + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + p.duration);

    osc.connect(gain).connect(audioCtx.destination);
    osc.start(now);
    osc.stop(now + p.duration + 0.02);
  }

  function bindOnce() {
    // Buttons: click tone
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn');
      if (!btn) return;
      const type = btn.classList.contains('success') ? 'success'
                 : btn.classList.contains('danger')  ? 'error'
                 : 'click';
      blip(type);
    });

    // Buttons, avatar, status pill: soft hover tone (throttled per element)
    const hoverTargets = '.btn, .avatar-container, .status-pill';
    document.addEventListener('mouseover', (e) => {
      const el = e.target.closest(hoverTargets);
      if (!el || el.dataset.soundHovering) return;
      el.dataset.soundHovering = '1';
      blip('hover');
    });
    document.addEventListener('mouseout', (e) => {
      const el = e.target.closest(hoverTargets);
      if (el) delete el.dataset.soundHovering;
    });

    // Optional: fire success/error tone automatically when a view-section
    // with the matching icon becomes active (e.g. via class toggle from
    // your existing app JS). Observes .view-section.active changes.
    const containers = document.querySelectorAll('.view-section');
    if (containers.length && 'MutationObserver' in window) {
      const seen = new WeakSet();
      const obs = new MutationObserver((mutations) => {
        for (const m of mutations) {
          const el = m.target;
          if (el.classList.contains('active') && !seen.has(el)) {
            seen.add(el);
            if (el.querySelector('.icon-wrapper.success')) blip('success');
            else if (el.querySelector('.icon-wrapper.error')) blip('error');
          }
        }
      });
      containers.forEach((el) => obs.observe(el, { attributes: true, attributeFilter: ['class'] }));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindOnce);
  } else {
    bindOnce();
  }
})();
