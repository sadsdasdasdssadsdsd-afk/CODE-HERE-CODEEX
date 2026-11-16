"""Discord presence helpers shared by the bot and the dashboard bridge."""

from __future__ import annotations

import discord

ACTIVITY_LABELS = {
    "playing": "Playing",
    "watching": "Watching",
    "listening": "Listening",
    "competing": "Competing",
    "custom": "Custom status",
}

STATUS_LABELS = {
    "online": "Online",
    "idle": "Idle",
    "dnd": "Do not disturb",
    "invisible": "Invisible",
}

# Presence only controls how Discord *displays* the bot. "invisible" makes the
# bot show as offline in the member list while the gateway session stays
# connected, so it keeps answering commands, tickets and AI questions.
DEFAULT_STATUS = "invisible"


def build_activity(activity: str, text: str) -> discord.Activity:
    """Turn a stored activity entry into a discord.py activity object."""

    activity = (activity or "playing").lower()
    text = (text or "").strip() or "Florida State Roleplay"

    if activity in {"playing", "streaming"}:
        return discord.Game(name=text)
    if activity == "watching":
        return discord.Activity(type=discord.ActivityType.watching, name=text)
    if activity == "listening":
        return discord.Activity(type=discord.ActivityType.listening, name=text)
    if activity == "competing":
        return discord.Activity(type=discord.ActivityType.competing, name=text)
    if activity == "custom":
        return discord.CustomActivity(name=text)
    return discord.Game(name=text)


def resolve_status(status: str | None) -> discord.Status:
    key = (status or DEFAULT_STATUS).lower()
    return {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible,
    }.get(key, discord.Status.online)


def status_key(status: object) -> str:
    """Map a live ``discord.Status`` (or stored label) to the dashboard's key.

    Some discord.py releases report the invisible presence as ``offline``; the
    dashboard calls it ``invisible`` because the bot is connected either way.
    """

    value = str(getattr(status, "value", status) or DEFAULT_STATUS).lower()
    return "invisible" if value == "offline" else value


def describe(activity: str, text: str) -> str:
    """Human readable presence summary for the dashboard."""

    label = ACTIVITY_LABELS.get((activity or "playing").lower(), "Playing")
    return f"{label} {text}".strip()
