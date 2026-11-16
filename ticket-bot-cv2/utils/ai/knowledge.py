"""Knowledge search and message filtering for ingestion.

Search is local and deterministic: it scores approved entries against the
member's question so the model always receives the most relevant policy text
instead of the whole database. Ingestion filtering decides which channel
messages are even worth sending to the model, so ordinary chatter never becomes
official server policy.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

WORD = re.compile(r"[a-z0-9]{3,}")
STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "can", "how", "what", "when", "where", "who", "why",
    "with", "have", "has", "was", "were", "this", "that", "there", "their", "from", "will", "would",
    "should", "could", "about", "into", "then", "than", "them", "they", "our", "out", "not", "but",
    "all", "any", "may", "get", "got", "does", "did", "just", "also", "his", "her", "its", "one",
}
NOISE_PREFIXES = ("lol", "lmao", "ok", "okay", "thanks", "thank you", "gg", "+1", "welcome")
MIN_CANDIDATE_CHARS = 60


def tokenize(text: str) -> set[str]:
    return {word for word in WORD.findall(str(text or "").lower()) if word not in STOPWORDS}


def score_entry(query_tokens: set[str], entry: dict[str, Any]) -> float:
    """Overlap between the question and an entry; tags count double."""

    if not query_tokens:
        return 0.0
    title_tokens = tokenize(entry.get("title", ""))
    body_tokens = tokenize(entry.get("body", ""))
    tag_tokens = tokenize(entry.get("tags", ""))
    title_hits = len(query_tokens & title_tokens) * 2.0
    tag_hits = len(query_tokens & tag_tokens) * 1.5
    body_hits = len(query_tokens & body_tokens)
    return round(title_hits + tag_hits + body_hits, 3)


def search(entries: Iterable[dict[str, Any]], query: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """Approved entries ranked for one question."""

    tokens = tokenize(query)
    scored = []
    for entry in entries:
        if str(entry.get("status") or "approved") != "approved":
            continue
        value = score_entry(tokens, entry)
        if value <= 0:
            continue
        scored.append((value, entry))
    scored.sort(key=lambda item: (-item[0], -int(item[1].get("id") or 0)))
    return [entry for _score, entry in scored[:limit]]


def prompt_block(entries: Iterable[dict[str, Any]], *, max_chars: int = 6000) -> str:
    """Compact, source-labelled knowledge text for a model prompt."""

    lines: list[str] = []
    used = 0
    for entry in entries:
        block = f"[KB-{entry.get('id')}] {entry.get('title')}\n{entry.get('body')}\n"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines)


def is_candidate(message: dict[str, Any]) -> bool:
    """Whether one channel message could carry official information.

    Bots, one-liners, reactions and chatter are skipped; only substantive
    announcements or policy text reach the model, which then still has to label
    its own output for administrator review.
    """

    content = str(message.get("content") or "").strip()
    if len(content) < MIN_CANDIDATE_CHARS:
        return False
    lowered = content.lower()
    if any(lowered.startswith(prefix) for prefix in NOISE_PREFIXES):
        return False
    if bool(message.get("author_bot")):
        return False
    words = content.split()
    if len(words) < 8:
        return False
    return True


def candidate_messages(messages: Iterable[dict[str, Any]], *, limit: int = 120) -> list[dict[str, Any]]:
    seen: set[str] = set()
    picked: list[dict[str, Any]] = []
    for message in messages:
        if not is_candidate(message):
            continue
        fingerprint = " ".join(str(message.get("content") or "").lower().split())[:400]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        picked.append(message)
        if len(picked) >= limit:
            break
    return picked


def message_to_prompt(messages: Iterable[dict[str, Any]], *, max_chars: int = 12000) -> str:
    lines: list[str] = []
    used = 0
    for message in messages:
        author = str(message.get("author_name") or "member")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        block = f"{author}: {content}\n---\n"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return "".join(lines)


def dedupe_key(title: str, body: str) -> str:
    return " ".join(f"{title} {body}".lower().split())[:200]
