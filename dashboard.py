"""dashboard.py – the web dashboard: login, overview, bot-wise data, broadcast to one/all bots, CSV export."""
import asyncio
import csv
import hashlib
import hmac
import html
import io
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web

import core

log = logging.getLogger("dashboard")
COOKIE = "bh_session"
SESSION_DAYS = 7
TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


def jerr(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ═══════════════════════════ auth ═══════════════════════════
def _sign(msg: str) -> str:
    return hmac.new(core.SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_sign('session:' + str(exp))}"


def valid_token(tok: str) -> bool:
    exp, _, sig = (tok or "").partition(".")
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign("session:" + exp))


_attempts: dict[str, list[float]] = {}


def _too_many(ip: str) -> bool:
    now = time.time()
    lst = [t for t in _attempts.get(ip, []) if now - t < 300]
    _attempts[ip] = lst
    return len(lst) >= 8


@web.middleware
async def guard(request: web.Request, handler):
    path = request.path
    if path.startswith("/api/"):
        if not core.DASHBOARD_PASSWORD:
            return jerr("Dashboard is disabled – set DASHBOARD_PASSWORD.", 503)
        if request.method not in ("GET", "HEAD") and request.headers.get("X-Requested-With") != "dashboard":
            return jerr("Bad request", 403)  # CSRF guard: browsers can't add this header cross-site
        if path != "/api/login" and not valid_token(request.cookies.get(COOKIE, "")):
            return jerr("Please sign in", 401)
    return await handler(request)


async def login(request: web.Request):
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote or "?"
    if _too_many(ip):
        return jerr("Too many attempts – wait a few minutes.", 429)
    try:
        pw = str((await request.json()).get("password", ""))
    except Exception:
        return jerr("Bad request")
    if not hmac.compare_digest(pw.encode(), core.DASHBOARD_PASSWORD.encode()):
        _attempts.setdefault(ip, []).append(time.time())
        return jerr("Wrong password", 401)
    resp = web.json_response({"ok": True})
    https = request.secure or request.headers.get("X-Forwarded-Proto") == "https"
    resp.set_cookie(COOKIE, make_token(), max_age=SESSION_DAYS * 86400, httponly=True, samesite="Strict", secure=https)
    return resp


async def logout(request: web.Request):
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE)
    return resp


async def me(request: web.Request):
    return web.json_response({"ok": True, "webhook": bool(core.WEBHOOK_URL)})


