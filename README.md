# Telegram Force-Join + Refer & Earn Bot

aiogram 3 • Render (webhook) • PostgreSQL (Neon / Render) • SQLite for local testing

## Features
- **Force join** – many channels, normal **and join-request** channels (a pending request counts).
- **User menu** after joining: 💰 Balance · 👥 Refer & Earn · 🎁 Bonus · 💸 Withdrawal · 🏦 Link UPI
- **Referrals** – reward is paid only after the invited user passes force-join.
- **Withdrawals** – user requests → admins get Approve / Reject buttons → user is notified (rejections auto-refund).
- **Admin panel** (`/admin`): stats, channels, broadcast, users (ban / balance), withdrawals,
  settings (Bot ON/OFF, force join, auto-approve, withdrawals ON/OFF, texts, rewards), admins.
- **Premium emoji + formatting**: send any text/broadcast formatted in Telegram – it's detected automatically (no HTML).

## Environment variables
| Name | Meaning |
|---|---|
| `BOT_TOKEN` | token from @BotFather |
| `ADMIN_IDS` | owner Telegram IDs, comma separated |
| `DATABASE_URL` | PostgreSQL URL (Neon/Supabase/Render). Empty = local SQLite |
| `WEBHOOK_URL` | optional; Render sets `RENDER_EXTERNAL_URL` automatically |

## Database
Tables are created and old databases are upgraded **automatically** on start. Just provide `DATABASE_URL`.
See the chat guide for the Neon (free) setup steps.

## Run locally / Termux
pip install aiogram aiohttp SQLAlchemy aiosqlite python-dotenv
cp .env.example .env  # fill BOT_TOKEN + ADMIN_IDS
python main.py
