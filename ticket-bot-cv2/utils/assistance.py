"""Hardcoded assistance content for the Border State Roleplay community.

The bot serves a single private community, so the panel, its categories and the
ticket surfaces carry fixed copy instead of an in-Discord setup flow. Change a
value here (or one of the banner variables below) and restart the bot: the panel
is republished with the new content on startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

BRAND_NAME = "Border State Roleplay"
BRAND_TAGLINE = "WHERE THE FUN STARTS"

PANEL_TITLE = "Assistance System"
PANEL_DESCRIPTION = (
    "Welcome to our **Assistance Channel**. Here you may ask for assistance by choosing a type of "
    "topic in the Dropdown Menu below. You can also find below our Tickets Terms Of Service so you "
    "are aware of what Terms we have for creating a ticket."
)
PANEL_PLACEHOLDER = "Select the type of support need..."
PANEL_SELECT_ID = "assistance_panel_category"

TOS_BUTTON_LABEL = "Ticket ToS"
TOS_BUTTON_EMOJI = "🎫"
TOS_BUTTON_ID = "assistance_ticket_tos"
TOS_TITLE = "Ticket Terms Of Service"
TOS_TEXT = (
    "**1.** Only open a ticket when you genuinely need assistance.\n"
    "**2.** Do not open tickets to troll, spam or waste staff time.\n"
    "**3.** Give honest information; false reports are punished.\n"
    "**4.** Respect the staff member handling your ticket at all times.\n"
    "**5.** Do not share the contents of your ticket outside the server.\n"
    "**6.** Punishments are at staff discretion and are final.\n"
    "**7.** Opening a ticket to argue a punishment results in a blacklist."
)

TICKET_OPENED_TITLE = "New Ticket Opened"
TICKET_GREETING = (
    "Hello, {user}. Thank you for contacting our support team. Assistance with your inquiry will "
    "arrive shortly. Thanks for your patience."
)

GOLD_ACCENT = 0xD9A04A


def _env_id(name: str) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else 0


def _env_url(name: str) -> str:
    return os.getenv(name, "").strip()


# Server wiring. Values saved in the Florida State Dashboard override these
# environment defaults; ``refresh_from_settings`` rebuilds them at runtime, so a
# value cleared in the dashboard really goes back to the environment default.
_ENV_ID_DEFAULTS = {
    "guild_id": _env_id("ASSISTANCE_GUILD_ID"),
    "panel_channel_id": _env_id("ASSISTANCE_PANEL_CHANNEL_ID"),
    "ticket_category_id": _env_id("ASSISTANCE_TICKET_CATEGORY_ID"),
    "support_role_id": _env_id("ASSISTANCE_SUPPORT_ROLE_ID"),
    "log_channel_id": _env_id("ASSISTANCE_LOG_CHANNEL_ID"),
    "staff_channel_id": _env_id("ASSISTANCE_STAFF_CHANNEL_ID"),
    "transcript_channel_id": _env_id("ASSISTANCE_TRANSCRIPT_CHANNEL_ID"),
}

_ENV_URL_DEFAULTS = {
    "public_base_url": _env_url("ASSISTANCE_PUBLIC_BASE_URL"),
    "panel_banner_url": _env_url("ASSISTANCE_PANEL_BANNER_URL"),
    "panel_footer_url": _env_url("ASSISTANCE_PANEL_FOOTER_URL"),
    "ticket_banner_url": _env_url("ASSISTANCE_TICKET_BANNER_URL"),
    "ticket_footer_url": _env_url("ASSISTANCE_TICKET_FOOTER_URL"),
}

GUILD_ID = _ENV_ID_DEFAULTS["guild_id"]
PANEL_CHANNEL_ID = _ENV_ID_DEFAULTS["panel_channel_id"]
TICKET_CATEGORY_ID = _ENV_ID_DEFAULTS["ticket_category_id"]
SUPPORT_ROLE_ID = _ENV_ID_DEFAULTS["support_role_id"]
LOG_CHANNEL_ID = _ENV_ID_DEFAULTS["log_channel_id"]
STAFF_CHANNEL_ID = _ENV_ID_DEFAULTS["staff_channel_id"]
TRANSCRIPT_CHANNEL_ID = _ENV_ID_DEFAULTS["transcript_channel_id"]
ADMIN_ROLE_IDS: tuple[int, ...] = ()
MODERATOR_ROLE_IDS: tuple[int, ...] = ()

# Fixed chain-of-command roles. The dashboard resolves these names in the server,
# so staff role IDs do not need to be copied by hand. Ticket access is granted to
# these administrator and management roles; primary and additional support are N/A.
ADMIN_ROLE_NAMES = ("Founder", "Co-Founder", "Assistant Founder", "Board of Directors")
MODERATOR_ROLE_NAMES = ("Lead Management", "Senior Management", "Management Team", "Junior Management")
SUPPORT_ROLE_NAMES: tuple[str, ...] = ()


def role_key(name: str) -> str:
    """Compare the final role-name segment, ignoring emoji and punctuation."""

    final_segment = str(name).split("|")[-1].casefold()
    return "".join(char for char in final_segment if char.isalnum())


def fixed_roles(roles: list[dict], names: tuple[str, ...]) -> list[dict]:
    """The server roles whose name matches one of the fixed ``names``."""

    wanted = {role_key(name) for name in names}
    return [
        {"id": str(role["id"]), "name": role["name"]}
        for role in roles
        if role_key(role.get("name", "")) in wanted
    ]

# Banner artwork, supplied as plain image URLs (or dashboard upload paths).
PUBLIC_BASE_URL = _ENV_URL_DEFAULTS["public_base_url"]
PANEL_BANNER_URL = _ENV_URL_DEFAULTS["panel_banner_url"]
PANEL_FOOTER_URL = _ENV_URL_DEFAULTS["panel_footer_url"]
TICKET_BANNER_URL = _ENV_URL_DEFAULTS["ticket_banner_url"]
TICKET_FOOTER_URL = _ENV_URL_DEFAULTS["ticket_footer_url"]


def _absolute_url(url: str, base: str) -> str:
    """Discord can only fetch absolute image URLs; dashboard uploads are relative."""

    url = (url or "").strip()
    if url.startswith("/") and base:
        return base.rstrip("/") + url
    return url


def _setting_id(settings: dict, key: str, fallback: int) -> int:
    raw = str(settings.get(key) or "").strip()
    return int(raw) if raw.isdigit() else fallback


def refresh_from_settings(settings: dict | None) -> None:
    """Apply dashboard configuration to this module's runtime wiring."""

    global GUILD_ID, PANEL_CHANNEL_ID, TICKET_CATEGORY_ID, SUPPORT_ROLE_ID, LOG_CHANNEL_ID
    global STAFF_CHANNEL_ID, TRANSCRIPT_CHANNEL_ID, ADMIN_ROLE_IDS, MODERATOR_ROLE_IDS
    global PUBLIC_BASE_URL, PANEL_BANNER_URL, PANEL_FOOTER_URL, TICKET_BANNER_URL, TICKET_FOOTER_URL

    settings = settings or {}
    PUBLIC_BASE_URL = str(settings.get("public_base_url") or _ENV_URL_DEFAULTS["public_base_url"]).strip()
    GUILD_ID = _setting_id(settings, "guild_id", _ENV_ID_DEFAULTS["guild_id"])
    PANEL_CHANNEL_ID = _setting_id(settings, "panel_channel_id", _ENV_ID_DEFAULTS["panel_channel_id"])
    TICKET_CATEGORY_ID = _setting_id(settings, "ticket_category_id", _ENV_ID_DEFAULTS["ticket_category_id"])
    SUPPORT_ROLE_ID = _setting_id(settings, "support_role_id", _ENV_ID_DEFAULTS["support_role_id"])
    LOG_CHANNEL_ID = _setting_id(settings, "log_channel_id", _ENV_ID_DEFAULTS["log_channel_id"])
    STAFF_CHANNEL_ID = _setting_id(settings, "staff_channel_id", _ENV_ID_DEFAULTS["staff_channel_id"])
    TRANSCRIPT_CHANNEL_ID = _setting_id(
        settings, "transcript_channel_id", _ENV_ID_DEFAULTS["transcript_channel_id"]
    )
    ADMIN_ROLE_IDS = tuple(_id_list(settings.get("admin_role_ids")))
    MODERATOR_ROLE_IDS = tuple(_id_list(settings.get("moderator_role_ids")))

    for key in ("panel_banner_url", "panel_footer_url", "ticket_banner_url", "ticket_footer_url"):
        value = str(settings.get(key) or _ENV_URL_DEFAULTS[key]).strip()
        globals()[key.upper()] = _absolute_url(value, PUBLIC_BASE_URL)


