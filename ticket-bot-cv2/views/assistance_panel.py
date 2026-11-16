"""Hardcoded assistance panel and ticket surfaces (Discord Components V2).

Everything the community sees is built here from :mod:`utils.assistance`, so the
panel and ticket messages look identical on every restart and no setup command
is needed. The custom IDs below are registered as persistent views in
``main.py`` so the dropdown and the ToS button keep working after a restart.
"""

from __future__ import annotations

import logging

import discord
from discord import ui

from utils import assistance
from utils.emojis import resolve_component_emoji
from utils.database import get_user_open_tickets
from utils.helpers import check_rate_limit, send_ephemeral_container

logger = logging.getLogger("discord")


def panel_emoji(slot: str, fallback: str, guild: discord.Guild | None = None) -> str:
    """Resolve a server copy of an emoji.gg asset, falling back to Unicode.

    The bot uploads the pack to every server it is in, so this returns the ID
    that server assigned. Until that copy exists every surface renders the
    Unicode glyph instead of a broken mention.
    """

    return resolve_component_emoji(slot, guild) or fallback


def guild_icon_url(guild: discord.Guild | None) -> str | None:
    return guild.icon.url if guild and guild.icon else None


def _branded_container(banner_url: str) -> ui.Container:
    """Container with the banner artwork that frames every assistance surface."""

    container = ui.Container(accent_color=assistance.GOLD_ACCENT)
    if banner_url:
        banner = ui.MediaGallery()
        banner.add_item(media=banner_url)
        container.add_item(banner)
    return container


def _footer(container: ui.Container, footer_url: str) -> None:
    container.add_item(ui.Separator())
    if footer_url:
        artwork = ui.MediaGallery()
        artwork.add_item(media=footer_url)
        container.add_item(artwork)
    else:
        container.add_item(ui.TextDisplay(assistance.brand_line()))


def _titled_section(title: str, body: str, thumbnail_url: str | None) -> ui.Item:
    text = ui.TextDisplay(f"## {title}\n{body}")
    if not thumbnail_url:
        return text
    section = ui.Section(accessory=ui.Thumbnail(media=thumbnail_url))
    section.add_item(text)
    return section


async def start_ticket_flow(interaction: discord.Interaction, category: str) -> None:
    """Validate one dropdown choice and open the confirmation step."""

    bot = interaction.client
    guild = interaction.guild
    if guild is None:
        await send_ephemeral_container(interaction, "Tickets can only be opened inside the server.")
        return

    async with bot.db.cursor() as cur:
        await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (guild.id,))
        row = await cur.fetchone()
    if row and row[0]:
        await send_ephemeral_container(
            interaction, "The ticket system is currently under maintenance. Please try again later."
        )
        return

    if await check_rate_limit(bot, guild.id, interaction.user.id):
        await send_ephemeral_container(
            interaction, "You're creating tickets too quickly. Please wait 60 seconds before trying again."
        )
        return

    async with bot.db.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
            (guild.id, interaction.user.id),
        )
        if await cur.fetchone():
            await send_ephemeral_container(interaction, "You are blacklisted from creating tickets in this server.")
            return

    open_tickets = await get_user_open_tickets(bot, guild.id, interaction.user.id)
    async with bot.db.cursor() as cur:
        await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (guild.id,))
        row = await cur.fetchone()
    ticket_limit = row[0] if row and row[0] else 3
    if open_tickets >= ticket_limit:
        await send_ephemeral_container(
            interaction,
            f"You already have {open_tickets} open tickets. Please close some before creating new ones.",
        )
        return

    from views.ticket_views import TicketConfirmationLayoutView

    confirmation = TicketConfirmationLayoutView(bot, category, guild.id, interaction.user.id)
    await interaction.response.send_message(view=confirmation, ephemeral=True)


class AssistancePanelSelect(ui.Select):
    """Persistent category dropdown for the hardcoded assistance panel."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(
            placeholder=assistance.PANEL_PLACEHOLDER,
            options=[
                discord.SelectOption(
                    label=category.label,
                    value=category.label,
                    description=category.description,
                    emoji=panel_emoji(category.emoji, category.fallback, guild),
                )
                for category in assistance.SUPPORT_CATEGORIES
            ],
            custom_id=assistance.PANEL_SELECT_ID,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await start_ticket_flow(interaction, self.values[0])
        except Exception:
            logger.exception("Assistance panel select failed for %s", interaction.user.id)
            await send_ephemeral_container(interaction, "Something went wrong opening your ticket. Please try again.")


class AssistanceTosButton(ui.Button):
    """Opens the ticket Terms of Service in an ephemeral container."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(
            label=assistance.TOS_BUTTON_LABEL,
            emoji=panel_emoji(assistance.TOS_BUTTON_EMOJI, assistance.TOS_BUTTON_FALLBACK, guild),
            style=discord.ButtonStyle.secondary,
            custom_id=assistance.TOS_BUTTON_ID,
        )

    async def callback(self, interaction: discord.Interaction):
        view = ui.LayoutView(timeout=180)
        container = ui.Container(accent_color=assistance.GOLD_ACCENT)
        container.add_item(ui.TextDisplay(f"### {assistance.TOS_TITLE}"))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(assistance.TOS_TEXT))
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)


