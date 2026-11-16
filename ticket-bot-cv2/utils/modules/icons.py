"""Custom emoji used by the card modules.

Every icon in the card text is one of the white glyphs in ``emojis/white`` that
:mod:`utils.emojis` uploads into each server, so a card never falls back to a
mismatched set of colourful Unicode emoji. A card is built once and mirrored
into other servers, whose copies of the pack have different IDs, so the text
keeps the *legacy* mention and is resolved per guild when the card is laid out.
When a server has no copy yet the Unicode glyph in ``fallback`` is used instead
of a broken emoji.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from utils.emojis import MENTION_RE, registry


@dataclass(frozen=True, slots=True)
class Icon:
    """One pack glyph: the PNG name, its legacy ID and a Unicode fallback."""

    name: str
    legacy_id: str
    fallback: str

    @property
    def mention(self) -> str:
        return f"<:{self.name}:{self.legacy_id}>"


ICONS: dict[str, Icon] = {
    "user": Icon("icons_Person", "1382703571056853082", "👤"),
    "staff": Icon("people_icons", "1384040549937451068", "👥"),
    "punish": Icon("gavel", "1390000000000000001", "⚖️"),
    "rank": Icon("trending_up", "1390000000000000002", "📈"),
    "rating": Icon("reviews", "1390000000000000003", "⭐"),
    "session": Icon("sports_esports", "1390000000000000004", "🎮"),
    "welcome": Icon("waving_hand", "1390000000000000005", "👋"),
    "approved": Icon("verified", "1390000000000000006", "✅"),
    "pending": Icon("hourglass", "1390000000000000007", "⏳"),
    "ad": Icon("campaign", "1390000000000000008", "📣"),
    "partnership": Icon("handshake", "1390000000000000009", "🤝"),
    "link": Icon("link", "1390000000000000010", "🔗"),
    "reason": Icon("clipboard1", "1383857546410070117", "📝"),
    "appeal": Icon("task_alt", "1390000000000000012", "✔️"),
    "clock": Icon("icons_clock", "1382701751206936697", "🕒"),
    "warning": Icon("warning", "1382701413284446228", "⚠️"),
    "folder": Icon("disk_icons", "1384042698192715899", "📂"),
}

FALLBACKS = {icon.legacy_id: icon.fallback for icon in ICONS.values()}


def mention(name: str) -> str:
    """The pack mention for one icon, resolved later per server."""

    try:
        return ICONS[name].mention
    except KeyError:  # pragma: no cover - a typo in a card layout
        raise KeyError(f"Unknown module icon: {name}") from None


def resolve(value: str, guild: Any = None) -> str:
    """Swap pack mentions for ``guild``'s copies, else the Unicode fallback."""

    if not isinstance(value, str) or "<:" not in value:
        return value
    mapping = registry.mapping(guild)

    def replace(match: "re.Match[str]") -> str:
        record = mapping.get(match.group("id"))
        if record:
            return f"<:{record['name']}:{record['id']}>"
        return FALLBACKS.get(match.group("id"), match.group(0))

    return MENTION_RE.sub(replace, value)
