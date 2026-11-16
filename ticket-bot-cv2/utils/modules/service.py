"""Validation, delivery, forwarding and auditing for the card modules.

This is the single path every card takes: a slash command, the welcome listener
and the dashboard preview all build the same spec from validated values, so a
card can never be posted with a value the issuer does not control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import discord

from utils import assistance
from utils.modules import cards
from utils.modules import settings as module_settings
from utils.modules import text as text_utils

logger = logging.getLogger("discord")

COOLDOWN_SECONDS = 5
MAX_REASON = 900
MAX_SHORT = 200
STAFF_ROLE_NAMES = (*assistance.ADMIN_ROLE_NAMES, *assistance.MODERATOR_ROLE_NAMES)

_LAST_ISSUED: dict[tuple[int, str, int], float] = {}


class CardError(ValueError):
    """A card was rejected; the message is safe to show to the issuer."""


# --------------------------------------------------------------------------- permissions
def is_staff(member: discord.Member | None, settings: dict[str, Any]) -> bool:
    """Administrators, configured staff roles and the fixed staff role names."""

    if member is None:
        return False
    permissions = getattr(member, "guild_permissions", None)
    if permissions and (permissions.administrator or permissions.manage_guild):
        return True
    roles = list(getattr(member, "roles", []))
    configured = {int(value) for value in settings.get("staff_role_ids", [])}
    if configured & {role.id for role in roles}:
        return True
    wanted = {assistance.role_key(name) for name in STAFF_ROLE_NAMES}
    return any(assistance.role_key(role.name) in wanted for role in roles)


def require_staff(member: discord.Member | None, settings: dict[str, Any]) -> None:
    if not is_staff(member, settings):
        raise CardError(
            "You need an administrator rank or a Management rank to publish cards."
        )


def _check_cooldown(guild_id: int, kind: str, actor_id: int) -> None:
    if len(_LAST_ISSUED) > 500:
        _LAST_ISSUED.clear()
    key = (guild_id, kind, actor_id)
    now = time.monotonic()
    last = _LAST_ISSUED.get(key, 0.0)
    if now - last < COOLDOWN_SECONDS:
        raise CardError(f"Slow down — wait a moment before publishing another {kind} card.")
    _LAST_ISSUED[key] = now


# --------------------------------------------------------------------------- validation
def _member(guild: discord.Guild, value: Any, field_name: str) -> discord.Member:
    if not isinstance(value, discord.Member) or value.guild.id != guild.id:
        raise CardError(f"Choose a {field_name} who is a member of this server.")
    return value


def _role(guild: discord.Guild, value: Any, field_name: str) -> discord.Role | None:
    if value is None:
        return None
    if not isinstance(value, discord.Role) or value.guild.id != guild.id:
        raise CardError(f"That {field_name} does not belong to this server.")
    if value.is_default():
        raise CardError(f"Pick a real {field_name}, not @everyone.")
    me = guild.me
    if me is not None and value >= me.top_role:
        raise CardError(
            f"**{value.name}** sits above my highest role, so I cannot show it as a {field_name}. "
            "Move my role above it and try again."
        )
    return value


def _role_or_text(guild: discord.Guild, value: Any, field_name: str) -> str:
    """A role mention, or a cleaned label — never Discord's "@unknown-role"."""

    role = _role(guild, value, field_name)
    if role is not None:
        return role.mention
    return text_utils.safe_text(value, MAX_SHORT) or "Not specified"


def _approver(guild: discord.Guild, actor: discord.Member, value: Any) -> discord.Member:
    """Who approved the card. Only an administrator may credit someone else."""

    if value is None or (isinstance(value, discord.Member) and value.id == actor.id):
        return actor
    member = _member(guild, value, "approver")
    permissions = getattr(actor, "guild_permissions", None)
    if not permissions or not permissions.administrator:
        raise CardError("Only an administrator can approve a card on behalf of another staff member.")
    return member


def _required(value: Any, limit: int, field_name: str) -> str:
    cleaned = text_utils.safe_text(value, limit, multiline=True)
    if not cleaned:
        raise CardError(f"Add a {field_name} before publishing the card.")
    return cleaned


def _optional(value: Any, limit: int) -> str:
    return text_utils.safe_text(value, limit, multiline=True)


def _stars(rating: Any) -> str:
    try:
        value = int(rating)
    except (TypeError, ValueError):
        raise CardError("The rating must be a whole number from 1 to 5.") from None
    if not 1 <= value <= 5:
        raise CardError("The rating must be a whole number from 1 to 5.")
    return f"{'★' * value}{'☆' * (5 - value)} ({value}/5)"


def _joined(member: discord.Member) -> str:
    if member.joined_at is None:
        return "—"
    return discord.utils.format_dt(member.joined_at, "R")


