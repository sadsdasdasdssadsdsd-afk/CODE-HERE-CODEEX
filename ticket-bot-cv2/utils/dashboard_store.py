"""Shared state between the Discord bot and the Florida State Dashboard.

The bot process and the dashboard process are separate, so everything they
exchange lives in the bot's SQLite database:

* ``dashboard_settings`` - configuration edited in the dashboard and read by the bot.
* ``dashboard_statuses`` - presence rotation entries.
* ``dashboard_commands``  - a tiny queue: the dashboard enqueues, the bot applies.
* ``bot_state``           - live facts the bot publishes for the dashboard.
* ``dashboard_activity``  - recent activity feed shown on the overview page.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_statuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity TEXT NOT NULL,
    text TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 600,
    enabled INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    payload TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

ACTIVITY_TYPES = ("playing", "watching", "listening", "competing", "custom")

CONFIG_KEYS = (
    "guild_id",
    "panel_channel_id",
    "staff_channel_id",
    "transcript_channel_id",
    "admin_role_ids",
    "moderator_role_ids",
    "panel_banner_url",
    "panel_footer_url",
    "ticket_banner_url",
    "ticket_footer_url",
    "public_base_url",
    "rotation_enabled",
    "presence_status",
    "custom_status_text",
    "admin_password_hash",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_schema(db) -> None:
    await db.executescript(SCHEMA)
    await db.commit()


# --------------------------------------------------------------------------- settings
async def get_settings(db) -> dict[str, str]:
    async with db.execute("SELECT key, value FROM dashboard_settings") as cursor:
        rows = await cursor.fetchall()
    return {key: value for key, value in rows}


async def set_settings(db, values: dict[str, Any]) -> None:
    now = utc_now()
    for key, value in values.items():
        if value is None:
            await db.execute("DELETE FROM dashboard_settings WHERE key = ?", (key,))
            continue
        await db.execute(
            "INSERT INTO dashboard_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), now),
        )
    await db.commit()


def parse_id_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except ValueError:
        values = []
    if not isinstance(values, list):
        return []
    return [int(value) for value in values if str(value).isdigit()]


def dump_id_list(values: Iterable[Any]) -> str:
    cleaned = sorted({int(value) for value in values if str(value).isdigit()})
    return json.dumps(cleaned)


# --------------------------------------------------------------------------- activity
async def log_activity(db, kind: str, message: str) -> None:
    await db.execute(
        "INSERT INTO dashboard_activity (kind, message, created_at) VALUES (?, ?, ?)",
        (kind, message, utc_now()),
    )
    await db.commit()


async def recent_activity(db, limit: int = 12) -> list[dict[str, str]]:
    async with db.execute(
        "SELECT kind, message, created_at FROM dashboard_activity ORDER BY id DESC LIMIT ?",
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"kind": kind, "message": message, "created_at": created_at} for kind, message, created_at in rows]


# --------------------------------------------------------------------------- bot state
async def write_bot_state(db, values: dict[str, Any]) -> None:
    now = utc_now()
    for key, value in values.items():
        await db.execute(
            "INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), now),
        )
    await db.commit()


async def read_bot_state(db) -> dict[str, Any]:
    async with db.execute("SELECT key, value FROM bot_state") as cursor:
        rows = await cursor.fetchall()
    state: dict[str, Any] = {}
    for key, value in rows:
        try:
            state[key] = json.loads(value)
        except ValueError:
            state[key] = value
    return state


# --------------------------------------------------------------------------- commands
async def enqueue_command(db, action: str, payload: dict[str, Any] | None = None) -> int:
    cursor = await db.execute(
        "INSERT INTO dashboard_commands (action, payload, state, created_at) VALUES (?, ?, 'pending', ?)",
        (action, json.dumps(payload or {}), utc_now()),
    )
    await db.commit()
    return int(cursor.lastrowid)


async def claim_commands(db) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT id, action, payload FROM dashboard_commands WHERE state = 'pending' ORDER BY id"
    ) as cursor:
        rows = await cursor.fetchall()
    claimed = []
    for command_id, action, payload in rows:
        await db.execute(
            "UPDATE dashboard_commands SET state = 'running' WHERE id = ? AND state = 'pending'",
            (command_id,),
        )
        try:
            parsed = json.loads(payload) if payload else {}
        except ValueError:
            parsed = {}
        claimed.append({"id": command_id, "action": action, "payload": parsed})
    await db.commit()
    return claimed


async def finish_command(db, command_id: int, ok: bool, result: str) -> None:
    await db.execute(
        "UPDATE dashboard_commands SET state = ?, result = ?, processed_at = ? WHERE id = ?",
        ("done" if ok else "failed", result[:500], utc_now(), command_id),
    )
    await db.commit()


async def recent_commands(db, limit: int = 10) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT action, state, result, processed_at FROM dashboard_commands ORDER BY id DESC LIMIT ?",
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {"action": action, "state": state, "result": result, "processed_at": processed_at}
        for action, state, result, processed_at in rows
    ]


# --------------------------------------------------------------------------- statuses
def _status_row(row) -> dict[str, Any]:
    status_id, activity, text, duration, enabled, position = row
    return {
        "id": status_id,
        "activity": activity,
        "text": text,
        "duration_seconds": duration,
        "enabled": bool(enabled),
        "position": position,
    }


async def list_statuses(db) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT id, activity, text, duration_seconds, enabled, position "
        "FROM dashboard_statuses ORDER BY position, id"
    ) as cursor:
        rows = await cursor.fetchall()
    return [_status_row(row) for row in rows]


async def add_status(db, activity: str, text: str, duration_seconds: int) -> dict[str, Any]:
    async with db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM dashboard_statuses") as cursor:
        position = (await cursor.fetchone())[0]
    cursor = await db.execute(
        "INSERT INTO dashboard_statuses (activity, text, duration_seconds, enabled, position, created_at) "
        "VALUES (?, ?, ?, 1, ?, ?)",
        (activity, text, duration_seconds, position, utc_now()),
    )
    await db.commit()
    return {"id": int(cursor.lastrowid), "activity": activity, "text": text, "duration_seconds": duration_seconds}


async def update_status(db, status_id: int, **fields: Any) -> bool:
    allowed = {key: value for key, value in fields.items() if key in {"activity", "text", "duration_seconds", "enabled"}}
    if not allowed:
        return False
    assignments = ", ".join(f"{key} = ?" for key in allowed)
    cursor = await db.execute(
        f"UPDATE dashboard_statuses SET {assignments} WHERE id = ?",
        (*allowed.values(), status_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_status(db, status_id: int) -> bool:
    cursor = await db.execute("DELETE FROM dashboard_statuses WHERE id = ?", (status_id,))
    await db.commit()
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- tickets
TICKET_FIELDS = (
    "id",
    "channel_id",
    "creator_id",
    "ticket_number",
    "category",
    "subject",
    "description",
    "status",
    "claimed_by",
    "created_at",
    "closed_at",
    "guild_id",
)
TICKET_COLUMNS = ", ".join(TICKET_FIELDS)


async def list_tickets(db, guild_id: int, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = f"SELECT {TICKET_COLUMNS} FROM ticket_instances WHERE guild_id = ?"
    params: list[Any] = [guild_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY ticket_number DESC LIMIT ?"
    params.append(limit)
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    return [dict(zip(TICKET_FIELDS, row)) for row in rows]


async def ticket_counts(db, guild_id: int) -> dict[str, int]:
    async with db.execute(
        "SELECT COUNT(*), SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) FROM ticket_instances WHERE guild_id = ?",
        (guild_id,),
    ) as cursor:
        total, open_count, closed_count = await cursor.fetchone()
    return {"total": total or 0, "open": open_count or 0, "closed": closed_count or 0}


async def get_ticket(db, ticket_id: int) -> dict[str, Any] | None:
    async with db.execute(
        f"SELECT {TICKET_COLUMNS} FROM ticket_instances WHERE id = ?",
        (ticket_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(zip(TICKET_FIELDS, row)) if row else None


async def active_guild_id(db) -> int | None:
    settings = await get_settings(db)
    raw = settings.get("guild_id", "").strip()
    if raw.isdigit():
        return int(raw)
    state = await read_bot_state(db)
    guild_id = state.get("guild_id")
    if isinstance(guild_id, int):
        return guild_id
    if isinstance(guild_id, str) and guild_id.isdigit():
        return int(guild_id)
    async with db.execute("SELECT guild_id FROM tickets ORDER BY guild_id LIMIT 1") as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else None
