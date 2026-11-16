"""Automated welcome: greets every new member in the configured channel.

The welcome is its own automation, not one of the card modules. It posts the
owner's welcome picture (Configuration -> Banners and images -> Welcome picture)
together with the greeting text, and it is switched on or off by its own
``welcome_enabled`` setting, which the Configuration page stores in
``dashboard_settings``.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import discord
from discord.ext import commands

from utils import dashboard_store as store
from utils.modules import settings as module_settings

logger = logging.getLogger(__name__)

DEFAULT_MESSAGE = "Welcome {user} to {server}! We hope you enjoy your stay with us."
MAX_MESSAGE = 320


def _channel_for(guild: discord.Guild, raw: str):
    """The configured welcome channel, but only in the guild it belongs to."""

    text = str(raw or "").strip()
    if not text.isdigit():
        return None
    channel = guild.get_channel(int(text))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None
    return channel


def _picture(settings: dict) -> discord.File | None:
    """The uploaded welcome picture, if it is still on disk."""

    name = Path(str(settings.get("welcome_image_url") or "")).name
    if not name:
        return None
    from utils import config as config_module

    database = Path(config_module.config.DATABASE_PATH)
    if not database.is_absolute():
        database = Path(__file__).resolve().parents[1] / database
    try:
        data = (database.parent / "uploads" / name).read_bytes()
    except OSError:
        return None
    return discord.File(io.BytesIO(data), filename=name)


class WelcomeAutomation(commands.Cog):
    """Posts the welcome picture and greeting when somebody joins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        if guild is None:
            return
        settings = await store.get_settings(self.bot.db)
        if str(settings.get("welcome_enabled", "1")) == "0":
            return
        channel_id = settings.get("welcome_channel_id")
        message = settings.get("welcome_message")
        if not channel_id or not message:
            # An install that configured the greeting on the card modules page keeps
            # working after the move: the Configuration values win once they are set.
            legacy = await module_settings.load(self.bot.db)
            channel_id = channel_id or legacy.get("welcome_channel_id")
            message = message or legacy.get("welcome_message")
        # A join in any other server (an applicant server during a partnership
        # review, for example) has no welcome channel here: skip it silently.
        channel = _channel_for(guild, channel_id)
        if channel is None:
            return
        template = str(message or DEFAULT_MESSAGE)[:MAX_MESSAGE]
        greeting = template.replace("{user}", member.mention).replace("{server}", guild.name)
        try:
            await channel.send(
                content=greeting,
                file=_picture(settings),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.Forbidden:
            logger.warning(
                "Welcome skipped in channel %s: the bot cannot send messages or attach files there",
                channel.id,
            )
        except discord.HTTPException:
            logger.exception("Welcome message failed in channel %s", channel.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeAutomation(bot))
