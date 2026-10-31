# BotHub – run many Telegram bots from one dashboard

One service, many bots. Each bot keeps its own users, channels, menu and settings.
Add a bot by pasting its token in the web dashboard – no redeploy.

## What you get
**Web dashboard** (`https://your-app.onrender.com`)
- Overview of every bot: users, active, blocked, new in 24 h, 7-day signup chart, online status
- Add a new bot (token) · pause / resume · delete (with all data)
- Bot-wise user list with search, filters and pagination · channel list
- **Broadcast** to one bot, several bots or **all bots** at once (text, photo, link button, `{name}`), live progress, stop button
- **Export** users to CSV – everything, or per bot (all / active / blocked / banned)

**Inside every bot** (`/admin` for your ADMIN_IDS)
- Force-join: normal, join-request and unchecked channels, social links, auto-generated links, Join Mode
- Join message with photo, extra message, 2 Join buttons per line, Claim button (colors, premium emoji)
- Custom menu buttons: name, reply (text/photo/premium emoji), color, layout (1–3 per line)
- Broadcast to the bot's users · ban/unban · channel broadcast · new-user alerts · texts editor

## Files
| File | Job |
|---|---|
| `main.py` | Starts the web server, loads every bot from the database, webhooks, add/pause/delete bots |
| `core.py` | Config, database (SQLite/PostgreSQL), per-bot settings & menu, force-join logic, helpers |
| `bot.py` | Everything inside a bot: gatekeeper, user handlers, menu replies, `/admin` panel |
| `dashboard.py` | The web dashboard: login, API, broadcast engine, CSV export and the page itself |
| `render.yaml` | One-click Render setup (web service + database) |
| `requirements.txt`, `.env.example`, `.gitignore` | Packages, env template, ignore list |

## Environment variables
| Name | Meaning |
|---|---|
| `DASHBOARD_PASSWORD` | **Required.** Login password of the web dashboard |
| `ADMIN_IDS` | Your Telegram user id(s) – admins of every bot |
| `BOT_TOKEN` | Optional first bot (existing single-bot installs keep working and their data is copied automatically) |
| `DATABASE_URL` | PostgreSQL URL (Neon / Render). Empty = local SQLite |
| `SECRET_KEY` | Random string for login cookies and webhook secrets (Render generates it) |

## Deploy on Render
1. Push this folder to GitHub → Render → **New → Blueprint** → pick the repo.
2. Fill `DASHBOARD_PASSWORD`, `ADMIN_IDS` (and optionally `BOT_TOKEN`).
3. Open the service URL → sign in → **Add bot**.

## Notes
- Bot tokens are stored in your database – keep `DATABASE_URL` secret.
- Multiple bots need the **webhook** mode (Render). Locally (no public URL) the bots that exist at start use long polling; restart after adding one.
- Render's free plan sleeps when idle – ping `/health` every 5–10 min (UptimeRobot) so bots answer fast.
