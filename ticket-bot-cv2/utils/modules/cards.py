"""Card layouts for the automated modules (Discord Components V2).

One spec per card is built from validated values, then rendered into a fresh
layout for every channel it is delivered to. Keeping the spec separate from the
layout lets one card be posted to the source channel and forwarded to other
servers without reusing a view that Discord has already bound to a message.

The text keeps the pack's legacy emoji mentions; :meth:`CardSpec.layout`
resolves them against the server the card is actually sent to, so the icons are
the custom white set in every guild instead of a mixed bag of Unicode emoji.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import discord
from discord import ui

from utils.modules import banners
from utils.modules import icons
from utils.modules import settings as module_settings
from utils.modules import text as text_utils

BANNER_FILENAME = "card.png"


@dataclass(slots=True)
class CardSpec:
    kind: str
    accent: int
    banner: bytes
    title: str
    subtitle: str
    description: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    box_label: str = ""
    box_text: str = ""
    footer: str = ""
    link_label: str = ""
    link_url: str = ""
    allow_mentions: bool = False

    def layout(self, guild: Any = None) -> tuple[ui.LayoutView, discord.File]:
        """A layout bound to this card, resolved for ``guild``'s emoji pack."""

        view = ui.LayoutView(timeout=None)
        container = ui.Container(accent_color=self.accent)

        gallery = ui.MediaGallery()
        gallery.add_item(media=f"attachment://{BANNER_FILENAME}")
        container.add_item(gallery)

        container.add_item(ui.TextDisplay(f"**{icons.resolve(self.title, guild)}**"))
        if self.subtitle:
            container.add_item(ui.TextDisplay(f"-# {icons.resolve(self.subtitle, guild)}"))
        container.add_item(ui.TextDisplay(icons.resolve(self.description, guild)))

        rendered_fields = [
            f"{icons.resolve(label, guild)}: {icons.resolve(value, guild)}"
            for label, value in self.fields
            if value
        ]
        if rendered_fields:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay("\n".join(rendered_fields)))

        if self.box_text:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(
                ui.TextDisplay(f"{icons.mention('folder')} **{icons.resolve(self.box_label, guild)}**")
            )
            container.add_item(ui.TextDisplay(text_utils.quote(self.box_text)))

        if self.footer:
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            container.add_item(ui.TextDisplay(f"-# {icons.resolve(self.footer, guild)}"))

        if self.link_url and self.link_label:
            container.add_item(ui.ActionRow(ui.Button(label=self.link_label, url=self.link_url)))

        view.add_item(container)
        return view, discord.File(io.BytesIO(self.banner), filename=BANNER_FILENAME)

    def allowed_mentions(self) -> discord.AllowedMentions:
        if self.allow_mentions:
            return discord.AllowedMentions(users=True, roles=False, everyone=False)
        return discord.AllowedMentions.none()

    def summary(self) -> str:
        return f"{self.title.title()}: {self.description.splitlines()[0]}"[:300]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")


def _spec(kind: str, settings: dict, label: str, *, title: str, description: str, **kwargs) -> CardSpec:
    tagline = str(settings.get("banner_tagline") or "")
    color = str(settings.get("brand_color") or module_settings.DEFAULTS["brand_color"])
    return CardSpec(
        kind=kind,
        accent=module_settings.color_int(settings),
        banner=banners.render(
            module_settings.KIND_LABELS[kind],
            tagline,
            label,
            color,
            kind,
            module_settings.banner_image(settings, kind),
        ),
        title=title,
        description=description,
        **kwargs,
    )


