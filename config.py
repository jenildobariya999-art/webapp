import hashlib
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set")

# Owners come from env; ADMINS = owners + admins stored in the DB (loaded at startup)
OWNERS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "")) if x.lstrip("-").isdigit()}
ADMINS = set(OWNERS)


def _normalize_db_url(url: str) -> str:
    if not url:
        return "sqlite+aiosqlite:///bot.db"
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+asyncpg://"):
        # asyncpg uses ssl=..., not sslmode=..., and doesn't know channel_binding
        parts = urlsplit(url)
        qs = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            if k == "channel_binding":
                continue
            qs.append(("ssl" if k == "sslmode" else k, v))
        url = urlunsplit(parts._replace(query=urlencode(qs)))
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", "").strip())

WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:48]
PORT = int(os.getenv("PORT", "8080"))

ALLOWED_UPDATES = ["message", "callback_query", "chat_join_request", "chat_member"]
