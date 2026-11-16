"""Database schema for the automated card modules."""

from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS module_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS module_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    actor_id INTEGER NOT NULL,
    target_id INTEGER,
    approved_by_id INTEGER,
    channel_id INTEGER,
    message_id INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    forwarded TEXT NOT NULL DEFAULT '[]',
    failures TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS module_entries_kind ON module_entries (guild_id, kind, id DESC);
"""


async def ensure_schema(db) -> None:
    """Create the module tables; safe to run from the bot and the dashboard."""

    await db.executescript(SCHEMA)
    await db.commit()