async def page(request: web.Request):
    if not core.DASHBOARD_PASSWORD:
        body = ("<body style='font-family:sans-serif;background:#090913;color:#eee;padding:40px'>"
                "<h2>Dashboard disabled</h2><p>Set the <code>DASHBOARD_PASSWORD</code> environment variable and redeploy.</p>")
        return web.Response(text=body, content_type="text/html")
    return web.Response(text=PAGE, content_type="text/html", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


# ═══════════════════════════ overview / data ═══════════════════════════
async def overview(request: web.Request):
    mgr = request.app["mgr"]
    bots = await core.fetch("SELECT id, username, name, active FROM bots ORDER BY created, id")
    agg = {r["bot_id"]: r for r in await core.fetch(
        "SELECT bot_id, COUNT(*) AS total,"
        " SUM(CASE WHEN blocked=0 AND banned=0 THEN 1 ELSE 0 END) AS act,"
        " SUM(CASE WHEN blocked=1 THEN 1 ELSE 0 END) AS blk,"
        " SUM(CASE WHEN banned=1 THEN 1 ELSE 0 END) AS ban,"
        " SUM(CASE WHEN verified=1 THEN 1 ELSE 0 END) AS ver FROM bot_users GROUP BY bot_id")}
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    new24 = {r["bot_id"]: int(r["n"]) for r in await core.fetch(
        "SELECT bot_id, COUNT(*) AS n FROM bot_users WHERE created >= :s GROUP BY bot_id", s=core.ts(now - timedelta(days=1)))}
    daily: dict[int, dict[str, int]] = {}
    for r in await core.fetch("SELECT bot_id, created FROM bot_users WHERE created >= :s", s=core.ts(today - timedelta(days=6))):
        d = str(r["created"])[:10]
        daily.setdefault(r["bot_id"], {}).setdefault(d, 0)
        daily[r["bot_id"]][d] += 1
    chans = {r["bot_id"]: int(r["n"]) for r in await core.fetch("SELECT bot_id, COUNT(*) AS n FROM bot_channels GROUP BY bot_id")}

    out, totals = [], {"bots": len(bots), "users": 0, "active": 0, "new24": 0}
    for b in bots:
        a = agg.get(b["id"], {})
        item = {
            "id": b["id"], "username": b["username"], "name": b["name"], "active": bool(b["active"]),
            "state": mgr.state.get(b["id"], ""), "users": int(a.get("total") or 0), "active_users": int(a.get("act") or 0),
            "blocked": int(a.get("blk") or 0), "banned": int(a.get("ban") or 0), "verified": int(a.get("ver") or 0),
            "channels": chans.get(b["id"], 0), "new24": new24.get(b["id"], 0),
            "daily": [daily.get(b["id"], {}).get(d, 0) for d in days],
        }
        totals["users"] += item["users"]
        totals["active"] += item["active_users"]
        totals["new24"] += item["new24"]
        out.append(item)
    return web.json_response({"bots": out, "totals": totals})


async def add_bot(request: web.Request):
    mgr = request.app["mgr"]
    try:
        token = str((await request.json()).get("token", "")).strip()
    except Exception:
        return jerr("Bad request")
    if not TOKEN_RE.match(token):
        return jerr("That doesn't look like a bot token (123456789:AA…).")
    try:
        info = await mgr.add_token(token)
    except ValueError as e:
        return jerr(str(e), 409 if "already" in str(e) else 400)
    return web.json_response(info)


def _bid(request: web.Request) -> int:
    try:
        return int(request.match_info["bid"])
    except ValueError:
        raise web.HTTPNotFound()


async def toggle_bot(request: web.Request):
    mgr, bid = request.app["mgr"], _bid(request)
    row = await core.fetchone("SELECT * FROM bots WHERE id=:i", i=bid)
    if not row:
        return jerr("Bot not found", 404)
    if row["active"]:
        await core.execute("UPDATE bots SET active=0 WHERE id=:i", i=bid)
        await mgr.deactivate(bid)
    else:
        await core.execute("UPDATE bots SET active=1 WHERE id=:i", i=bid)
        await mgr.activate({**row, "active": 1})
    return web.json_response({"ok": True})


async def delete_bot(request: web.Request):
    mgr, bid = request.app["mgr"], _bid(request)
    if not await core.fetchone("SELECT 1 AS x FROM bots WHERE id=:i", i=bid):
        return jerr("Bot not found", 404)
    await mgr.remove(bid)
    return web.json_response({"ok": True})


def _status_cond(status: str) -> str:
    return {"active": " AND blocked=0 AND banned=0", "blocked": " AND blocked=1", "banned": " AND banned=1"}.get(status, "")


async def bot_users(request: web.Request):
    bid = _bid(request)
    q = request.query.get("q", "").strip()
    page = max(1, int(request.query.get("page", "1") or 1)) if request.query.get("page", "1").isdigit() else 1
    per = 25
    cond, params = "bot_id=:b", {"b": bid}
    if q:
        if q.lstrip("@").isdigit():
            cond += " AND id=:qi"
            params["qi"] = int(q.lstrip("@"))
        else:
            cond += " AND (LOWER(COALESCE(username,'')) LIKE :ql OR LOWER(COALESCE(first_name,'')) LIKE :ql)"
            params["ql"] = f"%{q.lstrip('@').lower()}%"
    cond += _status_cond(request.query.get("status", ""))
    total = int(await core.scalar(f"SELECT COUNT(*) FROM bot_users WHERE {cond}", **params) or 0)
    pages = max(1, -(-total // per))
    page = min(page, pages)
    rows = await core.fetch(
        f"SELECT id, username, first_name, blocked, banned, verified, created FROM bot_users WHERE {cond} "
        f"ORDER BY created DESC, id DESC LIMIT :lim OFFSET :off", lim=per, off=(page - 1) * per, **params)
    items = [{"id": r["id"], "username": r["username"], "first_name": r["first_name"],
              "status": "banned" if r["banned"] else ("blocked" if r["blocked"] else "active"),
              "verified": bool(r["verified"]), "created": str(r["created"])[:16]} for r in rows]
    return web.json_response({"items": items, "total": total, "page": page, "pages": pages})


async def bot_channels(request: web.Request):
    rows = await core.fetch("SELECT chat_id, title, link, request_mode, unchecked, kind FROM bot_channels "
                            "WHERE bot_id=:b ORDER BY pos, added, chat_id", b=_bid(request))
    return web.json_response({"items": [{**r, "request_mode": bool(r["request_mode"]), "unchecked": bool(r["unchecked"])} for r in rows]})


# ═══════════════════════════ CSV export ═══════════════════════════
def _safe(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # stop spreadsheet formula injection


async def export_csv(request: web.Request):
    wanted = request.query.get("bots", "all")
    status = request.query.get("status", "all")
    bots = await core.fetch("SELECT id, username, name FROM bots ORDER BY id")
    if wanted != "all":
        ids = {int(x) for x in wanted.split(",") if x.strip().isdigit()}
        bots = [b for b in bots if b["id"] in ids]
    if not bots:
        return jerr("No bots selected", 404)
    name = f"users_{bots[0]['username']}" if len(bots) == 1 else "users_all_bots"
    resp = web.StreamResponse(headers={
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9_.-]", "_", name)}_{utcnow():%Y%m%d}.csv"'})
    await resp.prepare(request)
    head = io.StringIO()
    csv.writer(head).writerow(["bot_id", "bot_username", "user_id", "username", "first_name", "status", "passed_force_join", "joined_at"])
    await resp.write(b"\xef\xbb\xbf" + head.getvalue().encode())  # BOM so Excel reads UTF-8
    cond = _status_cond(status)
    for b in bots:
        last = -(1 << 62)
        while True:
            rows = await core.fetch(
                f"SELECT id, username, first_name, blocked, banned, verified, created FROM bot_users "
                f"WHERE bot_id=:b AND id>:l{cond} ORDER BY id LIMIT 2000", b=b["id"], l=last)
            if not rows:
                break
            buf = io.StringIO()
            w = csv.writer(buf)
            for r in rows:
                w.writerow([b["id"], _safe(b["username"]), r["id"], _safe(r["username"]), _safe(r["first_name"]),
                            "banned" if r["banned"] else ("blocked" if r["blocked"] else "active"),
                            "yes" if r["verified"] else "no", str(r["created"])[:19]])
            await resp.write(buf.getvalue().encode())
            last = rows[-1]["id"]
    await resp.write_eof()
    return resp


# ═══════════════════════════ broadcast engine ═══════════════════════════
SYSTEMIC = ("parse entities", "wrong file", "image", "photo", "button", "too long", "url")
JOBS: dict[str, "Job"] = {}
_tasks: set = set()


class Job:
    def __init__(self, text: str, bots: dict[int, str]):
        self.id = secrets.token_hex(4)
        self.state = "running"
        self.cancel = False
        self.error: str | None = None
        self.started = utcnow().strftime("%Y-%m-%d %H:%M UTC")
        self.preview = re.sub(r"<[^>]+>", "", text).strip().replace("\n", " ")[:70]
        self.per = {bid: {"username": u, "total": 0, "sent": 0, "blocked": 0, "failed": 0, "state": "queued", "last_error": ""}
                    for bid, u in bots.items()}

    def dict(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "preview": self.preview,
                "started": self.started, "bots": self.per}


def _photo_ok(data: bytes) -> bool:
    return data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


async def _run_bot(job: Job, bid: int, bot, text: str, photo: bytes | None, markup):
    st = job.per[bid]
    rows = await core.fetch("SELECT id, first_name FROM bot_users WHERE bot_id=:b AND blocked=0 AND banned=0 ORDER BY id", b=bid)
    st["total"], st["state"] = len(rows), "sending"
    ctx = {"file_id": None}

    async def send(uid: int, name: str):
        body = text.replace("{name}", html.escape(name or "friend"))
        for _ in range(2):
            try:
                if photo:
                    src = ctx["file_id"] or BufferedInputFile(photo, "image")
                    if len(body) <= 1024:
                        msg = await bot.send_photo(uid, src, caption=body or None, reply_markup=markup)
                    else:
                        msg = await bot.send_photo(uid, src)
                        await bot.send_message(uid, body, reply_markup=markup)
                    if not ctx["file_id"]:
                        ctx["file_id"] = msg.photo[-1].file_id  # reuse: upload the image only once per bot
                else:
                    await bot.send_message(uid, body, reply_markup=markup)
                st["sent"] += 1
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                await core.execute("UPDATE bot_users SET blocked=1 WHERE bot_id=:b AND id=:i", b=bid, i=uid)
                st["blocked"] += 1
                return
            except TelegramBadRequest as e:
                st["failed"] += 1
                st["last_error"] = str(e)[:140]
                return
            except Exception as e:
                st["failed"] += 1
                st["last_error"] = str(e)[:140]
                return
        st["failed"] += 1

    i = 0
    if photo and rows:  # first message alone, to learn the file_id
        await send(rows[0]["id"], rows[0]["first_name"])
        i = 1
    while i < len(rows) and not job.cancel:
        batch = rows[i:i + 25]  # ~25 msgs/s per bot, under Telegram's 30/s limit
        await asyncio.gather(*(send(r["id"], r["first_name"]) for r in batch))
        i += 25
        low = st["last_error"].lower()
        if st["sent"] == 0 and st["failed"] >= min(5, len(rows)) and any(k in low for k in SYSTEMIC):
            job.error = f"@{st['username']}: Telegram rejected the message — {st['last_error']}"
            job.cancel = True
            break
        await asyncio.sleep(1)
    st["state"] = "done"


async def _run_job(job: Job, bots: dict, text: str, photo, markup):
    await asyncio.gather(*(_run_bot(job, bid, bots[bid], text, photo, markup) for bid in bots), return_exceptions=True)
    job.state = "error" if job.error else ("stopped" if job.cancel else "done")
    while len(JOBS) > 15:  # keep the list short
        old = next((k for k, v in JOBS.items() if v.state != "running"), None)
        if not old:
            break
        JOBS.pop(old)


async def start_broadcast(request: web.Request):
    mgr = request.app["mgr"]
    form = await request.post()
    text = str(form.get("text", "")).strip()
    photo = None
    f = form.get("photo")
    if f is not None and hasattr(f, "file") and getattr(f, "filename", ""):
        photo = f.file.read()
        if len(photo) > 6 * 1024 * 1024 or not _photo_ok(photo):
            return jerr("Photo must be a JPG, PNG or WebP under 6 MB.")
    if not text and not photo:
        return jerr("Write a message or add a photo.")
    bt, bu = str(form.get("btn_text", "")).strip(), str(form.get("btn_url", "")).strip()
    markup = None
    if bt or bu:
        if not (bt and bu.startswith(("http://", "https://"))):
            return jerr("A link button needs a text and a URL starting with https://")
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=bt[:60], url=bu)]])
    ids = {int(x) for x in str(form.get("bots", "")).split(",") if x.strip().isdigit()}
    names = {r["id"]: r["username"] for r in await core.fetch("SELECT id, username FROM bots")}
    bots = {bid: mgr.bots[bid] for bid in ids if bid in mgr.bots}
    if not bots:
        return jerr("Pick at least one running bot.")
    busy = [bid for j in JOBS.values() if j.state == "running" for bid in j.per if bid in bots]
    if busy:
        return jerr(f"A broadcast is already running for @{names.get(busy[0], busy[0])}.", 409)
    job = Job(text, {bid: names.get(bid) or str(bid) for bid in bots})
    JOBS[job.id] = job
    t = asyncio.create_task(_run_job(job, bots, text, photo, markup))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return web.json_response({"id": job.id})


async def list_jobs(request: web.Request):
    return web.json_response({"items": [j.dict() for j in reversed(list(JOBS.values()))]})


async def get_job(request: web.Request):
    j = JOBS.get(request.match_info["jid"])
    return web.json_response(j.dict()) if j else jerr("Not found", 404)


async def stop_job(request: web.Request):
    j = JOBS.get(request.match_info["jid"])
    if not j:
        return jerr("Not found", 404)
    j.cancel = True
    return web.json_response({"ok": True})


# ═══════════════════════════ wiring ═══════════════════════════
def setup(app: web.Application, mgr):
    app["mgr"] = mgr
    app.middlewares.append(guard)
    r = app.router
    r.add_get("/", page)
    r.add_post("/api/login", login)
    r.add_post("/api/logout", logout)
    r.add_get("/api/me", me)
    r.add_get("/api/overview", overview)
    r.add_post("/api/bots", add_bot)
    r.add_post("/api/bots/{bid}/toggle", toggle_bot)
    r.add_delete("/api/bots/{bid}", delete_bot)
    r.add_get("/api/bots/{bid}/users", bot_users)
    r.add_get("/api/bots/{bid}/channels", bot_channels)
    r.add_get("/api/export", export_csv)
    r.add_post("/api/broadcast", start_broadcast)
    r.add_get("/api/broadcast", list_jobs)
    r.add_get("/api/broadcast/{jid}", get_job)
    r.add_post("/api/broadcast/{jid}/stop", stop_job)


PAGE = r'''
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#090913">
<title>BotHub · multi-bot dashboard</title>
<style>
:root{--bg:#090913;--panel:rgba(255,255,255,.055);--panel2:rgba(255,255,255,.09);--line:rgba(255,255,255,.11);
--text:#eef0ff;--muted:#9aa1c7;--a:#7c5cff;--b:#22d3ee;--ok:#34d399;--warn:#fbbf24;--bad:#fb7185;--r:18px}
*{box-sizing:border-box}
html,body{margin:0}
body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text);background:var(--bg);min-height:100vh;
background-image:radial-gradient(900px 520px at 8% -8%,rgba(124,92,255,.38),transparent 60%),radial-gradient(800px 500px at 100% 0,rgba(34,211,238,.22),transparent 55%),radial-gradient(700px 500px at 50% 125%,rgba(251,113,133,.16),transparent 60%);background-attachment:fixed}
button,input,select,textarea{font:inherit;color:inherit}
[hidden]{display:none!important}
.glass{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
.btn{border:1px solid var(--line);background:var(--panel2);padding:10px 16px;border-radius:12px;cursor:pointer;font-weight:600;font-size:14px;transition:.18s;display:inline-flex;align-items:center;gap:8px;white-space:nowrap}
.btn:hover{transform:translateY(-1px);background:rgba(255,255,255,.14)}
.btn.primary{background:linear-gradient(135deg,var(--a),#5b8cff 55%,var(--b));border-color:transparent;box-shadow:0 8px 24px rgba(124,92,255,.35)}
.btn.danger{background:rgba(251,113,133,.16);border-color:rgba(251,113,133,.4);color:#ffc2cc}
.btn.sm{padding:7px 11px;font-size:13px;border-radius:10px}
.btn:disabled{opacity:.5;pointer-events:none}
input,select,textarea{width:100%;background:rgba(0,0,0,.28);border:1px solid var(--line);border-radius:12px;padding:11px 13px;outline:none;transition:.15s}
input:focus,select:focus,textarea:focus{border-color:var(--a);box-shadow:0 0 0 3px rgba(124,92,255,.25)}
textarea{min-height:150px;resize:vertical;line-height:1.45}
label{font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.04em;text-transform:uppercase;display:block;margin:14px 0 7px}
.muted{color:var(--muted)}
/* layout */
#app{display:flex;min-height:100vh}
.side{width:230px;padding:22px 16px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:6px;border-right:1px solid var(--line);background:rgba(8,8,18,.55);backdrop-filter:blur(14px)}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;padding:4px 10px 18px}
.logo i{width:34px;height:34px;border-radius:11px;background:linear-gradient(135deg,var(--a),var(--b));display:grid;place-items:center;font-style:normal;box-shadow:0 8px 20px rgba(124,92,255,.4)}
.nav{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:12px;cursor:pointer;color:var(--muted);font-weight:600;border:0;background:none;text-align:left;width:100%}
.nav:hover{background:var(--panel)}
.nav.on{background:linear-gradient(135deg,rgba(124,92,255,.35),rgba(34,211,238,.18));color:#fff}
.spacer{flex:1}
main{flex:1;min-width:0;padding:26px clamp(16px,3vw,40px) 60px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:22px;flex-wrap:wrap}
h1{margin:0;font-size:clamp(22px,3vw,30px);letter-spacing:-.02em}
h2{margin:0 0 4px;font-size:18px}
.sub{color:var(--muted);font-size:14px;margin-top:4px}
/* stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:22px}
.stat{padding:18px;position:relative;overflow:hidden}
.stat b{display:block;font-size:30px;letter-spacing:-.02em;margin-top:6px}
.stat span{font-size:13px;color:var(--muted);font-weight:600}
.stat::after{content:"";position:absolute;right:-30px;top:-30px;width:100px;height:100px;border-radius:50%;background:var(--c,var(--a));opacity:.22;filter:blur(20px)}
/* bots */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.bot{padding:18px;display:flex;flex-direction:column;gap:14px;transition:.2s}
.bot:hover{transform:translateY(-3px);border-color:rgba(124,92,255,.55);box-shadow:0 18px 40px rgba(0,0,0,.35)}
.bhead{display:flex;gap:12px;align-items:center}
.av{width:46px;height:46px;border-radius:15px;display:grid;place-items:center;font-weight:800;font-size:19px;flex:none;color:#fff}
.bname{font-weight:700;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bhead>div:nth-child(2){min-width:0;flex:1}
.chip{font-size:11px;font-weight:700;padding:4px 9px;border-radius:99px;border:1px solid var(--line);display:inline-flex;gap:6px;align-items:center;white-space:nowrap}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted)}
.ok{color:#9af0cc;background:rgba(52,211,153,.12);border-color:rgba(52,211,153,.35)}.ok .dot{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.warn{color:#fde08a;background:rgba(251,191,36,.12);border-color:rgba(251,191,36,.35)}.warn .dot{background:var(--warn)}
.bad{color:#ffc2cc;background:rgba(251,113,133,.12);border-color:rgba(251,113,133,.35)}.bad .dot{background:var(--bad)}
.nums{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.nums div{background:rgba(0,0,0,.22);border-radius:12px;padding:9px 6px;text-align:center}
.nums b{display:block;font-size:16px}.nums span{font-size:10.5px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.acts{display:flex;gap:8px;flex-wrap:wrap}
.add{border:2px dashed var(--line);background:transparent;cursor:pointer;align-items:center;justify-content:center;min-height:230px;color:var(--muted);font-weight:700;gap:10px}
.add:hover{color:#fff;border-color:var(--a)}
.add i{font-style:normal;font-size:34px;width:60px;height:60px;border-radius:20px;display:grid;place-items:center;background:var(--panel2)}
/* broadcast */
.cols{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:18px}
.card{padding:20px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.pick{padding:8px 13px;border-radius:99px;border:1px solid var(--line);background:var(--panel);cursor:pointer;font-size:13px;font-weight:600;user-select:none;transition:.15s}
.pick.on{background:linear-gradient(135deg,rgba(124,92,255,.55),rgba(34,211,238,.35));border-color:transparent;color:#fff}
.tools{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.tools button{padding:6px 11px;border-radius:9px;border:1px solid var(--line);background:var(--panel);cursor:pointer;font-weight:700}
.tools button:hover{background:var(--panel2)}
.phone{background:#0e1621;border-radius:22px;padding:16px;border:1px solid var(--line);min-height:200px}
.bubble{background:#182533;border-radius:14px 14px 14px 4px;padding:10px 12px;max-width:100%;font-size:14.5px;line-height:1.45;word-wrap:break-word;overflow-wrap:anywhere}
.bubble img{width:100%;border-radius:10px;display:block;margin-bottom:8px}
.bubble a{color:#6ab3f3}.bubble code,.bubble pre{background:rgba(255,255,255,.08);padding:1px 5px;border-radius:5px}
.bubble .tbtn{margin-top:8px;text-align:center;padding:8px;border-radius:9px;background:rgba(106,179,243,.18);color:#8cc8ff;font-weight:600}
.bar{height:8px;border-radius:9px;background:rgba(255,255,255,.1);overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--a),var(--b));transition:width .5s}
.jobrow{padding:12px 0;border-bottom:1px solid var(--line)}
.jobrow:last-child{border:0}
.row{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
/* modal + drawer */
.back{position:fixed;inset:0;background:rgba(3,3,10,.66);backdrop-filter:blur(4px);z-index:50;display:grid;place-items:center;padding:16px}
.modal{width:min(480px,100%);padding:24px;background:#12121f}
.drawer{position:fixed;right:0;top:0;bottom:0;width:min(780px,100%);z-index:60;background:#0f0f1c;border-left:1px solid var(--line);overflow:auto;padding:22px;animation:slide .22s ease}
@keyframes slide{from{transform:translateX(40px);opacity:0}}
.tabs{display:flex;gap:6px;margin:16px 0}
.tabs button{border:0;background:var(--panel);padding:9px 15px;border-radius:10px;cursor:pointer;font-weight:700;color:var(--muted)}
.tabs button.on{background:var(--a);color:#fff}
table{width:100%;border-collapse:collapse;font-size:14px}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);text-align:left;padding:8px 10px}
td{padding:11px 10px;border-top:1px solid var(--line)}
.tw{overflow-x:auto}
.toast{position:fixed;left:50%;bottom:90px;transform:translateX(-50%);padding:12px 18px;border-radius:14px;background:#1b1b2e;border:1px solid var(--line);z-index:99;font-weight:600;box-shadow:0 14px 40px rgba(0,0,0,.5);max-width:92vw}
.toast.err{border-color:var(--bad);color:#ffc2cc}.toast.good{border-color:var(--ok)}
/* login */
#login{min-height:100vh;display:grid;place-items:center;padding:20px}
.lcard{width:min(420px,100%);padding:30px}
/* mobile */
@media(max-width:860px){
 #app{display:block}
 .side{position:fixed;inset:auto 0 0 0;top:auto;height:auto;width:auto;flex-direction:row;padding:8px 10px calc(8px + env(safe-area-inset-bottom));border-right:0;border-top:1px solid var(--line);z-index:40;gap:2px;justify-content:space-around}
 .logo,.spacer,.side .out{display:none}
 .nav{flex-direction:column;gap:3px;padding:8px 10px;font-size:11px;text-align:center;width:auto;flex:1}
 main{padding-bottom:110px}
 .cols{grid-template-columns:1fr}
 .nums{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>

<div id="login" hidden>
  <div class="glass lcard">
    <div class="logo" style="padding:0 0 18px"><i>🤖</i>BotHub</div>
    <h2>Welcome back</h2>
    <div class="sub">Sign in to manage all your bots.</div>
    <label>Password</label>
    <input id="pw" type="password" placeholder="Dashboard password" autocomplete="current-password">
    <button class="btn primary" id="loginBtn" style="width:100%;justify-content:center;margin-top:18px">Sign in</button>
    <div id="lerr" class="sub" style="color:var(--bad);margin-top:12px"></div>
  </div>
</div>

<div id="app" hidden>
  <aside class="side">
    <div class="logo"><i>🤖</i>BotHub</div>
    <button class="nav on" data-view="overview"><span>📊</span>Overview</button>
    <button class="nav" data-view="broadcast"><span>📣</span>Broadcast</button>
    <button class="nav" data-view="export"><span>📥</span>Export</button>
    <div class="spacer"></div>
    <button class="nav out" id="logout"><span>🚪</span>Sign out</button>
  </aside>
  <main>
    <div class="top">
      <div><h1 id="title">Overview</h1><div class="sub" id="subtitle"></div></div>
      <div class="acts"><button class="btn" id="refresh">↻ Refresh</button><button class="btn primary" id="addTop">＋ Add bot</button></div>
    </div>

    <section id="v-overview">
      <div class="stats" id="stats"></div>
      <div class="grid" id="bots"></div>
    </section>

    <section id="v-broadcast" hidden>
      <div class="cols">
        <div class="glass card">
          <h2>New broadcast</h2>
          <div class="sub">Send one message to the users of one bot, several bots, or all of them.</div>
          <label>Send to</label>
          <div class="chips" id="targets"></div>
          <label>Message</label>
          <div class="tools">
            <button data-t="b"><b>B</b></button><button data-t="i"><i>I</i></button><button data-t="u"><u>U</u></button>
            <button data-t="s"><s>S</s></button><button data-t="code">&lt;/&gt;</button><button data-t="a">🔗</button><button data-t="tg-spoiler">👁</button>
          </div>
          <textarea id="text" placeholder="Write your message…  Use {name} for the user's first name. Telegram HTML works (b, i, u, s, code, a, tg-spoiler, tg-emoji)."></textarea>
          <div class="two">
            <div><label>Photo (optional)</label><input id="photo" type="file" accept="image/png,image/jpeg,image/webp"></div>
            <div><label>Link button text</label><input id="btnText" placeholder="Open now"></div>
          </div>
          <label>Link button URL</label>
          <input id="btnUrl" placeholder="https://…">
          <div class="row" style="margin-top:18px">
            <div class="sub" id="reach"></div>
            <button class="btn primary" id="send">🚀 Send broadcast</button>
          </div>
        </div>
        <div>
          <div class="glass card"><h2>Live preview</h2><div class="sub" style="margin-bottom:12px">How it looks in Telegram</div>
            <div class="phone"><div class="bubble" id="pv"></div></div></div>
          <div class="glass card" style="margin-top:18px"><h2>Progress</h2><div id="job" class="sub">No broadcast running.</div></div>
        </div>
      </div>
      <div class="glass card" style="margin-top:18px"><h2>Recent broadcasts</h2><div id="history" class="sub">—</div></div>
    </section>

    <section id="v-export" hidden>
      <div class="glass card" style="margin-bottom:18px">
        <div class="row"><div><h2>Export everything</h2><div class="sub">One CSV with the users of every bot (bot, user id, username, name, status, joined).</div></div>
          <div class="acts"><select id="exStatus" style="width:auto"><option value="all">All users</option><option value="active">Active only</option><option value="blocked">Blocked bot</option><option value="banned">Banned</option></select>
          <a class="btn primary" id="exAll" href="#">📥 Download all bots</a></div></div>
      </div>
      <div class="glass card"><h2>Per bot</h2><div id="exList" style="margin-top:10px"></div></div>
    </section>
  </main>
</div>

<div class="back" id="addModal" hidden>
  <div class="glass modal">
    <h2>Add a new bot</h2>
    <div class="sub">Create a bot with <b>@BotFather</b>, then paste its token here. It starts instantly — no redeploy.</div>
    <label>Bot token</label>
    <input id="tok" placeholder="123456789:AA…" autocomplete="off" spellcheck="false">
    <div id="aerr" class="sub" style="color:var(--bad);margin-top:10px"></div>
    <div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn" id="addCancel">Cancel</button><button class="btn primary" id="addGo">Add bot</button></div>
  </div>
</div>

<div id="drawerWrap"></div>
<div id="toast" class="toast" hidden></div>

<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nf=n=>Number(n||0).toLocaleString();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let BOTS=[],VIEW="overview",SEL=new Set(),JOBPOLL=0;

function toast(m,t){const e=$("#toast");e.textContent=m;e.className="toast "+(t||"");e.hidden=false;clearTimeout(toast.t);toast.t=setTimeout(()=>e.hidden=true,3200)}
async function api(path,o={}){
  const opt={credentials:"same-origin",method:o.method||"GET",headers:{"X-Requested-With":"dashboard"}};
  if(o.json){opt.method=o.method||"POST";opt.headers["Content-Type"]="application/json";opt.body=JSON.stringify(o.json)}
  if(o.form){opt.method="POST";opt.body=o.form}
  const r=await fetch(path,opt);
  if(r.status===401&&path!=="/api/login"){showLogin();throw new Error("Please sign in")}
  let d={};try{d=await r.json()}catch(e){}
  if(!r.ok)throw new Error(d.error||("Error "+r.status));
  return d;
}
function showLogin(){$("#app").hidden=true;$("#login").hidden=false;$("#pw").focus()}
function showApp(){$("#login").hidden=true;$("#app").hidden=false}
const hue=id=>{let h=0;for(const c of String(id))h=(h*31+c.charCodeAt(0))%360;return h};
const avatar=b=>`<div class="av" style="background:linear-gradient(135deg,hsl(${hue(b.id)} 80% 58%),hsl(${(hue(b.id)+50)%360} 85% 52%))">${esc((b.name||b.username||"?").trim().charAt(0).toUpperCase())}</div>`;
const statusChip=b=>{const s=b.state||"";const c=!b.active?"warn":s.startsWith("online")?"ok":s.startsWith("error")?"bad":"warn";return `<span class="chip ${c}"><i class="dot"></i>${esc(!b.active?"paused":(s||"starting"))}</span>`};
function spark(a){const w=120,h=34,m=Math.max(1,...a);const pts=a.map((v,i)=>`${(i*(w/(a.length-1))).toFixed(1)},${(h-3-(v/m)*(h-8)).toFixed(1)}`).join(" ");
  return `<svg width="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:38px"><defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#7c5cff"/><stop offset="1" stop-color="#22d3ee"/></linearGradient></defs><polyline fill="none" stroke="url(#g)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" points="${pts}"/></svg>`}

/* ───── navigation ───── */
const TITLES={overview:["Overview","All your bots at a glance"],broadcast:["Broadcast","Message your users"],export:["Export","Download user data"]};
function go(v){VIEW=v;$$(".nav[data-view]").forEach(n=>n.classList.toggle("on",n.dataset.view===v));
  ["overview","broadcast","export"].forEach(x=>$("#v-"+x).hidden=x!==v);
  $("#title").textContent=TITLES[v][0];$("#subtitle").textContent=TITLES[v][1];
  if(v==="broadcast"){renderTargets();loadHistory()} if(v==="export")renderExport()}
$$(".nav[data-view]").forEach(n=>n.onclick=()=>go(n.dataset.view));

/* ───── overview ───── */
async function loadOverview(){
  const d=await api("/api/overview");BOTS=d.bots;const t=d.totals;
  $("#stats").innerHTML=[["🤖","Bots",t.bots,"#7c5cff"],["👥","Total users",t.users,"#22d3ee"],["✅","Active users",t.active,"#34d399"],["🆕","New · 24h",t.new24,"#fbbf24"]]
    .map(s=>`<div class="glass stat" style="--c:${s[3]}"><span>${s[0]} ${s[1]}</span><b>${nf(s[2])}</b></div>`).join("");
  $("#bots").innerHTML=BOTS.map(b=>`<div class="glass bot">
    <div class="bhead">${avatar(b)}<div><div class="bname">${esc(b.name||("Bot "+b.id))}</div><div class="muted" style="font-size:13px">@${esc(b.username||"…")}</div></div>${statusChip(b)}</div>
    <div class="nums"><div><b>${nf(b.users)}</b><span>Users</span></div><div><b>${nf(b.active_users)}</b><span>Active</span></div><div><b>${nf(b.blocked)}</b><span>Blocked</span></div><div><b>${nf(b.new24)}</b><span>New 24h</span></div></div>
    <div><div class="muted" style="font-size:11px;font-weight:700;letter-spacing:.05em">NEW USERS · 7 DAYS</div>${spark(b.daily)}</div>
    <div class="acts"><button class="btn sm primary" data-a="open" data-id="${b.id}">Open</button><button class="btn sm" data-a="bc" data-id="${b.id}">📣 Broadcast</button><a class="btn sm" href="/api/export?bots=${b.id}">📥</a></div>
  </div>`).join("")+`<div class="glass bot add" id="addCard"><i>＋</i>Add new bot</div>`;
  $("#addCard").onclick=openAdd;
  $$("#bots [data-a]").forEach(el=>el.onclick=()=>{const id=+el.dataset.id;if(el.dataset.a==="open")openBot(id);else{SEL=new Set([id]);go("broadcast")}});
}

/* ───── add bot ───── */
function openAdd(){$("#tok").value="";$("#aerr").textContent="";$("#addModal").hidden=false;setTimeout(()=>$("#tok").focus(),50)}
$("#addTop").onclick=openAdd;$("#addCancel").onclick=()=>$("#addModal").hidden=true;
$("#addGo").onclick=async()=>{const b=$("#addGo");b.disabled=true;$("#aerr").textContent="";
  try{const r=await api("/api/bots",{json:{token:$("#tok").value.trim()}});$("#addModal").hidden=true;toast("✅ @"+r.username+" added","good");loadOverview()}
  catch(e){$("#aerr").textContent=e.message}finally{b.disabled=false}};

/* ───── bot drawer ───── */
let DR={id:0,tab:"users",page:1,q:"",st:""};
async function openBot(id){DR={id,tab:"users",page:1,q:"",st:""};drawer()}
function closeDrawer(){$("#drawerWrap").innerHTML=""}
async function drawer(){
  const b=BOTS.find(x=>x.id===DR.id);if(!b)return closeDrawer();
  $("#drawerWrap").innerHTML=`<div class="back" id="dback" style="place-items:stretch"></div><aside class="drawer">
   <div class="row"><div class="bhead">${avatar(b)}<div><div class="bname" style="font-size:19px">${esc(b.name||b.id)}</div><div class="muted">@${esc(b.username||"")} · ID ${b.id}</div></div></div><button class="btn sm" id="dx">✕</button></div>
   <div class="nums" style="margin-top:16px"><div><b>${nf(b.users)}</b><span>Users</span></div><div><b>${nf(b.active_users)}</b><span>Active</span></div><div><b>${nf(b.verified)}</b><span>Verified</span></div><div><b>${nf(b.channels)}</b><span>Channels</span></div></div>
   <div class="tabs"><button data-t="users" class="${DR.tab==="users"?"on":""}">👥 Users</button><button data-t="channels" class="${DR.tab==="channels"?"on":""}">📢 Channels</button><button data-t="manage" class="${DR.tab==="manage"?"on":""}">⚙️ Manage</button></div>
   <div id="dbody"></div></aside>`;
  $("#dback").onclick=closeDrawer;$("#dx").onclick=closeDrawer;
  $$(".tabs button").forEach(t=>t.onclick=()=>{DR.tab=t.dataset.t;drawer()});
  if(DR.tab==="users")drawUsers(b);else if(DR.tab==="channels")drawChannels(b);else drawManage(b);
}
async function drawUsers(b){
  $("#dbody").innerHTML=`<div class="two" style="grid-template-columns:2fr 1fr auto;align-items:center"><input id="uq" placeholder="Search id, @username or name" value="${esc(DR.q)}"><select id="ust"><option value="">All</option><option value="active">Active</option><option value="blocked">Blocked</option><option value="banned">Banned</option></select><a class="btn sm" href="/api/export?bots=${b.id}">📥 CSV</a></div><div class="tw" id="ut" style="margin-top:12px"></div><div class="row" id="up" style="margin-top:12px"></div>`;
  $("#ust").value=DR.st;
  const run=async()=>{const d=await api(`/api/bots/${b.id}/users?page=${DR.page}&q=${encodeURIComponent(DR.q)}&status=${DR.st}`);
    $("#ut").innerHTML=`<table><tr><th>User</th><th>ID</th><th>Status</th><th>Joined</th></tr>`+(d.items.map(u=>`<tr><td><b>${esc(u.first_name||"—")}</b><div class="muted" style="font-size:12px">${u.username?"@"+esc(u.username):""}</div></td><td>${u.id}</td><td><span class="chip ${u.status==="active"?"ok":u.status==="blocked"?"warn":"bad"}"><i class="dot"></i>${u.status}</span>${u.verified?" ✅":""}</td><td class="muted">${esc(u.created)}</td></tr>`).join("")||`<tr><td colspan="4" class="muted">No users found.</td></tr>`)+`</table>`;
    $("#up").innerHTML=`<span class="muted">${nf(d.total)} users · page ${d.page}/${d.pages}</span><div class="acts"><button class="btn sm" id="pp" ${d.page<=1?"disabled":""}>← Prev</button><button class="btn sm" id="pn" ${d.page>=d.pages?"disabled":""}>Next →</button></div>`;
    $("#pp").onclick=()=>{DR.page--;run()};$("#pn").onclick=()=>{DR.page++;run()}};
  let tm;$("#uq").oninput=e=>{clearTimeout(tm);tm=setTimeout(()=>{DR.q=e.target.value.trim();DR.page=1;run()},350)};
  $("#ust").onchange=e=>{DR.st=e.target.value;DR.page=1;run()};run();
}
async function drawChannels(b){
  const d=await api(`/api/bots/${b.id}/channels`);
  $("#dbody").innerHTML=(d.items.map(c=>`<div class="glass" style="padding:14px;margin-bottom:10px"><div class="row"><b>${esc(c.title||c.chat_id)}</b><span class="chip ${c.kind==="social"?"warn":c.unchecked?"warn":"ok"}">${c.kind==="social"?"🔗 social":c.unchecked?"☑️ unchecked":c.request_mode?"📨 request":"📢 normal"}</span></div><div class="muted" style="font-size:12px;word-break:break-all;margin-top:6px">${esc(c.link)}</div></div>`).join("")||`<div class="muted">No channels yet. Open the bot in Telegram → /admin → Channels.</div>`)+`<div class="sub" style="margin-top:12px">Channels, menu buttons and texts are edited inside the bot with <b>/admin</b>.</div>`;
}
function drawManage(b){
  $("#dbody").innerHTML=`<div class="glass card"><h2>Open the bot</h2><div class="sub">Send <b>/admin</b> to <b>@${esc(b.username||"")}</b> in Telegram to edit channels, menu, texts and colors. Your ADMIN_IDS are admins of every bot.</div><div class="acts" style="margin-top:12px"><a class="btn" target="_blank" rel="noopener" href="https://t.me/${esc(b.username||"")}">Open @${esc(b.username||"")}</a></div></div>
  <div class="glass card" style="margin-top:14px"><h2>${b.active?"Pause bot":"Resume bot"}</h2><div class="sub">${b.active?"Stops answering users until you resume it. Data is kept.":"Start answering users again."}</div><button class="btn" id="tg" style="margin-top:12px">${b.active?"⏸ Pause":"▶ Resume"}</button></div>
  <div class="glass card" style="margin-top:14px;border-color:rgba(251,113,133,.4)"><h2 style="color:#ffc2cc">Delete bot</h2><div class="sub">Removes the bot and <b>all its data</b> (users, channels, settings). This cannot be undone. Type <b>${esc(b.username||b.id)}</b> to confirm.</div><input id="cf" style="margin-top:12px" placeholder="${esc(b.username||b.id)}"><button class="btn danger" id="del" style="margin-top:12px">🗑 Delete forever</button></div>`;
  $("#tg").onclick=async()=>{try{await api(`/api/bots/${b.id}/toggle`,{json:{}});toast("Done","good");await loadOverview();drawer()}catch(e){toast(e.message,"err")}};
  $("#del").onclick=async()=>{if($("#cf").value.trim().replace("@","")!==String(b.username||b.id))return toast("Type the bot username to confirm","err");
    try{await api(`/api/bots/${b.id}`,{method:"DELETE"});closeDrawer();toast("Bot deleted","good");loadOverview()}catch(e){toast(e.message,"err")}};
}

/* ───── broadcast ───── */
function renderTargets(){
  const all=BOTS.length&&BOTS.every(b=>SEL.has(b.id));
  $("#targets").innerHTML=`<span class="pick ${all?"on":""}" data-id="all">★ All bots</span>`+BOTS.map(b=>`<span class="pick ${SEL.has(b.id)?"on":""}" data-id="${b.id}">@${esc(b.username||b.id)}</span>`).join("");
  $$("#targets .pick").forEach(p=>p.onclick=()=>{if(p.dataset.id==="all"){if(all)SEL.clear();else BOTS.forEach(b=>SEL.add(b.id))}else{const i=+p.dataset.id;SEL.has(i)?SEL.delete(i):SEL.add(i)}renderTargets()});
  const n=BOTS.filter(b=>SEL.has(b.id)).reduce((a,b)=>a+b.active_users,0);
  $("#reach").textContent=SEL.size?`≈ ${nf(n)} reachable users in ${SEL.size} bot(s)`:"Pick at least one bot";
}
function tgHtml(t){let s=esc(t);
  s=s.replace(/&lt;(\/?)(b|i|u|s|code|pre|strong|em)&gt;/g,"<$1$2>").replace(/&lt;(\/?)tg-spoiler&gt;/g,"<$1span>")
   .replace(/&lt;a href=&quot;(https?:\/\/[^&"<>\s]+)&quot;&gt;/g,'<a href="$1" target="_blank" rel="noopener">').replace(/&lt;\/a&gt;/g,"</a>")
   .replace(/&lt;tg-emoji emoji-id=&quot;\d+&quot;&gt;(.*?)&lt;\/tg-emoji&gt;/g,"$1").replace(/\{name\}/g,"Rahul").replace(/\n/g,"<br>");return s}
function preview(){const f=$("#photo").files[0];let h="";if(f)h+=`<img src="${URL.createObjectURL(f)}">`;
  h+=tgHtml($("#text").value)||(f?"":'<span class="muted">Your message appears here…</span>');
  if($("#btnText").value.trim())h+=`<div class="tbtn">${esc($("#btnText").value)}</div>`;$("#pv").innerHTML=h}
["text","btnText","btnUrl","photo"].forEach(i=>$("#"+i).addEventListener("input",preview));
$$(".tools button").forEach(b=>b.onclick=()=>{const t=$("#text"),s=t.selectionStart,e=t.selectionEnd,sel=t.value.slice(s,e)||"text";let tag=b.dataset.t,open="<"+tag+">",close="</"+tag+">";
  if(tag==="a"){const u=prompt("Link URL","https://");if(!u)return;open=`<a href="${u}">`;close="</a>"}
  t.setRangeText(open+sel+close,s,e,"end");t.focus();preview()});
$("#send").onclick=async()=>{
  if(!SEL.size)return toast("Pick at least one bot","err");
  if(!$("#text").value.trim()&&!$("#photo").files[0])return toast("Write a message or add a photo","err");
  if(!confirm(`Send this broadcast to ${SEL.size} bot(s)?`))return;
  const f=new FormData();f.append("bots",[...SEL].join(","));f.append("text",$("#text").value);f.append("btn_text",$("#btnText").value);f.append("btn_url",$("#btnUrl").value);
  if($("#photo").files[0])f.append("photo",$("#photo").files[0]);
  $("#send").disabled=true;
  try{const r=await api("/api/broadcast",{form:f});toast("🚀 Broadcast started","good");pollJob(r.id)}catch(e){toast(e.message,"err")}finally{$("#send").disabled=false}};
function jobHtml(j){const tot=Object.values(j.bots).reduce((a,b)=>a+b.total,0),done=Object.values(j.bots).reduce((a,b)=>a+b.sent+b.blocked+b.failed,0);
  const chip=j.state==="running"?"warn":j.state==="done"?"ok":"bad";
  return `<div class="row" style="margin-bottom:10px"><span class="chip ${chip}"><i class="dot"></i>${j.state}</span><span class="muted">${nf(done)} / ${nf(tot)}</span>${j.state==="running"?'<button class="btn sm danger" id="stopJob">⏹ Stop</button>':""}</div>`+
   (j.error?`<div class="chip bad" style="margin-bottom:10px;white-space:normal">${esc(j.error)}</div>`:"")+
   Object.entries(j.bots).map(([id,b])=>`<div style="margin:10px 0"><div class="row" style="font-size:13px"><b>@${esc(b.username||id)}</b><span class="muted">✅ ${nf(b.sent)} · 🚫 ${nf(b.blocked)} · ⚠️ ${nf(b.failed)} / ${nf(b.total)}</span></div><div class="bar"><i style="width:${b.total?Math.min(100,(b.sent+b.blocked+b.failed)/b.total*100):(b.state==="done"?100:0)}%"></i></div></div>`).join("")}
async function pollJob(id){const my=++JOBPOLL;
  while(my===JOBPOLL){try{const j=await api("/api/broadcast/"+id);$("#job").innerHTML=jobHtml(j);const s=$("#stopJob");if(s)s.onclick=()=>api(`/api/broadcast/${id}/stop`,{json:{}}).catch(e=>toast(e.message,"err"));if(j.state!=="running")break}catch(e){break}await sleep(1200)}
  loadHistory()}
async function loadHistory(){try{const d=await api("/api/broadcast");
  $("#history").innerHTML=d.items.length?d.items.map(j=>{const sent=Object.values(j.bots).reduce((a,b)=>a+b.sent,0);return `<div class="jobrow row"><div><b>${esc(j.preview||"(photo)")}</b><div class="muted" style="font-size:12px">${esc(j.started)} · ${Object.keys(j.bots).length} bot(s)</div></div><div class="acts"><span class="chip ${j.state==="done"?"ok":j.state==="running"?"warn":"bad"}">${j.state}</span><span class="muted">✅ ${nf(sent)}</span><button class="btn sm" data-j="${j.id}">View</button></div></div>`}).join(""):"No broadcasts yet.";
  $$("#history [data-j]").forEach(b=>b.onclick=()=>pollJob(b.dataset.j));
  const run=d.items.find(j=>j.state==="running");if(run&&!$("#stopJob"))pollJob(run.id)}catch(e){}}

/* ───── export ───── */
function renderExport(){
  const upd=()=>{$("#exAll").href="/api/export?bots=all&status="+$("#exStatus").value};$("#exStatus").onchange=upd;upd();
  $("#exList").innerHTML=BOTS.map(b=>`<div class="row" style="padding:12px 0;border-bottom:1px solid var(--line)"><div class="bhead">${avatar(b)}<div><div class="bname">${esc(b.name||b.id)}</div><div class="muted" style="font-size:13px">@${esc(b.username||"")} · ${nf(b.users)} users</div></div></div><a class="btn sm" href="/api/export?bots=${b.id}&status=${$("#exStatus").value}">📥 CSV</a></div>`).join("")||'<div class="muted">No bots yet.</div>';
}

/* ───── boot ───── */
$("#refresh").onclick=()=>loadOverview().then(()=>{if(VIEW==="broadcast")renderTargets();if(VIEW==="export")renderExport()}).catch(e=>toast(e.message,"err"));
$("#logout").onclick=async()=>{await api("/api/logout",{json:{}}).catch(()=>{});showLogin()};
async function doLogin(){$("#lerr").textContent="";try{await api("/api/login",{json:{password:$("#pw").value}});$("#pw").value="";await start()}catch(e){$("#lerr").textContent=e.message}}
$("#loginBtn").onclick=doLogin;$("#pw").onkeydown=e=>{if(e.key==="Enter")doLogin()};
async function start(){showApp();go("overview");try{await loadOverview();preview()}catch(e){}}
(async()=>{try{await api("/api/me");await start()}catch(e){showLogin()}})();
setInterval(()=>{if(!$("#app").hidden&&VIEW==="overview"&&!$("#drawerWrap").innerHTML)loadOverview().catch(()=>{})},20000);
</script>
</body>
</html>
'''
