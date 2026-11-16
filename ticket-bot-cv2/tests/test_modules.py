"""Offline checks for the automated card modules."""

import io
import unittest
from types import SimpleNamespace

import aiosqlite
from PIL import Image

from utils import emojis
from utils.modules import banners, cards, icons, schema, service
from utils.modules import settings as module_settings
from utils.modules import text as text_utils


def settings(**overrides):
    values = {key: module_settings.decode(key, raw) for key, raw in module_settings.DEFAULTS.items()}
    values.update(overrides)
    return values


class TextTests(unittest.TestCase):
    def test_clean_collapses_whitespace_and_caps_length(self):
        self.assertEqual(text_utils.clean("  mass\n\tinactive  ", 40), "mass inactive")
        self.assertEqual(len(text_utils.clean("x" * 200, 20)), 20)

    def test_control_characters_are_stripped(self):
        self.assertEqual(text_utils.clean("a\x00b\x1fc", 20), "abc")

    def test_mentions_are_neutralised(self):
        cleaned = text_utils.safe_text("@everyone <@123456789012345678> <@&987>", 200)
        self.assertNotIn("<@123456789012345678>", cleaned)
        self.assertNotIn("@everyone", cleaned)
        self.assertIn("\u200b", cleaned)

    def test_quote_renders_every_line_as_a_block(self):
        self.assertEqual(text_utils.quote("line one\nline two"), "> line one\n> line two")
        self.assertEqual(text_utils.quote("   "), "> —")

    def test_initials_ignore_decoration(self):
        self.assertEqual(text_utils.initials("Florida State Roleplay"), "FS")
        self.assertEqual(text_utils.initials("🤝・partnerships"), "P")


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await schema.ensure_schema(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_defaults_are_complete(self):
        loaded = await module_settings.load(self.db)
        self.assertEqual(loaded["brand_color"], "#FCC200")
        self.assertEqual(set(loaded["enabled"]), set(module_settings.KINDS))
        self.assertTrue(all(loaded["enabled"].values()))

    async def test_save_normalises_every_value(self):
        loaded = await module_settings.save(
            self.db,
            {
                "banner_tagline": "  Cali   Life ",
                "brand_color": "fcc200",
                "dashboard_url": "javascript:alert(1)",
                "welcome_channel_id": "1563801520766717987",
                "forward_channel_ids": ["123", "123", "abc", 456],
                "staff_role_ids": [str(value) for value in range(30)],
                "enabled": {"infraction": False},
                "unknown_key": "ignored",
            },
        )
        self.assertEqual(loaded["banner_tagline"], "Cali Life")
        self.assertEqual(loaded["brand_color"], "#FCC200")
        self.assertEqual(loaded["dashboard_url"], "")
        self.assertEqual(loaded["welcome_channel_id"], 1563801520766717987)
        self.assertEqual(loaded["forward_channel_ids"], [123, 456])
        self.assertEqual(len(loaded["staff_role_ids"]), module_settings.MAX_STAFF_ROLES)
        self.assertFalse(loaded["enabled"]["infraction"])
        self.assertTrue(loaded["enabled"]["promotion"])

    async def test_bad_colour_falls_back(self):
        loaded = await module_settings.save(self.db, {"brand_color": "orange"})
        self.assertEqual(loaded["brand_color"], "#FCC200")

    async def test_banner_uploads_are_stored_as_bare_filenames(self):
        loaded = await module_settings.save(
            self.db,
            {
                "banner_image_welcome": "/media/uploads/banner_welcome-ab12cd34.png",
                "banner_image_promotion": "../../etc/passwd",
            },
        )
        self.assertEqual(loaded["banner_image_welcome"], "banner_welcome-ab12cd34.png")
        self.assertEqual(loaded["banner_image_promotion"], "")

    async def test_public_view_lists_every_kind(self):
        loaded = await module_settings.load(self.db)
        view = module_settings.public_view(loaded)
        self.assertEqual([item["kind"] for item in view["kinds"]], list(module_settings.KINDS))
        self.assertTrue(all(item["label"] for item in view["kinds"]))


class BannerTests(unittest.TestCase):
    def test_banner_is_a_wide_png(self):
        png = banners.render("Infraction", "Experience Cali Like Never Before", "Florida State", "#FCC200")
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (banners.WIDTH, banners.HEIGHT))
            self.assertEqual(image.format, "PNG")

    def test_long_titles_still_render(self):
        png = banners.render("Session Shutdown " * 6, "", "", "#123456")
        self.assertGreater(len(png), 1000)

    def test_every_card_kind_has_its_own_artwork(self):
        renders = {
            kind: banners.render("Title", "Tagline", "Florida State", "#FCC200", kind)
            for kind in module_settings.KINDS
        }
        self.assertEqual(len(set(renders.values())), len(module_settings.KINDS))
        with Image.open(io.BytesIO(renders["infraction"])) as image:
            self.assertEqual(image.size, (banners.WIDTH, banners.HEIGHT))

    def test_uploaded_banner_is_cropped_to_the_banner_size(self):
        source = io.BytesIO()
        Image.new("RGB", (400, 400), (20, 90, 160)).save(source, format="PNG")
        png = banners.render("Title", "Tagline", "Server", "#FCC200", "welcome", source.getvalue())
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (banners.WIDTH, banners.HEIGHT))