def build_infraction(settings: dict, label: str, data: dict) -> CardSpec:
    return _spec(
        "infraction",
        settings,
        label,
        title="INFRACTIONS",
        description=(
            f"{data['member_mention']}, you have received an infraction.\n"
            f"The {label} High Ranking Team has issued this infraction. Please review the details "
            "below and reach out to staff if you have questions."
        ),
        subtitle=label,
        fields=[
            (f"{icons.mention('user')} **User**", data["member_mention"]),
            (f"{icons.mention('punish')} **Punishment**", data["punishment"]),
            (f"{icons.mention('approved')} **Approved by**", data["approved_by"]),
            (f"{icons.mention('appeal')} **Appealable**", data["appealable"]),
        ],
        box_label="Reason",
        box_text=data["reason"],
        footer=f"Issued by {data['actor_name']} · {_stamp()}",
    )


def build_promotion(settings: dict, label: str, data: dict) -> CardSpec:
    return _spec(
        "promotion",
        settings,
        label,
        title="PROMOTIONS",
        description=(
            f"Congratulations {data['member_mention']}!\n"
            f"The {label} High Ranking Team has decided to promote you! Your work and dedication to "
            "the community has been recognized and will not be forgotten."
        ),
        subtitle=label,
        fields=[
            (f"{icons.mention('user')} **User**", data["member_mention"]),
            (f"{icons.mention('rank')} **New Rank**", data["rank"]),
            (f"{icons.mention('approved')} **Approved by**", data["approved_by"]),
            (f"{icons.mention('reason')} **Notes**", data["notes"]),
        ],
        box_label="Notes",
        box_text=data["notes"] or "No additional notes.",
        footer=f"Approved by {data['actor_name']} · {_stamp()}",
    )


def build_feedback(settings: dict, label: str, data: dict) -> CardSpec:
    return _spec(
        "feedback",
        settings,
        label,
        title="STAFF FEEDBACK",
        description=(
            f"{data['staff_mention']} received new staff feedback.\n"
            f"The {label} High Ranking Team reviews every submission below."
        ),
        subtitle=label,
        fields=[
            (f"{icons.mention('staff')} **Staff Member**", data["staff_mention"]),
            (f"{icons.mention('rating')} **Rating**", data["rating"]),
            (f"{icons.mention('user')} **Submitted by**", data["submitted_by"]),
        ],
        box_label="Feedback",
        box_text=data["feedback"],
        footer=f"Logged by {data['actor_name']} · {_stamp()}",
    )


def build_session(settings: dict, label: str, data: dict) -> CardSpec:
    return _spec(
        "session",
        settings,
        label,
        title="SESSION SHUTDOWN",
        description=(
            f"The **{data['session']}** session has ended.\n"
            "Thank you to everyone who joined — the details are below."
        ),
        subtitle=label,
        fields=[
            (f"{icons.mention('user')} **Hosted by**", data["host_mention"]),
            (f"{icons.mention('session')} **Session**", data["session"]),
            (f"{icons.mention('staff')} **Attendees**", data["attendees"]),
            (f"{icons.mention('clock')} **Duration**", data["duration"]),
            (f"{icons.mention('approved')} **Shutdown by**", data["actor_mention"]),
        ],
        box_label="Summary",
        box_text=data["summary"],
        footer=f"Closed by {data['actor_name']} · {_stamp()}",
    )


def build_welcome(settings: dict, label: str, data: dict) -> CardSpec:
    link_url = str(settings.get("dashboard_url") or "")
    return _spec(
        "welcome",
        settings,
        label,
        title="WELCOME",
        description=data["greeting"],
        subtitle=label,
        fields=[
            (f"{icons.mention('welcome')} **Member count**", data["member_count"]),
            (f"{icons.mention('clock')} **Joined**", data["joined"]),
        ],
        box_label="",
        box_text="",
        footer=f"{label} · {_stamp()}",
        link_label=data["link_label"] if link_url else "",
        link_url=link_url,
        allow_mentions=True,
    )


BUILDERS = {
    "infraction": build_infraction,
    "promotion": build_promotion,
    "feedback": build_feedback,
    "session": build_session,
    "welcome": build_welcome,
}


def build(kind: str, settings: dict, label: str, data: dict) -> CardSpec:
    builder = BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown card kind: {kind}")
    return builder(settings, label, data)
