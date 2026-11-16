"""Configuration for the automated Discord card modules.

Every value is stored as text in ``module_settings`` and decoded on load, so the
bot and the dashboard always read the same picture. Each key has its own
normaliser: nothing a browser or a slash command sends is stored unvalidated.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KINDS = ("infraction", "promotion", "feedback", "session", "welcome")

KIND_LABELS = {
    "infraction": "Infraction",
    "promotion": "Promotion",
    "feedback": "Feedback",
    "session": "Session Shutdown",
    "welcome": "Welcome",
}

KIND_BLURBS = {
    "infraction": "Punishments issued by the high ranking team.",
    "promotion": "Promotions that recognise a member's work.",
    "feedback": "Star ratings submitted about a staff member.",
    "session": "Shutdown summary after a hosted session.",
    "welcome": "Greeting card posted for every new member.",
}

COMMAND_NAMES = {
    "infraction": "/infraction issue",
    "promotion": "/promotion issue",
    "feedback": "/feedback submit",
    "session": "/session end",
    "welcome": "/welcome post",
}

COLOR_PATTERN = re.compile(r"^#[0-9A-F]{6}$")
URL_PATTERN = re.compile(r"^https?://[^\s<>\"']{4,400}$")
IMAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.(?:png|jpe?g|webp|gif)$", re.IGNORECASE)
MAX_BANNER_BYTES = 6 * 1024 * 1024

DEFAULT_WELCOME = "Hey {user}, welcome to {server}. We hope that you have a good journey with us!"
MAX_FORWARD_CHANNELS = 10
MAX_STAFF_ROLES = 20

# Owner-uploaded banner artwork, one optional file per card type.
IMAGE_KEYS = {f"banner_image_{kind}": kind for kind in KINDS}

DEFAULTS: dict[str, str] = {
    "banner_tagline": "Experience Cali Like Never Before",
    "server_label": "",
    "brand_color": "#FCC200",
    "dashboard_url": "",
    "welcome_message": DEFAULT_WELCOME,
    "welcome_channel_id": "",
    "forward_channel_ids": "[]",
    "staff_role_ids": "[]",
    "enabled": json.dumps({kind: True for kind in KINDS}, sort_keys=True),
    **{key: "" for key in IMAGE_KEYS},
}

TEXT_LIMITS = {
    "banner_tagline": 60,
    "server_label": 48,
    "welcome_message": 320,
}

JSON_LIST_KEYS = {"forward_channel_ids": MAX_FORWARD_CHANNELS, "staff_role_ids": MAX_STAFF_ROLES}
ID_KEYS = {"welcome_channel_id"}


def _text(value: Any, limit: int) -> str:
    raw = "".join(char for char in str(value or "") if char.isprintable())
    return " ".join(raw.split())[:limit]


def _id_list(value: Any, limit: int) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = []
    if not isinstance(value, list):
        return []
    seen: list[int] = []
    for item in value:
        text = str(item).strip()
        # Discord snowflakes are 64-bit; int() keeps every digit.
        if text.isdigit() and int(text) not in seen:
            seen.append(int(text))
        if len(seen) >= limit:
            break
    return seen


def _enabled_map(value: Any) -> dict[str, bool]:
    parsed: Any = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {kind: _as_flag(parsed.get(kind, True)) for kind in KINDS}


def _as_flag(value: Any) -> bool:
    """A real boolean, never the truthiness of the string ``"false"``."""

    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def decode(key: str, value: Any) -> Any:
    """Turn one stored value into the shape callers expect."""

    if key in TEXT_LIMITS:
        return _text(value, TEXT_LIMITS[key])
    if key == "brand_color":
        text = str(value or "").strip()
        candidate = text.upper() if text.startswith("#") else f"#{text.upper()}"
        return candidate if COLOR_PATTERN.match(candidate) else DEFAULTS["brand_color"]
    if key == "dashboard_url":
        text = str(value or "").strip()
        return text if URL_PATTERN.match(text) else ""
    if key in IMAGE_KEYS:
        text = Path(str(value or "").strip()).name
        return text if IMAGE_NAME_PATTERN.match(text) else ""
    if key in JSON_LIST_KEYS:
        return _id_list(value, JSON_LIST_KEYS[key])
    if key in ID_KEYS:
        text = str(value or "").strip()
        return int(text) if text.isdigit() else 0
    if key == "enabled":
        return _enabled_map(value)
    return _text(value, 200)


def normalise(key: str, value: Any) -> str:
    """Serialise one key back to the text stored in the database."""

    decoded = decode(key, value)
    if key in JSON_LIST_KEYS:
        return json.dumps(decoded)
    if key == "enabled":
        return json.dumps(decoded, sort_keys=True)
    if key in ID_KEYS:
        return str(decoded) if decoded else ""
    return str(decoded)


async def load(db) -> dict[str, Any]:
    """Every module setting, decoded and merged over the defaults."""

    async with db.execute("SELECT key, value FROM module_settings") as cursor:
        rows = await cursor.fetchall()
    stored = {str(key): value for key, value in rows}
    return {key: decode(key, stored.get(key, default)) for key, default in DEFAULTS.items()}


async def save(db, values: dict[str, Any]) -> dict[str, Any]:
    """Persist the keys the caller sent; unknown keys are ignored."""

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        await db.execute(
            "INSERT INTO module_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, normalise(key, value), stamp),
        )
    await db.commit()
    return await load(db)


def public_view(settings: dict[str, Any]) -> dict[str, Any]:
    """Settings plus the labels the dashboard renders."""

    return {
        **settings,
        "kinds": [
            {
                "kind": kind,
                "label": KIND_LABELS[kind],
                "blurb": KIND_BLURBS[kind],
                "command": COMMAND_NAMES[kind],
                "enabled": bool(settings.get("enabled", {}).get(kind, True)),
                "banner_image": str(settings.get(f"banner_image_{kind}") or ""),
                "banner_url": (
                    f"/media/uploads/{settings[f'banner_image_{kind}']}"
                    if settings.get(f"banner_image_{kind}")
                    else ""
                ),
            }
            for kind in KINDS
        ],
    }


def uploads_dir() -> Path:
    """Where the dashboard keeps uploaded images (beside the bot database)."""

    from utils import config as config_module

    database = Path(config_module.config.DATABASE_PATH)
    if not database.is_absolute():
        database = Path(__file__).resolve().parents[2] / database
    return database.parent / "uploads"


def banner_image(settings: dict[str, Any], kind: str) -> bytes | None:
    """The owner-uploaded banner for one card type, if it is still on disk."""

    name = str(settings.get(f"banner_image_{kind}") or "")
    if not name:
        return None
    path = uploads_dir() / name
    try:
        if path.is_file() and path.stat().st_size <= MAX_BANNER_BYTES:
            return path.read_bytes()
    except OSError:  # pragma: no cover - unreadable file falls back to the artwork
        return None
    return None


def color_int(settings: dict[str, Any]) -> int:
    return int(str(settings.get("brand_color") or DEFAULTS["brand_color"])[1:], 16)


def enabled(settings: dict[str, Any], kind: str) -> bool:
    return bool(settings.get("enabled", {}).get(kind, True))
