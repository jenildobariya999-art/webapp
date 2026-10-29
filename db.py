"""Tiny async DB layer. Works with SQLite (local) and PostgreSQL (Render)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import config

engine = create_async_engine(config.DATABASE_URL, pool_pre_ping=True)

SCHEMA = [
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
]


async def init():
    async with engine.begin() as conn:
        for stmt in SCHEMA:
            await conn.execute(text(stmt))


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


async def execute(sql: str, **params):
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def close():
    await engine.dispose()
