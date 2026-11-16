"""Upload the emoji.gg artwork into every server and resolve the assigned IDs.

Discord only renders a custom emoji in the servers where that emoji actually
exists. The bot therefore uploads the asset pack in ``emojis/white`` to every
server it is in (it needs Manage Expressions, which Administrator covers) and
remembers the ID each server assigned. Discord has no endpoint that replaces an
emoji's picture, so when a PNG in the pack changes its SHA-256 digest no longer
matches ``emoji_sync_state.json`` and the stale server copy is deleted and
re-uploaded under the same name; that copy gets a new ID, so messages already
using the old one keep the old artwork. Those IDs are what the assistance panel,
the ticket surfaces, the help pages and the dashboard use.

Source code keeps the legacy mentions (``<:icons_help:1382704281945112645>``)
because the filenames of the pack identify the assets by that legacy ID; every
outgoing string and component emoji goes through :func:`resolve_emojis` or
:func:`resolve_component_emoji`, which swap in the ID of the guild the message
is sent to.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import discord

logger = logging.getLogger("discord")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_DIRECTORY = PROJECT_ROOT / "emojis" / "white"
STATE_FILENAME = "emoji_sync_state.json"

ASSET_FILENAME_RE = re.compile(r"^(?P<name>[A-Za-z0-9_]+)-(?P<legacy_id>\d{15,25})\.png$")
MENTION_RE = re.compile(r"<a?:(?P<name>[A-Za-z0-9_]+):(?P<id>\d{15,25})>")
MAX_EMOJI_NAME = 32
UPLOAD_DELAY_SECONDS = 0.4


@dataclass(frozen=True, slots=True)
class EmojiAsset:
    """One PNG of the pack, addressed by the legacy emoji ID it replaces."""

    legacy_id: str
    name: str
    path: Path


@dataclass(slots=True)
class GuildEmojiReport:
    """Outcome of one sync run across every server the bot is in."""

    assets: int = 0
    guilds: int = 0
    created: int = 0
    updated: int = 0
    reused: int = 0
    failures: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": self.assets,
            "guilds": self.guilds,
            "created": self.created,
            "updated": self.updated,
            "reused": self.reused,
            "failures": dict(self.failures),
        }


def discover_assets(directory: Path = DEFAULT_ASSET_DIRECTORY) -> list[EmojiAsset]:
    """Every PNG in the pack, sorted by the emoji name it becomes."""

    assets: list[EmojiAsset] = []
    if not directory.is_dir():
        return assets
    for path in sorted(directory.glob("*.png")):
        match = ASSET_FILENAME_RE.match(path.name)
        if not match:
            logger.warning("Ignoring emoji asset with an unexpected filename: %s", path.name)
            continue
        name = match.group("name")[:MAX_EMOJI_NAME]
        if len(name) < 2 or path.stat().st_size == 0:
            continue
        assets.append(EmojiAsset(legacy_id=match.group("legacy_id"), name=name, path=path))
    return sorted(assets, key=lambda asset: asset.name.lower())


class GuildEmojiRegistry:
    """Which emoji ID each server assigned to each legacy emoji ID."""

    def __init__(self) -> None:
        self._by_guild: dict[int, dict[str, dict[str, str]]] = {}
        self._default_guild_id: int | None = None

    def install(self, guild_id: int, mapping: dict[str, dict[str, str]], *, default: bool = False) -> None:
        self._by_guild[int(guild_id)] = dict(mapping)
        if default or self._default_guild_id not in self._by_guild:
            self._default_guild_id = int(guild_id)

    def clear(self) -> None:
        self._by_guild.clear()
        self._default_guild_id = None

    def mapping(self, guild: Any = None) -> dict[str, dict[str, str]]:
        key = _guild_key(guild)
        if key is not None and key in self._by_guild:
            return self._by_guild[key]
        if self._default_guild_id in self._by_guild:
            return self._by_guild[self._default_guild_id]
        return {}

    def summary(self, guild: Any = None) -> dict[str, Any]:
        mapping = self.mapping(guild)
        return {"synced": len(mapping), "guild_id": self._default_guild_id, "guilds": len(self._by_guild)}


def _guild_key(guild: Any) -> int | None:
    if guild is None:
        return None
    value = getattr(guild, "id", guild)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


registry = GuildEmojiRegistry()
_pack_ids: set[str] = set()


def install_guild_emojis(guild_id: int, mapping: dict[str, dict[str, str]], *, default: bool = False) -> None:
    """Register one server's emoji IDs (used by the sync and by the tests)."""

    registry.install(guild_id, mapping, default=default)


def known_legacy_ids() -> set[str]:
    """Legacy IDs that belong to the pack, even before a server assigned one."""

    global _pack_ids
    if not _pack_ids:
        _pack_ids = {asset.legacy_id for asset in discover_assets()}
    return _pack_ids


