# Telegram Force-Join Bot (with custom menu)

aiogram 3 • Render (webhook) • PostgreSQL (Neon / Render) • SQLite for local testing

## Features
- **Force join** – many channels: normal, **join-request** (pending request counts) and **☑️ unchecked** (shown as a Join button, never verified), plus 🔗 social links.
- Both links (normal + request) are generated automatically when you add a channel; tap a channel name to replace its link.
- **Join Mode**: *Show All Channels* or *Not Joined Only*.
- Join buttons are 2 per line; **Claim** re-checks, deletes the old join + extra message and shows fresh ones (no pop-up).
- Join message / extra message / welcome support **photo + caption + premium emoji**.
- **Custom menu buttons**: name, reply (text / photo / premium emoji), color and **layout** (1, 2 or 3 per line) – all from the admin panel.
- **Button colors** (primary / success / danger) for Join, Claim and every menu button.
- **Admin panel** (`/admin`): stats, channels, channel broadcast, user broadcast, users (ban/unban), settings, menu buttons, admins.
- New-user alerts to admins (ON/OFF).

## What each file does
| File | Job |
|---|---|
| `main.py` | Starts everything: web server (Render), webhook / polling, DB init, router wiring. Retries if Telegram or the DB is slow. |
| `config.py` | Reads environment variables (`BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, webhook URL). |
| `db.py` | Database layer (SQLite/PostgreSQL), creates tables and auto-adds new columns on old databases. |
| `services.py` | Brain: cached settings, default texts, menu buttons + layout, force-join checking, photo+caption sending. |
| `middleware.py` | Gatekeeper on every message/button: registers users, bans, bot ON/OFF, force-join message. |
| `icons.py` | Premium-emoji icon + color style for buttons, with automatic fallback if Telegram refuses them. |
| `keyboards.py` | Builds the user menu from the buttons and layout set in the admin panel. |
| `handlers/user.py` | `/start`, Claim button, join-request tracking, welcome message. |
| `handlers/menu.py` | Sends the admin-set reply when a user taps a menu button. |
| `handlers/admin.py` | The whole admin panel `/admin`. |
| `render.yaml` | One-click Render Blueprint (web service + database). |
| `requirements.txt` | Python packages. |
| `.env.example` | Template of environment variables for local runs. |

## Environment variables
| Name | Meaning |
|---|---|
| `BOT_TOKEN` | token from @BotFather |
| `ADMIN_IDS` | owner Telegram IDs, comma separated |
| `DATABASE_URL` | PostgreSQL URL (Neon/Supabase/Render). Empty = local SQLite |
| `WEBHOOK_URL` | optional; Render sets `RENDER_EXTERNAL_URL` automatically |

## Run locally / Termux
    pip install aiogram aiohttp SQLAlchemy aiosqlite python-dotenv
    cp .env.example .env   # fill BOT_TOKEN + ADMIN_IDS
    python main.py
