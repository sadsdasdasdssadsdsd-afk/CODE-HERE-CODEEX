"""Durable background job queue shared by the dashboard and the bot.

The dashboard enqueues work (channel ingestion, partnership analysis, publishing)
and the bot process claims and executes it, so no HTTP request waits on the
model. Claiming is a single UPDATE, retries back off, and ``idempotency_key``
keeps a double click from queueing the same work twice.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.ai import settings as ai_settings

JOB_KINDS = (
    "ingest_knowledge",
    "notify_staff",
    "send_ticket_message",
    "partnership_join",
    "partnership_analyse",
    "partnership_verify_ad",
    "partnership_monitor_ad",
    "publish_advertisement",
    "partnership_finish",
)


def _later(seconds: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=max(0, seconds))
    return moment.isoformat(timespec="seconds")


async def enqueue(
    db,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    idempotency_key: str | None = None,
    delay_seconds: int = 0,
    max_attempts: int = 3,
    retry_failed: bool = False,
) -> int | None:
    """Queue once; explicit retries can revive a failed job, never an active one."""

    now = ai_settings.utc_now()
    if retry_failed and idempotency_key:
        async with db.execute(
            "UPDATE ai_jobs SET status = 'queued', attempts = 0, last_error = NULL, "
            "started_at = NULL, finished_at = NULL, available_at = ? "
            "WHERE idempotency_key = ? AND status = 'failed' RETURNING id",
            (_later(delay_seconds), idempotency_key),
        ) as cursor:
            row = await cursor.fetchone()
        await db.commit()
        if row:
            return int(row[0])
    cursor = await db.execute(
        """
        INSERT INTO ai_jobs (kind, payload, status, max_attempts, idempotency_key, available_at, created_at)
        VALUES (?, ?, 'queued', ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (
            kind, json.dumps(payload or {}), max(1, int(max_attempts)),
            idempotency_key, _later(delay_seconds), now,
        ),
    )
    await db.commit()
    return int(cursor.lastrowid or 0) if cursor.rowcount else None


async def claim(db) -> dict[str, Any] | None:
    """Atomically take the oldest runnable job."""

    now = ai_settings.utc_now()
    await db.execute(
        """
        UPDATE ai_jobs SET status = 'running', started_at = ?, attempts = attempts + 1
        WHERE id = (
            SELECT id FROM ai_jobs
            WHERE status = 'queued' AND available_at <= ?
            ORDER BY id LIMIT 1
        )
        """,
        (now, now),
    )
    await db.commit()
    async with db.execute(
        "SELECT * FROM ai_jobs WHERE status = 'running' ORDER BY id LIMIT 1"
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [description[0] for description in cursor.description]
    job = dict(zip(columns, row))
    try:
        job["payload"] = json.loads(job.get("payload") or "{}")
    except ValueError:
        job["payload"] = {}
    return job


async def complete(db, job_id: int) -> None:
    await db.execute(
        "UPDATE ai_jobs SET status = 'done', finished_at = ?, last_error = NULL WHERE id = ?",
        (ai_settings.utc_now(), job_id),
    )
    await db.commit()


async def fail(db, job_id: int, error: str) -> str:
    """Record a failure; requeue with backoff until the attempts run out."""

    async with db.execute("SELECT attempts, max_attempts FROM ai_jobs WHERE id = ?", (job_id,)) as cursor:
        row = await cursor.fetchone()
    attempts, max_attempts = (row[0], row[1]) if row else (1, 1)
    if attempts < max_attempts:
        await db.execute(
            "UPDATE ai_jobs SET status = 'queued', last_error = ?, available_at = ? WHERE id = ?",
            (str(error)[:1000], _later(30 * attempts), job_id),
        )
        await db.commit()
        return "queued"
    await db.execute(
        "UPDATE ai_jobs SET status = 'failed', last_error = ?, finished_at = ? WHERE id = ?",
        (str(error)[:1000], ai_settings.utc_now(), job_id),
    )
    await db.commit()
    return "failed"


async def stats(db) -> dict[str, int]:
    async with db.execute(
        "SELECT status, COUNT(*) FROM ai_jobs GROUP BY status"
    ) as cursor:
        rows = await cursor.fetchall()
    counts = {status: int(count) for status, count in rows}
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
    }


async def recent(db, limit: int = 20) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT id, kind, status, attempts, last_error, created_at, finished_at FROM ai_jobs "
        "ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 100)),),
    ) as cursor:
        rows = await cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


async def requeue_stale(db, *, older_than_seconds: int = 600) -> int:
    """Reset jobs abandoned by a bot restart so they run again."""

    cutoff = _later(-older_than_seconds)
    cursor = await db.execute(
        "UPDATE ai_jobs SET status = 'queued', available_at = ? "
        "WHERE status = 'running' AND started_at < ?",
        (ai_settings.utc_now(), cutoff),
    )
    await db.commit()
    return cursor.rowcount
