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
| `keyboards.py` | The 5-button user menu (Balance, Refer & Earn, Bonus, Withdrawal, Link UPI) and the withdrawal approve buttons. |
| `handlers/user.py` | `/start`, "I've joined" check, join-request tracking, welcome message. |
| `handlers/menu.py` | What the 5 menu buttons do (wallet, referral link, bonus, UPI, withdrawal requests). |
| `handlers/admin.py` | The whole admin panel `/admin`: stats, channels panel, broadcast, users, withdrawals, settings, admins. |
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
