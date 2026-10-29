# Telegram Force-Join Bot (with Join-Request support)

Advanced Telegram bot built with **aiogram 3**, ready for **Render** (webhook) and local/Termux (polling).

## Features
- **Force join** – multiple channels, one button per channel + "✅ I've joined".
- **Join-request channels** – a *pending* join request counts as joined.
- Optional **auto-approve** of join requests.
- **Admin panel** (`/admin`)
  - 📊 Stats
  - 📢 Channels: add (by @username, ID or forwarded post), delete, switch normal ⇄ request mode
  - 📨 Broadcast: any content type, optional URL buttons, preview, live progress, stop button, auto-flags users who blocked the bot
  - 👥 Users: find, ban / unban, export all IDs
  - ⚙️ Settings: force join ON/OFF, auto-approve, edit welcome / force-join texts
  - 👮 Admins: owners can add / remove admins
- PostgreSQL on Render, SQLite locally (same code).

## Telegram setup
1. Create the bot with @BotFather, copy the token.
2. Add the bot as **admin** in every channel (for request channels enable **Invite users via link**).
3. Get your own user ID (e.g. from @userinfobot) → `ADMIN_IDS`.

## Deploy on Render
1. Push this folder to a GitHub repo.
2. Render dashboard → **New → Blueprint** → pick the repo (it reads `render.yaml`, creating the web service + Postgres).
3. Set the env vars `BOT_TOKEN` and `ADMIN_IDS` when asked.
4. After deploy, open your bot and send `/admin`.

The webhook URL is detected automatically from `RENDER_EXTERNAL_URL`.

### Notes about Render free tier
- Free web services **sleep after ~15 min** without traffic; the first update after sleeping is slow.
  Ping `https://YOUR-APP.onrender.com/health` every 5–10 min with UptimeRobot / cron-job.org, or use a paid plan.
- Render's free Postgres has a limited lifetime. For permanent free storage use
  [Neon](https://neon.tech) or [Supabase](https://supabase.com) and set `DATABASE_URL` manually.

## Run locally / Termux
```bash
pip install aiogram aiohttp SQLAlchemy aiosqlite python-dotenv   # (asyncpg not needed locally)
cp .env.example .env   # fill BOT_TOKEN and ADMIN_IDS
python main.py
```
With no `WEBHOOK_URL` the bot uses long polling and a local `bot.db` SQLite file.

## Known limitation
Telegram doesn't tell bots when a join request is *declined*, so a declined user still counts as "pending"
until they join or leave (the bot cleans the record on `chat_member` updates).
