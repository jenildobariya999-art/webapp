// Vercel serverless function: same-site proxy so the mini app page can reach the
// Telebot Creator webhook even if the browser blocks cross-site (CORS) calls.
// Place this file at  api/rpc.js  next to miniapp.html in your Vercel project.
const ALLOWED_HOST = 'api.telebotcreator.com';

module.exports = async function (req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'POST only' });

  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = null; } }
  if (!body || typeof body !== 'object') return res.status(400).json({ ok: false, error: 'Bad request' });

  let target;
  try { target = new URL(String(body.api || '')); } catch (e) { return res.status(400).json({ ok: false, error: 'Bad api url' }); }
  if (target.protocol !== 'https:' || target.hostname !== ALLOWED_HOST) {
    return res.status(403).json({ ok: false, error: 'Forbidden host' });
  }

  const payload = Object.assign({}, body);
  delete payload.api;

  try {
    const up = await fetch(target.toString(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const text = await up.text();
    let out;
    try { out = JSON.parse(text); } catch (e) { out = { ok: false, error: 'Bad response from bot server', detail: text.slice(0, 160) }; }
    return res.status(200).json(out);
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Bot server unreachable', detail: String(e && e.message || e) });
  }
};