def _id_list(raw) -> list[int]:
    import json

    if not raw:
        return []
    try:
        values = json.loads(raw)
    except ValueError:
        return []
    return [int(value) for value in values if str(value).isdigit()] if isinstance(values, list) else []


@dataclass(frozen=True, slots=True)
class SupportCategory:
    """One hardcoded dropdown choice on the assistance panel.

    ``emoji`` is an application-emoji slot supplied by the emoji.gg asset pack in
    ``emojis/white``; ``fallback`` is the Unicode glyph used when that custom
    emoji has not been uploaded to the application yet. ``channel_id`` is the
    Discord category channel the ticket is created under, so each choice lands
    in its own section of the server without any dashboard configuration.
    """

    label: str
    emoji: str
    fallback: str
    description: str
    channel_id: int = 0


TOS_BUTTON_FALLBACK = "🎫"

SUPPORT_CATEGORIES: tuple[SupportCategory, ...] = (
    SupportCategory(
        "General Support",
        "<:icons_help:1382704281945112645>",
        "✉️",
        "Questions and general assistance",
        channel_id=1563801523048161319,
    ),
    SupportCategory(
        "Management Support",
        "<:people_icons:1384040549937451068>",
        "📋",
        "Management and staff related requests",
        channel_id=1563801523228778497,
    ),
    SupportCategory(
        "Partnership Support",
        "<:icons_heart:1382705238619984005>",
        "🤝",
        "Partnership requests",
        channel_id=1563801523228778500,
    ),
)

CATEGORY_LABELS = tuple(category.label for category in SUPPORT_CATEGORIES)


def category_channel_id(label: str) -> int:
    """The hardcoded Discord category channel for one dropdown label (0 if unknown)."""

    wanted = str(label or "").strip().casefold()
    for category in SUPPORT_CATEGORIES:
        if category.label.casefold() == wanted:
            return category.channel_id
    return 0


def brand_line() -> str:
    """Bottom decoration used when no footer artwork is configured."""

    return f"-# ✦ {BRAND_TAGLINE} ✦"
