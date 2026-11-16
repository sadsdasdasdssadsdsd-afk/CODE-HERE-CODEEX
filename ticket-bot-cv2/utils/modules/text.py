"""Text hygiene for the card modules.

Every string that reaches a card comes from a person: a slash command, a modal
or the dashboard. It is cleaned here so it cannot ping the server, break out of
the quote block, or push a card past Discord's component limits.
"""

from __future__ import annotations

import re

MAX_LINES = 12
MENTION_PATTERN = re.compile(r"<(@[!&]?|#)(\d+)>")
BROADCAST_PATTERN = re.compile(r"@(everyone|here)\b", re.IGNORECASE)
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(value: object, limit: int, *, multiline: bool = False) -> str:
    """Strip control characters, collapse whitespace and cap the length."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_PATTERN.sub("", text)
    if not multiline:
        return " ".join(text.split())[:limit]
    lines = [" ".join(line.split()) for line in text.split("\n")]
    lines = [line for line in lines if line][:MAX_LINES]
    return "\n".join(lines)[:limit]


def neutralise_mentions(text: str) -> str:
    """Keep the words, drop the ping: free text never mentions anyone."""

    text = BROADCAST_PATTERN.sub(lambda match: f"@\u200b{match.group(1)}", text)
    # A zero-width space after "<@" keeps the ID readable but stops Discord
    # turning it into a mention pill the sender did not earn.
    return MENTION_PATTERN.sub(lambda match: f"<{match.group(1)}\u200b{match.group(2)}>", text)


def safe_text(value: object, limit: int, *, multiline: bool = False) -> str:
    return neutralise_mentions(clean(value, limit, multiline=multiline))


def quote(value: str) -> str:
    """Render text as a quote block, the darker inset box on the card."""

    lines = [line for line in str(value or "").split("\n") if line.strip()] or ["—"]
    return "\n".join(f"> {line}" for line in lines[:MAX_LINES])


def initials(label: str) -> str:
    words = [word for word in re.split(r"[^0-9A-Za-z]+", str(label or "")) if word]
    if not words:
        return "FS"
    return "".join(word[0] for word in words[:2]).upper()[:2]
