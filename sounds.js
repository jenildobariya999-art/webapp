/* ==========================================================================
   sounds.js: zero-file sound effects for the status portal.
   Uses the Web Audio API to synthesise every sound, so there is nothing to
   download and nothing to license.

   Add before </body>:   <script src="sounds.js" defer></script>

   What it does automatically:
   - Button presses make a key "thunk" (pointer or keyboard)
   - Hovering a button or footer link makes a soft tick
   - When a .view-section becomes .active it plays a sound matching its
     icon: success, error, info, or a rising chime for the default view
   - Progress bar width changes tick upward as it fills
   - Adds a mute button (remembered between visits)

   Manual use:  Sfx.play('success' | 'error' | 'info' | 'primary' | 'click' | 'hover')
                Sfx.setMuted(true)
   Browsers block audio until the first tap or key press; that is expected.
   ========================================================================== */
(function () {
  'use strict';

  var STORE_KEY = 'sfx-muted';
  var ctx = null;
  var master = null;
  var muted = false;

  try {
    muted = localStorage.getItem(STORE_KEY) === '1';
  } catch (e) {
    /* storage unavailable: stay unmuted for this visit */
  }

  /* ---------- Audio engine ---------- */
  function ensure() {
    if (!ctx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
      master = ctx.createGain();
      master.gain.value = 0.55;
      var comp = ctx.createDynamicsCompressor();
      master.connect(comp);
      comp.connect(ctx.destination);
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  /* One synthesised note. `slide` is a multiplier for the end frequency. */
  function tone(o) {
    var c = ensure();
    if (!c || muted) return;
    var start = c.currentTime + (o.at || 0);
    var dur = o.dur || 0.15;
    var osc = c.createOscillator();
    var gain = c.createGain();

    osc.type = o.type || 'sine';
    osc.frequency.setValueAtTime(o.freq, start);
    if (o.slide) {
      osc.frequency.exponentialRampToValueAtTime(Math.max(20, o.freq * o.slide), start + dur);
    }

    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.linearRampToValueAtTime(o.vol || 0.2, start + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);

    osc.connect(gain);
    gain.connect(master);
    osc.start(start);
    osc.stop(start + dur + 0.05);
  }

  /* ---------- Sound library ---------- */
  var NOTE = { C5: 523.25, E5: 659.25, G5: 783.99, C6: 1046.5, A4: 440, D5: 587.33 };

  var sounds = {
    click: function () {
      tone({ freq: 320, slide: 0.35, dur: 0.09, type: 'triangle', vol: 0.32 });
      tone({ freq: 1800, slide: 0.5, dur: 0.03, type: 'square', vol: 0.05 });
    },
    hover: function () {
      tone({ freq: 880, dur: 0.035, type: 'sine', vol: 0.05 });
    },
    success: function () {
      [NOTE.C5, NOTE.E5, NOTE.G5, NOTE.C6].forEach(function (f, i) {
        tone({ freq: f, at: i * 0.085, dur: 0.32, type: 'triangle', vol: 0.2 });
      });
      tone({ freq: NOTE.C6 * 2, at: 0.34, dur: 0.4, type: 'sine', vol: 0.06 });
    },
    error: function () {
      tone({ freq: 233, slide: 0.6, dur: 0.2, type: 'sawtooth', vol: 0.13 });
      tone({ freq: 175, slide: 0.6, at: 0.17, dur: 0.3, type: 'sawtooth', vol: 0.13 });
    },
    info: function () {
      tone({ freq: NOTE.D5, dur: 0.16, type: 'sine', vol: 0.2 });
      tone({ freq: NOTE.A4 * 2, at: 0.11, dur: 0.24, type: 'sine', vol: 0.2 });
    },
    primary: function () {
      tone({ freq: NOTE.A4, slide: 1.6, dur: 0.28, type: 'sine', vol: 0.18 });
      tone({ freq: NOTE.A4 * 1.5, slide: 1.6, at: 0.06, dur: 0.28, type: 'triangle', vol: 0.08 });
    },
    tick: function (progress) {
      tone({ freq: 700 + progress * 900, dur: 0.03, type: 'sine', vol: 0.05 });
    },
    on: function () {
      tone({ freq: 660, dur: 0.08, type: 'triangle', vol: 0.2 });
      tone({ freq: 990, at: 0.07, dur: 0.12, type: 'triangle', vol: 0.2 });
    }
  };

  function play(name, arg) {
    if (sounds[name]) sounds[name](arg);
  }

  /* ---------- Unlock audio on first interaction ---------- */
  function unlock() {
    ensure();
    window.removeEventListener('pointerdown', unlock, true);
    window.removeEventListener('keydown', unlock, true);
  }
  window.addEventListener('pointerdown', unlock, true);
  window.addEventListener('keydown', unlock, true);

  /* ---------- Buttons and links ---------- */
  document.addEventListener(
    'pointerdown',
    function (e) {
      var btn = e.target.closest && e.target.closest('.btn');
      if (btn && !btn.disabled) play('click');
    },
    true
  );

  document.addEventListener(
    'keydown',
    function (e) {
      if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) {
        var btn = e.target.closest && e.target.closest('.btn');
        if (btn && !btn.disabled) play('click');
      }
    },
    true
  );

  document.addEventListener('pointerover', function (e) {
    if (e.pointerType !== 'mouse') return;
    var t = e.target.closest && e.target.closest('.btn, .footer a');
    if (t && !t.contains(e.relatedTarget)) play('hover');
  });

  /* ---------- View changes and progress ---------- */
  function stateOf(view) {
    var icon = view.querySelector('.icon-wrapper');
    if (!icon) return 'primary';
    if (icon.classList.contains('success')) return 'success';
    if (icon.classList.contains('error')) return 'error';
    if (icon.classList.contains('info')) return 'info';
    return 'primary';
  }

  var lastStep = -1;

  var observer = new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      var el = m.target;

      if (m.attributeName === 'class' && el.classList.contains('view-section')) {
        var was = (m.oldValue || '').split(/\s+/).indexOf('active') !== -1;
        if (el.classList.contains('active') && !was) play(stateOf(el));
      }

      if (m.attributeName === 'style' && el.classList.contains('progress-bar')) {
        var pct = parseFloat(el.style.width) || 0;
        var step = Math.floor(pct / 10);
        if (pct === 0) lastStep = -1;
        else if (step > lastStep && pct < 100) {
          lastStep = step;
          play('tick', pct / 100);
        }
      }
    });
  });

  /* ---------- Mute toggle ---------- */
  var ICON_ON =
    '<svg class="sfx-on" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
  var ICON_OFF =
    '<svg class="sfx-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/></svg>';

  function setMuted(value) {
    muted = !!value;
    try {
      localStorage.setItem(STORE_KEY, muted ? '1' : '0');
    } catch (e) {
      /* ignore */
    }
    var btn = document.querySelector('.sfx-toggle');
    if (btn) {
      btn.setAttribute('aria-pressed', String(muted));
      btn.setAttribute('aria-label', muted ? 'Turn sound on' : 'Turn sound off');
    }
  }

  function init() {
    var toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'sfx-toggle';
    toggle.innerHTML = ICON_ON + ICON_OFF;
    document.body.appendChild(toggle);
    setMuted(muted);

    toggle.addEventListener('click', function () {
      setMuted(!muted);
      if (!muted) play('on');
    });

    observer.observe(document.body, {
      subtree: true,
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ['class', 'style']
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.Sfx = {
    play: play,
    setMuted: setMuted,
    get muted() {
      return muted;
    }
  };
})();
