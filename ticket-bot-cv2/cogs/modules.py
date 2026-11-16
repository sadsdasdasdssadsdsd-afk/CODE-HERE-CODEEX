"""Automated high-rank cards: infractions, promotions, feedback, sessions, welcome.

Every card is built from validated values by :mod:`utils.modules.service`, gets
its banner artwork rendered locally, and is mirrored into the forwarding
channels configured in the dashboard.

Commands are grouped per module so they read like the action they perform in
Discord's command picker: ``/infraction issue``, ``/promotion issue``,
``/feedback submit``, ``/session end``, ``/welcome post`` and ``/cards preview``.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.modules import cards
from utils.modules import service
from utils.modules import settings as module_settings
from utils.modules import text as text_utils

logger = logging.getLogger("discord")


# --------------------------------------------------------------------------- publishing
async def _publish(bot: commands.Bot, interaction: discord.Interaction, kind: str, **payload: Any) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Cards can only be published inside a server.", ephemeral=True)
        return
    channel = payload.pop("channel", None) or interaction.channel
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        result = await service.issue(bot, guild, interaction.user, kind, channel=channel, **payload)
    except service.CardError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except Exception:
        logger.exception("Publishing the %s card failed", kind)
        await interaction.followup.send("Something went wrong while publishing that card.", ephemeral=True)
        return

    label = module_settings.KIND_LABELS[kind]
    note = f"**{label}** card published in {channel.mention}."
    if result["forwarded"]:
        note += f"\nForwarded to {len(result['forwarded'])} channel(s)."
    note += service.failure_note(result["failures"])
    await interaction.followup.send(note, ephemeral=True)


def _sample_values(
    guild: discord.Guild, user: discord.Member, settings: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """One realistic sample per card kind, built from live server values."""

    role = next((item for item in guild.roles if not item.is_default() and not item.managed), None)
    fallback = role.mention if role else "Management"
    template = str(settings.get("welcome_message") or module_settings.DEFAULT_WELCOME)
    return [
        (
            "infraction",
            {
                "member_mention": user.mention,
                "punishment": fallback,
                "approved_by": user.mention,
                "appealable": "Yes",
                "reason": "Sample reason — this preview is private to you.",
                "actor_name": user.display_name,
            },
        ),
        (
            "promotion",
            {
                "member_mention": user.mention,
                "rank": fallback,
                "approved_by": user.mention,
                "notes": "Sample notes for the promotion card.",
                "actor_name": user.display_name,
            },
        ),
        (
            "feedback",
            {
                "staff_mention": user.mention,
                "rating": "★★★★★ (5/5)",
                "submitted_by": user.mention,
                "feedback": "Sample staff feedback shown in the quote box.",
                "actor_name": user.display_name,
            },
        ),
        (
            "session",
            {
                "host_mention": user.mention,
                "session": "Sample Session",
                "attendees": "12 members",
                "duration": "48 minutes",
                "actor_mention": user.mention,
                "summary": "Sample shutdown summary.",
                "actor_name": user.display_name,
            },
        ),
        (
            "welcome",
            {
                "greeting": text_utils.clean(
                    template.replace("{user}", user.mention).replace("{server}", guild.name),
                    320,
                    multiline=True,
                ),
                "member_count": f"{guild.member_count:,}" if guild.member_count else "—",
                "joined": "just now",
                "link_label": f"{guild.member_count:,} Dashboard" if guild.member_count else "Dashboard",
            },
        ),
    ]


async def _preview(bot: commands.Bot, interaction: discord.Interaction) -> None:
    """Send one private sample of every card, built from the live settings."""

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Open this inside a server to preview the cards.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    settings = await module_settings.load(bot.db)
    try:
        service.require_staff(interaction.user, settings)
    except service.CardError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return

    label = str(settings.get("server_label") or guild.name)
    sent = 0
    for kind, data in _sample_values(guild, interaction.user, settings):
        try:
            spec = cards.build(kind, settings, label, data)
            view, file = spec.layout(guild)
        except Exception:
            logger.exception("Building the %s preview failed", kind)
            continue
        await interaction.followup.send(view=view, file=file, ephemeral=True)
        sent += 1
    await interaction.followup.send(f"Those {sent} cards are the live templates.", ephemeral=True)


# --------------------------------------------------------------------------- commands
def build_groups(bot: commands.Bot) -> list[app_commands.Group]:
    """One group per module, so every action is its own command."""

    infractions = app_commands.Group(
        name="infraction", description="Issue infraction cards", guild_only=True
    )
    promotions = app_commands.Group(
        name="promotion", description="Publish promotion cards", guild_only=True
    )
    feedback = app_commands.Group(
        name="feedback", description="Submit staff feedback cards", guild_only=True
    )
    sessions = app_commands.Group(
        name="session", description="Close a hosted session with a summary card", guild_only=True
    )
    welcomes = app_commands.Group(
        name="welcome", description="Post welcome cards", guild_only=True
    )
    cards_group = app_commands.Group(
        name="cards", description="Preview the automated card templates", guild_only=True
    )

    @infractions.command(name="issue", description="Issue an infraction card to a member.")
    @app_commands.describe(
        member="Member who received the infraction",
        punishment="Punishment role that was applied",
        reason="Reason shown in the card",
        approved_by="Staff member who approved it (administrators only)",
        appealable="Whether the member may appeal",
        channel="Channel to publish in (defaults to here)",
    )
    async def infraction_issue(
        interaction: discord.Interaction,
        member: discord.Member,
        punishment: discord.Role,
        reason: app_commands.Range[str, 3, 900],
        approved_by: discord.Member | None = None,
        appealable: bool = True,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await _publish(
            bot,
            interaction,
            "infraction",
            member=member,
            punishment=punishment,
            reason=reason,
            approved_by=approved_by,
            appealable=appealable,
            channel=channel,
        )

    @promotions.command(name="issue", description="Publish a promotion card.")
    @app_commands.describe(
        member="Member who was promoted",
        rank="New rank role",
        notes="Short note for the card",
        approved_by="Staff member who approved it (administrators only)",
        channel="Channel to publish in (defaults to here)",
    )
    async def promotion_issue(
        interaction: discord.Interaction,
        member: discord.Member,
        rank: discord.Role,
        notes: app_commands.Range[str, 2, 900] | None = None,
        approved_by: discord.Member | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await _publish(
            bot,
            interaction,
            "promotion",
            member=member,
            rank=rank,
            notes=notes,
            approved_by=approved_by,
            channel=channel,
        )

    @feedback.command(name="submit", description="Publish a staff feedback card.")
    @app_commands.describe(
        staff="Staff member the feedback is about",
        rating="Star rating from 1 to 5",
        feedback="The feedback text",
        channel="Channel to publish in (defaults to here)",
    )
    async def feedback_submit(
        interaction: discord.Interaction,
        staff: discord.Member,
        rating: app_commands.Range[int, 1, 5],
        feedback: app_commands.Range[str, 3, 900],
        channel: discord.TextChannel | None = None,
    ) -> None:
        await _publish(
            bot,
            interaction,
            "feedback",
            staff=staff,
            rating=rating,
            feedback=feedback,
            channel=channel,
        )

    @sessions.command(name="end", description="Publish a session shutdown card.")
    @app_commands.describe(
        host="Member who hosted the session",
        session="Session name",
        summary="What happened during the session",
        attendees="Attendee note (optional)",
        duration="How long the session ran (optional)",
        channel="Channel to publish in (defaults to here)",
    )
    async def session_end(
        interaction: discord.Interaction,
        host: discord.Member,
        session: app_commands.Range[str, 2, 200],
        summary: app_commands.Range[str, 3, 900],
        attendees: app_commands.Range[str, 1, 200] | None = None,
        duration: app_commands.Range[str, 1, 200] | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await _publish(
            bot,
            interaction,
            "session",
            host=host,
            session=session,
            summary=summary,
            attendees=attendees,
            duration=duration,
            channel=channel,
        )

    @welcomes.command(name="post", description="Publish a welcome card for a member.")
    @app_commands.describe(
        member="Member to welcome",
        channel="Channel to publish in (defaults to the configured welcome channel)",
    )
    async def welcome_post(
        interaction: discord.Interaction,
        member: discord.Member,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await _publish(bot, interaction, "welcome", member=member, channel=channel)

    @cards_group.command(name="preview", description="Preview every card privately.")
    async def cards_preview(interaction: discord.Interaction) -> None:
        await _preview(bot, interaction)

    return [infractions, promotions, feedback, sessions, welcomes, cards_group]


class ModuleCards(commands.Cog):
    """Registers the card commands and the automatic welcome."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.groups = build_groups(bot)

    async def cog_load(self) -> None:
        for group in self.groups:
            self.bot.tree.add_command(group)

    async def cog_unload(self) -> None:
        for group in self.groups:
            self.bot.tree.remove_command(group.name, type=discord.AppCommandType.chat_input)

    # The automatic join greeting is its own automation (cogs/welcome.py, configured
    # on the dashboard's Configuration page). This cog only publishes cards on
    # demand, so a join never depends on the card modules being enabled.


async def setup(bot: commands.Bot):
    await bot.add_cog(ModuleCards(bot))
