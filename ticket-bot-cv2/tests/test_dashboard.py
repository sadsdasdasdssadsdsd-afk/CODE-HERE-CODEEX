import os
import unittest
from unittest import mock

import aiosqlite

from dashboard import app as dashboard_app
from dashboard import auth
from dashboard.runtime import RATE_LIMIT_DELAY, RESTART_DELAY, BotRuntime
from utils import assistance, dashboard_store as store
from utils.database import ensure_canonical_schema

MINIMAL_TICKETS = """
CREATE TABLE IF NOT EXISTS ticket_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER UNIQUE,
    creator_id INTEGER NOT NULL,
    ticket_number INTEGER NOT NULL,
    category TEXT,
    subject TEXT,
    description TEXT,
    status TEXT DEFAULT 'open',
    claimed_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);
"""


class DashboardStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await store.ensure_schema(self.db)
        await self.db.execute(MINIMAL_TICKETS)
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_settings_round_trip_and_clear(self):
        await store.set_settings(self.db, {"guild_id": "42", "rotation_enabled": "1"})
        self.assertEqual((await store.get_settings(self.db))["guild_id"], "42")

        await store.set_settings(self.db, {"guild_id": None})
        self.assertNotIn("guild_id", await store.get_settings(self.db))

    async def test_id_lists_ignore_noise(self):
        self.assertEqual(store.parse_id_list('["12", 34, "x", null]'), [12, 34])
        self.assertEqual(store.dump_id_list(["9", 9, "8"]), "[8, 9]")
        self.assertEqual(store.parse_id_list("not json"), [])

    async def test_command_queue_is_claimed_once(self):
        command_id = await store.enqueue_command(self.db, "presence", {"text": "hi"})
        claimed = await store.claim_commands(self.db)
        self.assertEqual([item["id"] for item in claimed], [command_id])
        self.assertEqual(claimed[0]["payload"], {"text": "hi"})
        self.assertEqual(await store.claim_commands(self.db), [])

        await store.finish_command(self.db, command_id, True, "applied")
        recent = await store.recent_commands(self.db)
        self.assertEqual(recent[0]["state"], "done")
        self.assertEqual(recent[0]["result"], "applied")

    async def test_status_entries_lifecycle(self):
        entry = await store.add_status(self.db, "watching", "tickets", 300)
        self.assertTrue(await store.update_status(self.db, entry["id"], enabled=0, text="queue"))
        statuses = await store.list_statuses(self.db)
        self.assertEqual(statuses[0]["text"], "queue")
        self.assertFalse(statuses[0]["enabled"])
        self.assertTrue(await store.delete_status(self.db, entry["id"]))
        self.assertEqual(await store.list_statuses(self.db), [])

    async def test_ticket_listing_and_counts(self):
        await self.db.execute(
            "INSERT INTO ticket_instances (guild_id, channel_id, creator_id, ticket_number, status) "
            "VALUES (1, 11, 100, 1, 'open'), (1, 12, 101, 2, 'closed')"
        )
        await self.db.commit()

        self.assertEqual(await store.ticket_counts(self.db, 1), {"total": 2, "open": 1, "closed": 1})
        open_tickets = await store.list_tickets(self.db, 1, "open")
        self.assertEqual([ticket["ticket_number"] for ticket in open_tickets], [1])
        self.assertEqual(open_tickets[0]["guild_id"], 1)

    async def test_bot_state_round_trip(self):
        await store.write_bot_state(self.db, {"guild_name": "Bot Testing", "ready": True})
        state = await store.read_bot_state(self.db)
        self.assertEqual(state["guild_name"], "Bot Testing")
        self.assertTrue(state["ready"])

    async def test_activity_feed_is_newest_first(self):
        await store.log_activity(self.db, "bot", "first")
        await store.log_activity(self.db, "ticket", "second")
        feed = await store.recent_activity(self.db)
        self.assertEqual([item["message"] for item in feed], ["second", "first"])


class DashboardAuthTests(unittest.TestCase):
    def test_password_hash_round_trip(self):
        encoded = auth.hash_password("correct horse battery")
        self.assertTrue(auth.verify_password("correct horse battery", encoded))
        self.assertFalse(auth.verify_password("wrong password", encoded))
        self.assertFalse(auth.verify_password("correct horse battery", None))
        self.assertNotIn("correct horse", encoded)

    def test_password_strength(self):
        self.assertIsNotNone(auth.check_password_strength("abcd"))
        self.assertIsNone(auth.check_password_strength("Admin"))

    def test_sessions_issue_and_revoke(self):
        sessions = auth.Sessions()
        session = sessions.create({"name": "admin"})
        self.assertEqual(sessions.resolve(session["token"])["user"]["name"], "admin")
        self.assertIsNone(sessions.resolve("unknown"))
        sessions.destroy(session["token"])
        self.assertIsNone(sessions.resolve(session["token"]))

    def test_login_throttle_blocks_after_failures(self):
        throttle = auth.LoginThrottle()
        for _ in range(auth.MAX_LOGIN_FAILURES):
            self.assertFalse(throttle.blocked("1.2.3.4"))
            throttle.record_failure("1.2.3.4")
        self.assertTrue(throttle.blocked("1.2.3.4"))
        self.assertFalse(throttle.blocked("5.6.7.8"))
        throttle.reset("1.2.3.4")
        self.assertFalse(throttle.blocked("1.2.3.4"))


