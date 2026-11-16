import unittest
from types import SimpleNamespace
from unittest.mock import patch

import aiosqlite

from cogs import ai_control
from utils.ai import engine, schema, store
from utils.ai import settings as ai_settings
from utils.ai.provider import AIUnavailable


class IntCoercionTests(unittest.TestCase):
    def test_snowflake_ids_survive_int_coercion(self):
        # Discord ids are 64-bit; float conversion silently changed the last digits.
        for value in (1563801520166666301, "1563801520166666301", " 1563801520166666301 "):
            self.assertEqual(ai_settings.as_int(value), 1563801520166666301)

    def test_numbers_and_junk(self):
        self.assertEqual(ai_settings.as_int("42"), 42)
        self.assertEqual(ai_settings.as_int("42.9"), 42)
        self.assertEqual(ai_settings.as_int(None), 0)
        self.assertEqual(ai_settings.as_int("nope", default=7), 7)


class RoutingTests(unittest.TestCase):
    def test_category_rule_wins_when_the_guessed_department_is_unconfigured(self):
        routes = [
            {"id": 1, "name": "Staff reports", "department": "Management", "report_category": "staff_report", "enabled": 1},
        ]
        route, reason = engine.choose_route(routes, department="Moderation", report_category="staff_report")
        self.assertEqual((route or {}).get("id"), 1)
        self.assertIn("No rule matched department", reason)

    def test_configured_department_still_wins_over_a_mismatched_one(self):
        routes = [
            {"id": 1, "name": "Management", "department": "Management", "report_category": "staff_report", "enabled": 1},
            {"id": 2, "name": "Moderation", "department": "Moderation", "report_category": "staff_report", "enabled": 1},
        ]
        route, _reason = engine.choose_route(routes, department="Moderation", report_category="staff_report")
        self.assertEqual((route or {}).get("id"), 2)

    def test_unmatched_category_returns_no_route(self):
        routes = [{"id": 1, "name": "Partners", "department": "Partnership", "report_category": "partnership", "enabled": 1}]
        route, reason = engine.choose_route(routes, department="Moderation", report_category="staff_report")
        self.assertIsNone(route)
        self.assertIn("No enabled routing rule", reason)


class InviteExtractionTests(unittest.TestCase):
    def test_invite_code_from_a_link(self):
        self.assertEqual(engine.invite_in_text("Join us: https://discord.gg/playcali"), "playcali")
        self.assertEqual(engine.invite_in_text("https://discord.com/invite/abc-123"), "abc-123")

    def test_plain_text_is_not_an_invite(self):
        self.assertEqual(engine.invite_in_text("Our server runs daily sessions."), "")


class PartnershipApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await schema.ensure_schema(self.db)
        self.cog = object.__new__(ai_control.AIControl)
        self.cog.bot = SimpleNamespace(db=self.db)
        self.cog.conversations = {}
        self.cog.locks = {}
        self.sent: list[str] = []

    async def asyncTearDown(self):
        await self.db.close()

    def message(self, content: str) -> SimpleNamespace:
        async def send(content: str, **_kwargs) -> None:
            self.sent.append(content)

        return SimpleNamespace(
            id=100,
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=20, send=send),
            author=SimpleNamespace(id=30),
            content=content,
        )

    async def apply(self, content: str) -> None:
        info = {"ticket_number": "0001", "category": "Partnership Support", "subject": "Partnership"}
        await self.cog.handle_partnership_message(self.message(content), info, {})

    async def test_advertisement_and_invite_are_read_from_the_message(self):
        ad = (
            "California State Roleplay\n"
            "Our community runs daily sessions and welcomes new members.\n"
            "Join us: https://discord.gg/playcali"
        )
        with patch.object(ai_control.engine, "extract_partnership_submission", side_effect=AIUnavailable("bad json")):
            await self.apply(ad)

        case = await store.find_open_case(self.db, 20)
        record = await store.find_partnership_by_case(self.db, case["id"])
        self.assertEqual(record["advertisement"], ad)
        self.assertEqual(record["invite_code"], "playcali")

        async with self.db.execute("SELECT kind FROM ai_jobs") as cursor:
            queued = [row[0] for row in await cursor.fetchall()]
        self.assertIn("partnership_join", queued)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("checking the server connection", self.sent[0])

    async def test_a_reopened_ticket_continues_the_same_application(self):
        ad = "California State Roleplay keeps the same application across tickets, join https://discord.gg/playcali"
        with patch.object(ai_control.engine, "extract_partnership_submission", side_effect=AIUnavailable("bad json")):
            await self.apply(ad)
            case = await store.find_open_case(self.db, 20)
            await store.update_case(self.db, case["id"], status="escalated")
            await self.apply(ad)

        async with self.db.execute("SELECT COUNT(*) FROM ai_partnerships") as cursor:
            self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_short_messages_still_ask_for_the_advertisement(self):
        with patch.object(ai_control.engine, "extract_partnership_submission", side_effect=AIUnavailable("bad json")):
            await self.apply("hi")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("your server advertisement", self.sent[0])


class StoreUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await schema.ensure_schema(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_case_updates_accept_keyword_fields(self):
        case_id = await store.create_case(self.db, 1, ticket_channel_id=20, requester_id=30)
        self.assertTrue(
            await store.update_case(
                self.db,
                case_id,
                details={"awaiting_form": None, "analysis": {"severity": "high"}},
                escalated=True,
                status="escalated",
                kind="report",
            )
        )
        case = await store.get_case(self.db, case_id)
        self.assertEqual(case["status"], "escalated")
        self.assertEqual(case["kind"], "report")
        self.assertEqual(case["escalated"], 1)
        self.assertEqual(case["details"]["analysis"]["severity"], "high")

    async def test_partnership_role_ids_load_as_a_list(self):
        # The dashboard iterates this value; decoding it as an object broke the tab.
        await ai_settings.save(self.db, {"partnership_role_ids": [1, 2]})
        settings = await ai_settings.load(self.db)
        self.assertEqual(settings["partnership_role_ids"], [1, 2])

    async def test_partnership_updates_accept_keyword_fields(self):
        partnership_id = await store.create_partnership(self.db, 1, applicant_id=30)
        self.assertTrue(await store.update_partnership(self.db, partnership_id, invite_status="invalid", member_count=1200))
        record = await store.get_partnership(self.db, partnership_id)
        self.assertEqual(record["invite_status"], "invalid")
        self.assertEqual(record["member_count"], 1200)


if __name__ == "__main__":
    unittest.main()