def resolve_emojis(value: str, guild: Any = None) -> str:
    """Swap legacy pack mentions for the emoji IDs of ``guild``.

    A pack mention that has no server copy yet is dropped (it would render as
    broken text); mentions of emojis the pack does not own are left untouched so
    user-authored content survives.
    """

    if not isinstance(value, str) or "<:" not in value:
        return value

    mapping = registry.mapping(guild)
    if not mapping and not known_legacy_ids():
        return value

    def replace(match: re.Match[str]) -> str:
        record = mapping.get(match.group("id"))
        if record:
            return f"<:{record['name']}:{record['id']}>"
        if match.group("id") in known_legacy_ids():
            return ""
        return match.group(0)

    return MENTION_RE.sub(replace, value)


def resolve_component_emoji(value: str | None, guild: Any = None) -> str | None:
    """Resolve one emoji for a button, select option or thumbnail.

    Returns ``None`` when the server copy is missing so callers can fall back to
    a Unicode glyph instead of sending a broken custom emoji.
    """

    if not value:
        return value
    match = MENTION_RE.fullmatch(value.strip())
    if not match:
        return value
    record = registry.mapping(guild).get(match.group("id"))
    if record:
        return f"<:{record['name']}:{record['id']}>"
    if match.group("id") in known_legacy_ids():
        return None
    return value


def _state_path() -> Path:
    """Where the artwork already uploaded to each server is recorded."""

    from utils import config as config_module

    database = Path(config_module.config.DATABASE_PATH)
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    return database.parent / STATE_FILENAME


def _read_state(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: value for name, value in data.items() if isinstance(value, str)}


def _write_state(path: Path, state: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as error:
        logger.warning("Could not record emoji artwork state in %s: %s", path, error)


def _default_guild_id(guilds: list[discord.Guild]) -> int | None:
    """Prefer the configured community server, otherwise the first one."""

    from utils import assistance

    for guild in guilds:
        if assistance.GUILD_ID and guild.id == assistance.GUILD_ID:
            return guild.id
    return guilds[0].id if guilds else None


async def sync_guild_emojis(
    bot: Any,
    guilds: Iterable[discord.Guild] | None = None,
    *,
    asset_directory: Path | None = None,
    state_path: Path | None = None,
) -> GuildEmojiReport:
    """Upload the pack to every server, reusing or refreshing existing emojis."""

    assets = discover_assets(asset_directory or DEFAULT_ASSET_DIRECTORY)
    report = GuildEmojiReport(assets=len(assets))
    target_guilds = list(bot.guilds if guilds is None else guilds)
    if not target_guilds or not assets:
        return report

    images = {asset.name: asset.path.read_bytes() for asset in assets}
    digests = {name: hashlib.sha256(image).hexdigest() for name, image in images.items()}
    state_file = state_path or _state_path()
    uploaded = _read_state(state_file)
    state_changed = False

    default_guild_id = _default_guild_id(target_guilds)
    for guild in target_guilds:
        existing = {emoji.name: emoji for emoji in guild.emojis}
        mapping: dict[str, dict[str, str]] = {}
        for asset in assets:
            emoji = existing.get(asset.name)
            changed = emoji is not None and uploaded.get(asset.name) != digests[asset.name]
            try:
                if emoji is None:
                    if len(existing) >= guild.emoji_limit:
                        report.failures[asset.name] = f"{guild.name}: no free server emoji slots"
                        continue
                elif changed:
                    # Discord has no endpoint to replace an emoji's picture, so
                    # the stale copy is deleted and re-uploaded under the same name.
                    await emoji.delete(reason="Ticket system emoji pack")
                    existing.pop(asset.name, None)
                else:
                    report.reused += 1
                    mapping[asset.legacy_id] = {"name": emoji.name, "id": str(emoji.id)}
                    continue

                emoji = await guild.create_custom_emoji(
                    name=asset.name,
                    image=images[asset.name],
                    reason="Ticket system emoji pack",
                )
            except (discord.HTTPException, OSError) as error:
                report.failures[asset.name] = f"{guild.name}: {error}"
                logger.warning("Could not upload emoji %s to %s: %s", asset.name, guild.name, error)
                continue

            existing[emoji.name] = emoji
            uploaded[asset.name] = digests[asset.name]
            state_changed = True
            if changed:
                report.updated += 1
            else:
                report.created += 1
            await asyncio.sleep(UPLOAD_DELAY_SECONDS)
            mapping[asset.legacy_id] = {"name": emoji.name, "id": str(emoji.id)}

        registry.install(guild.id, mapping, default=guild.id == default_guild_id)
        report.guilds += 1
        logger.info(
            "Server emoji pack ready for %s: %d emoji(s) available, %d missing",
            guild.name,
            len(mapping),
            len(assets) - len(mapping),
        )

    if state_changed:
        _write_state(state_file, uploaded)
    return report