class IconTests(unittest.TestCase):
    def test_every_icon_falls_back_to_unicode_without_a_server_copy(self):
        for name in icons.ICONS:
            with self.subTest(icon=name):
                self.assertTrue(icons.resolve(icons.mention(name)).strip())
                self.assertNotIn("<:", icons.resolve(icons.mention(name)))

    def test_server_copy_replaces_the_legacy_mention(self):
        emojis.install_guild_emojis(4242, {"1390000000000000001": {"name": "gavel", "id": "999"}})
        resolved = icons.resolve(icons.mention("punish"), SimpleNamespace(id=4242))
        self.assertEqual(resolved, "<:gavel:999>")


class CardTests(unittest.TestCase):
    def test_infraction_card_carries_the_reference_fields(self):
        spec = cards.build(
            "infraction",
            settings(),
            "Florida State Roleplay",
            {
                "member_mention": "<@1>",
                "punishment": "<@&2>",
                "approved_by": "<@3>",
                "appealable": "Yes",
                "reason": "Mass inactive.",
                "actor_name": "IK_Gage",
            },
        )
        self.assertEqual(spec.title, "INFRACTIONS")
        labels = [icons.resolve(label) for label, _ in spec.fields]
        self.assertEqual(len(labels), 4)
        self.assertIn("👤 **User**", labels)
        self.assertEqual(spec.box_label, "Reason")
        self.assertEqual(spec.box_text, "Mass inactive.")
        self.assertEqual(spec.accent, 0xFCC200)

    def test_welcome_link_only_appears_with_a_dashboard_url(self):
        payload = {
            "greeting": "Welcome!",
            "member_count": "117",
            "joined": "just now",
            "link_label": "117 Dashboard",
        }
        without = cards.build("welcome", settings(), "Florida State", payload)
        self.assertEqual(without.link_url, "")
        with_url = cards.build(
            "welcome",
            settings(dashboard_url="https://example.com/dashboard"),
            "Florida State",
            payload,
        )
        self.assertEqual(with_url.link_url, "https://example.com/dashboard")

    def test_every_kind_builds(self):
        data = {
            "infraction": {
                "member_mention": "<@1>", "punishment": "Muted", "approved_by": "<@3>",
                "appealable": "No", "reason": "reason", "actor_name": "staff",
            },
            "promotion": {
                "member_mention": "<@1>", "rank": "High Rank", "approved_by": "<@3>",
                "notes": "notes", "actor_name": "staff",
            },
            "feedback": {
                "staff_mention": "<@1>", "rating": "★★★★★ (5/5)", "submitted_by": "<@2>",
                "feedback": "great", "actor_name": "staff",
            },
            "session": {
                "host_mention": "<@1>", "session": "Patrol", "attendees": "18", "duration": "52m",
                "actor_mention": "<@2>", "summary": "quiet", "actor_name": "staff",
            },
            "welcome": {
                "greeting": "hi", "member_count": "1", "joined": "now", "link_label": "Dashboard",
            },
        }
        for kind in module_settings.KINDS:
            spec = cards.build(kind, settings(), "Florida State", data[kind])
            view, file = spec.layout()
            self.assertIsNotNone(view)
            self.assertEqual(file.filename, cards.BANNER_FILENAME)


class ServiceTests(unittest.TestCase):
    def test_staff_by_permission(self):
        member = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True, manage_guild=False), roles=[])
        self.assertTrue(service.is_staff(member, settings()))

    def test_staff_by_fixed_role_name(self):
        member = SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
            roles=[SimpleNamespace(id=1, name="𝗙𝗦𝗥𝗣 | Senior High Rank")],
        )
        self.assertTrue(service.is_staff(member, settings()))

    def test_plain_member_is_not_staff(self):
        member = SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
            roles=[SimpleNamespace(id=1, name="Member")],
        )
        self.assertFalse(service.is_staff(member, settings()))
        with self.assertRaises(service.CardError):
            service.require_staff(member, settings())

    def test_rating_must_be_between_one_and_five(self):
        self.assertEqual(service._stars(4), "★★★★☆ (4/5)")
        for bad in (0, 6, "x", None):
            with self.assertRaises(service.CardError):
                service._stars(bad)

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(service.CardError):
            service.validate("announcement", None, None, {})

    def test_target_must_be_a_real_member(self):
        guild = SimpleNamespace(id=1, name="Guild")
        actor = SimpleNamespace(id=2, display_name="staff")
        with self.assertRaises(service.CardError):
            service.validate("infraction", guild, actor, {"member": None})

    def test_cooldown_blocks_a_second_card(self):
        service._LAST_ISSUED.clear()
        service._check_cooldown(1, "infraction", 99)
        with self.assertRaises(service.CardError):
            service._check_cooldown(1, "infraction", 99)
        service._LAST_ISSUED.clear()


class IssueGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await schema.ensure_schema(self.db)
        self.bot = SimpleNamespace(db=self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_disabled_module_is_refused(self):
        await module_settings.save(self.db, {"enabled": {"infraction": False}})
        guild = SimpleNamespace(id=1, name="Guild", get_channel=lambda _id: None)
        with self.assertRaises(service.CardError):
            await service.issue(self.bot, guild, None, "infraction", channel=None, reason="x")

    async def test_non_staff_cannot_publish(self):
        guild = SimpleNamespace(id=1, name="Guild", get_channel=lambda _id: None)
        actor = SimpleNamespace(
            id=5,
            display_name="member",
            roles=[],
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )
        with self.assertRaises(service.CardError) as caught:
            await service.issue(self.bot, guild, actor, "promotion", channel=None)
        self.assertIn("staff role", str(caught.exception))

    async def test_unknown_kind_is_refused_before_any_discord_call(self):
        with self.assertRaises(service.CardError):
            await service.issue(self.bot, None, None, "announcement")


if __name__ == "__main__":
    unittest.main()