class DashboardSettingsBridgeTests(unittest.TestCase):
    def tearDown(self):
        assistance.refresh_from_settings({})

    def test_dashboard_values_override_env_defaults(self):
        assistance.refresh_from_settings(
            {
                "guild_id": "1417295227961475295",
                "panel_channel_id": "1429219230812864657",
                "staff_channel_id": "1",
                "transcript_channel_id": "2",
                "admin_role_ids": '["10", "11"]',
                "panel_banner_url": "/media/uploads/panel.png",
                "public_base_url": "https://3000-example.preview",
            }
        )
        self.assertEqual(assistance.GUILD_ID, 1417295227961475295)
        self.assertEqual(assistance.PANEL_CHANNEL_ID, 1429219230812864657)
        self.assertEqual(assistance.ADMIN_ROLE_IDS, (10, 11))
        self.assertEqual(assistance.PANEL_BANNER_URL, "https://3000-example.preview/media/uploads/panel.png")

    def test_empty_settings_keep_env_fallbacks(self):
        assistance.refresh_from_settings({})
        self.assertEqual(assistance.PANEL_CHANNEL_ID, 0)
        self.assertEqual(assistance.PANEL_BANNER_URL, "")


class FixedRoleTests(unittest.TestCase):
    def test_role_key_ignores_server_prefix(self):
        self.assertEqual(assistance.role_key("𝗙𝗦𝗥𝗣 | Founder"), "founder")

    def test_fixed_roles_match_exact_role_names(self):
        roles = [
            {"id": "1", "name": "𝗙𝗦𝗥𝗣 | Founder"},
            {"id": "2", "name": "𝗙𝗦𝗥𝗣 | Director"},
            {"id": "3", "name": "𝗙𝗦𝗥𝗣 | Deputy Director"},
            {"id": "4", "name": "𝗙𝗦𝗥𝗣 | Senior High Rank"},
            {"id": "5", "name": "𝗙𝗦𝗥𝗣 | High Rank"},
        ]
        self.assertEqual(
            [role["id"] for role in assistance.fixed_roles(roles, assistance.ADMIN_ROLE_NAMES)], ["1", "2"]
        )
        self.assertEqual(
            [role["id"] for role in assistance.fixed_roles(roles, assistance.MODERATOR_ROLE_NAMES)], ["4"]
        )
        self.assertEqual(
            [role["id"] for role in assistance.fixed_roles(roles, assistance.SUPPORT_ROLE_NAMES)], ["5"]
        )


