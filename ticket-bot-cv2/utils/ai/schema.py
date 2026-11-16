"""Database schema for the AI Control System."""

from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_knowledge_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scanned_at TEXT,
    messages_scanned INTEGER NOT NULL DEFAULT 0,
    entries_found INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS ai_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    confidence REAL NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT 'dashboard',
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ai_knowledge_status ON ai_knowledge (guild_id, status);

CREATE TABLE IF NOT EXISTS ai_forms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'General',
    questions TEXT NOT NULL DEFAULT '[]',
    required_fields TEXT NOT NULL DEFAULT '[]',
    department TEXT NOT NULL DEFAULT '',
    role_ids TEXT NOT NULL DEFAULT '[]',
    destination_channel_id INTEGER,
    instructions TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    report_category TEXT NOT NULL DEFAULT 'any',
    severity TEXT NOT NULL DEFAULT 'any',
    role_ids TEXT NOT NULL DEFAULT '[]',
    backup_role_ids TEXT NOT NULL DEFAULT '[]',
    destination_channel_id INTEGER,
    escalate_after_minutes INTEGER NOT NULL DEFAULT 60,
    notify INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'support',
    status TEXT NOT NULL DEFAULT 'open',
    ticket_channel_id INTEGER,
    ticket_number INTEGER,
    requester_id INTEGER,
    subject TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    route_id INTEGER,
    severity TEXT NOT NULL DEFAULT 'normal',
    confidence REAL NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '{}',
    destination_channel_id INTEGER,
    destination_roles TEXT NOT NULL DEFAULT '[]',
    ai_handled INTEGER NOT NULL DEFAULT 0,
    escalated INTEGER NOT NULL DEFAULT 0,
    human_review TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    review_note TEXT,
    response_ms INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ai_cases_created ON ai_cases (guild_id, created_at);

CREATE TABLE IF NOT EXISTS ai_case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    actor TEXT NOT NULL DEFAULT 'ai',
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    case_id INTEGER,
    actor TEXT NOT NULL DEFAULT 'ai',
    user_id INTEGER,
    ticket_channel_id INTEGER,
    action TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    destination_role TEXT NOT NULL DEFAULT '',
    destination_channel TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    review_status TEXT NOT NULL DEFAULT 'none',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ai_activity_created ON ai_activity (created_at);

CREATE TABLE IF NOT EXISTS ai_partnerships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,
    guild_id INTEGER NOT NULL,
    applicant_id INTEGER,
    server_name TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    member_count INTEGER,
    advertisement TEXT NOT NULL DEFAULT '',
    invite_code TEXT NOT NULL DEFAULT '',
    invite_status TEXT NOT NULL DEFAULT 'pending',
    bot_joined INTEGER NOT NULL DEFAULT 0,
    joined_guild_id INTEGER,
    joined_guild_name TEXT NOT NULL DEFAULT '',
    permissions_status TEXT NOT NULL DEFAULT 'unknown',
    human_verified INTEGER NOT NULL DEFAULT 0,
    verified_by TEXT,
    verification_note TEXT NOT NULL DEFAULT '',
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis TEXT NOT NULL DEFAULT '{}',
    requirements TEXT NOT NULL DEFAULT '[]',
    eligibility TEXT NOT NULL DEFAULT 'manual_review',
    recommendation TEXT NOT NULL DEFAULT '',
    publish_status TEXT NOT NULL DEFAULT 'pending',
    ad_status TEXT NOT NULL DEFAULT 'pending',
    ad_verified_at TEXT,
    ad_source_message_id INTEGER,
    ad_source_channel_id INTEGER,
    ad_monitor_announced_at TEXT,
    ad_checks INTEGER NOT NULL DEFAULT 0,
    published_message_id INTEGER,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    idempotency_key TEXT UNIQUE,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS ai_jobs_queue ON ai_jobs (status, available_at);
"""


# Columns added after the first release: existing databases get them here.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "ai_partnerships": {
        "ad_status": "TEXT NOT NULL DEFAULT 'pending'",
        "ad_verified_at": "TEXT",
        "ad_source_message_id": "INTEGER",
        "ad_source_channel_id": "INTEGER",
        "ad_monitor_announced_at": "TEXT",
        "ad_checks": "INTEGER NOT NULL DEFAULT 0",
    },
}


async def _ensure_columns(db, table: str, columns: dict[str, str]) -> None:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        existing = {row[1] for row in await cursor.fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


async def ensure_schema(db) -> None:
    """Create every AI table; safe to call from both processes on every start."""

    await db.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        await _ensure_columns(db, table, columns)
    await db.commit()
