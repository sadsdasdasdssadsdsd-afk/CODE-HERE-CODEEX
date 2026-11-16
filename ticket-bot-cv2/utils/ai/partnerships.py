"""Ticket-driven partnership copy and deterministic input handling."""

from __future__ import annotations

import re
from typing import Any

from utils.ai import engine

DONE = re.compile(r"(?:done|verified|verification complete|verification completed|all done|finished)[.!\s]*", re.I)
GATE_CHANNEL = re.compile(r"(?:verif|captcha|welcome|rules|start.here|onboarding)", re.I)
LINK = re.compile(r"https?://\S+", re.I)
WORD = re.compile(r"[a-z0-9]{4,}")
# Filler words carry no fingerprint: an advertisement is recognised by its own
# wording, not by the greeting every advertisement contains.
STOPWORDS = frozenset(
    "about after also and are best bring come could discord does every from have here into "
    "join just like make many more most much must nice only open other over please our partner "
    "partners partnership people really server servers some such than that their them then "
    "there these they this those very want welcome were what when where which while will with "
    "your".split()
)
MIN_REFERENCE_WORDS = 8
MIN_FALLBACK_WORDS = 10

# How long the review waits for the applicant to post our advertisement, and how
# much wording of it has to appear for the post to count.
AD_CHECK_INTERVAL_SECONDS = 300
AD_REMIND_EVERY = 6
AD_MONITOR_INTERVAL_SECONDS = 3600


def is_done(text: str) -> bool:
    return bool(DONE.fullmatch(text.strip()))


def advertisement(text: str) -> str:
    """A bare invitation or conversational greeting is not an advertisement."""
    text = text.strip()
    remainder = engine.INVITE_LINK.sub("", text)
    remainder = re.sub(r"https?://|[<>*_`#\s:/.!-]", "", remainder)
    return text[:4000] if len(remainder) >= 20 and not is_done(text) else ""


def fingerprint(text: str) -> set[str]:
    """The distinctive words of an advertisement, ignoring links, emoji and filler."""

    return {word for word in WORD.findall(LINK.sub(" ", text or "").casefold()) if word not in STOPWORDS}


def posted_ad(reference: str, candidate: str, marker: str = "") -> bool:
    """True when the candidate message is the applicant posting our advertisement.

    With a reference advertisement fetched from the home server, most of its
    distinctive wording has to reappear. Without one, our server name plus real
    advertisement copy is required, so a bare greeting never passes.
    """

    words = fingerprint(reference)
    if len(words) >= MIN_REFERENCE_WORDS:
        overlap = words & fingerprint(candidate)
        return len(overlap) >= max(MIN_REFERENCE_WORDS, round(len(words) * 0.5))
    marker = (marker or "").casefold().strip()
    return (
        bool(marker)
        and marker in (candidate or "").casefold()
        and len(fingerprint(candidate)) >= MIN_FALLBACK_WORDS
    )


def install_url(application_id: int, guild_id: int) -> str:
    # View Channels + Send Messages + Read Message History; no administrator permission.
    return (
        f"https://discord.com/oauth2/authorize?client_id={application_id}"
        f"&scope=bot&permissions=68608&guild_id={guild_id}&disable_guild_select=true"
    )


def report(record: dict[str, Any], result: dict[str, Any], recommendation: str) -> str:
    lines = [f"### 📋 Partnership report — {record.get('server_name') or record.get('joined_guild_name') or 'Applicant server'}"]
    for item in result["requirements"]:
        mark = "✅" if item["passed"] is True else "❌" if item["passed"] is False else "⚠️"
        lines.append(f"{mark} **{item['label']}:** {item['actual']}")
    summary = (result.get("findings") or {}).get("summary")
    if summary:
        lines.append(f"\n**Review:** {str(summary)[:700]}")
    lines.append(f"\n{recommendation}")
    if result["eligibility"] == "eligible":
        lines.append("📣 Approved automatically; preparing your advertisement for the partnerships channel.")
    elif result["eligibility"] == "manual_review":
        lines.append("🛡️ Nothing will be published until staff resolve the checks that could not be verified.")
    else:
        lines.append("📌 The requirements were not met, so no advertisement will be published.")
    return "\n".join(lines)
