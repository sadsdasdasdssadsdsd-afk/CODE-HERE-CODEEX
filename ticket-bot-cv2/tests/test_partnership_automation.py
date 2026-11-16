"""Offline regressions: no Discord or AI-provider traffic is sent."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite

from cogs.ai_control import AIControl
from utils.ai import engine, jobs, messages, partnerships, schema, settings, store


class FakeChannel:
    """Minimal text channel exposing the async history iterator the cog uses."""

    def __init__(self, *history):
        self.id = 41
        self.name = "partnerships"
        self._history = list(history)
        self.send = AsyncMock()
        self.guild = None

    def permissions_for(self, _member):
        return SimpleNamespace(view_channel=True, read_message_history=True, send_messages=True)

    def history(self, limit=100):
        async def walk():
            for message in self._history[:limit]:
                yield message

        return walk()


class PartnershipRulesTests(unittest.TestCase):
    def test_only_the_fixed_15_member_minimum_gates_the_review(self):
        checklist = engine.evaluate_requirements(
            {"member_count": 15, "channel_count": 0},
            {"min_members": 100, "require_organisation": True, "min_channels": 50},
            {"organisation": {"status": "disorganised", "reason": "Many duplicate channels"}},
        )
        self.assertEqual(settings.DEFAULT_REQUIREMENTS, {"min_members": 15})
        self.assertEqual([item["key"] for item in checklist], ["members"])
        self.assertEqual(engine.overall_eligibility(checklist), "eligible")
        self.assertFalse(engine.evaluate_requirements({"member_count": 14}, {})[0]["passed"])
        self.assertIsNone(engine.evaluate_requirements({}, {})[0]["passed"])
        self.assertEqual(
            settings.normalise("partnership_requirements", {"min_members": 100, "require_organisation": True}),
            '{"min_members": 15}',
        )

    def test_bare_invite_and_done_are_not_advertisements(self):
        self.assertEqual(partnerships.advertisement("https://discord.gg/longinvitecode123456789"), "")
        self.assertEqual(partnerships.advertisement("done"), "")
        self.assertTrue(partnerships.is_done("Done!"))
        self.assertFalse(partnerships.is_done("not done"))

    def test_publication_needs_actual_review_not_dashboard_approval(self):
        record = {
            "bot_joined": 1, "human_verified": 1, "analysis_status": "complete",
            "eligibility": "eligible", "publish_status": "pending", "advertisement": "Our advertisement",
            "requirements": [{"passed": True}], "ad_status": "verified",
        }
        self.assertTrue(engine.can_publish(record)[0])
        for field, value in (("human_verified", 0), ("analysis_status", "pending"), ("publish_status", "paused")):
            self.assertFalse(engine.can_publish({**record, field: value})[0])
        self.assertTrue(engine.can_publish({**record, "analysis_status": "degraded"})[0])
        self.assertFalse(engine.can_publish({**record, "requirements": [{"passed": None}]})[0])
        self.assertFalse(engine.can_publish({**record, "ad_status": "awaiting_ad"})[0])

    def test_only_a_real_repost_of_our_advertisement_counts(self):
        reference = (
            "𝗙𝗦𝗥𝗣 is a serious roleplay community with immersive storylines, active staff and "
            "weekly events for every member who joins us today."
        )
        copy = (
            "𝗙𝗦𝗥𝗣 is a serious roleplay community with immersive storylines, active staff and "
            "weekly events for every member who joins us today! https://discord.gg/fsrp"
        )
        self.assertTrue(partnerships.posted_ad(reference, copy))
        self.assertFalse(partnerships.posted_ad(reference, "hey, want to partner with us?"))
        self.assertFalse(partnerships.posted_ad(reference, "https://discord.gg/fsrp"))
        self.assertFalse(partnerships.posted_ad(reference, "done"))
        # Without a fetched reference our server name plus real copy is required.
        self.assertTrue(partnerships.posted_ad(
            "", "Our server 𝗙𝗦𝗥𝗣 welcomes creative writers and passionate storytellers "
                "with weekly immersive events and friendly moderation", "𝗙𝗦𝗥𝗣"))
        self.assertFalse(partnerships.posted_ad("", "hey 𝗙𝗦𝗥𝗣", "𝗙𝗦𝗥𝗣"))
        self.assertFalse(partnerships.posted_ad("", "Our community welcomes creative writers every week", ""))

    def test_install_url_is_guild_specific_and_does_not_request_administrator(self):
        url = partnerships.install_url(123, 456)
        self.assertIn("guild_id=456", url)
        self.assertIn("permissions=68608", url)
        self.assertIn("scope=bot", url)

    def test_long_reports_are_not_truncated(self):
        text = "A" * 5000
        parts = messages.chunks(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part) <= 1900 for part in parts))
        self.assertIn("✅", messages.render("✅ Connected", SimpleNamespace(id=999)))

    def test_partnership_welcome_explains_trigger_and_new_rule(self):
        self.assertIn("random letter", engine.PARTNERSHIP_WELCOME)
        self.assertIn("at least 15 members", engine.PARTNERSHIP_WELCOME)
        self.assertNotIn("professionally organised", engine.PARTNERSHIP_WELCOME)


class AutomaticPartnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await schema.ensure_schema(self.db)
        self.channel = SimpleNamespace(id=20, send=AsyncMock(), guild=SimpleNamespace(id=10))
        self.target = SimpleNamespace(id=40, name="Applicant", member_count=50)
        self.applicant_channel = FakeChannel()
        self.applicant_channel.guild = self.target
        self.target.me = SimpleNamespace(id=999)
        self.target.text_channels = [self.applicant_channel]
        self.target.get_channel = lambda channel_id: self.applicant_channel if channel_id == 41 else None
        self.bot = SimpleNamespace(
            db=self.db, application_id=123, get_channel=lambda _id: self.channel,
            get_guild=lambda _id: self.target if _id == 40 else None,
        )
        self.cog = AIControl(self.bot)
        self.case_id = await store.create_case(self.db, 10, ticket_channel_id=20, idempotency_key="ticket:20")
        self.application_id = await store.create_partnership(
            self.db, 10, case_id=self.case_id, applicant_id=30,
            advertisement="Our intentional, welcoming roleplay community. https://discord.gg/applicant",
            invite_code="applicant", server_name="Applicant",
        )
        await store.update_partnership(self.db, self.application_id, joined_guild_id=40)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_partnership_welcome_uses_ticket_label_with_stale_category_id(self):
        class FakeTextChannel:
            id = 20
            category_id = 999
            name = "member-partnership-support-0001"
            guild = SimpleNamespace(id=10)

        info = {"category": "Partnership Support", "status": "open"}
        channel = FakeTextChannel()
        self.cog.say = AsyncMock()
        with patch("cogs.ai_control.discord.TextChannel", FakeTextChannel), \
             patch("cogs.ai_control.get_ticket_info", new=AsyncMock(side_effect=[None, info])), \
             patch("cogs.ai_control.asyncio.sleep", new=AsyncMock()):
            await self.cog.on_guild_channel_create(channel)
        self.cog.say.assert_awaited_once_with(channel, engine.PARTNERSHIP_WELCOME)
        async with self.db.execute(
            "SELECT action, decision FROM ai_activity ORDER BY id DESC LIMIT 1"
        ) as cursor:
            self.assertEqual(await cursor.fetchone(), ("partnership.welcome", "welcome sent"))

    def message(self):
        return SimpleNamespace(id=100, guild=SimpleNamespace(id=10), channel=self.channel,
                               author=SimpleNamespace(id=30), content="done")

    async def test_done_checks_access_and_queues_analysis_once(self):
        with patch.object(self.cog, "partnership_channels", return_value=[SimpleNamespace(name="general")]):
            await self.cog.handle_partnership_message(self.message(), {}, {})
            await self.cog.handle_partnership_message(self.message(), {}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["human_verified"], 1)
        self.assertEqual(record["verified_by"], "ticket:30")
        self.assertEqual(record["analysis_status"], "queued")
        self.assertEqual((await jobs.stats(self.db))["queued"], 1)

    async def test_done_cannot_claim_access_when_bot_only_sees_verification(self):
        with patch.object(self.cog, "partnership_channels", return_value=[]):
            await self.cog.handle_partnership_message(self.message(), {}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertFalse(record["human_verified"])
        self.assertEqual((await jobs.stats(self.db))["queued"], 0)
        self.assertIn("still restricted", self.channel.send.call_args.kwargs["content"])

    async def test_invite_provides_install_link_without_attempting_user_only_join(self):
        self.bot.get_guild = lambda _id: None
        self.bot.fetch_invite = AsyncMock(return_value=SimpleNamespace(guild=self.target, approximate_member_count=50))
        await self.cog.run_partnership_join({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["invite_status"], "awaiting_install")
        self.assertFalse(record["bot_joined"])
        self.assertIn("oauth2/authorize", self.channel.send.call_args.kwargs["content"])

    async def test_passing_review_reports_in_ticket_and_queues_publication(self):
        await store.update_partnership(self.db, self.application_id, human_verified=True, bot_joined=True)
        checklist = engine.evaluate_requirements(
            {"member_count": 50}, settings.DEFAULT_REQUIREMENTS,
            {"organisation": {"status": "organised", "reason": "Coherent server information"}},
        )
        result = {"requirements": checklist, "eligibility": "eligible", "findings": {"summary": "Clear organisation", "confidence": 0.9}}
        with patch.object(self.cog, "partnership_channels", return_value=[self.channel]), \
             patch.object(self.cog, "server_snapshot", new=AsyncMock(return_value={})), \
             patch.object(engine, "analyse_server", new=AsyncMock(return_value=result)):
            await self.cog.run_partnership_analyse({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["publish_status"], "approved")
        self.assertEqual(record["ad_status"], "awaiting_ad")
        kinds = [job["kind"] for job in await jobs.recent(self.db)]
        self.assertEqual(kinds[0], "partnership_verify_ad")
        # Nothing is published and the review is not torn down until our ad appears.
        self.assertNotIn("publish_advertisement", kinds)
        self.assertNotIn("partnership_finish", kinds)
        sent = "\n".join(call.kwargs["content"] for call in self.channel.send.call_args_list)
        self.assertIn("Partnership report", sent)
        self.assertIn("post our advertisement", sent)

    async def test_review_waits_until_our_advertisement_is_posted_then_publishes(self):
        await store.update_partnership(
            self.db, self.application_id, human_verified=True, bot_joined=True, invite_status="joined",
            analysis_status="complete", eligibility="eligible", publish_status="approved",
            requirements=[{"passed": True}], ad_status="awaiting_ad",
        )
        reference = "𝗙𝗦𝗥𝗣 is a serious roleplay community with immersive storylines and active staff for writers."
        posted = SimpleNamespace(
            id=501, channel=SimpleNamespace(id=41), embeds=[], author=SimpleNamespace(id=30, bot=False),
            content=reference + " https://discord.gg/fsrp",
        )
        applicant_channel = FakeChannel(posted)
        with patch.object(self.cog, "home_advertisement", new=AsyncMock(return_value=reference)), \
             patch.object(self.cog, "partnership_channels", return_value=[applicant_channel]):
            await self.cog.run_partnership_verify_ad({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["ad_status"], "verified")
        self.assertEqual(record["ad_source_message_id"], 501)
        self.assertEqual(record["ad_source_channel_id"], 41)
        self.assertIn("publish_advertisement", [job["kind"] for job in await jobs.recent(self.db)])
        self.assertIn("I found our advertisement", self.channel.send.call_args.kwargs["content"])

    async def test_missing_advertisement_keeps_the_bot_in_the_server_and_retries(self):
        await store.update_partnership(
            self.db, self.application_id, human_verified=True, bot_joined=True, invite_status="joined",
            analysis_status="complete", eligibility="eligible", publish_status="approved",
            requirements=[{"passed": True}], ad_status="awaiting_ad",
        )
        with patch.object(self.cog, "home_advertisement", new=AsyncMock(return_value="")), \
             patch.object(self.cog, "partnership_channels", return_value=[FakeChannel()]):
            await self.cog.run_partnership_verify_ad({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["ad_status"], "awaiting_ad")
        self.assertEqual(record["ad_checks"], 1)
        self.assertEqual(record["invite_status"], "joined")
        kinds = [job["kind"] for job in await jobs.recent(self.db)]
        self.assertEqual(kinds, ["partnership_verify_ad"])
        self.assertIn("not seen our advertisement", self.channel.send.call_args.kwargs["content"])

    async def test_failed_review_leaves_the_applicant_server_then_closes_the_ticket(self):
        # The automated finish closes the ticket but must never delete the channel.
        await store.update_partnership(
            self.db, self.application_id, bot_joined=True, invite_status="joined",
            analysis_status="complete", publish_status="blocked",
        )
        order = []
        self.target.leave = AsyncMock(side_effect=lambda: order.append("leave"))
        home = SimpleNamespace(id=10)
        self.bot.get_guild = lambda guild_id: self.target if guild_id == 40 else home
        with patch("cogs.ai_control.close_and_delete_ticket", new=AsyncMock(
            side_effect=lambda *a, **k: (order.append("close") or (True, "closed"))
        )) as closer:
            await self.cog.run_partnership_finish({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(order, ["leave", "close"])
        self.assertEqual(closer.await_count, 1)
        self.assertFalse(closer.await_args.kwargs["delete_channel"])
        self.assertEqual(record["invite_status"], "left")
        self.assertFalse(record["bot_joined"])

    async def test_published_partnership_stays_and_announces_ad_monitoring(self):
        await store.update_partnership(
            self.db, self.application_id,
            bot_joined=True, invite_status="joined", analysis_status="complete",
            eligibility="eligible", publish_status="published", ad_status="verified",
            published_at=settings.utc_now(), ad_source_message_id=501, ad_source_channel_id=41,
            requirements=[{"passed": True}],
        )
        self.target.leave = AsyncMock()
        home = SimpleNamespace(id=10)
        self.bot.get_guild = lambda guild_id: self.target if guild_id == 40 else home
        with patch.object(self.cog, "partnership_channels", return_value=[self.applicant_channel]), \
             patch("cogs.ai_control.close_and_delete_ticket", new=AsyncMock(return_value=(True, "closed"))) as closer:
            await self.cog.run_partnership_finish({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.target.leave.assert_not_awaited()
        self.assertTrue(record["bot_joined"])
        self.assertEqual(record["invite_status"], "joined")
        self.assertTrue(record["ad_monitor_announced_at"])
        self.assertIn("remain in", self.applicant_channel.send.call_args.kwargs["content"])
        self.assertFalse(closer.await_args.kwargs["delete_channel"])
        self.assertIn("partnership_monitor_ad", [job["kind"] for job in await jobs.recent(self.db)])

    async def test_monitor_ignores_unrelated_ads_and_keeps_checking(self):
        unrelated = SimpleNamespace(
            author=SimpleNamespace(bot=False), embeds=[],
            content="Partner with our unrelated game server for weekly events and special rewards!",
        )
        channel = FakeChannel(unrelated)
        channel.guild = self.target
        self.target.text_channels = [channel]
        self.target.get_channel = lambda _channel_id: channel
        home = SimpleNamespace(id=10, name="Home Community")
        self.bot.get_guild = lambda guild_id: self.target if guild_id == 40 else home
        reference = "Our required roleplay advertisement welcomes creative writers with immersive stories and weekly events."
        await store.update_partnership(
            self.db, self.application_id,
            bot_joined=True, invite_status="joined", analysis_status="complete",
            eligibility="eligible", publish_status="published", ad_status="verified",
            published_at=settings.utc_now(), requirements=[{"passed": True}],
        )
        with patch.object(self.cog, "partnership_channels", return_value=[channel]), \
             patch.object(self.cog, "home_advertisement", new=AsyncMock(return_value=reference)):
            await self.cog.run_partnership_monitor_ad({"partnership_id": self.application_id}, {})
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["ad_status"], "missing")
        self.assertIn("partnership_monitor_ad", [job["kind"] for job in await jobs.recent(self.db)])

    async def test_finished_application_gets_no_new_install_link_and_is_left_again(self):
        await store.update_partnership(self.db, self.application_id, invite_status="left")
        self.bot.fetch_invite = AsyncMock()
        await self.cog.run_partnership_join({"partnership_id": self.application_id}, {})
        self.bot.fetch_invite.assert_not_awaited()
        applicant = SimpleNamespace(id=40, leave=AsyncMock())
        self.assertTrue(await self.cog.leave_finished_guild(applicant))
        applicant.leave.assert_awaited_once()

    async def test_failed_review_also_closes_the_review(self):
        await store.update_partnership(self.db, self.application_id, human_verified=True, bot_joined=True)
        checklist = engine.evaluate_requirements(
            {"member_count": 12}, settings.DEFAULT_REQUIREMENTS,
            {"organisation": {"status": "disorganised", "reason": "Empty channels"}},
        )
        result = {"requirements": checklist, "eligibility": "not_eligible", "findings": {"summary": "Too small", "confidence": 0.8}}
        with patch.object(self.cog, "partnership_channels", return_value=[self.channel]), \
             patch.object(self.cog, "server_snapshot", new=AsyncMock(return_value={})), \
             patch.object(engine, "analyse_server", new=AsyncMock(return_value=result)):
            await self.cog.run_partnership_analyse({"partnership_id": self.application_id}, {})
        kinds = [job["kind"] for job in await jobs.recent(self.db)]
        self.assertIn("partnership_finish", kinds)
        self.assertNotIn("publish_advertisement", kinds)

    async def test_publication_posts_once_without_dashboard_clicks(self):
        await store.update_partnership(
            self.db, self.application_id, human_verified=True, bot_joined=True,
            analysis_status="complete", eligibility="eligible", publish_status="approved",
            requirements=[{"passed": True}], ad_status="verified",
        )
        self.channel.name = "🤝・partnerships"
        self.channel.permissions_for = lambda _member: SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
        self.channel.send.return_value = SimpleNamespace(id=900, jump_url="https://discord.com/channels/10/20/900")
        home = SimpleNamespace(text_channels=[self.channel], me=SimpleNamespace())
        self.bot.get_guild = lambda guild_id: home if guild_id == 10 else self.target
        with patch.object(self.cog, "partnership_channels", return_value=[self.channel]):
            await self.cog.run_publish_advertisement({"partnership_id": self.application_id}, {})
            calls_after_publish = self.channel.send.call_count
            await self.cog.run_publish_advertisement({"partnership_id": self.application_id}, {})
        self.assertEqual(self.channel.send.call_count, calls_after_publish)
        record = await store.get_partnership(self.db, self.application_id)
        self.assertEqual(record["publish_status"], "published")
        self.assertEqual(record["published_message_id"], 900)
        self.assertIn("embed", self.channel.send.call_args_list[0].kwargs)
        self.assertIn("https://discord.gg/applicant", self.channel.send.call_args_list[0].kwargs["embed"].description)

    async def test_new_ticket_does_not_resume_a_deleted_tickets_application(self):
        msg = self.message()
        msg.channel = SimpleNamespace(id=21, send=AsyncMock(), guild=msg.guild)
        msg.content = "hello"
        await self.cog.handle_partnership_message(msg, {}, {})
        new_case = await store.find_open_case(self.db, 21)
        record = await store.find_partnership_by_case(self.db, new_case["id"])
        self.assertNotEqual(record["id"], self.application_id)
        self.assertEqual(record["advertisement"], "")

    async def test_repeated_and_concurrent_case_creation_is_idempotent(self):
        other = await store.create_case(self.db, 10, idempotency_key="ticket:21")
        ids = await asyncio.gather(*[
            store.create_case(self.db, 10, idempotency_key="ticket:20", subject="Do not overwrite")
            for _ in range(4)
        ])
        self.assertEqual(ids, [self.case_id] * 4)
        self.assertNotEqual(other, self.case_id)
        self.assertEqual((await store.get_case(self.db, self.case_id))["subject"], "")

    async def test_failed_publish_can_be_retried_but_active_and_done_jobs_cannot(self):
        key = "partnership:1:publish"
        job_id = await jobs.enqueue(self.db, "publish_advertisement", {}, idempotency_key=key, max_attempts=1)
        await jobs.claim(self.db)
        await jobs.fail(self.db, job_id, "missing permission")
        self.assertEqual(await jobs.enqueue(self.db, "publish_advertisement", {}, idempotency_key=key, retry_failed=True), job_id)
        self.assertIsNone(await jobs.enqueue(self.db, "publish_advertisement", {}, idempotency_key=key, retry_failed=True))
        await jobs.complete(self.db, job_id)
        self.assertIsNone(await jobs.enqueue(self.db, "publish_advertisement", {}, idempotency_key=key, retry_failed=True))
