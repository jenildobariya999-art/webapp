# Telegram Force-Join + Refer & Earn Bot

aiogram 3 • Render (webhook) • PostgreSQL (Neon / Render) • SQLite for local testing

## What each file does
| File | Job |
|---|---|
| `main.py` | Starts everything: web server (Render), webhook / polling, DB init, router wiring. Retries if Telegram or the DB is slow. |
| `config.py` | Reads environment variables (`BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, webhook URL). |
| `db.py` | Database layer (SQLite/PostgreSQL), creates tables and auto-adds new columns on old databases. |
| `services.py` | Brain: settings (cached), default texts, money helpers, referral payout, force-join checking, photo+caption sending. |
| `middleware.py` | Gatekeeper on every message/button: registers users, bans, bot ON/OFF, force-join prompt (photo + extra message + buttons). |
| `icons.py` | Premium-emoji icon for the Join / Claim / social-link buttons, with automatic fallback if Telegram refuses it. |
| `keyboards.py` | The user menu (custom buttons from the admin panel + optional wallet buttons) and the withdrawal approve buttons. |
| `handlers/user.py` | `/start`, "I've joined" check, join-request tracking, welcome message. |
| `handlers/menu.py` | What the menu buttons do: custom buttons send their admin-edited reply; wallet buttons (balance, referral link, bonus, UPI, withdrawal) when the wallet menu is ON. |
| `handlers/admin.py` | The whole admin panel `/admin`: stats, channels panel, broadcast, users, withdrawals, settings, admins. |
| `render.yaml` | One-click Render Blueprint (web service + database). |
| `requirements.txt` | Python packages. |
| `.env.example` | Template of environment variables for local runs. |

## Menu buttons
Settings → **Menu buttons**: add / rename / delete buttons and edit the reply each one sends (text, photo, premium emoji, color).
Defaults: `New yono app` and `Lucky Gullak | Big Promo code`. The wallet buttons can be switched on in Settings → **Wallet menu**.

## Force-join message
Pressing **Claim** deletes that join message and its extra message. If the user hasn't joined, fresh ones are sent (no pop-up).
`/start` never deletes anything.

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