class DashboardFreshDeployTests(unittest.IsolatedAsyncioTestCase):
    """A Render deploy serves the dashboard before the bot ever created its tables."""

    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await store.ensure_schema(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_canonical_schema_unblocks_dashboard_reads(self):
        with self.assertRaises(aiosqlite.OperationalError):
            await store.active_guild_id(self.db)
        await ensure_canonical_schema(self.db)
        self.assertIsNone(await store.active_guild_id(self.db))
        async with self.db.execute("SELECT COUNT(*) FROM ticket_instances") as cursor:
            self.assertEqual((await cursor.fetchone())[0], 0)


class BotRuntimeBackoffTests(unittest.TestCase):
    """Discord's Cloudflare 1015 ban must not be retried every 30 seconds."""

    def test_rate_limit_waits_instead_of_hammering(self):
        runtime = BotRuntime()
        runtime._observe("Bot failed to start: 429 Too Many Requests (error code: 0): <!doctype html>")
        self.assertTrue(runtime.rate_limited)
        self.assertEqual(runtime._next_delay(), RATE_LIMIT_DELAY)

    def test_ordinary_failure_keeps_the_short_backoff(self):
        runtime = BotRuntime()
        runtime._observe("Bot failed to start: HTTP 500 discord is down")
        self.assertFalse(runtime.rate_limited)
        self.assertEqual(runtime._next_delay(), RESTART_DELAY)

    def test_success_clears_the_failure_state(self):
        runtime = BotRuntime()
        runtime._observe("Login successful logged in as Florida State Roleplay Help")
        runtime._observe("   System Operational")
        self.assertEqual(runtime.identity, "Florida State Roleplay Help")
        self.assertIsNone(runtime.fatal)


class DashboardPortTests(unittest.TestCase):
    """The web entry point must start on a PaaS that only injects ``PORT``."""

    def test_explicit_preview_port_wins(self):
        with mock.patch.dict(os.environ, {"PREVIEW_STATUS_PORT": "4100", "PORT": "10000"}, clear=True):
            self.assertEqual(dashboard_app.resolve_port(), 4100)

    def test_paas_port_is_the_fallback(self):
        with mock.patch.dict(os.environ, {"PORT": "10000"}, clear=True):
            self.assertEqual(dashboard_app.resolve_port(), 10000)

    def test_default_and_invalid_values(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(dashboard_app.resolve_port(), 3000)
        with mock.patch.dict(os.environ, {"PREVIEW_STATUS_PORT": "nope"}, clear=True):
            self.assertEqual(dashboard_app.resolve_port(), 3000)


class _FakeDiscord:
    """Stands in for DiscordAPI: no network, just the calls the card makes."""

    def __init__(self, *, token="token", guilds=(), summaries=None):
        self.token = token
        self._guilds = list(guilds)
        self._summaries = summaries or {}
        self.summary_calls = []
        self.user_calls = 0

    async def server_summary(self, guild_id):
        self.summary_calls.append(guild_id)
        if guild_id not in self._summaries:
            raise dashboard_app.DiscordAPIError(404, "Unknown Guild")
        return {"id": str(guild_id), **self._summaries[guild_id]}

    async def bot_guilds(self):
        return [{"id": str(guild_id)} for guild_id in self._guilds]

    async def bot_user(self):
        self.user_calls += 1
        return {"username": "ticketbot", "global_name": "Ticket Bot", "discriminator": "0"}


def _request(api):
    return mock.Mock(app={"discord": api})


class ServerCardTests(unittest.IsolatedAsyncioTestCase):
    """A fresh deploy shows the real server even before the bot publishes state."""

    def setUp(self):
        patcher = mock.patch.object(dashboard_app.assistance, "GUILD_ID", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _summary(self, **overrides):
        return {
            "name": "Florida State Roleplay",
            "icon": "https://cdn.discordapp.com/icons/1/abc.png?size=128",
            "members": 51,
            "channels": 152,
            "roles": 118,
            **overrides,
        }

    async def test_published_state_is_used_without_calling_discord(self):
        api = _FakeDiscord(guilds=[1], summaries={1: self._summary()})
        state = {"guild_name": "Florida State Roleplay", "guild_id": "1", "member_count": 51}
        card = await dashboard_app._server_card(_request(api), state, {})
        self.assertEqual(card["name"], "Florida State Roleplay")
        self.assertEqual(card["members"], 51)
        self.assertEqual(api.summary_calls, [])

    async def test_empty_state_discovers_the_bot_server(self):
        api = _FakeDiscord(guilds=[1], summaries={1: self._summary()})
        card = await dashboard_app._server_card(_request(api), {}, {})
        self.assertEqual(card["id"], "1")
        self.assertEqual(card["name"], "Florida State Roleplay")
        self.assertEqual((card["members"], card["channels"], card["roles"]), (51, 152, 118))
        self.assertTrue(card["icon"])

    async def test_configured_server_the_bot_left_falls_back_to_a_real_one(self):
        api = _FakeDiscord(guilds=[2], summaries={2: self._summary(name="Second Server")})
        card = await dashboard_app._server_card(_request(api), {}, {"guild_id": "1"})
        self.assertEqual(card["name"], "Second Server")
        self.assertEqual(api.summary_calls, [1, 2])

    async def test_assistance_guild_is_preferred_over_the_first_guild(self):
        api = _FakeDiscord(
            guilds=[9, 7],
            summaries={9: self._summary(name="Bot Testing"), 7: self._summary(name="Home Server")},
        )
        with mock.patch.object(dashboard_app.assistance, "GUILD_ID", 7):
            card = await dashboard_app._server_card(_request(api), {}, {})
        self.assertEqual(card["name"], "Home Server")
        self.assertEqual(api.summary_calls, [7])

    async def test_biggest_guild_wins_when_nothing_is_configured(self):
        api = _FakeDiscord(
            guilds=[9, 7],
            summaries={9: self._summary(name="Small", members=4), 7: self._summary(name="Home", members=51)},
        )
        card = await dashboard_app._server_card(_request(api), {}, {})
        self.assertEqual(card["name"], "Home")

    async def test_without_a_token_the_card_stays_empty(self):
        api = _FakeDiscord(token="", guilds=[1], summaries={1: self._summary()})
        card = await dashboard_app._server_card(_request(api), {}, {})
        self.assertIsNone(card["name"])
        self.assertEqual(api.summary_calls, [])

    async def test_bot_account_comes_from_discord_when_state_is_empty(self):
        api = _FakeDiscord()
        self.assertEqual(await dashboard_app._bot_account(_request(api), {}), "Ticket Bot")
        self.assertEqual(await dashboard_app._bot_account(_request(api), {"bot_user": "Stored#1"}), "Stored#1")
        self.assertEqual(api.user_calls, 1)


if __name__ == "__main__":
    unittest.main()
