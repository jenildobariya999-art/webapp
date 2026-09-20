/* ==========================================================================
   sounds.js: loud, clear 8-bit sound effects. No audio files needed;
   everything is synthesised with the Web Audio API.

   Add before </body>:   <script src="sounds.js" defer></script>

   Automatic:
   - Button press: crisp "click-blip" (mouse, touch and keyboard)
   - Hover on buttons / footer links: tiny high tick
   - A .view-section becoming .active plays its sound:
       success = level-up jingle, error = descending buzz,
       info = two-note ding-dong, default = power-up sweep
   - Progress bar ticks upward in pitch as it fills
   - Mute button (remembered). Shows "Tap for sound" until first tap,
     because browsers keep audio locked until the user interacts.
   - On that first tap, the current screen's sound plays once so you
     immediately hear that audio works.

   Manual:  Sfx.play('success'|'error'|'info'|'primary'|'click'|'hover'|'on')
            Sfx.setMuted(true)
   ========================================================================== */
(function () {
  'use strict';

  var STORE_KEY = 'sfx-muted';
  var ctx = null;
  var master = null;
  var muted = false;
  var unlocked = false;

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
      master.gain.value = 0.9;

      /* Soften harsh square-wave overtones without dulling the sound */
      var lp = ctx.createBiquadFilter();
      lp.type = 'lowpass';
      lp.frequency.value = 6500;

      var comp = ctx.createDynamicsCompressor();
      comp.threshold.value = -14;
      comp.ratio.value = 6;

      master.connect(lp);
      lp.connect(comp);
      comp.connect(ctx.destination);
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  /* One note: full volume for most of its length, then a quick decay,
     which is what makes chiptune notes sound clear and punchy. */
  function tone(o) {
    var c = ensure();
    if (!c || muted) return;
    var t0 = c.currentTime + (o.at || 0);
    var dur = o.dur || 0.15;
    var vol = o.vol || 0.3;
    var osc = c.createOscillator();
    var gain = c.createGain();

    osc.type = o.type || 'square';
    osc.frequency.setValueAtTime(o.freq, t0);
    if (o.slide) {
      osc.frequency.exponentialRampToValueAtTime(Math.max(20, o.freq * o.slide), t0 + dur);
    }

    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.linearRampToValueAtTime(vol, t0 + 0.004);
    gain.gain.setValueAtTime(vol, t0 + dur * 0.6);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

    osc.connect(gain);
    gain.connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.05);
  }

  /* Short burst of filtered noise, for the "click" part of a key press */
  function noise(o) {
    var c = ensure();
    if (!c || muted) return;
    var t0 = c.currentTime + (o.at || 0);
    var dur = o.dur || 0.03;
    var size = Math.max(1, Math.floor(c.sampleRate * dur));
    var buf = c.createBuffer(1, size, c.sampleRate);
    var data = buf.getChannelData(0);
    for (var i = 0; i < size; i++) data[i] = Math.random() * 2 - 1;

    var src = c.createBufferSource();
    src.buffer = buf;

    var hp = c.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = o.hp || 2000;

    var gain = c.createGain();
    gain.gain.setValueAtTime(o.vol || 0.25, t0);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

    src.connect(hp);
    hp.connect(gain);
    gain.connect(master);
    src.start(t0);
  }

  /* ---------- Sound library ---------- */
  var sounds = {
    click: function () {
      noise({ dur: 0.03, vol: 0.3, hp: 2500 });
      tone({ freq: 440, slide: 2, dur: 0.08, vol: 0.32 });
    },
    hover: function () {
      tone({ freq: 1319, dur: 0.04, vol: 0.1 });
    },
    success: function () {
      /* Rising four-note run, then a held chord */
      [523.25, 659.25, 783.99, 1046.5].forEach(function (f, i) {
        tone({ freq: f, at: i * 0.08, dur: 0.11, vol: 0.28 });
      });
      [1046.5, 1318.5, 1568].forEach(function (f) {
        tone({ freq: f, at: 0.34, dur: 0.6, type: 'triangle', vol: 0.3 });
      });
      tone({ freq: 1046.5, at: 0.34, dur: 0.25, vol: 0.12 });
    },
    error: function () {
      tone({ freq: 311, slide: 0.5, dur: 0.22, vol: 0.32 });
      tone({ freq: 233, slide: 0.4, at: 0.2, dur: 0.38, vol: 0.32 });
      tone({ freq: 90, dur: 0.6, type: 'sawtooth', vol: 0.22 });
    },
    info: function () {
      tone({ freq: 784, dur: 0.14, vol: 0.28 });
      tone({ freq: 1046.5, at: 0.13, dur: 0.24, vol: 0.28 });
    },
    primary: function () {
      tone({ freq: 196, slide: 4, dur: 0.28, vol: 0.26 });
      tone({ freq: 1175, at: 0.3, dur: 0.14, type: 'triangle', vol: 0.32 });
    },
    tick: function (progress) {
      tone({ freq: 600 + progress * 900, dur: 0.045, vol: 0.14 });
    },
    on: function () {
      tone({ freq: 988, dur: 0.07, vol: 0.3 });
      tone({ freq: 1319, at: 0.07, dur: 0.35, vol: 0.3 });
    }
  };

  function play(name, arg) {
    if (sounds[name]) sounds[name](arg);
  }

  function stateOf(view) {
    var icon = view.querySelector('.icon-wrapper');
    if (!icon) return 'primary';
    if (icon.classList.contains('success')) return 'success';
    if (icon.classList.contains('error')) return 'error';
    if (icon.classList.contains('info')) return 'info';
    return 'primary';
  }

  /* ---------- Unlock audio on first interaction ---------- */
  function unlock(e) {
    ensure();
    if (unlocked) return;
    unlocked = true;
    window.removeEventListener('pointerdown', unlock, true);
    window.removeEventListener('keydown', unlock, true);

    var toggle = document.querySelector('.sfx-toggle');
    if (toggle) toggle.classList.remove('sfx-hint');

    /* Let the person hear that audio works, unless they tapped a control
       that makes its own sound. */
    var t = e && e.target;
    var onControl = t && t.closest && t.closest('.btn, .sfx-toggle');
    if (!onControl) {
      var view = document.querySelector('.view-section.active');
      if (view) play(stateOf(view));
    }
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
        if (pct === 0) {
          lastStep = -1;
        } else if (step > lastStep && pct < 100) {
          lastStep = step;
          play('tick', pct / 100);
        }
      }
    });
  });

  /* ---------- Mute toggle ---------- */
  var ICON_ON =
    '<svg class="sfx-on" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
  var ICON_OFF =
    '<svg class="sfx-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/></svg>';

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
    toggle.className = 'sfx-toggle' + (unlocked ? '' : ' sfx-hint');
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