class AssistancePanelView(discord.ui.View):
    """Persistent view registered on startup so the panel survives restarts."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.add_item(AssistancePanelSelect(guild))
        self.add_item(AssistanceTosButton(guild))


class AssistancePanelLayout(ui.LayoutView):
    """The hardcoded assistance panel posted in the support channel."""

    def __init__(self, thumbnail_url: str | None = None, guild: discord.Guild | None = None):
        super().__init__(timeout=None)

        container = _branded_container(assistance.PANEL_BANNER_URL)
        panel_icon = panel_emoji("<:Ticket_icons:1382703084815257610>", "🎫", guild)
        container.add_item(
            _titled_section(f"{panel_icon} {assistance.PANEL_TITLE}", assistance.PANEL_DESCRIPTION, thumbnail_url)
        )
        container.add_item(ui.Separator())
        container.add_item(ui.ActionRow(AssistancePanelSelect(guild)))
        container.add_item(ui.ActionRow(AssistanceTosButton(guild)))
        _footer(container, assistance.PANEL_FOOTER_URL)

        self.add_item(container)


class TicketOpenedLayout(ui.LayoutView):
    """The "New Ticket Opened" message posted inside a freshly created ticket."""

    def __init__(
        self,
        *,
        user: discord.abc.User,
        category: str,
        inquiry: str,
        additional_info: str,
        thumbnail_url: str | None = None,
        cc_role: discord.Role | None = None,
        guild: discord.Guild | None = None,
    ):
        super().__init__(timeout=None)

        container = _branded_container(assistance.TICKET_BANNER_URL)
        ticket_icon = panel_emoji("<:Ticket_icons:1382703084815257610>", "🎫", guild)
        container.add_item(
            _titled_section(f"{ticket_icon} {assistance.TICKET_OPENED_TITLE}", "", thumbnail_url)
        )
        if cc_role is not None:
            container.add_item(ui.TextDisplay(f"**CC:** {cc_role.mention}"))
        container.add_item(ui.TextDisplay(assistance.TICKET_GREETING.format(user=user.mention)))

        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"**__User Information__**\n"
                f"{panel_emoji('<:icons_Person:1382703571056853082>', '👤', guild)} **User:** {user.mention}\n"
                f"{panel_emoji('<:id_icons:1384041001114407013>', '🆔', guild)} **ID:** {user.id}"
            )
        )
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"{panel_emoji('<:clipboard1:1383857546410070117>', '📋', guild)} **__Ticket Information__**\n"
                f"➖ **Inquiry:** {inquiry}\n"
                f"➖ **Additional Info:** {additional_info or '—'}\n"
                f"➖ **Category:** {category}"
            )
        )
        _footer(container, assistance.TICKET_FOOTER_URL)

        self.add_item(container)


async def publish_assistance_panel(bot) -> bool:
    """Create or refresh the hardcoded panel in the configured channel."""

    guild_id = assistance.GUILD_ID
    if not guild_id and len(bot.guilds) == 1:
        guild_id = bot.guilds[0].id
    if not guild_id or not assistance.PANEL_CHANNEL_ID:
        logger.info(
            "Assistance panel not published yet: set ASSISTANCE_PANEL_CHANNEL_ID "
            "(and ASSISTANCE_GUILD_ID when the bot is in more than one server)."
        )
        return False

    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(assistance.PANEL_CHANNEL_ID) if guild else None
    if not isinstance(channel, discord.abc.Messageable):
        logger.warning("Assistance panel channel %s is missing or not text-based", assistance.PANEL_CHANNEL_ID)
        return False

    view = AssistancePanelLayout(guild_icon_url(guild), guild=guild)
    async with bot.db.cursor() as cursor:
        await cursor.execute("SELECT message_id FROM ticket_panels WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()

    message = None
    if row and row[0]:
        try:
            message = await channel.fetch_message(row[0])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None

    try:
        if message is not None:
            await message.edit(view=view)
        else:
            message = await channel.send(view=view)
    except discord.HTTPException:
        logger.exception("Could not publish the assistance panel in channel %s", channel.id)
        return False

    async with bot.db.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO ticket_panels (guild_id, channel_id, message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id, "
            "message_id = excluded.message_id",
            (guild_id, channel.id, message.id),
        )
        await bot.db.commit()
    logger.info("Assistance panel published in channel %s (message %s)", channel.id, message.id)
    return True
