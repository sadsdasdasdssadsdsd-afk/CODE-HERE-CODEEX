"""Persistence and queries for the AI Control System.

Every function takes the shared ``aiosqlite`` connection and encodes/decodes the
JSON columns so callers work with plain Python values.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from utils.ai import settings as ai_settings

JSON_COLUMNS = {
    "questions",
    "required_fields",
    "role_ids",
    "backup_role_ids",
    "details",
    "destination_roles",
    "analysis",
    "requirements",
}


def _decode(key: str, value: Any) -> Any:
    if key in JSON_COLUMNS and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return [] if key != "details" and key != "analysis" else {}
    return value


def _record(cursor, row) -> dict[str, Any]:
    columns = [description[0] for description in cursor.description]
    return {column: _decode(column, value) for column, value in zip(columns, row)}


async def _all(db, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    async with db.execute(sql, tuple(params)) as cursor:
        return [_record(cursor, row) for row in await cursor.fetchall()]


async def _one(db, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    async with db.execute(sql, tuple(params)) as cursor:
        row = await cursor.fetchone()
        return _record(cursor, row) if row else None


# --------------------------------------------------------------------------- activity
async def log_activity(
    db,
    action: str,
    *,
    guild_id: int | None = None,
    case_id: int | None = None,
    actor: str = "ai",
    user_id: int | None = None,
    ticket_channel_id: int | None = None,
    decision: str = "",
    confidence: float = 0.0,
    destination_role: str = "",
    destination_channel: str = "",
    status: str = "completed",
    review_status: str = "none",
    detail: str = "",
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO ai_activity (
            guild_id, case_id, actor, user_id, ticket_channel_id, action, decision, confidence,
            destination_role, destination_channel, status, review_status, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            case_id,
            actor,
            user_id,
            ticket_channel_id,
            action,
            str(decision)[:200],
            float(confidence or 0),
            str(destination_role)[:120],
            str(destination_channel)[:120],
            status,
            review_status,
            str(detail)[:2000],
            ai_settings.utc_now(),
        ),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def list_activity(
    db,
    *,
    guild_id: int | None = None,
    action: str | None = None,
    status: str | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if guild_id:
        clauses.append("guild_id = ?")
        params.append(guild_id)
    if action:
        clauses.append("action LIKE ?")
        params.append(f"{action}%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if query:
        clauses.append("(detail LIKE ? OR decision LIKE ? OR action LIKE ? OR actor LIKE ?)")
        params.extend([f"%{query}%"] * 4)
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = await _one(db, f"SELECT COUNT(*) AS total FROM ai_activity {where}", params)
    rows = await _all(
        db,
        f"SELECT * FROM ai_activity {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(limit, 200)), max(0, offset)],
    )
    return {"total": int((total or {}).get("total") or 0), "activity": rows}


# --------------------------------------------------------------------------- knowledge
async def add_knowledge(
    db,
    guild_id: int,
    *,
    title: str,
    body: str,
    tags: str = "",
    source_type: str = "manual",
    source_ref: str | None = None,
    status: str = "pending",
    confidence: float = 0.0,
    created_by: str = "dashboard",
) -> int:
    now = ai_settings.utc_now()
    cursor = await db.execute(
        """
        INSERT INTO ai_knowledge (
            guild_id, title, body, tags, source_type, source_ref, status, confidence, created_by,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            title.strip()[:160],
            body.strip()[:4000],
            tags.strip()[:240],
            source_type,
            source_ref,
            status,
            float(confidence or 0),
            created_by,
            now,
            now,
        ),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def list_knowledge(
    db, guild_id: int, *, status: str | None = None, query: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    clauses = ["guild_id = ?"]
    params: list[Any] = [guild_id]
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    if query:
        clauses.append("(title LIKE ? OR body LIKE ? OR tags LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    return await _all(
        db,
        f"SELECT * FROM ai_knowledge WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
        [*params, max(1, min(limit, 500))],
    )


async def approved_knowledge(db, guild_id: int, limit: int = 120) -> list[dict[str, Any]]:
    return await _all(
        db,
        "SELECT * FROM ai_knowledge WHERE guild_id = ? AND status = 'approved' ORDER BY id DESC LIMIT ?",
        (guild_id, limit),
    )


async def update_knowledge(db, entry_id: int, values: dict[str, Any]) -> bool:
    allowed = {"title", "body", "tags", "status", "confidence", "reviewed_by"}
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return False
    updates["updated_at"] = ai_settings.utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    cursor = await db.execute(
        f"UPDATE ai_knowledge SET {assignments} WHERE id = ?", (*updates.values(), entry_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_knowledge(db, entry_id: int) -> bool:
    cursor = await db.execute("DELETE FROM ai_knowledge WHERE id = ?", (entry_id,))
    await db.commit()
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- sources
async def add_source(db, guild_id: int, channel_id: int, label: str = "") -> int:
    cursor = await db.execute(
        "INSERT INTO ai_knowledge_sources (guild_id, channel_id, label, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(guild_id, channel_id) DO UPDATE SET label = excluded.label, enabled = 1",
        (guild_id, channel_id, label[:120], ai_settings.utc_now()),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def list_sources(db, guild_id: int) -> list[dict[str, Any]]:
    return await _all(
        db, "SELECT * FROM ai_knowledge_sources WHERE guild_id = ? ORDER BY id DESC", (guild_id,)
    )


async def delete_source(db, guild_id: int, source_id: int) -> bool:
    cursor = await db.execute(
        "DELETE FROM ai_knowledge_sources WHERE guild_id = ? AND id = ?", (guild_id, source_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def mark_source_scanned(db, source_id: int, *, messages: int, entries: int) -> None:
    await db.execute(
        "UPDATE ai_knowledge_sources SET last_scanned_at = ?, messages_scanned = ?, entries_found = ? WHERE id = ?",
        (ai_settings.utc_now(), int(messages), int(entries), source_id),
    )
    await db.commit()


# --------------------------------------------------------------------------- forms
async def create_form(db, guild_id: int, values: dict[str, Any]) -> int:
    now = ai_settings.utc_now()
    cursor = await db.execute(
        """
        INSERT INTO ai_forms (
            guild_id, name, description, category, questions, required_fields, department, role_ids,
            destination_channel_id, instructions, enabled, source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            str(values.get("name") or "Untitled form")[:160],
            str(values.get("description") or "")[:2000],
            str(values.get("category") or "General")[:80],
            json.dumps(values.get("questions") or []),
            json.dumps(values.get("required_fields") or []),
            str(values.get("department") or "")[:120],
            json.dumps(ai_settings.as_id_list(values.get("role_ids"))),
            ai_settings.as_int(values.get("destination_channel_id")) or None,
            str(values.get("instructions") or "")[:2000],
            1 if ai_settings.as_bool(values.get("enabled"), default=True) else 0,
            str(values.get("source") or "manual")[:40],
            now,
            now,
        ),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def update_form(db, form_id: int, values: dict[str, Any] | None = None, **kwargs: Any) -> bool:
    values = {**(values or {}), **kwargs}
    mapping = {
        "name": lambda v: str(v)[:160],
        "description": lambda v: str(v)[:2000],
        "category": lambda v: str(v)[:80],
        "questions": lambda v: json.dumps(v if isinstance(v, list) else []),
        "required_fields": lambda v: json.dumps(v if isinstance(v, list) else []),
        "department": lambda v: str(v)[:120],
        "role_ids": lambda v: json.dumps(ai_settings.as_id_list(v)),
        "destination_channel_id": lambda v: ai_settings.as_int(v) or None,
        "instructions": lambda v: str(v)[:2000],
        "enabled": lambda v: 1 if ai_settings.as_bool(v, default=True) else 0,
    }
    updates = {key: mapping[key](value) for key, value in values.items() if key in mapping}
    if not updates:
        return False
    updates["updated_at"] = ai_settings.utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    cursor = await db.execute(f"UPDATE ai_forms SET {assignments} WHERE id = ?", (*updates.values(), form_id))
    await db.commit()
    return cursor.rowcount > 0


async def list_forms(db, guild_id: int, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    clause = "" if include_disabled else " AND enabled = 1"
    return await _all(
        db, f"SELECT * FROM ai_forms WHERE guild_id = ?{clause} ORDER BY id DESC", (guild_id,)
    )


async def get_form(db, form_id: int) -> dict[str, Any] | None:
    return await _one(db, "SELECT * FROM ai_forms WHERE id = ?", (form_id,))


async def delete_form(db, form_id: int) -> bool:
    cursor = await db.execute("DELETE FROM ai_forms WHERE id = ?", (form_id,))
    await db.commit()
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- routes
async def create_route(db, guild_id: int, values: dict[str, Any]) -> int:
    now = ai_settings.utc_now()
    cursor = await db.execute(
        """
        INSERT INTO ai_routes (
            guild_id, name, department, report_category, severity, role_ids, backup_role_ids,
            destination_channel_id, escalate_after_minutes, notify, priority, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            str(values.get("name") or "Route")[:120],
            str(values.get("department") or "")[:120],
            str(values.get("report_category") or "any")[:80],
            str(values.get("severity") or "any")[:24],
            json.dumps(ai_settings.as_id_list(values.get("role_ids"))),
            json.dumps(ai_settings.as_id_list(values.get("backup_role_ids"))),
            ai_settings.as_int(values.get("destination_channel_id")) or None,
            max(0, ai_settings.as_int(values.get("escalate_after_minutes"), default=60)),
            1 if ai_settings.as_bool(values.get("notify"), default=True) else 0,
            ai_settings.as_int(values.get("priority"), default=100),
            1 if ai_settings.as_bool(values.get("enabled"), default=True) else 0,
            now,
            now,
        ),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def update_route(db, route_id: int, values: dict[str, Any] | None = None, **kwargs: Any) -> bool:
    values = {**(values or {}), **kwargs}
    mapping = {
        "name": lambda v: str(v)[:120],
        "department": lambda v: str(v)[:120],
        "report_category": lambda v: str(v)[:80],
        "severity": lambda v: str(v)[:24],
        "role_ids": lambda v: json.dumps(ai_settings.as_id_list(v)),
        "backup_role_ids": lambda v: json.dumps(ai_settings.as_id_list(v)),
        "destination_channel_id": lambda v: ai_settings.as_int(v) or None,
        "escalate_after_minutes": lambda v: max(0, ai_settings.as_int(v, default=60)),
        "notify": lambda v: 1 if ai_settings.as_bool(v, default=True) else 0,
        "priority": lambda v: ai_settings.as_int(v, default=100),
        "enabled": lambda v: 1 if ai_settings.as_bool(v, default=True) else 0,
    }
    updates = {key: mapping[key](value) for key, value in values.items() if key in mapping}
    if not updates:
        return False
    updates["updated_at"] = ai_settings.utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    cursor = await db.execute(f"UPDATE ai_routes SET {assignments} WHERE id = ?", (*updates.values(), route_id))
    await db.commit()
    return cursor.rowcount > 0


async def list_routes(db, guild_id: int) -> list[dict[str, Any]]:
    return await _all(db, "SELECT * FROM ai_routes WHERE guild_id = ? ORDER BY priority, id", (guild_id,))


async def delete_route(db, route_id: int) -> bool:
    cursor = await db.execute("DELETE FROM ai_routes WHERE id = ?", (route_id,))
    await db.commit()
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- cases
async def create_case(db, guild_id: int, **values) -> int:
    now = ai_settings.utc_now()
    cursor = await db.execute(
        """
        INSERT INTO ai_cases (
            guild_id, kind, status, ticket_channel_id, ticket_number, requester_id, subject, department,
            route_id, severity, confidence, summary, details, destination_channel_id, destination_roles,
            ai_handled, escalated, human_review, response_ms, idempotency_key, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (
            guild_id,
            str(values.get("kind") or "support"),
            str(values.get("status") or "open"),
            values.get("ticket_channel_id"),
            values.get("ticket_number"),
            values.get("requester_id"),
            str(values.get("subject") or "")[:300],
            str(values.get("department") or "")[:120],
            values.get("route_id"),
            str(values.get("severity") or "normal")[:24],
            float(values.get("confidence") or 0),
            str(values.get("summary") or "")[:4000],
            json.dumps(values.get("details") or {}),
            values.get("destination_channel_id"),
            json.dumps(ai_settings.as_id_list(values.get("destination_roles"))),
            1 if values.get("ai_handled") else 0,
            1 if values.get("escalated") else 0,
            str(values.get("human_review") or "pending"),
            int(values.get("response_ms") or 0),
            values.get("idempotency_key"),
            now,
            now,
        ),
    )
    await db.commit()
    if cursor.rowcount == 0:
        existing = await find_case_by_key(db, values["idempotency_key"])
        return int(existing["id"])
    return int(cursor.lastrowid or 0)


async def update_case(db, case_id: int, values: dict[str, Any] | None = None, **kwargs: Any) -> bool:
    # Callers pass either one dict or keyword fields (the bot cog uses keywords).
    values = {**(values or {}), **kwargs}
    mapping = {
        "kind": lambda v: str(v)[:24],
        "status": lambda v: str(v)[:24],
        "department": lambda v: str(v)[:120],
        "route_id": lambda v: v,
        "severity": lambda v: str(v)[:24],
        "confidence": lambda v: float(v or 0),
        "summary": lambda v: str(v)[:4000],
        "details": lambda v: json.dumps(v or {}),
        "destination_channel_id": lambda v: v,
        "destination_roles": lambda v: json.dumps(ai_settings.as_id_list(v)),
        "ai_handled": lambda v: 1 if v else 0,
        "escalated": lambda v: 1 if v else 0,
        "human_review": lambda v: str(v)[:24],
        "reviewed_by": lambda v: v,
        "review_note": lambda v: str(v or "")[:1000],
        "response_ms": lambda v: int(v or 0),
        "subject": lambda v: str(v)[:300],
    }
    updates = {key: mapping[key](value) for key, value in values.items() if key in mapping}
    if not updates:
        return False
    updates["updated_at"] = ai_settings.utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    cursor = await db.execute(f"UPDATE ai_cases SET {assignments} WHERE id = ?", (*updates.values(), case_id))
    await db.commit()
    return cursor.rowcount > 0


async def get_case(db, case_id: int) -> dict[str, Any] | None:
    return await _one(db, "SELECT * FROM ai_cases WHERE id = ?", (case_id,))


async def find_case_by_key(db, idempotency_key: str) -> dict[str, Any] | None:
    return await _one(db, "SELECT * FROM ai_cases WHERE idempotency_key = ?", (idempotency_key,))


async def find_open_case(db, channel_id: int) -> dict[str, Any] | None:
    return await _one(
        db,
        "SELECT * FROM ai_cases WHERE ticket_channel_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
        (channel_id,),
    )


async def list_cases(
    db,
    *,
    guild_id: int | None = None,
    kind: str | None = None,
    status: str | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if guild_id:
        clauses.append("guild_id = ?")
        params.append(guild_id)
    if kind and kind != "all":
        clauses.append("kind = ?")
        params.append(kind)
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    if query:
        clauses.append("(summary LIKE ? OR subject LIKE ? OR department LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = await _one(db, f"SELECT COUNT(*) AS total FROM ai_cases {where}", params)
    cases = await _all(
        db,
        f"SELECT * FROM ai_cases {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, max(1, min(limit, 200)), max(0, offset)],
    )
    return {"total": int((total or {}).get("total") or 0), "cases": cases}


async def add_case_event(db, case_id: int, actor: str, action: str, detail: str = "", status: str = "completed") -> None:
    await db.execute(
        "INSERT INTO ai_case_events (case_id, actor, action, detail, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (case_id, actor[:80], action[:120], detail[:2000], status, ai_settings.utc_now()),
    )
    await db.commit()


async def case_events(db, case_id: int) -> list[dict[str, Any]]:
    return await _all(db, "SELECT * FROM ai_case_events WHERE case_id = ? ORDER BY id", (case_id,))


# --------------------------------------------------------------------------- partnerships
async def create_partnership(db, guild_id: int, **values) -> int:
    now = ai_settings.utc_now()
    cursor = await db.execute(
        """
        INSERT INTO ai_partnerships (
            case_id, guild_id, applicant_id, server_name, purpose, member_count, advertisement, invite_code,
            invite_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values.get("case_id"),
            guild_id,
            values.get("applicant_id"),
            str(values.get("server_name") or "")[:200],
            str(values.get("purpose") or "")[:2000],
            values.get("member_count"),
            str(values.get("advertisement") or "")[:4000],
            str(values.get("invite_code") or "")[:120],
            str(values.get("invite_status") or "pending")[:24],
            now,
            now,
        ),
    )
    await db.commit()
    return int(cursor.lastrowid or 0)


async def update_partnership(db, partnership_id: int, values: dict[str, Any] | None = None, **kwargs: Any) -> bool:
    values = {**(values or {}), **kwargs}
    mapping = {
        "case_id": lambda v: v,
        "applicant_id": lambda v: v,
        "server_name": lambda v: str(v)[:200],
        "purpose": lambda v: str(v)[:2000],
        "member_count": lambda v: v,
        "advertisement": lambda v: str(v)[:4000],
        "invite_code": lambda v: str(v)[:120],
        "invite_status": lambda v: str(v)[:24],
        "bot_joined": lambda v: 1 if v else 0,
        "joined_guild_id": lambda v: v,
        "joined_guild_name": lambda v: str(v or "")[:200],
        "permissions_status": lambda v: str(v)[:40],
        "human_verified": lambda v: 1 if v else 0,
        "verified_by": lambda v: v,
        "verification_note": lambda v: str(v or "")[:2000],
        "analysis_status": lambda v: str(v)[:24],
        "analysis": lambda v: json.dumps(v or {}),
        "requirements": lambda v: json.dumps(v or []),
        "eligibility": lambda v: str(v)[:24],
        "recommendation": lambda v: str(v)[:2000],
        "publish_status": lambda v: str(v)[:24],
        "ad_status": lambda v: str(v)[:24],
        "ad_verified_at": lambda v: v,
        "ad_source_message_id": lambda v: v,
        "ad_source_channel_id": lambda v: v,
        "ad_monitor_announced_at": lambda v: v,
        "ad_checks": lambda v: int(v or 0),
        "published_message_id": lambda v: v,
        "published_at": lambda v: v,
    }
    updates = {key: mapping[key](value) for key, value in values.items() if key in mapping}
    if not updates:
        return False
    updates["updated_at"] = ai_settings.utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    cursor = await db.execute(
        f"UPDATE ai_partnerships SET {assignments} WHERE id = ?", (*updates.values(), partnership_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_partnership(db, partnership_id: int) -> dict[str, Any] | None:
    return await _one(db, "SELECT * FROM ai_partnerships WHERE id = ?", (partnership_id,))


async def find_partnership_by_case(db, case_id: int) -> dict[str, Any] | None:
    return await _one(
        db, "SELECT * FROM ai_partnerships WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    )


async def find_partnership_for_applicant(
    db, guild_id: int, applicant_id: int, *, include_published: bool = True
) -> dict[str, Any] | None:
    """Latest application from one applicant, so a reopened ticket continues it."""

    sql = "SELECT * FROM ai_partnerships WHERE guild_id = ? AND applicant_id = ?"
    params: list[Any] = [guild_id, applicant_id]
    if not include_published:
        sql += " AND published_at IS NULL"
    sql += " ORDER BY id DESC LIMIT 1"
    return await _one(db, sql, params)


async def list_partnerships(db, guild_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    return await _all(
        db,
        "SELECT * FROM ai_partnerships WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
        (guild_id, max(1, min(limit, 200))),
    )


# --------------------------------------------------------------------------- analytics
async def analytics(db, guild_id: int | None = None) -> dict[str, Any]:
    def where(column: str) -> tuple[str, list[Any]]:
        if guild_id:
            return f" WHERE {column} = ?", [guild_id]
        return "", []

    async def scalar(sql: str, params: Iterable[Any] = ()) -> float:
        row = await _one(db, sql, params)
        return float(next(iter(row.values())) or 0) if row else 0.0

    case_where, case_params = where("guild_id")
    activity_where, activity_params = where("guild_id")

    total_requests = await scalar(f"SELECT COUNT(*) FROM ai_cases{case_where}", case_params)
    auto_resolved = await scalar(
        f"SELECT COUNT(*) FROM ai_cases{case_where + (' AND' if case_where else ' WHERE')} ai_handled = 1 AND escalated = 0",
        case_params,
    )
    forwarded = await scalar(
        f"SELECT COUNT(*) FROM ai_cases{case_where + (' AND' if case_where else ' WHERE')} escalated = 1",
        case_params,
    )
    forms_analyzed = await scalar(
        f"SELECT COUNT(*) FROM ai_activity{activity_where + (' AND' if activity_where else ' WHERE')} action = 'form.analyze'",
        activity_params,
    )
    partnerships = await scalar(
        f"SELECT COUNT(*) FROM ai_partnerships{case_where + (' AND' if case_where else ' WHERE')} analysis_status = 'complete'",
        case_params,
    )
    avg_response = await scalar(
        f"SELECT AVG(response_ms) FROM ai_cases{case_where + (' AND' if case_where else ' WHERE')} response_ms > 0",
        case_params,
    )
    approved = await scalar(
        f"SELECT COUNT(*) FROM ai_cases{case_where + (' AND' if case_where else ' WHERE')} human_review = 'approved'",
        case_params,
    )
    rejected = await scalar(
        f"SELECT COUNT(*) FROM ai_cases{case_where + (' AND' if case_where else ' WHERE')} human_review = 'rejected'",
        case_params,
    )
    pending = await scalar(
        f"SELECT COUNT(*) FROM ai_cases{case_where + (' AND' if case_where else ' WHERE')} human_review = 'pending'",
        case_params,
    )
    knowledge_approved = await scalar(
        f"SELECT COUNT(*) FROM ai_knowledge{case_where + (' AND' if case_where else ' WHERE')} status = 'approved'",
        case_params,
    )
    knowledge_pending = await scalar(
        f"SELECT COUNT(*) FROM ai_knowledge{case_where + (' AND' if case_where else ' WHERE')} status = 'pending'",
        case_params,
    )
    forms = await scalar(f"SELECT COUNT(*) FROM ai_forms{case_where}", case_params)
    routes = await scalar(f"SELECT COUNT(*) FROM ai_routes{case_where}", case_params)
    jobs_queued = await scalar("SELECT COUNT(*) FROM ai_jobs WHERE status IN ('queued', 'running')")
    jobs_failed = await scalar("SELECT COUNT(*) FROM ai_jobs WHERE status = 'failed'")

    reviewed = approved + rejected
    accuracy = round(approved / reviewed * 100, 1) if reviewed else None
    return {
        "total_requests": int(total_requests),
        "auto_resolved": int(auto_resolved),
        "forwarded": int(forwarded),
        "forms_analyzed": int(forms_analyzed),
        "partnerships_processed": int(partnerships),
        "avg_response_ms": int(avg_response),
        "review": {
            "approved": int(approved),
            "rejected": int(rejected),
            "pending": int(pending),
            "accuracy": accuracy,
        },
        "knowledge": {"approved": int(knowledge_approved), "pending": int(knowledge_pending)},
        "forms": int(forms),
        "routes": int(routes),
        "jobs": {"queued": int(jobs_queued), "failed": int(jobs_failed)},
    }


async def overview(db, guild_id: int | None) -> dict[str, Any]:
    config = await ai_settings.load(db)
    stats = await analytics(db, guild_id)
    activity = await list_activity(db, guild_id=guild_id, limit=12)
    return {
        "settings": ai_settings.public_view(config),
        "analytics": stats,
        "recent_activity": activity["activity"],
        "queue": stats["jobs"],
        "status": {
            "enabled": ai_settings.as_bool(config.get("enabled")),
            "key_configured": ai_settings.key_configured(),
            "provider": config.get("provider"),
            "model": config.get("model"),
            "base_url": config.get("base_url"),
        },
    }