def validate(kind: str, guild: discord.Guild, actor: discord.Member, payload: dict[str, Any]) -> dict[str, str]:
    """Turn raw command input into the display values a card is built from."""

    if kind == "infraction":
        member = _member(guild, payload.get("member"), "member")
        approver = _approver(guild, actor, payload.get("approved_by"))
        return {
            "member_mention": member.mention,
            "member_name": member.display_name,
            "punishment": _role_or_text(guild, payload.get("punishment"), "punishment role"),
            "approved_by": approver.mention,
            "appealable": "Yes" if payload.get("appealable") else "No",
            "reason": _required(payload.get("reason"), MAX_REASON, "reason"),
            "actor_name": actor.display_name,
        }
    if kind == "promotion":
        member = _member(guild, payload.get("member"), "member")
        approver = _approver(guild, actor, payload.get("approved_by"))
        return {
            "member_mention": member.mention,
            "rank": _role_or_text(guild, payload.get("rank"), "rank role"),
            "approved_by": approver.mention,
            "notes": _optional(payload.get("notes"), MAX_REASON),
            "actor_name": actor.display_name,
        }
    if kind == "feedback":
        staff = _member(guild, payload.get("staff"), "staff member")
        return {
            "staff_mention": staff.mention,
            "rating": _stars(payload.get("rating")),
            "submitted_by": actor.mention,
            "feedback": _required(payload.get("feedback"), MAX_REASON, "feedback message"),
            "actor_name": actor.display_name,
        }
    if kind == "session":
        host = _member(guild, payload.get("host"), "host")
        attendees = _optional(payload.get("attendees"), MAX_SHORT) or "Not recorded"
        duration = _optional(payload.get("duration"), MAX_SHORT) or "Not recorded"
        return {
            "host_mention": host.mention,
            "session": _required(payload.get("session"), MAX_SHORT, "session name"),
            "attendees": attendees,
            "duration": duration,
            "actor_mention": actor.mention,
            "summary": _required(payload.get("summary"), MAX_REASON, "summary"),
            "actor_name": actor.display_name,
        }
    if kind == "welcome":
        member = _member(guild, payload.get("member"), "member")
        template = str(payload.get("template") or module_settings.DEFAULT_WELCOME)
        greeting = template.replace("{user}", member.mention).replace("{server}", guild.name)
        return {
            "greeting": text_utils.clean(greeting, 320, multiline=True),
            "member_count": f"{guild.member_count:,}" if guild.member_count else "—",
            "joined": _joined(member),
            "link_label": f"{guild.member_count:,} Dashboard" if guild.member_count else "Dashboard",
        }
    raise CardError(f"Unknown card type: {kind}")


# --------------------------------------------------------------------------- delivery
def _postable(channel: Any, guild: discord.Guild) -> tuple[bool, str]:
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return False, "That channel cannot receive cards."
    if getattr(getattr(channel, "guild", None), "id", None) != guild.id:
        return False, "That channel belongs to another server."
    me = guild.me
    if me is None:
        return False, "I am not fully connected to this server yet."
    permissions = channel.permissions_for(me)
    missing = [
        name
        for name, allowed in (
            ("View Channel", permissions.view_channel),
            ("Send Messages", permissions.send_messages),
            ("Attach Files", permissions.attach_files),
        )
        if not allowed
    ]
    if missing:
        return False, f"I need {', '.join(missing)} in #{channel.name}."
    return True, ""


async def deliver(channel: Any, spec: cards.CardSpec) -> discord.Message:
    """Send one card, retrying a single transient failure."""

    last_error: Exception | None = None
    for _attempt in range(2):
        view, file = spec.layout(getattr(channel, "guild", None))
        try:
            return await channel.send(view=view, file=file, allowed_mentions=spec.allowed_mentions())
        except discord.HTTPException as error:  # rate limits, gateway blips
            last_error = error
            logger.warning("Card delivery to %s failed: %s", getattr(channel, "id", "?"), error)
    raise CardError(f"Discord refused the card in #{getattr(channel, 'name', 'channel')}: {last_error}")


async def forward(
    bot, guild: discord.Guild, spec: cards.CardSpec, source_channel_id: int, targets: list[int]
) -> tuple[list[int], list[dict[str, Any]]]:
    """Mirror the card into the configured channels of other servers."""

    forwarded: list[int] = []
    failures: list[dict[str, Any]] = []
    for channel_id in targets:
        if channel_id == source_channel_id:
            continue
        channel = bot.get_channel(channel_id)
        if channel is None:
            failures.append({"channel_id": channel_id, "error": "Channel not found or not cached."})
            continue
        channel_guild = getattr(channel, "guild", None)
        if channel_guild is None:
            failures.append({"channel_id": channel_id, "error": "Not a server channel."})
            continue
        ok, reason = _postable(channel, channel_guild)
        if not ok:
            failures.append({"channel_id": channel_id, "error": reason})
            continue
        try:
            await deliver(channel, spec)
        except CardError as error:
            failures.append({"channel_id": channel_id, "error": str(error)})
        else:
            forwarded.append(channel.id)
    return forwarded, failures


