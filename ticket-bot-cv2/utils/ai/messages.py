"""Consistent AI message icons using the server's coordinated emoji pack."""

from __future__ import annotations

from utils.emojis import resolve_component_emoji

ICONS = {
    "🤝": "<:icons_heart:1382705238619984005>",
    "✅": "<:j_icons_Correct:1382701297987485706>",
    "❌": "<:icons_Wrong:1382701332955402341>",
    "⚠️": "<:warning:1382701413284446228>",
    "🛡️": "<:shield:1382703287891136564>",
    "📋": "<:clipboard1:1383857546410070117>",
    "🔎": "<:stats_1:1382703019334045830>",
    "🔗": "<:icons_refresh:1382701477759549523>",
    "📣": "<:UA_Rocket_icons:1382701592851124254>",
    "📌": "<:Target:1382706193855942737>",
}


def render(content: str, guild=None) -> str:
    """Resolve pack icons when available; Unicode remains a safe fallback."""
    has_icon = any(symbol in content for symbol in ICONS) or "<:" in content
    if not has_icon:
        content = "📋 " + content
    for symbol, mention in ICONS.items():
        content = content.replace(symbol, resolve_component_emoji(mention, guild) or symbol)
    return content


def chunks(content: str, limit: int = 1900) -> list[str]:
    """Keep full reports instead of silently truncating them at Discord's limit."""
    result = []
    while len(content) > limit:
        end = content.rfind("\n", 0, limit)
        if end < limit // 2:
            end = content.rfind(" ", 0, limit)
        if end < limit // 2:
            end = limit
        result.append(content[:end])
        content = content[end:].lstrip("\n")
    if content:
        result.append(content)
    return result
