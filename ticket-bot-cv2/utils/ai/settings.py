"""AI settings shared by the dashboard and the bot.

Values live in ``ai_settings`` as strings; JSON-shaped settings are decoded on
load so callers always see real lists/dicts. The provider API key is the one
exception: it comes from the environment (``AI_API_KEY`` then
``GROQ_API_KEY``) and is never written to the database or returned to a
browser.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

DEFAULT_SYSTEM_INSTRUCTIONS = (
    "You are the support assistant for a Discord roleplay community. Answer only from the approved "
    "knowledge base and the server facts you are given. Never invent policy, staff names, punishments, "
    "channel names or requirements. When the knowledge does not cover the question, say that you cannot "
    "verify the answer and offer to forward the question to staff."
)

DEFAULT_PERSONALITY = "Professional, calm and concise. Never claim a human reviewed anything."
DEFAULT_ESCALATION = "notify_staff"
ESCALATION_MODES = ("notify_staff", "assign_ticket")
MIN_PARTNERSHIP_MEMBERS = 15

DEFAULT_REQUIREMENTS: dict[str, Any] = {
    "min_members": MIN_PARTNERSHIP_MEMBERS,
}

DEFAULT_FEATURES: dict[str, Any] = {
    "knowledge_ingest": True,
    "staff_routing": True,
    "partnership_automation": True,
}

DEFAULT_PERMISSIONS: dict[str, Any] = {
    "control_role_ids": [],
    "sensitive_role_ids": [],
}

DEFAULTS: dict[str, str] = {
    "enabled": "1",
    "provider": "groq",
    "model": "openai/gpt-oss-120b",
    "base_url": "https://api.groq.com/openai/v1",
    "temperature": "0.2",
    "max_output_tokens": "700",
    "system_instructions": DEFAULT_SYSTEM_INSTRUCTIONS,
    "personality": DEFAULT_PERSONALITY,
    "confidence_threshold": "0.62",
    "human_approval_required": "0",
    "max_processing_seconds": "90",
    "escalation_behavior": DEFAULT_ESCALATION,
    "retention_days": "180",
    "partnership_channel_id": "",
    "partnership_ad_channel_id": "",
    "partnership_role_ids": "[]",
    "partnership_requirements": json.dumps(DEFAULT_REQUIREMENTS),
    "features": json.dumps(DEFAULT_FEATURES),
    "permissions": json.dumps(DEFAULT_PERMISSIONS),
}

JSON_KEYS = {"partnership_role_ids", "partnership_requirements", "features", "permissions"}
# partnership_role_ids is a JSON list, not an object: decoding it with as_dict
# returned {}, which broke every caller that iterates the IDs.
ID_LIST_KEYS = {"partnership_role_ids"}
BOOL_KEYS = {"enabled", "human_approval_required"}
NUMBER_KEYS = {"temperature", "max_output_tokens", "confidence_threshold", "max_processing_seconds", "retention_days"}
ID_KEYS = {"partnership_channel_id", "partnership_ad_channel_id"}
KNOWN_KEYS = tuple(DEFAULTS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def as_int(value: Any, *, default: int = 0) -> int:
    text = str(value if value is not None else "").strip()
    try:
        # Discord snowflakes are 64-bit: never route them through float.
        return int(text)
    except ValueError:
        pass
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_id_list(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = []
    if not isinstance(value, list):
        return []
    return sorted({int(item) for item in value if str(item).isdigit()})


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def normalise(key: str, value: Any) -> str:
    """Serialise one setting for storage, rejecting values a key cannot hold."""

    if key == "partnership_requirements":
        # Partnership terms are intentionally fixed: the 15-member threshold is
        # the only server-quality requirement and old dashboard values must not
        # reintroduce a higher limit or an organisation check.
        return json.dumps(DEFAULT_REQUIREMENTS, sort_keys=True)
    if key in JSON_KEYS:
        return json.dumps(value if isinstance(value, (dict, list)) else as_dict(value), sort_keys=True)
    if key in BOOL_KEYS:
        return "1" if as_bool(value) else "0"
    if key in NUMBER_KEYS:
        return str(as_float(value))
    if key in ID_KEYS:
        raw = str(value or "").strip()
        return raw if raw.isdigit() else ""
    return str(value or "").strip()[:4000]


async def load(db) -> dict[str, Any]:
    """Every setting, merged over the defaults and JSON-decoded."""

    async with db.execute("SELECT key, value FROM ai_settings") as cursor:
        rows = await cursor.fetchall()
    stored = {key: value for key, value in rows}
    merged: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        raw = stored.get(key, default)
        if key in ID_LIST_KEYS:
            merged[key] = as_id_list(raw)
        elif key in JSON_KEYS:
            value = as_dict(raw)
            if key == "partnership_requirements":
                value = dict(DEFAULT_REQUIREMENTS)
            merged[key] = value
        elif key in BOOL_KEYS:
            merged[key] = as_bool(raw, default=as_bool(default))
        elif key in NUMBER_KEYS:
            merged[key] = as_float(raw, default=as_float(default))
        elif key in ID_KEYS:
            merged[key] = as_int(raw)
        else:
            merged[key] = str(raw)
    return merged


async def save(db, values: dict[str, Any]) -> dict[str, Any]:
    """Persist the keys the caller sent; unknown keys are ignored."""

    now = utc_now()
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        await db.execute(
            "INSERT INTO ai_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, normalise(key, value), now),
        )
    await db.commit()
    return await load(db)


def api_key() -> str:
    """The provider key, from the environment only."""

    return (os.getenv("AI_API_KEY") or os.getenv("GROQ_API_KEY") or "").strip()


def key_configured() -> bool:
    return bool(api_key())


def permissions(settings: dict[str, Any]) -> dict[str, list[int]]:
    raw = as_dict(settings.get("permissions"))
    return {
        "control_role_ids": as_id_list(raw.get("control_role_ids")),
        "sensitive_role_ids": as_id_list(raw.get("sensitive_role_ids")),
    }


def features(settings: dict[str, Any]) -> dict[str, bool]:
    raw = as_dict(settings.get("features"))
    return {name: as_bool(raw.get(name), default=default) for name, default in DEFAULT_FEATURES.items()}


def provider_label(settings: dict[str, Any]) -> str:
    return f"{settings.get('provider') or 'groq'} · {settings.get('model') or 'unset'}"


def public_view(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings safe to hand to the browser (no credentials, no key state beyond a flag)."""

    return {
        **settings,
        "key_configured": key_configured(),
        "permissions": permissions(settings),
        "features": features(settings),
    }