async def _record(
    bot,
    *,
    guild_id: int,
    kind: str,
    actor_id: int,
    target_id: int | None,
    approved_by_id: int | None,
    channel_id: int | None,
    message_id: int | None,
    summary: str,
    payload: dict[str, Any],
    forwarded: list[int],
    failures: list[dict[str, Any]],
) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        await bot.db.execute(
            "INSERT INTO module_entries (guild_id, kind, actor_id, target_id, approved_by_id, channel_id, "
            "message_id, summary, payload, forwarded, failures, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                kind,
                actor_id,
                target_id,
                approved_by_id,
                channel_id,
                message_id,
                summary[:300],
                json.dumps(payload),
                json.dumps(forwarded),
                json.dumps(failures),
                stamp,
            ),
        )
        await bot.db.commit()
    except Exception:
        logger.exception("Could not record the %s card entry", kind)


async def issue(
    bot,
    guild: discord.Guild,
    actor: discord.Member,
    kind: str,
    *,
    channel: Any = None,
    system: bool = False,
    **payload: Any,
) -> dict[str, Any]:
    """Validate, publish, forward and audit one card."""

    if kind not in module_settings.KINDS:
        raise CardError(f"Unknown card type: {kind}")
    settings = await module_settings.load(bot.db)
    if not module_settings.enabled(settings, kind):
        label = module_settings.KIND_LABELS[kind]
        raise CardError(f"The {label} module is switched off. Turn it on in the dashboard first.")

    if system:
        if actor is None:
            raise CardError("The welcome card needs a sender.")
    else:
        require_staff(actor, settings)
        _check_cooldown(guild.id, kind, actor.id)

    label = str(settings.get("server_label") or guild.name)
    if kind == "welcome":
        payload.setdefault("template", settings.get("welcome_message"))
        target_channel_id = int(settings.get("welcome_channel_id") or 0)
        channel = channel or (guild.get_channel(target_channel_id) if target_channel_id else None)
        if channel is None:
            raise CardError("Set a welcome channel in the dashboard before posting welcome cards.")
    if channel is None:
        raise CardError("Choose a channel to publish the card in.")

    ok, reason = _postable(channel, guild)
    if not ok:
        raise CardError(reason)

    data = validate(kind, guild, actor, payload)
    # Rendering the 1600x420 banner is CPU-bound Pillow work: keep it off the
    # gateway's event loop so other events are still handled while a card builds.
    spec = await asyncio.to_thread(cards.build, kind, settings, label, data)
    message = await deliver(channel, spec)

    forwarded, failures = await forward(
        bot, guild, spec, channel.id, list(settings.get("forward_channel_ids", []))
    )
    if failures:
        logger.warning("Card %s forwarded with %d failure(s): %s", kind, len(failures), failures)

    target_id = None
    if kind == "infraction" or kind == "promotion":
        target = payload.get("member")
        target_id = getattr(target, "id", None)
    elif kind == "feedback":
        target_id = getattr(payload.get("staff"), "id", None)
    elif kind == "session":
        target_id = getattr(payload.get("host"), "id", None)
    elif kind == "welcome":
        target_id = getattr(payload.get("member"), "id", None)

    await _record(
        bot,
        guild_id=guild.id,
        kind=kind,
        actor_id=getattr(actor, "id", 0) or 0,
        target_id=target_id,
        approved_by_id=getattr(payload.get("approved_by"), "id", None),
        channel_id=channel.id,
        message_id=message.id,
        summary=spec.summary(),
        payload={key: value for key, value in data.items() if isinstance(value, str)},
        forwarded=forwarded,
        failures=failures,
    )
    logger.info("Published %s card %s in #%s", kind, message.id, getattr(channel, "name", "?"))
    return {
        "message": message,
        "spec": spec,
        "forwarded": forwarded,
        "failures": failures,
        "data": data,
    }


async def post_welcome(bot, guild: discord.Guild, member: discord.Member, channel: Any = None) -> dict[str, Any]:
    """Welcome a new member without a staff member in the loop."""

    return await issue(bot, guild, guild.me, "welcome", channel=channel, system=True, member=member)


def failure_note(failures: list[dict[str, Any]]) -> str:
    if not failures:
        return ""
    listed = ", ".join(f"`{item['channel_id']}`" for item in failures[:5])
    return f"\n**Forwarding failed for:** {listed}"
