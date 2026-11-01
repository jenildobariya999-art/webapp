// Cloudflare Worker: CORS proxy for the Telebot Creator webhook.
// Only forwards to api.telebotcreator.com/new-webhook (not an open proxy).
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
};

export default {
  async fetch(req) {
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    if (req.method !== "POST") return new Response("POST only", { status: 405, headers: CORS });

    const target = new URL(req.url).searchParams.get("t") || "";
    if (!target.startsWith("https://api.telebotcreator.com/new-webhook?")) {
      return new Response(JSON.stringify({ ok: false, error: "Bad target" }), {
        status: 400, headers: { ...CORS, "Content-Type": "application/json" },
      });
    }

    try {
      const r = await fetch(target, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: await req.text(),
      });
      return new Response(await r.text(), {
        status: r.status,
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    } catch (e) {
      return new Response(JSON.stringify({ ok: false, error: "Proxy error: " + e.message }), {
        status: 502, headers: { ...CORS, "Content-Type": "application/json" },
      });
    }
  },
};
