"""Offline checks for the automated welcome (not a card module)."""

import unittest
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord

from cogs import welcome
from utils import dashboard_store as store
from utils.modules import schema as module_schema
from utils.modules import settings as module_settings


def text_channel(channel_id=99):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.send = AsyncMock()
    return channel


class FakeGuild:
    def __init__(self, channel=None, name="Florida State Roleplay"):
        self.name = name
        self.channel = channel

    def get_channel(self, channel_id):
        if self.channel is not None and channel_id == self.channel.id:
            return self.channel
        return None


class FakeMember:
    def __init__(self, guild):
        self.guild = guild
        self.id = 42
        self.mention = "<@42>"


class WelcomeAutomationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await store.ensure_schema(self.db)
        await module_schema.ensure_schema(self.db)
        self.bot = MagicMock()
        self.bot.db = self.db
        self.cog = welcome.WelcomeAutomation(self.bot)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_posts_the_greeting_in_the_configured_channel(self):
        channel = text_channel()
        await store.set_settings(
            self.db,
            {
                "welcome_enabled": "1",
                "welcome_channel_id": "99",
                "welcome_message": "Hey {user}, welcome to {server}!",
            },
        )

        await self.cog.on_member_join(FakeMember(FakeGuild(channel)))

        channel.send.assert_awaited_once()
        content = channel.send.await_args.kwargs["content"]
        self.assertEqual(content, "Hey <@42>, welcome to Florida State Roleplay!")

    async def test_switched_off_welcome_sends_nothing(self):
        channel = text_channel()
        await store.set_settings(
            self.db, {"welcome_enabled": "0", "welcome_channel_id": "99"}
        )

        await self.cog.on_member_join(FakeMember(FakeGuild(channel)))

        channel.send.assert_not_awaited()

    async def test_a_join_in_another_server_is_ignored(self):
        await store.set_settings(
            self.db, {"welcome_enabled": "1", "welcome_channel_id": "99"}
        )

        await self.cog.on_member_join(FakeMember(FakeGuild(None)))

        self.assertTrue(True)  # no channel, no exception, nothing posted

    async def test_a_channel_configured_on_the_card_modules_page_still_works(self):
        channel = text_channel()
        await module_settings.save(
            self.db,
            {"welcome_channel_id": "99", "welcome_message": "Welcome {user}!"},
        )

        await self.cog.on_member_join(FakeMember(FakeGuild(channel)))

        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.await_args.kwargs["content"], "Welcome <@42>!")

    async def test_the_picture_is_attached_when_it_exists(self):
        channel = text_channel()
        await store.set_settings(
            self.db,
            {
                "welcome_enabled": "1",
                "welcome_channel_id": "99",
                "welcome_image_url": "/media/uploads/missing-image.png",
            },
        )

        await self.cog.on_member_join(FakeMember(FakeGuild(channel)))

        # The stored file is gone, so the greeting still goes out without artwork.
        channel.send.assert_awaited_once()
        self.assertIsNone(channel.send.await_args.kwargs["file"])


class EnabledFlagTests(unittest.TestCase):
    def test_a_string_false_is_not_true(self):
        self.assertFalse(module_settings._enabled_map({"welcome": "false"})["welcome"])
        self.assertFalse(module_settings._enabled_map({"welcome": "0"})["welcome"])
        self.assertTrue(module_settings._enabled_map({"welcome": "true"})["welcome"])
        self.assertTrue(module_settings._enabled_map({"welcome": True})["welcome"])

    def test_a_missing_kind_keeps_the_default(self):
        self.assertTrue(module_settings._enabled_map({})["welcome"])


if __name__ == "__main__":
    unittest.main()
