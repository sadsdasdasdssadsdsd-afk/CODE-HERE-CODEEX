import tempfile
import unittest
from pathlib import Path

from utils.emojis import (
    DEFAULT_ASSET_DIRECTORY,
    discover_assets,
    registry,
    resolve_component_emoji,
    resolve_emojis,
    sync_guild_emojis,
)


PACK_ID = "1382704281945112645"  # icons_help, part of the shipped asset pack
PANEL_MENTION = f"<:icons_help:{PACK_ID}>"


class FakeEmoji:
    def __init__(self, name, emoji_id):
        self.name = name
        self.id = emoji_id
        self.deleted = False

    async def delete(self, *, reason=None):
        self.deleted = True
        return self


class FakeGuild:
    def __init__(self, guild_id, name, emojis=None, emoji_limit=50):
        self.id = guild_id
        self.name = name
        self.emojis = list(emojis or [])
        self.emoji_limit = emoji_limit
        self.created = []

    async def create_custom_emoji(self, *, name, image, reason=None):
        if not image:
            raise AssertionError("emoji upload needs image bytes")
        emoji = FakeEmoji(name, 900000000000000000 + len(self.created))
        self.created.append(name)
        self.emojis.append(emoji)
        return emoji


class AssetPackTests(unittest.TestCase):
    def test_shipped_pack_filenames_are_valid_emoji_names(self):
        assets = discover_assets(DEFAULT_ASSET_DIRECTORY)
        self.assertGreater(len(assets), 10)
        names = [asset.name for asset in assets]
        self.assertEqual(len(set(names)), len(names), "duplicate emoji names would collide in the server")
        for asset in assets:
            self.assertTrue(2 <= len(asset.name) <= 32, asset.name)
            self.assertTrue(asset.name.replace("_", "").isalnum(), asset.name)
            self.assertGreater(asset.path.stat().st_size, 0)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        registry.clear()

    def test_pack_mention_without_a_server_copy_is_dropped(self):
        self.assertNotIn("<:", resolve_emojis(f"### {PANEL_MENTION} Bot Help"))
        self.assertIsNone(resolve_component_emoji(PANEL_MENTION))

    def test_foreign_mentions_are_left_alone(self):
        other = "<:somebody_else:123456789012345678>"
        self.assertEqual(resolve_emojis(other), other)
        self.assertEqual(resolve_component_emoji(other), other)

    def test_server_copy_is_used_with_its_guild_id(self):
        registry.install(
            42,
            {PACK_ID: {"name": "icons_help", "id": "111111111111111111"}},
            default=True,
        )
        self.assertEqual(resolve_emojis(PANEL_MENTION), "<:icons_help:111111111111111111>")
        self.assertEqual(resolve_component_emoji(PANEL_MENTION), "<:icons_help:111111111111111111>")


class DefaultStatePathTests(unittest.TestCase):
    def test_default_state_lives_beside_the_runtime_database(self):
        from utils import config as config_module
        from utils.emojis import PROJECT_ROOT, STATE_FILENAME, _state_path

        path = _state_path()
        # A relative DATABASE_PATH (the built-in default) resolves against the
        # project root, so the expectation has to do the same.
        database = Path(config_module.config.DATABASE_PATH)
        if not database.is_absolute():
            database = PROJECT_ROOT / database
        self.assertEqual(path.name, STATE_FILENAME)
        self.assertEqual(path.parent, database.parent)


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        registry.clear()
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        for name, legacy_id in (("icons_help", PACK_ID), ("ticket", "111111111111111111")):
            (root / f"{name}-{legacy_id}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
        self.pack = root
        self.state = root / "emoji_sync_state.json"

    def tearDown(self):
        self.directory.cleanup()
        registry.clear()

    async def test_uploads_missing_emojis_and_reuses_them_next_run(self):
        guild = FakeGuild(7, "Test Server", emojis=[FakeEmoji("ticket", 555555555555555555)])

        report = await sync_guild_emojis(
            FakeBot([guild]), asset_directory=self.pack, state_path=self.state
        )

        # The pre-existing copy carries artwork the pack does not know yet, so it
        # is replaced; the missing one is uploaded.
        self.assertEqual(guild.created, ["icons_help", "ticket"])
        self.assertEqual((report.created, report.updated, report.reused), (1, 1, 0))
        help_emoji = next(emoji for emoji in guild.emojis if emoji.name == "icons_help")
        self.assertEqual(resolve_emojis(PANEL_MENTION, guild), f"<:icons_help:{help_emoji.id}>")
        ticket_emoji = next(e for e in guild.emojis if e.name == "ticket" and not e.deleted)
        self.assertEqual(
            resolve_emojis("<:ticket:111111111111111111>", guild), f"<:ticket:{ticket_emoji.id}>"
        )

        again = await sync_guild_emojis(
            FakeBot([guild]), asset_directory=self.pack, state_path=self.state
        )
        self.assertEqual((again.created, again.updated, again.reused), (0, 0, 2))

    async def test_changed_artwork_replaces_the_stale_server_copy_once(self):
        stale = FakeEmoji("ticket", 777777777777777777)
        guild = FakeGuild(11, "Test Server", emojis=[stale])
        await sync_guild_emojis(FakeBot([guild]), asset_directory=self.pack, state_path=self.state)
        self.assertTrue(stale.deleted)
        current = guild.emojis[-1]

        (self.pack / "ticket-111111111111111111.png").write_bytes(b"\x89PNG\r\n\x1a\nredrawn")
        report = await sync_guild_emojis(
            FakeBot([guild]), asset_directory=self.pack, state_path=self.state
        )

        self.assertEqual(report.updated, 1)
        self.assertTrue(current.deleted)
        self.assertEqual(guild.created, ["icons_help", "ticket", "ticket"])
        self.assertEqual(resolve_emojis("<:ticket:111111111111111111>", guild), f"<:ticket:{guild.emojis[-1].id}>")

        settled = await sync_guild_emojis(
            FakeBot([guild]), asset_directory=self.pack, state_path=self.state
        )
        self.assertEqual((settled.created, settled.updated, settled.reused), (0, 0, 2))

    async def test_full_server_reports_the_missing_emoji_instead_of_failing(self):
        guild = FakeGuild(9, "Full Server", emojis=[FakeEmoji("filler", 1)], emoji_limit=1)

        report = await sync_guild_emojis(
            FakeBot([guild]), asset_directory=self.pack, state_path=self.state
        )

        self.assertEqual(report.created, 0)
        self.assertEqual(len(report.failures), 2)
        self.assertIsNone(resolve_component_emoji(PANEL_MENTION, guild))


class FakeBot:
    def __init__(self, guilds):
        self.guilds = guilds


if __name__ == "__main__":
    unittest.main()
