"""Tiny async DB layer. Works with SQLite (local) and PostgreSQL (Render/Neon).
Tables are created and old databases are migrated automatically on startup."""
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

import config

engine = create_async_engine(config.DATABASE_URL, pool_pre_ping=True)

# columns added to `users` for the wallet / referral system (added automatically if missing)
USER_COLUMNS = {
    "balance": "BIGINT NOT NULL DEFAULT 0",      # stored in paise (₹1 = 100)
    "referred_by": "BIGINT",
    "ref_paid": "INTEGER NOT NULL DEFAULT 0",
    "refs": "INTEGER NOT NULL DEFAULT 0",
    "upi": "TEXT",
    "last_bonus": "BIGINT NOT NULL DEFAULT 0",   # unix time of last bonus
    "verified": "INTEGER NOT NULL DEFAULT 0",    # pressed "I've joined" at least once
    "extra_msg": "TEXT",                         # message id of the extra force-join message (deleted after Claim)
}

# columns added to `channels` (unchecked = shown as a Join button but membership is NOT verified)
CHANNEL_COLUMNS = {
    "unchecked": "INTEGER NOT NULL DEFAULT 0",
    "kind": "TEXT NOT NULL DEFAULT 'channel'",   # 'channel' | 'social' (link button only)
    "pos": "INTEGER NOT NULL DEFAULT 0",         # order in the panel (⬆️ ⬇️)
    "icon": "TEXT",                              # premium-emoji id for this button (social links)
    "req_link": "TEXT",                          # auto-generated join-REQUEST invite link (link = normal link)
}


def _schema() -> list[str]:
    pg = engine.dialect.name == "postgresql"
    pk = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        """CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            blocked INTEGER NOT NULL DEFAULT 0,
            banned INTEGER NOT NULL DEFAULT 0,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS channels(
            chat_id BIGINT PRIMARY KEY,
            title TEXT,
            link TEXT NOT NULL,
            request_mode INTEGER NOT NULL DEFAULT 0,
            added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS join_requests(
            user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, chat_id)
        )""",
        """CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS admins(
            user_id BIGINT PRIMARY KEY
        )""",
        f"""CREATE TABLE IF NOT EXISTS withdrawals(
            id {pk},
            user_id BIGINT NOT NULL,
            amount BIGINT NOT NULL,
            upi TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]


async def init():
    async with engine.begin() as conn:
        for stmt in _schema():
            await conn.execute(text(stmt))
        for table, wanted in (("users", USER_COLUMNS), ("channels", CHANNEL_COLUMNS)):
            cols = await conn.run_sync(lambda c, t=table: {x["name"] for x in inspect(c).get_columns(t)})
            for name, ddl in wanted.items():
                if name not in cols:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def fetch(sql: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        res = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in res.fetchall()]


async def fetchone(sql: str, **params):
    rows = await fetch(sql, **params)
    return rows[0] if rows else None


async def scalar(sql: str, **params):
    async with engine.connect() as conn:
        res = await conn.execute(text(sql), params)
        return res.scalar()


async def execute(sql: str, **params) -> int:
    """Run a write statement, returns number of affected rows."""
    async with engine.begin() as conn:
        res = await conn.execute(text(sql), params)
        return res.rowcount or 0


async def insert_returning(sql: str, **params):
    """INSERT ... and return the new row id."""
    async with engine.begin() as conn:
        res = await conn.execute(text(sql + " RETURNING id"), params)
        return res.scalar()


async def close():
    await engine.dispose()
