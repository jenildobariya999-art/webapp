/* ==========================================================================
   sounds.js: sound + haptics for the verification page.
   Built for phones and Telegram Mini Apps. Three layers, so at least one
   works everywhere:
     1. Web Audio   (best quality, lowest delay)
     2. HTML <audio> fallback (used whenever Web Audio is still locked)
     3. Haptic feedback (Telegram HapticFeedback, else navigator.vibrate)

   Add before </body>:   <script src="sounds.js" defer></script>

   Why the first screen may be silent: phones only allow audio after a
   finished tap. Until then a "Tap for sound" chip shows, and the most
   recent state sound (scanning done, verified, error) plays on that first
   tap so you immediately hear it works.

   If you still hear nothing: raise MEDIA volume (not ringer), turn off
   silent mode, and make sure the speaker button is not crossed out.

   Manual:  Sfx.play('success'|'error'|'info'|'primary'|'click'|'hover'|'on')
            Sfx.setMuted(true)    Sfx.status()
   ========================================================================== */
(function () {
  'use strict';

  var STORE_KEY = 'sfx-muted';
  var SR = 22050; /* sample rate of the generated sounds */

  var ctx = null;
  var master = null;
  var muted = false;
  var activated = false; /* a completed tap/key press has happened */
  var soundedOnDown = false;
  var pendingState = null; /* latest state sound requested before activation */
  var cache = {};

  try {
    muted = localStorage.getItem(STORE_KEY) === '1';
  } catch (e) {
    /* storage unavailable: stay unmuted for this visit */
  }

  /* ======================================================================
     Synthesis: build each sound as raw samples once, then reuse them for
     both Web Audio and the <audio> fallback.
     ====================================================================== */
  function wave(type, p) {
    switch (type) {
      case 'sine':
        return Math.sin(p);
      case 'triangle':
        return (2 / Math.PI) * Math.asin(Math.sin(p));
      case 'saw':
        return 2 * ((p / (2 * Math.PI)) % 1) - 1;
      default:
        /* soft square: a few odd harmonics, so it is bright but not harsh */
        return (4 / Math.PI) * (Math.sin(p) + Math.sin(3 * p) / 3 + Math.sin(5 * p) / 5 + Math.sin(7 * p) / 7);
    }
  }

  function synth(notes, total) {
    var n = Math.ceil(total * SR);
    var out = new Float32Array(n);

    notes.forEach(function (nt) {
      var start = Math.floor((nt.at || 0) * SR);
      var len = Math.floor(nt.dur * SR);
      var vol = nt.vol == null ? 0.4 : nt.vol;
      var phase = 0;
      var prev = 0;

      for (var i = 0; i < len && start + i < n; i++) {
        var t = i / len;
        var attack = Math.min(1, i / (SR * 0.004));
        /* full level for 60% of the note, then a quick decay: punchy and clear */
        var env = t < 0.6 ? 1 : Math.pow(0.0001, (t - 0.6) / 0.4);
        var s;

        if (nt.type === 'noise') {
          var r = Math.random() * 2 - 1;
          s = (r - prev) * 0.5; /* differencing = high-pass, a crisp "tick" */
          prev = r;
        } else {
          var f = nt.freq * (nt.slide ? Math.pow(nt.slide, t) : 1);
          phase += (2 * Math.PI * f) / SR;
          s = wave(nt.type, phase);
        }
        out[start + i] += s * vol * env * attack;
      }
    });

    /* Gentle saturation makes it loud without harsh clipping */
    for (var k = 0; k < n; k++) out[k] = Math.tanh(out[k] * 1.5);
    return out;
  }

  function chord(freqs, at, dur, type, vol) {
    return freqs.map(function (f) {
      return { freq: f, at: at, dur: dur, type: type, vol: vol };
    });
  }

  var defs = {
    click: {
      total: 0.12,
      notes: [
        { type: 'noise', dur: 0.03, vol: 0.55 },
        { freq: 440, slide: 2, dur: 0.08, vol: 0.45 }
      ]
    },
    hover: { total: 0.06, notes: [{ freq: 1319, dur: 0.04, vol: 0.18 }] },
    success: {
      total: 1.05,
      notes: [523.25, 659.25, 783.99, 1046.5]
        .map(function (f, i) {
          return { freq: f, at: i * 0.09, dur: 0.12, vol: 0.45 };
        })
        .concat(chord([1046.5, 1318.5, 1568], 0.38, 0.65, 'triangle', 0.4))
        .concat([{ freq: 1046.5, at: 0.38, dur: 0.3, vol: 0.15 }])
    },
    error: {
      total: 0.7,
      notes: [
        { freq: 311, slide: 0.5, dur: 0.24, vol: 0.5 },
        { freq: 233, slide: 0.4, at: 0.2, dur: 0.4, vol: 0.5 },
        { freq: 90, dur: 0.6, type: 'saw', vol: 0.3 }
      ]
    },
    info: {
      total: 0.45,
      notes: [
        { freq: 784, dur: 0.15, vol: 0.45 },
        { freq: 1046.5, at: 0.13, dur: 0.28, vol: 0.45 }
      ]
    },
    primary: {
      total: 0.5,
      notes: [
        { freq: 196, slide: 4, dur: 0.3, vol: 0.4 },
        { freq: 1175, at: 0.3, dur: 0.16, type: 'triangle', vol: 0.45 }
      ]
    },
    on: {
      total: 0.5,
      notes: [
        { freq: 988, dur: 0.08, vol: 0.45 },
        { freq: 1319, at: 0.08, dur: 0.38, vol: 0.45 }
      ]
    }
  };

  function getSound(name, arg) {
    var key = name;
    var def = defs[name];
    if (name === 'tick') {
      var step = Math.round((arg || 0) * 10);
      key = 'tick' + step;
      def = { total: 0.07, notes: [{ freq: 600 + step * 90, dur: 0.05, vol: 0.28 }] };
    }
    if (!def) return null;
    if (!cache[key]) cache[key] = { samples: synth(def.notes, def.total), buf: null, url: null };
    return cache[key];
  }

  function wavUrl(samples) {
    var n = samples.length;
    var buf = new ArrayBuffer(44 + n * 2);
    var v = new DataView(buf);
    function str(o, s) {
      for (var i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i));
    }
    str(0, 'RIFF');
    v.setUint32(4, 36 + n * 2, true);
    str(8, 'WAVE');
    str(12, 'fmt ');
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);
    v.setUint16(22, 1, true);
    v.setUint32(24, SR, true);
    v.setUint32(28, SR * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    str(36, 'data');
    v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 32767, true);
    }
    return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
  }

  /* ======================================================================
     Playback
     ====================================================================== */
  function getCtx() {
    if (!ctx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try {
        ctx = new AC();
        master = ctx.createGain();
        master.gain.value = 1;
        master.connect(ctx.destination);
      } catch (e) {
        ctx = null;
      }
    }
    return ctx;
  }

  function playWebAudio(entry) {
    var c = getCtx();
    if (!c || c.state !== 'running') return false;
    try {
      if (!entry.buf) {
        entry.buf = c.createBuffer(1, entry.samples.length, SR);
        entry.buf.getChannelData(0).set(entry.samples);
      }
      var src = c.createBufferSource();
      src.buffer = entry.buf;
      src.connect(master);
      src.start(0);
      return true;
    } catch (e) {
      return false;
    }
  }

  function playHtmlAudio(entry) {
    try {
      if (!entry.url) entry.url = wavUrl(entry.samples);
      var a = new Audio(entry.url);
      a.volume = 1;
      var p = a.play();
      if (p && p.catch) p.catch(function () {});
    } catch (e) {
      /* nothing else to try */
    }
  }

  function haptic(kind) {
    if (muted) return;
    try {
      var tg = window.Telegram && window.Telegram.WebApp;
      var h = tg && tg.HapticFeedback;
      if (h) {
        if (kind === 'success') h.notificationOccurred('success');
        else if (kind === 'error') h.notificationOccurred('error');
        else if (kind === 'info') h.notificationOccurred('warning');
        else if (kind === 'tick') h.selectionChanged();
        else if (kind === 'click' || kind === 'on') h.impactOccurred('medium');
        return;
      }
      if (navigator.vibrate) {
        var pattern = { success: [20, 40, 20], error: [70, 40, 70], info: [25], click: 12, on: 12 }[kind];
        if (pattern) navigator.vibrate(pattern);
      }
    } catch (e) {
      /* haptics are a bonus; ignore failures */
    }
  }

  function play(name, arg) {
    if (muted) return;
    var isState = name === 'success' || name === 'error' || name === 'info' || name === 'primary';

    haptic(name);

    if (!activated) {
      /* Audio is still locked: remember the latest state sound and play it
         on the first tap instead of losing it. */
      if (isState) pendingState = name;
      return;
    }

    var entry = getSound(name, arg);
    if (!entry) return;
    if (!playWebAudio(entry)) playHtmlAudio(entry);
  }

  /* ======================================================================
     Unlock: a tap only counts once the finger lifts (pointerup/touchend/
     click), which is why pressing alone is not enough on phones.
     ====================================================================== */
  function stateOf(view) {
    var icon = view.querySelector('.icon-wrapper');
    if (!icon) return 'primary';
    if (icon.classList.contains('success')) return 'success';
    if (icon.classList.contains('error')) return 'error';
    if (icon.classList.contains('info')) return 'info';
    return 'primary';
  }

  function unlock(e) {
    var first = !activated;
    activated = true;

    var c = getCtx();
    if (c) {
      try {
        if (c.state !== 'running') c.resume();
        /* play one silent sample: fully unlocks iOS */
        var b = c.createBuffer(1, 1, SR);
        var s = c.createBufferSource();
        s.buffer = b;
        s.connect(c.destination);
        s.start(0);
      } catch (err) {
        /* fall back to <audio> */
      }
    }

    if (first) {
      var toggle = document.querySelector('.sfx-toggle');
      if (toggle) toggle.classList.remove('sfx-hint');

      /* Silent <audio> once: on iPhones this lets sound play even with the
         ring/silent switch on. */
      try {
        var silent = new Audio(wavUrl(new Float32Array(Math.floor(SR * 0.05))));
        var sp = silent.play();
        if (sp && sp.catch) sp.catch(function () {});
      } catch (err2) {
        /* ignore */
      }

      /* Let the person hear it work right away, unless they tapped a
         control that makes its own sound. */
      var t = e && e.target;
      var onControl = t && t.closest && t.closest('.btn, .sfx-toggle');
      if (!onControl && !muted) {
        var name = pendingState;
        if (!name) {
          var view = document.querySelector('.view-section.active');
          if (view) name = stateOf(view);
        }
        if (name) {
          var entry = getSound(name);
          if (entry && !playWebAudio(entry)) playHtmlAudio(entry);
        }
      }
      pendingState = null;
    }
  }

  ['pointerup', 'touchend', 'click', 'keydown'].forEach(function (evt) {
    window.addEventListener(evt, unlock, true);
  });

  /* ---------- Buttons ---------- */
  document.addEventListener(
    'pointerdown',
    function (e) {
      var btn = e.target.closest && e.target.closest('.btn');
      soundedOnDown = false;
      if (btn && !btn.disabled && activated) {
        play('click');
        soundedOnDown = true;
      }
    },
    true
  );

  /* click also covers keyboard presses and the very first (locked) tap */
  document.addEventListener(
    'click',
    function (e) {
      var btn = e.target.closest && e.target.closest('.btn');
      if (btn && !btn.disabled && !soundedOnDown) play('click');
      soundedOnDown = false;
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
          if (activated) play('tick', pct / 100);
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
    toggle.className = 'sfx-toggle' + (activated ? '' : ' sfx-hint');
    toggle.innerHTML = ICON_ON + ICON_OFF;
    document.body.appendChild(toggle);
    setMuted(muted);

    toggle.addEventListener('click', function () {
      setMuted(!muted);
      if (!muted) play('on'); /* a beep confirms sound is working */
    });

    observer.observe(document.body, {
      subtree: true,
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ['class', 'style']
    });

    /* Catch the screen that was already showing before this script ran */
    var view = document.querySelector('.view-section.active');
    if (view && !activated) pendingState = stateOf(view);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.Sfx = {
    play: play,
    setMuted: setMuted,
    status: function () {
      return { activated: activated, muted: muted, webAudio: ctx ? ctx.state : 'not created' };
    },
    get muted() {
      return muted;
    }
  };
})();
