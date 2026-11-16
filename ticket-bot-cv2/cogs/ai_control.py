"""AI Control System executor: background jobs, ticket conversations, partnerships.

The dashboard owns configuration and review; this cog performs the Discord work
it enqueues: reading channels for knowledge ingestion, answering members in
tickets, routing cases to configured staff, joining applicant servers and
publishing approved advertisements. Every decision is written to the shared
database so the dashboard can show exactly what happened.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import assistance
from utils.ai import engine, jobs, knowledge, messages, partnerships, schema, store
from utils.ai import settings as ai_settings
from utils.ai.provider import AIProvider, AIUnavailable
from utils.tickets import close_and_delete_ticket, get_ticket_info, is_ticket_channel

logger = logging.getLogger("discord")

PARTNERSHIP_CATEGORY_ID = assistance.category_channel_id("Partnership Support")
WORKER_INTERVAL_SECONDS = 2.0
# Hard ceiling for a single background job (analysis, ad check, publication). The
# AI provider has its own 90s timeout; this also covers the Discord work around it.
JOB_TIMEOUT_SECONDS = 240.0
MAX_CONVERSATION_MESSAGES = 12


class AIControl(commands.Cog):
    """Runs AI jobs and answers members inside support tickets."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.seen_messages: deque[int] = deque(maxlen=800)
        self.conversations: dict[int, list[dict[str, str]]] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ lifecycle
    async def cog_load(self) -> None:
        await schema.ensure_schema(self.bot.db)
        if not self.worker.is_running():
            self.worker.start()

    async def cog_unload(self) -> None:
        self.worker.cancel()

    @property
    def db(self):
        return self.bot.db

    async def config(self) -> dict[str, Any]:
        return await ai_settings.load(self.db)

    def lock_for(self, channel_id: int) -> asyncio.Lock:
        return self.locks.setdefault(channel_id, asyncio.Lock())

    @tasks.loop(seconds=WORKER_INTERVAL_SECONDS)
    async def worker(self) -> None:
        try:
            await self.drain_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI job worker failed")

    @worker.before_loop
    async def before_worker(self) -> None:
        await self.bot.wait_until_ready()
        try:
            requeued = await jobs.requeue_stale(self.db, older_than_seconds=300)
            if requeued:
                logger.info("Requeued %d interrupted AI job(s)", requeued)
        except Exception:
            logger.exception("Could not requeue stale AI jobs")

    async def drain_jobs(self) -> None:
        config = await self.config()
        if not ai_settings.as_bool(config.get("enabled")):
            return
        job = await jobs.claim(self.db)
        if not job:
            return
        handler = {
            "ingest_knowledge": self.run_ingest_knowledge,
            "notify_staff": self.run_notify_staff,
            "send_ticket_message": self.run_send_ticket_message,
            "partnership_join": self.run_partnership_join,
            "partnership_analyse": self.run_partnership_analyse,
            "partnership_verify_ad": self.run_partnership_verify_ad,
            "partnership_monitor_ad": self.run_partnership_monitor_ad,
            "publish_advertisement": self.run_publish_advertisement,
            "partnership_finish": self.run_partnership_finish,
        }.get(job["kind"])
        if handler is None:
            await jobs.fail(self.db, job["id"], f"Unknown job kind {job['kind']}")
            return
        try:
            # Nothing in the pipeline may hold the bot hostage: a stuck provider call,
            # a slow channel walk or a throttled REST call is cancelled at the deadline
            # instead of leaving the applicant with a review that never finishes.
            await asyncio.wait_for(handler(job["payload"], job), timeout=JOB_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                "AI job %s (%s) exceeded %.0fs and was cancelled",
                job["id"], job["kind"], JOB_TIMEOUT_SECONDS,
            )
            await self.job_failed(
                job,
                "This step took longer than "
                f"{int(JOB_TIMEOUT_SECONDS / 60)} minutes and was stopped. Nothing was published.",
            )
            if job["kind"] == "partnership_analyse":
                partnership_id = (job["payload"] or {}).get("partnership_id")
                if partnership_id:
                    await store.update_partnership(
                        self.db, partnership_id, analysis_status="pending"
                    )
        except AIUnavailable as error:
            await self.job_failed(job, str(error), degraded=True)
        except Exception as error:
            logger.exception("AI job %s (%s) failed", job["id"], job["kind"])
            await self.job_failed(job, str(error))
        else:
            await jobs.complete(self.db, job["id"])

    async def job_failed(self, job: dict[str, Any], error: str, *, degraded: bool = False) -> None:
        state = await jobs.fail(self.db, job["id"], error)
        partnership_id = (job["payload"] or {}).get("partnership_id")
        if state == "failed" and partnership_id:
            record = await store.get_partnership(self.db, partnership_id)
            if record:
                if job["kind"] == "partnership_analyse":
                    await store.update_partnership(self.db, partnership_id, analysis_status="degraded")
                elif job["kind"] == "publish_advertisement":
                    await store.update_partnership(self.db, partnership_id, publish_status="blocked")
                    # The review is over even when the post failed: leave the
                    # applicant server and close the ticket.
                    await self.enqueue_finish(record)
                await self.say(
                    await self.ticket_channel_for(record),
                    "⚠️ I could not finish the partnership step automatically. Nothing further was posted. "
                    "Staff can review the recorded error in the activity log. "
                    + (str(error)[:450] if job["kind"] == "publish_advertisement" else "Please ask staff to check the connection and permissions."),
                )
        await store.log_activity(
            self.db,
            "job.failed",
            case_id=(job["payload"] or {}).get("case_id"),
            decision=str(error),
            status="degraded" if degraded else "failed",
            detail=f"job={job['kind']} id={job['id']} state={state}",
        )

    # ------------------------------------------------------------------ jobs
    async def run_ingest_knowledge(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        channel_id = ai_settings.as_int(payload.get("channel_id"))
        source_id = ai_settings.as_int(payload.get("source_id")) or None
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Knowledge ingestion needs a text channel the bot can read.")

        records = []
        async for message in channel.history(limit=min(500, max(20, ai_settings.as_int(payload.get("limit"), default=250)))):
            records.append(
                {
                    "content": message.content,
                    "author_name": message.author.display_name,
                    "author_bot": message.author.bot,
                    "created_at": message.created_at.isoformat(),
                }
            )
        candidates = knowledge.candidate_messages(records)
        config = await self.config()
        added = 0
        if candidates:
            provider = AIProvider(config)
            entries = await engine.organise_knowledge(
                provider, config, knowledge.message_to_prompt(candidates), channel_name=channel.name
            )
            existing = await store.list_knowledge(self.db, channel.guild.id, status="all")
            seen = {knowledge.dedupe_key(entry["title"], entry["body"]) for entry in existing}
            for entry in entries:
                if not entry.get("official", True):
                    continue
                key = knowledge.dedupe_key(entry["title"], entry["body"])
                if key in seen:
                    continue
                seen.add(key)
                await store.add_knowledge(
                    self.db,
                    channel.guild.id,
                    title=entry["title"],
                    body=entry["body"],
                    tags=entry.get("tags", ""),
                    source_type="channel",
                    source_ref=f"channel:{channel.id}",
                    status="pending",
                    confidence=entry.get("confidence", 0),
                    created_by="ai-ingest",
                )
                added += 1

        if source_id:
            await store.mark_source_scanned(self.db, source_id, messages=len(records), entries=added)
        await store.log_activity(
            self.db,
            "knowledge.ingest",
            guild_id=channel.guild.id,
            decision=f"{added} candidate entr{'y' if added == 1 else 'ies'} for review",
            confidence=1.0 if candidates else 0.0,
            destination_channel=str(channel.id),
            detail=f"#{channel.name}: scanned {len(records)} messages, {len(candidates)} usable",
        )

    async def run_notify_staff(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        channel_id = ai_settings.as_int(payload.get("channel_id"))
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        content = str(payload.get("content") or "").strip()
        role_ids = ai_settings.as_id_list(payload.get("role_ids"))
        if not content:
            raise RuntimeError("Notification had no content.")
        mentions = " ".join(f"<@&{role_id}>" for role_id in role_ids)
        await channel.send(
            content=messages.render(f"{mentions}\n{content}".strip(), getattr(channel, "guild", None))[:2000],
            allowed_mentions=discord.AllowedMentions(roles=True, users=True),
        )
        await store.log_activity(
            self.db,
            "staff.notify",
            case_id=payload.get("case_id"),
            destination_role=",".join(str(role_id) for role_id in role_ids),
            destination_channel=str(channel_id),
            detail=content[:400],
        )

    async def run_send_ticket_message(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        channel_id = ai_settings.as_int(payload.get("channel_id"))
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await self.say(channel, str(payload.get("content") or ""))

    # ------------------------------------------------------------------ partnerships
    async def run_partnership_join(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        partnership_id = ai_settings.as_int(payload.get("partnership_id"))
        record = await store.get_partnership(self.db, partnership_id)
        if not record:
            raise RuntimeError("Partnership application no longer exists.")
        code = record.get("invite_code") or ""
        if not code:
            raise RuntimeError("No invitation code was collected for this application.")
        if record.get("publish_status") in {"paused", "rejected", "published"}:
            return
        # A finished application never gets a new installation link: the bot has
        # already left that server and stays out of it.
        if record.get("published_at") or record.get("invite_status") == "left":
            return
        if not ai_settings.features(await self.config())["partnership_automation"]:
            return
        ticket_channel = await self.ticket_channel_for(record)
        try:
            invite = await self.bot.fetch_invite(code, with_counts=True)
        except discord.NotFound:
            await store.update_partnership(self.db, partnership_id, invite_status="invalid")
            await self.say(ticket_channel, "That Discord invitation is invalid or has expired. Please post a fresh invite link.")
            await store.log_activity(
                self.db,
                "partnership.invite",
                guild_id=record["guild_id"],
                case_id=record["case_id"],
                decision="invalid invitation",
                status="failed",
                detail=f"invite={code}",
            )
            return
        except discord.Forbidden:
            await store.update_partnership(self.db, partnership_id, invite_status="inaccessible")
            await self.say(ticket_channel, "Discord would not let the bot read that invitation. Please check the link.")
            return

        guild = invite.guild
        if guild is None:
            await store.update_partnership(self.db, partnership_id, invite_status="invalid")
            await self.say(ticket_channel, "⚠️ Please send a server invite, not a group or friend invitation.")
            return
        await store.update_partnership(
            self.db, partnership_id,
            joined_guild_id=guild.id, joined_guild_name=guild.name,
            server_name=guild.name, member_count=invite.approximate_member_count,
        )
        target = self.bot.get_guild(guild.id)
        if target is None:
            # Bot tokens cannot accept ordinary server invitations. Only an
            # authorised server administrator can install an application.
            application_id = self.bot.application_id or self.bot.user.id
            url = partnerships.install_url(application_id, guild.id)
            await store.update_partnership(self.db, partnership_id, invite_status="awaiting_install")
            await self.say(
                ticket_channel,
                "### 🔗 Add the bot to your server\n"
                f"I found **{guild.name}**. Discord requires a server administrator to "
                f"[install this bot]({url}); an ordinary invite cannot add a bot.\n\n"
                "🛡️ Then complete any verification or role gate for the bot inside that server, "
                "grant access to community channels, and reply **done** here. "
                "The report and eligible advertisement will follow automatically.",
            )
            await store.log_activity(
                self.db, "partnership.install_required", guild_id=record["guild_id"],
                case_id=record["case_id"], decision="administrator installation required",
                destination_channel=str(guild.id), status="pending",
            )
            return
        await self.partnership_connected(record, target)

    def partnership_channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        member = guild.me
        if member is None or getattr(member, "pending", False):
            return []
        return [
            channel for channel in guild.text_channels
            if channel.permissions_for(member).view_channel
            and channel.permissions_for(member).read_message_history
            and not partnerships.GATE_CHANNEL.search(channel.name)
        ]

    async def partnership_connected(self, record: dict[str, Any], target: discord.Guild) -> None:
        visible = len(self.partnership_channels(target))
        await store.update_partnership(
            self.db, record["id"], invite_status="joined", bot_joined=True,
            joined_guild_id=target.id, joined_guild_name=target.name, server_name=target.name,
            permissions_status="granted" if visible else "missing", member_count=target.member_count,
        )
        await self.say(await self.ticket_channel_for(record), engine.PARTNERSHIP_CONNECTED)
        await store.log_activity(
            self.db, "partnership.join", guild_id=record["guild_id"], case_id=record["case_id"],
            decision=f"connected to {target.name}", destination_channel=str(target.id),
            detail=f"{visible} community channel(s) readable; waiting for done in the ticket",
        )

    async def leave_finished_guild(self, guild: discord.Guild) -> bool:
        """Keep the private bot out of a server whose partnership review already finished."""

        async with self.db.execute(
            "SELECT 1 FROM ai_partnerships WHERE guild_id = ? LIMIT 1", (guild.id,)
        ) as cursor:
            if await cursor.fetchone():
                return False  # never leave the home server
        async with self.db.execute(
            "SELECT id, case_id, guild_id FROM ai_partnerships WHERE joined_guild_id = ? "
            "AND invite_status = 'left' AND published_at IS NULL ORDER BY id DESC LIMIT 1",
            (guild.id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        try:
            await guild.leave()
        except discord.HTTPException:
            logger.warning("Could not leave guild %s after a finished application", guild.id, exc_info=True)
            return False
        await store.log_activity(
            self.db, "partnership.left_again", guild_id=row[2], case_id=row[1],
            decision="left server again", destination_channel=str(guild.id),
            detail="The bot was added to a server whose partnership review had already finished.",
        )
        return True

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        config = await self.config()
        if not config["enabled"] or not ai_settings.features(config)["partnership_automation"]:
            return
        if await self.leave_finished_guild(guild):
            return
        async with self.db.execute(
            "SELECT id FROM ai_partnerships WHERE joined_guild_id = ? AND bot_joined = 0 "
            "AND (published_at IS NULL OR invite_status = 'left')",
            (guild.id,),
        ) as cursor:
            ids = [row[0] for row in await cursor.fetchall()]
        for partnership_id in ids:
            record = await store.get_partnership(self.db, partnership_id)
            if record and record.get("published_at"):
                await store.update_partnership(
                    self.db, partnership_id, bot_joined=True, invite_status="joined",
                    permissions_status="granted", member_count=guild.member_count,
                )
                record = await store.get_partnership(self.db, partnership_id) or record
                await self.announce_partnership_stay(record, guild)
                await self.enqueue_ad_monitor(record)
            elif record:
                await self.partnership_connected(record, guild)

    async def run_partnership_analyse(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        partnership_id = ai_settings.as_int(payload.get("partnership_id"))
        record = await store.get_partnership(self.db, partnership_id)
        if not record:
            raise RuntimeError("Partnership application no longer exists.")
        if record.get("published_at") or record.get("publish_status") in {"paused", "rejected"}:
            return
        if not record.get("human_verified"):
            await store.update_partnership(self.db, partnership_id, analysis_status="awaiting_verification")
            return

        guild = self.bot.get_guild(record.get("joined_guild_id") or 0)
        if guild is None or not self.partnership_channels(guild):
            await store.update_partnership(
                self.db, partnership_id, human_verified=False, permissions_status="missing",
                analysis_status="awaiting_verification",
            )
            await self.say(await self.ticket_channel_for(record), "🛡️ I no longer have community-channel access. Please have your administrator restore it, then reply **done** here.")
            return
        await store.update_partnership(self.db, partnership_id, analysis_status="running")

        config = await self.config()
        if not ai_settings.features(config)["partnership_automation"]:
            await store.update_partnership(self.db, partnership_id, analysis_status="pending")
            return
        requirements = ai_settings.as_dict(config.get("partnership_requirements"))
        snapshot = await self.server_snapshot(guild, record)
        provider = AIProvider(config)
        try:
            result = await engine.analyse_server(provider, config, snapshot, requirements)
            status = "complete"
        except AIUnavailable as error:
            checklist = engine.evaluate_requirements(snapshot, requirements, {})
            result = {
                "findings": {"summary": str(error), "limitations": ["AI provider unavailable"], "confidence": 0},
                "requirements": checklist,
                "eligibility": engine.overall_eligibility(checklist),
                "error": str(error),
            }
            status = "degraded"

        recommendation = engine.recommendation(result["eligibility"], result["requirements"])
        await store.update_partnership(
            self.db,
            partnership_id,
            analysis_status=status,
            analysis=result,
            requirements=result["requirements"],
            eligibility=result["eligibility"],
            recommendation=recommendation,
            server_name=record.get("server_name") or guild.name,
        )
        ticket_channel = await self.ticket_channel_for(record)
        failed = [item["label"] for item in result["requirements"] if item["passed"] is False]
        current = await store.get_partnership(self.db, partnership_id)
        if current.get("publish_status") in {"paused", "rejected"}:
            await self.say(ticket_channel, "🛡️ Staff paused or rejected this application during the review; automatic publishing is stopped.")
            return
        await self.say(ticket_channel, partnerships.report(record, result, recommendation))
        # Nothing is published in the home server, and the bot does not leave the
        # applicant server, until our advertisement has been posted there.
        ad_wait = False
        if result["eligibility"] == "eligible":
            await store.update_partnership(
                self.db, partnership_id, publish_status="approved", ad_status="awaiting_ad"
            )
            await self.say(ticket_channel, await self.ad_instruction(record, config))
            ad_wait = bool(await self.enqueue_ad_check(record))
        routes = await store.list_routes(self.db, record["guild_id"])
        route, explanation = engine.choose_route(
            routes, department="Partnership", report_category="partnership", severity="normal"
        )
        # Reports belong in the ticket or a staff route, not the public ad channel.
        destination = (route or {}).get("destination_channel_id")
        role_ids = (route or {}).get("role_ids") or ai_settings.as_id_list(config.get("partnership_role_ids"))
        if destination:
            await jobs.enqueue(
                self.db,
                "notify_staff",
                {
                    "channel_id": destination,
                    "role_ids": role_ids,
                    "case_id": record["case_id"],
                    "content": (
                        f"### 📋 Partnership report — {record.get('server_name') or guild.name}\n"
                        f"Eligibility: **{result['eligibility'].replace('_', ' ')}**\n{recommendation}\n"
                        + (f"Failed: {', '.join(failed)}\n" if failed else "")
                        + f"-# {explanation}"
                    ),
                },
                idempotency_key=f"partnership:{partnership_id}:notify",
            )
        await store.log_activity(
            self.db,
            "partnership.analyse",
            guild_id=record["guild_id"],
            case_id=record["case_id"],
            decision=result["eligibility"],
            confidence=float((result.get("findings") or {}).get("confidence") or 0),
            destination_channel=str(destination or ""),
            destination_role=",".join(str(role_id) for role_id in role_ids),
            status="degraded" if status == "degraded" else "completed",
            detail=recommendation,
        )
        # An eligible review remains open until the applicant posts our ad. Failed
        # reviews can finish normally; eligible applicants keep the bot in-server.
        if not ad_wait:
            await self.enqueue_finish(record)

    # ------------------------------------------------------------------ advertisement proof
    async def enqueue_ad_check(
        self, record: dict[str, Any], *, delay_seconds: int = 0, token: str = ""
    ) -> int | None:
        """Queue one look for our advertisement in the applicant server."""

        suffix = token or f"auto{ai_settings.as_int(record.get('ad_checks'), default=0) + 1}"
        return await jobs.enqueue(
            self.db,
            "partnership_verify_ad",
            {"partnership_id": record["id"], "case_id": record.get("case_id")},
            idempotency_key=f"partnership:{record['id']}:ad:{suffix}",
            delay_seconds=delay_seconds,
        )

    async def enqueue_ad_monitor(self, record: dict[str, Any]) -> int | None:
        """Schedule a low-frequency exact-match check for a published partnership."""

        next_run = datetime.now(timezone.utc) + timedelta(seconds=partnerships.AD_MONITOR_INTERVAL_SECONDS)
        slot = int(next_run.timestamp() // partnerships.AD_MONITOR_INTERVAL_SECONDS)
        return await jobs.enqueue(
            self.db,
            "partnership_monitor_ad",
            {"partnership_id": record["id"], "case_id": record.get("case_id")},
            idempotency_key=f"partnership:{record['id']}:ad-monitor:{slot}",
            delay_seconds=partnerships.AD_MONITOR_INTERVAL_SECONDS,
        )

    def ad_source_channel(self, home: discord.Guild, config: dict[str, Any]) -> discord.TextChannel | None:
        """The home channel holding our advertisement for applicants to repost."""

        for key in ("partnership_ad_channel_id", "partnership_channel_id"):
            channel = home.get_channel(ai_settings.as_int(config.get(key)))
            if isinstance(channel, discord.TextChannel):
                return channel
        for channel in home.text_channels:
            name = "".join(char for char in channel.name.casefold() if char.isalnum())
            if name in {"ads", "advert", "adverts", "advertisement", "advertisements", "partnerad", "ourpartnerad"}:
                return channel
        return None

    def ad_text(self, message: discord.Message) -> str:
        parts = [message.content or ""]
        parts.extend(f"{embed.title or ''}\n{embed.description or ''}".strip() for embed in message.embeds)
        return "\n".join(part for part in parts if part).strip()

    async def home_advertisement(self, record: dict[str, Any], config: dict[str, Any]) -> str:
        """Fetch our own advertisement; applicant ads we published are skipped."""

        home = self.bot.get_guild(ai_settings.as_int(record.get("guild_id")))
        if home is None:
            return ""
        channel = self.ad_source_channel(home, config)
        if channel is None:
            return ""
        bot_id = getattr(getattr(self.bot, "user", None), "id", 0)
        try:
            for message in await channel.pins():
                text = self.ad_text(message)
                if len(partnerships.fingerprint(text)) >= partnerships.MIN_REFERENCE_WORDS:
                    return text
            async for message in channel.history(limit=100):
                if message.author.id == bot_id:
                    continue
                text = self.ad_text(message)
                if len(partnerships.fingerprint(text)) >= partnerships.MIN_REFERENCE_WORDS:
                    return text
        except discord.HTTPException:
            logger.warning("Could not read our advertisement in channel %s", channel.id)
        return ""

    async def find_applicant_ad(self, guild: discord.Guild, reference: str, marker: str):
        """Return the applicant message that reproduces our advertisement."""

        for channel in self.partnership_channels(guild)[:25]:
            try:
                async for message in channel.history(limit=60):
                    if message.author.bot:
                        continue
                    if partnerships.posted_ad(reference, self.ad_text(message), marker):
                        return message
            except discord.HTTPException:
                continue
        return None

    async def tracked_applicant_ad(self, guild: discord.Guild, record: dict[str, Any], reference: str, marker: str):
        """Check the exact saved post first, then search readable public channels."""

        verifiable = True
        channel = guild.get_channel(ai_settings.as_int(record.get("ad_source_channel_id")))
        message_id = ai_settings.as_int(record.get("ad_source_message_id"))
        if channel is not None and message_id:
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                pass
            except discord.Forbidden:
                verifiable = False
            except discord.HTTPException:
                verifiable = False
            else:
                if not message.author.bot and partnerships.posted_ad(reference, self.ad_text(message), marker):
                    return message, True
        found = await self.find_applicant_ad(guild, reference, marker)
        return found, verifiable

    def announcement_channel(self, guild: discord.Guild, preferred_channel_id: int = 0):
        """Choose a readable community channel where the bot can also post."""

        member = guild.me
        if member is None:
            return None
        candidates = self.partnership_channels(guild)
        preferred = guild.get_channel(preferred_channel_id) if preferred_channel_id else None
        if preferred in candidates and preferred.permissions_for(member).send_messages:
            return preferred
        return next((channel for channel in candidates if channel.permissions_for(member).send_messages), None)

    async def announce_partnership_stay(self, record: dict[str, Any], guild: discord.Guild) -> bool:
        """Announce why the bot remains, without treating unrelated ads as proof."""

        if record.get("ad_monitor_announced_at"):
            return True
        channel = self.announcement_channel(
            guild, ai_settings.as_int(record.get("ad_source_channel_id"))
        )
        content = (
            "### 🤝 Partnership active\n"
            "Our advertisement was found and the partnership is now published. This bot will remain in "
            "the server to monitor that **our required advertisement** stays visible. Other advertisements "
            "and ordinary channel messages do not count. Please keep the bot installed and the required ad posted."
        )
        if not await self.say(channel, content):
            return False
        await store.update_partnership(
            self.db, record["id"], ad_monitor_announced_at=ai_settings.utc_now()
        )
        await store.log_activity(
            self.db, "partnership.monitor_announcement", guild_id=record["guild_id"],
            case_id=record["case_id"], decision="stay announced",
            destination_channel=str(channel.id),
            detail="The bot will remain in the applicant server and monitor only the matching advertisement.",
        )
        return True

    async def ad_instruction(self, record: dict[str, Any], config: dict[str, Any]) -> str:
        ad = await self.home_advertisement(record, config)
        lines = [
            "### 📣 One last step: post our advertisement",
            "Please paste **our advertisement** into a public channel of your server so I can see it. "
            "I keep checking for it, and I neither leave your server nor close this ticket until it appears.",
        ]
        if ad:
            quoted = "\n".join(f"> {line}" for line in ad[:1200].splitlines() if line.strip())
            lines.append(f"\n**Our advertisement:**\n{quoted}")
        else:
            lines.append("Staff can pick the channel holding our advertisement under Partnership Automation.")
        return "\n".join(lines)

    async def run_partnership_verify_ad(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        partnership_id = ai_settings.as_int(payload.get("partnership_id"))
        record = await store.get_partnership(self.db, partnership_id)
        if not record:
            raise RuntimeError("Partnership application no longer exists.")
        if record.get("publish_status") in {"paused", "rejected"}:
            return
        if record.get("published_at") or record.get("publish_status") == "published":
            await self.run_partnership_monitor_ad(payload, job)
            return
        if record.get("ad_status") == "verified":
            # The check already passed but the publication never ran (a restart, say).
            await jobs.enqueue(
                self.db, "publish_advertisement",
                {"partnership_id": partnership_id, "case_id": record["case_id"]},
                idempotency_key=f"partnership:{partnership_id}:publish",
            )
            return
        config = await self.config()
        if not ai_settings.features(config)["partnership_automation"]:
            return
        guild = self.bot.get_guild(ai_settings.as_int(record.get("joined_guild_id")))
        ticket_channel = await self.ticket_channel_for(record)
        if guild is None:
            await store.update_partnership(self.db, partnership_id, ad_status="awaiting_access")
            await self.say(
                ticket_channel,
                "🛡️ I am no longer in your server, so I cannot see our advertisement there. "
                "Please have an administrator reinstall me, then reply **done** here.",
            )
            return

        home = self.bot.get_guild(ai_settings.as_int(record.get("guild_id")))
        reference = await self.home_advertisement(record, config)
        found = await self.find_applicant_ad(guild, reference, getattr(home, "name", ""))
        checks = ai_settings.as_int(record.get("ad_checks"), default=0) + 1
        if found is not None:
            await store.update_partnership(
                self.db, partnership_id,
                ad_status="verified", ad_verified_at=ai_settings.utc_now(),
                ad_source_message_id=found.id,
                ad_source_channel_id=getattr(getattr(found, "channel", None), "id", None),
                ad_checks=checks, publish_status="approved",
            )
            await self.say(
                ticket_channel,
                f"✅ I found our advertisement in **{guild.name}**. Publishing the partnership in our "
                "partnerships channel now.",
            )
            await store.log_activity(
                self.db, "partnership.ad_verified",
                guild_id=record["guild_id"], case_id=record["case_id"],
                decision="advertisement found in the applicant server",
                destination_channel=str(getattr(getattr(found, "channel", None), "id", "")),
                detail=f"message={found.id} after {checks} check(s)",
            )
            await jobs.enqueue(
                self.db, "publish_advertisement",
                {"partnership_id": partnership_id, "case_id": record["case_id"]},
                idempotency_key=f"partnership:{partnership_id}:publish",
            )
            return

        await store.update_partnership(self.db, partnership_id, ad_status="awaiting_ad", ad_checks=checks)
        if checks == 1 or checks % partnerships.AD_REMIND_EVERY == 0:
            await self.say(
                ticket_channel,
                "📣 I have not seen our advertisement in your server yet. Post it in a public channel and "
                "I will publish the partnership automatically — or reply here and I will check again.",
            )
        await self.enqueue_ad_check(
            record, delay_seconds=partnerships.AD_CHECK_INTERVAL_SECONDS, token=f"auto{checks + 1}"
        )

    async def run_partnership_monitor_ad(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        """Keep monitoring only the required ad after a partnership is published."""

        partnership_id = ai_settings.as_int(payload.get("partnership_id"))
        record = await store.get_partnership(self.db, partnership_id)
        if not record or not record.get("published_at"):
            return
        if record.get("publish_status") in {"paused", "rejected"}:
            return
        config = await self.config()
        if not ai_settings.features(config)["partnership_automation"]:
            return
        guild = self.bot.get_guild(ai_settings.as_int(record.get("joined_guild_id")))
        if guild is None or not self.partnership_channels(guild):
            await store.update_partnership(self.db, partnership_id, ad_status="awaiting_access")
            await self.enqueue_ad_monitor(record)
            return
        home = self.bot.get_guild(ai_settings.as_int(record.get("guild_id")))
        if home is None:
            return
        reference = await self.home_advertisement(record, config)
        found, verifiable = await self.tracked_applicant_ad(guild, record, reference, home.name)
        checks = ai_settings.as_int(record.get("ad_checks"), default=0) + 1
        if found is not None:
            was_missing = record.get("ad_status") == "missing"
            await store.update_partnership(
                self.db, partnership_id, ad_status="verified", ad_checks=checks,
                ad_source_message_id=found.id,
                ad_source_channel_id=getattr(getattr(found, "channel", None), "id", None),
            )
            if was_missing:
                await self.say(
                    getattr(found, "channel", None),
                    "✅ Our required advertisement is visible again. I will keep monitoring for the matching copy.",
                )
            await self.announce_partnership_stay(record, guild)
        elif verifiable:
            was_visible = record.get("ad_status") == "verified"
            await store.update_partnership(self.db, partnership_id, ad_status="missing", ad_checks=checks)
            if was_visible:
                channel = self.announcement_channel(
                    guild, ai_settings.as_int(record.get("ad_source_channel_id"))
                )
                await self.say(
                    channel,
                    "⚠️ I can no longer find our required partnership advertisement. Please repost the exact "
                    "advertisement from the partnership ticket. Other ads do not count; I will keep monitoring "
                    "for our copy.",
                )
                await store.log_activity(
                    self.db, "partnership.ad_missing", guild_id=record["guild_id"],
                    case_id=record["case_id"], decision="required ad not visible",
                    status="pending", detail=f"monitor check {checks}",
                )
        await self.enqueue_ad_monitor(await store.get_partnership(self.db, partnership_id) or record)

    async def run_publish_advertisement(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        partnership_id = ai_settings.as_int(payload.get("partnership_id"))
        record = await store.get_partnership(self.db, partnership_id)
        if not record:
            raise RuntimeError("Partnership application no longer exists.")
        if record.get("publish_status") in {"paused", "rejected"}:
            return
        if record.get("ad_status") != "verified":
            # Waiting for the applicant to post our advertisement is not a failure:
            # keep the application open and look again.
            await store.update_partnership(
                self.db, partnership_id, publish_status="approved", ad_status="awaiting_ad"
            )
            await self.enqueue_ad_check(record)
            return
        if record.get("published_message_id") or record.get("published_at"):
            # A retried job closes the review it may have missed before a restart.
            await self.enqueue_finish(record)
            return
        config = await self.config()
        if not ai_settings.features(config)["partnership_automation"]:
            return
        allowed, reason = engine.can_publish(record)
        if not allowed:
            await store.update_partnership(self.db, partnership_id, publish_status="blocked")
            await store.log_activity(
                self.db,
                "partnership.publish",
                guild_id=record["guild_id"],
                case_id=record["case_id"],
                decision="blocked",
                status="failed",
                detail=reason,
            )
            raise RuntimeError(reason)

        target = self.bot.get_guild(record.get("joined_guild_id") or 0)
        if target is None or not self.partnership_channels(target):
            raise RuntimeError("Applicant server access was lost. Restore access and reply done in the ticket.")
        minimum = ai_settings.MIN_PARTNERSHIP_MEMBERS
        if target.member_count is None or target.member_count < minimum:
            raise RuntimeError("The member requirement can no longer be verified. Advertisement not published.")
        channel = await self.partnership_destination(record, config)
        destination = channel.id
        role_ids = ai_settings.as_id_list(config.get("partnership_role_ids"))
        advertisement = record.get("advertisement") or ""
        link = f"https://discord.gg/{record['invite_code']}"
        if not engine.invite_in_text(advertisement):
            advertisement += f"\n\n🔗 {link}"
        # An embed accommodates the full applicant advertisement without the
        # 2,000-character plain-message failure, with mentions disabled.
        embed = discord.Embed(
            title=f"🤝 Partnership — {record.get('server_name') or 'Server'}"[:256],
            description=advertisement[:4096], color=assistance.GOLD_ACCENT, url=link,
        )
        embed.set_footer(text="Automated review • 15-member minimum met")
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await store.update_partnership(
            self.db,
            partnership_id,
            publish_status="published",
            published_message_id=message.id,
            published_at=ai_settings.utc_now(),
        )
        record = await store.get_partnership(self.db, partnership_id) or record
        if role_ids:
            await jobs.enqueue(
                self.db,
                "notify_staff",
                {
                    "channel_id": destination,
                    "role_ids": role_ids,
                    "case_id": record["case_id"],
                    "content": f"📣 Partnership advertisement published automatically for {record.get('server_name') or 'a server'}.",
                },
                idempotency_key=f"partnership:{partnership_id}:published",
            )
        ticket_channel = await self.ticket_channel_for(record)
        await self.say(ticket_channel, f"### ✅ Partnership published\nYour advertisement is now in <#{destination}>. [View the post]({message.jump_url}). Thank you for partnering with us!")
        if target is not None:
            await self.announce_partnership_stay(record, target)
            await self.enqueue_ad_monitor(record)
        await store.log_activity(
            self.db,
            "partnership.publish",
            guild_id=record["guild_id"],
            case_id=record["case_id"],
            decision="published",
            confidence=1.0,
            destination_channel=str(destination),
            destination_role=",".join(str(role_id) for role_id in role_ids),
            detail=f"message={message.id}",
        )
        await self.enqueue_finish(record)

    # ------------------------------------------------------------------ review teardown
    async def enqueue_finish(self, record: dict[str, Any]) -> None:
        await jobs.enqueue(
            self.db,
            "partnership_finish",
            {"partnership_id": record["id"], "case_id": record.get("case_id")},
            idempotency_key=f"partnership:{record['id']}:finish",
        )

    async def run_partnership_finish(self, payload: dict[str, Any], job: dict[str, Any]) -> None:
        partnership_id = ai_settings.as_int(payload.get("partnership_id"))
        record = await store.get_partnership(self.db, partnership_id)
        if not record:
            return
        if record.get("publish_status") in {"paused", "rejected"}:
            return
        await self.finish_partnership(record)

    async def leave_applicant_server(self, record: dict[str, Any]) -> None:
        """Remove the private bot from the applicant server once the review is over."""

        guild_id = ai_settings.as_int(record.get("joined_guild_id"))
        if not guild_id or guild_id == ai_settings.as_int(record.get("guild_id")):
            return
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            try:
                await guild.leave()
            except discord.NotFound:
                pass  # already gone
            except discord.HTTPException as error:
                raise RuntimeError(f"I could not leave the applicant server: {error}") from error
        await store.update_partnership(
            self.db, record["id"], bot_joined=False, invite_status="left", permissions_status="revoked",
        )
        await store.log_activity(
            self.db,
            "partnership.leave",
            guild_id=record["guild_id"],
            case_id=record["case_id"],
            decision="left applicant server",
            destination_channel=str(guild_id),
            detail="Review finished; the bot left the applicant server and will not join it again.",
        )

    async def finish_partnership(self, record: dict[str, Any]) -> None:
        """Keep accepted partners in-server; close the review ticket without deleting it."""

        accepted = bool(record.get("published_at"))
        if accepted:
            guild = self.bot.get_guild(ai_settings.as_int(record.get("joined_guild_id")))
            if guild is not None:
                await self.announce_partnership_stay(record, guild)
                await self.enqueue_ad_monitor(record)
        elif record.get("invite_status") != "left":
            await self.leave_applicant_server(record)
            record = await store.get_partnership(self.db, record["id"]) or record

        case = await store.get_case(self.db, record.get("case_id") or 0)
        if not case or not case.get("ticket_channel_id"):
            return
        channel = self.bot.get_channel(case["ticket_channel_id"])
        home = self.bot.get_guild(record["guild_id"])
        if channel is None or home is None:
            return
        closed, detail = await close_and_delete_ticket(
            self.bot,
            home,
            channel,
            {
                "creator_id": case.get("requester_id"),
                "ticket_number": case.get("ticket_number") or 0,
                "category": case.get("department") or "Partnership Support",
                "subject": case.get("subject") or "",
            },
            actor="Automated partnership review",
            delete_channel=False,
        )
        if not closed:
            raise RuntimeError(detail)
        await store.update_case(self.db, case["id"], status="closed")
        await store.add_case_event(
            self.db, case["id"], "ai", "partnership.finished",
            (
                "Partnership published; the bot stays to monitor its advertisement. "
                "The ticket was closed without deleting the channel."
                if accepted else "Review finished; the bot left the applicant server and closed the ticket without deleting the channel."
            ),
        )
        await store.log_activity(
            self.db,
            "partnership.finish",
            guild_id=record["guild_id"],
            case_id=record["case_id"],
            ticket_channel_id=channel.id,
            decision="ticket closed",
            detail=detail,
        )

    async def partnership_destination(self, record: dict[str, Any], config: dict[str, Any]) -> discord.TextChannel:
        home = self.bot.get_guild(record["guild_id"])
        if home is None:
            raise RuntimeError("The home server is unavailable; no advertisement was posted.")
        configured = ai_settings.as_int(config.get("partnership_channel_id"))
        if configured:
            channel = home.get_channel(configured)
            candidates = [channel] if isinstance(channel, discord.TextChannel) else []
        else:
            candidates = [
                channel for channel in home.text_channels
                if "".join(char for char in channel.name.casefold() if char.isalnum()) in {"partnership", "partnerships"}
            ]
        if len(candidates) != 1:
            raise RuntimeError("I could not identify one partnerships text channel. Staff must select the publishing channel under Partnership Automation.")
        channel = candidates[0]
        permissions = channel.permissions_for(home.me) if home.me else None
        if not permissions or not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            raise RuntimeError("I need View Channels, Send Messages and Embed Links in the home partnerships channel.")
        return channel

    async def server_snapshot(self, guild: discord.Guild, record: dict[str, Any]) -> dict[str, Any]:
        """Facts the bot can actually see in the applicant server."""

        accessible = self.partnership_channels(guild)
        sample_lines = []
        for channel in accessible[:3]:
            try:
                async for message in channel.history(limit=25):
                    if message.content:
                        sample_lines.append(f"#{channel.name}: {message.content[:200]}")
                    if len(sample_lines) >= 20:
                        break
            except discord.HTTPException:
                continue
            if len(sample_lines) >= 20:
                break
        return {
            "name": guild.name,
            "description": guild.description or "",
            "member_count": guild.member_count,
            "channel_count": len(accessible),
            "channels": [
                {"name": channel.name, "type_label": "Text channel"} for channel in accessible[:60]
            ],
            "message_sample": "\n".join(sample_lines),
            "advertisement": record.get("advertisement") or "",
            "invite_code": record.get("invite_code") or "",
        }

    async def ticket_channel_for(self, record: dict[str, Any]) -> discord.abc.Messageable | None:
        case = await store.get_case(self.db, record.get("case_id") or 0)
        if not case or not case.get("ticket_channel_id"):
            return None
        return self.bot.get_channel(case["ticket_channel_id"])

    async def say(self, channel, content: str) -> bool:
        if channel is None or not content:
            return False
        try:
            for part in messages.chunks(messages.render(content, getattr(channel, "guild", None))):
                await channel.send(content=part, allowed_mentions=discord.AllowedMentions.none())
            return True
        except discord.HTTPException:
            logger.warning("Could not deliver an AI message to channel %s", getattr(channel, "id", None))
            return False

    # ------------------------------------------------------------------ ticket conversation
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.id in self.seen_messages:
            return
        self.seen_messages.append(message.id)
        try:
            await self.handle_ticket_message(message)
        except Exception:
            logger.exception("AI ticket handling failed for channel %s", getattr(message.channel, "id", None))

    async def handle_ticket_message(self, message: discord.Message) -> None:
        if not isinstance(message.channel, discord.TextChannel):
            return
        config = await self.config()
        if not ai_settings.as_bool(config.get("enabled")):
            return
        features = ai_settings.features(config)
        if not await is_ticket_channel(self.bot, message.channel):
            return
        info = await get_ticket_info(self.bot, message.channel.id)
        if not info or int(info.get("creator_id") or 0) != message.author.id:
            return
        category = str(info.get("category") or "")
        if message.channel.category_id != PARTNERSHIP_CATEGORY_ID and category != "Partnership Support":
            # General and Management tickets are staff-only; the AI only works
            # inside Partnership Support.
            return
        if not features["partnership_automation"]:
            return
        async with self.lock_for(message.channel.id):
            await self.handle_partnership_message(message, info, config)

    def conversation(self, channel_id: int) -> list[dict[str, str]]:
        history = self.conversations.setdefault(channel_id, [])
        return history

    def remember(self, channel_id: int, role: str, content: str) -> str:
        history = self.conversation(channel_id)
        history.append({"role": role, "content": content[:1500]})
        del history[:-MAX_CONVERSATION_MESSAGES]
        return "\n".join(f"{item['role']}: {item['content']}" for item in history)

    async def case_for_ticket(self, message: discord.Message, info: dict[str, Any], kind: str) -> dict[str, Any]:
        case = await store.find_open_case(self.db, message.channel.id)
        if case:
            return case
        # One case per ticket channel: a case closed by an escalation is reopened
        # instead of colliding with the unique ticket key on the next message.
        existing = await store.find_case_by_key(self.db, f"ticket:{message.channel.id}")
        if existing:
            await store.update_case(self.db, existing["id"], status="open")
            return await store.get_case(self.db, existing["id"])
        case_id = await store.create_case(
            self.db,
            message.guild.id,
            kind=kind,
            ticket_channel_id=message.channel.id,
            ticket_number=info.get("ticket_number"),
            requester_id=message.author.id,
            subject=str(info.get("subject") or message.content or "")[:300],
            idempotency_key=f"ticket:{message.channel.id}",
        )
        await store.add_case_event(self.db, case_id, "member", "ticket.opened", str(info.get("category") or ""))
        return await store.get_case(self.db, case_id)

    async def handle_partnership_message(
        self, message: discord.Message, info: dict[str, Any], config: dict[str, Any]
    ) -> None:
        case = await self.case_for_ticket(message, info, "partnership")
        record = await store.find_partnership_by_case(self.db, case["id"])
        if record is None:
            partnership_id = await store.create_partnership(
                self.db, message.guild.id, case_id=case["id"], applicant_id=message.author.id
            )
            record = await store.get_partnership(self.db, partnership_id)
        await store.add_case_event(self.db, case["id"], "member", "partnership.message", message.content[:300])

        if record.get("published_at"):
            await self.say(message.channel, "✅ Your partnership advertisement is already published. Thank you for partnering with us!")
            return
        if record.get("publish_status") in {"paused", "rejected"}:
            await self.say(message.channel, "🛡️ Staff have paused or rejected this application. Please wait for their follow-up.")
            return
        if record.get("analysis_status") in {"queued", "running"}:
            await self.say(message.channel, "🔎 Your server review is in progress. I will post the report here automatically.")
            return
        if record.get("analysis_status") == "complete":
            if record.get("publish_status") == "approved" or record.get("ad_status") in {"awaiting_ad", "awaiting_access"}:
                # A reply is also a prompt to look for our advertisement again.
                await self.enqueue_ad_check(record, token=f"reply{message.id}")
                await self.say(
                    message.channel,
                    "🔎 Checking your server for our advertisement again. I publish the partnership in our "
                    "partnerships channel as soon as I can see it.",
                )
            else:
                await self.say(message.channel, record.get("recommendation") or "📋 Your review is complete; the report is above.")
            return
        if partnerships.is_done(message.content) and record.get("invite_code") and record.get("advertisement"):
            await self.confirm_partnership_access(message, record)
            return

        # Detect the ad and invite directly. No AI field extraction or dashboard
        # registration is needed, and a new ticket starts a fresh application.
        text = message.content.strip()
        updates: dict[str, Any] = {}
        ad = partnerships.advertisement(text)
        if not record.get("advertisement") and ad:
            updates["advertisement"] = ad
        invite = engine.invite_in_text(text)
        if invite and invite != record.get("invite_code"):
            updates.update(invite_code=invite, invite_status="pending")
        if updates:
            await store.update_partnership(self.db, record["id"], **updates)
            record = await store.get_partnership(self.db, record["id"])

        missing = []
        if len(record.get("advertisement") or "") < 20:
            missing.append("your server advertisement")
        if not record.get("invite_code"):
            missing.append("a valid Discord server invitation")
        if missing:
            await self.say(message.channel,
                content=(
                    f"🤝 Please send {' and '.join(missing)} here so I can continue automatically. "
                    f"{engine.PARTNERSHIP_WELCOME if len(missing) == 2 else ''}".strip()
                )[:1900]
            )
            await store.log_activity(
                self.db,
                "partnership.collect",
                guild_id=message.guild.id,
                case_id=case["id"],
                user_id=message.author.id,
                ticket_channel_id=message.channel.id,
                decision="awaiting applicant",
                detail="missing: " + ", ".join(missing),
            )
            return

        await store.update_case(
            self.db,
            case["id"],
            subject=record.get("server_name") or case.get("subject", ""),
            department="Partnership",
            summary=f"Partnership application for {record.get('server_name') or 'a server'}",
        )
        if record.get("bot_joined"):
            await self.say(message.channel, engine.PARTNERSHIP_CONNECTED)
            return
        queued = await jobs.enqueue(
            self.db,
            "partnership_join",
            {"partnership_id": record["id"], "case_id": case["id"]},
            idempotency_key=f"partnership:{record['id']}:join:{record['invite_code']}:{message.id}",
        )
        if queued:
            await self.say(message.channel, "🔗 Advertisement and invite received. I am checking the server connection now.")
        await store.log_activity(
            self.db,
            "partnership.collect",
            guild_id=message.guild.id,
            case_id=case["id"],
            user_id=message.author.id,
            ticket_channel_id=message.channel.id,
            decision="submission complete",
            detail=f"invite={record.get('invite_code')} queued={bool(queued)}",
        )

    async def confirm_partnership_access(self, message: discord.Message, record: dict[str, Any]) -> None:
        target = self.bot.get_guild(record.get("joined_guild_id") or 0)
        if target is None:
            await jobs.enqueue(
                self.db, "partnership_join", {"partnership_id": record["id"], "case_id": record["case_id"]},
                idempotency_key=f"partnership:{record['id']}:connection:{message.id}",
            )
            await self.say(message.channel, "🔗 I will check whether the administrator has installed me in the applicant server.")
            return
        if not self.partnership_channels(target):
            await store.update_partnership(
                self.db, record["id"], bot_joined=True, invite_status="joined",
                human_verified=False, permissions_status="missing", analysis_status="awaiting_verification",
            )
            await self.say(
                message.channel,
                "### 🛡️ Server access is still restricted\n"
                "An administrator of your server must verify **this bot**, assign any required role, "
                "and allow **View Channels** and **Read Message History** in community channels, plus "
                "**Send Messages** where the advertisement will be posted. "
                "Seeing only a welcome, rules or verification lobby is not enough. Reply **done** again once that is fixed.",
            )
            return
        await store.update_partnership(
            self.db, record["id"], bot_joined=True, invite_status="joined",
            human_verified=True, verified_by=f"ticket:{message.author.id}",
            verification_note="Applicant replied done; bot membership and readable community channels checked.",
            permissions_status="granted", analysis_status="queued",
        )
        await jobs.enqueue(
            self.db, "partnership_analyse", {"partnership_id": record["id"], "case_id": record["case_id"]},
            idempotency_key=f"partnership:{record['id']}:analyse:{message.id}",
        )
        await store.add_case_event(self.db, record["case_id"], "member", "partnership.access_confirmed", str(message.author.id))
        await store.log_activity(
            self.db, "partnership.access_confirmed", guild_id=record["guild_id"], case_id=record["case_id"],
            user_id=message.author.id, ticket_channel_id=message.channel.id,
            decision="done received; review queued", detail="Bot membership and channel permissions checked.",
        )
        await self.say(message.channel, "🔎 Access confirmed. I am checking whether your server has at least 15 members; your report will appear here automatically.")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.TextChannel):
            return
        # The channel-create event fires before create_ticket_channel saves its
        # ticket row. The configured category IDs can also be absent/stale, so
        # recognize the category from the persisted ticket label instead.
        name = str(getattr(channel, "name", "")).casefold()
        suffix = name.rsplit("-", 1)[-1]
        looks_like_partnership_ticket = (
            "partnership-support" in name and len(suffix) == 4 and suffix.isdigit()
        )
        if channel.category_id != PARTNERSHIP_CATEGORY_ID and not looks_like_partnership_ticket:
            return
        config = await self.config()
        if not ai_settings.as_bool(config.get("enabled")) or not ai_settings.features(config)["partnership_automation"]:
            return
        info = None
        for attempt in range(20):
            info = await get_ticket_info(self.bot, channel.id)
            if info:
                break
            if attempt < 19:
                await asyncio.sleep(0.5)
        if (
            not info
            or info.get("status") != "open"
            or str(info.get("category") or "").strip().casefold() != "partnership support"
        ):
            return
        try:
            await self.say(channel, engine.PARTNERSHIP_WELCOME)
            await store.log_activity(
                self.db,
                "partnership.welcome",
                guild_id=channel.guild.id,
                ticket_channel_id=channel.id,
                decision="welcome sent",
            )
        except Exception:
            logger.exception("Could not send the partnership welcome to %s", channel.id)

    # ------------------------------------------------------------------ staff commands
    def member_allowed(self, member: discord.Member, role_ids: list[int]) -> bool:
        if member.guild_permissions.administrator:
            return True
        return bool({role.id for role in member.roles} & set(role_ids))

    @app_commands.command(name="ai-status", description="Show the AI Control System status for this server")
    @app_commands.guild_only()
    async def ai_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        config = await self.config()
        permissions = ai_settings.permissions(config)
        if not self.member_allowed(interaction.user, permissions["control_role_ids"]):
            await interaction.followup.send(
                "🛡️ You are not allowed to use AI controls. An administrator can grant access under AI Permissions.",
                ephemeral=True,
            )
            return
        stats = await store.analytics(self.db, interaction.guild_id)
        state = "enabled" if ai_settings.as_bool(config.get("enabled")) else "disabled"
        provider_state = "connected" if ai_settings.key_configured() else "no API key (degraded, human escalation)"
        lines = [
            f"📋 **AI Control System** — {state}",
            f"Provider: {config.get('provider')} · {config.get('model')} · {provider_state}",
            f"Cases: {stats['total_requests']} · auto-answered {stats['auto_resolved']} · escalated {stats['forwarded']}",
            f"Knowledge: {stats['knowledge']['approved']} approved, {stats['knowledge']['pending']} pending review",
            f"Staff routing rules: {stats['routes']}",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="ai-case", description="Show one AI case (sensitive)")
    @app_commands.guild_only()
    async def ai_case(self, interaction: discord.Interaction, case_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        config = await self.config()
        permissions = ai_settings.permissions(config)
        allowed_roles = list({*permissions["control_role_ids"], *permissions["sensitive_role_ids"]})
        if not self.member_allowed(interaction.user, allowed_roles):
            await interaction.followup.send(
                "🛡️ This case is restricted to authorised staff. An administrator can grant access under AI Permissions.",
                ephemeral=True,
            )
            return
        case = await store.get_case(self.db, case_id)
        if not case:
            await interaction.followup.send("⚠️ No case with that ID exists.", ephemeral=True)
            return
        events = await store.case_events(self.db, case_id)
        timeline = "\n".join(f"`{event['created_at']}` {event['actor']}: {event['action']}" for event in events[-8:])
        await interaction.followup.send(
            (
                f"📋 **Case #{case['id']}** · {case['kind']} · {case['status']}\n"
                f"Department: {case['department'] or '—'} · severity: {case['severity']} · confidence: {case['confidence']}\n"
                f"Review: {case['human_review']}\n"
                f"Summary: {case['summary'][:900] or '—'}\n"
                f"{timeline}"
            )[:1900],
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIControl(bot))
