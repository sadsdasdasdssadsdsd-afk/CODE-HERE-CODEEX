"""Applies dashboard commands inside the running bot process.

The dashboard and the bot are separate processes that share the bot's SQLite
database. The dashboard enqueues commands (presence changes, ticket closes,
panel republishes) and reads the live facts this bridge publishes back.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from types import SimpleNamespace

from utils import assistance
from utils import dashboard_store as store
from utils.emojis import registry as emoji_registry
from utils.presence import build_activity, describe, resolve_status

logger = logging.getLogger("discord")

COMMAND_INTERVAL = 2.0
STATE_INTERVAL = 10.0
IDLE_ROTATION_INTERVAL = 10.0


class DashboardBridge:
    """Polls the dashboard command queue and publishes live bot state."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._tasks: list[asyncio.Task] = []
        self._wake = asyncio.Event()
        self._rotation_index = 0
        self._last_activity: str | None = None

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._command_loop(), name="dashboard-commands"),
            asyncio.create_task(self._state_loop(), name="dashboard-state"),
            asyncio.create_task(self._rotation_loop(), name="dashboard-rotation"),
        ]
        logger.info("Dashboard bridge started")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def note_activity(self, message: str) -> None:
        self._last_activity = f"{store.utc_now()} {message}"
        await store.log_activity(self.bot.db, "bot", message)

    # ------------------------------------------------------------------ config
    async def refresh_config(self) -> dict[str, str]:
        """Re-read dashboard settings into the modules that render Discord views."""

        settings = await store.get_settings(self.bot.db)
        assistance.refresh_from_settings(settings)
        return settings

    # ------------------------------------------------------------------ commands
    async def _command_loop(self) -> None:
        while True:
            try:
                for command in await store.claim_commands(self.bot.db):
                    ok, result = await self._handle_command(command)
                    await store.finish_command(self.bot.db, command["id"], ok, result)
                    if not ok:
                        logger.warning("Dashboard command %s failed: %s", command["action"], result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Dashboard command loop error")
            await asyncio.sleep(COMMAND_INTERVAL)

    async def _handle_command(self, command: dict) -> tuple[bool, str]:
        action = command["action"]
        payload = command.get("payload") or {}

        if action == "presence":
            activity = payload.get("activity", "playing")
            text = payload.get("text", "")
            status = payload.get("status", "online")
            if not await self.apply_presence(activity, text, status):
                return False, "Bot is not connected to Discord"
            await self.note_activity(f"Presence updated to {describe(activity, text)}")
            self._wake.set()
            return True, f"Presence set to {describe(activity, text)}"

        if action == "reload_settings":
            await self.refresh_config()
            self._wake.set()
            await self.note_activity("Dashboard configuration reloaded")
            return True, "Configuration reloaded"

        if action == "republish_panel":
            return await self._republish_panel()

        if action == "close_ticket":
            return await self._close_ticket(payload)

        return False, f"Unknown dashboard action {action!r}"

    async def _republish_panel(self) -> tuple[bool, str]:
        from views.assistance_panel import publish_assistance_panel

        await self.refresh_config()
        try:
            published = await publish_assistance_panel(self.bot)
        except Exception as error:
            logger.exception("Republishing the assistance panel failed")
            return False, f"Panel republish failed: {error}"
        if not published:
            return False, "Panel channel is not configured or not reachable"
        await self.note_activity("Assistance panel republished")
        return True, "Assistance panel republished"

    async def _close_ticket(self, payload: dict) -> tuple[bool, str]:
        from views.ticket_views import close_ticket_channel

        ticket = await store.get_ticket(self.bot.db, int(payload.get("ticket_id") or 0))
        if ticket is None:
            return False, "Ticket not found"
        if ticket.get("status") != "open":
            return False, "Ticket is already closed"

        guild = self.bot.get_guild(int(ticket.get("guild_id") or 0))
        channel = guild.get_channel(ticket["channel_id"]) if guild else None
        if channel is None:
            async with self.bot.db.cursor() as cursor:
                await cursor.execute(
                    "UPDATE ticket_instances SET status = 'closed', closed_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'open'",
                    (ticket["id"],),
                )
            await self.bot.db.commit()
            await self.note_activity(f"Ticket #{ticket['ticket_number']:04d} closed from the dashboard (channel missing)")
            return True, "Ticket closed - its channel no longer exists"

        closed_by = str(payload.get("closed_by") or "Dashboard administrator")
        # close_ticket_channel only reads channel/guild/user off the interaction.
        operator = SimpleNamespace(
            channel=channel,
            guild=guild,
            user=SimpleNamespace(id=int(payload.get("closed_by_id") or 0), display_name=closed_by),
        )
        ok, message = await close_ticket_channel(self.bot, operator, ticket)
        if ok:
            await self.note_activity(f"Ticket #{ticket['ticket_number']:04d} closed by {closed_by}")
        return ok, message

    # ------------------------------------------------------------------ presence
    async def apply_presence(self, activity: str, text: str, status: str) -> bool:
        if self.bot.user is None:
            return False
        await self.bot.change_presence(activity=build_activity(activity, text), status=resolve_status(status))
        await store.write_bot_state(
            self.bot.db,
            {
                "activity": describe(activity, text),
                "activity_type": activity,
                "activity_text": text,
                "presence_status": status,
                "presence_updated_at": store.utc_now(),
            },
        )
        return True

    async def _rotation_loop(self) -> None:
        while True:
            try:
                settings = await store.get_settings(self.bot.db)
                rotation_enabled = settings.get("rotation_enabled") == "1"
                statuses = [status for status in await store.list_statuses(self.bot.db) if status["enabled"]]
                if not rotation_enabled or not statuses:
                    await self._sleep_or_wake(IDLE_ROTATION_INTERVAL)
                    continue

                entry = statuses[self._rotation_index % len(statuses)]
                self._rotation_index += 1
                await self.apply_presence(
                    entry["activity"],
                    entry["text"],
                    settings.get("presence_status", "online"),
                )
                await self._sleep_or_wake(max(30, int(entry["duration_seconds"])))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Status rotation error")
                await self._sleep_or_wake(IDLE_ROTATION_INTERVAL)

    async def _sleep_or_wake(self, seconds: float) -> None:
        """Sleep, but wake immediately when the dashboard changed something."""

        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------ state
    async def _state_loop(self) -> None:
        while True:
            try:
                await self.publish_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Publishing dashboard state failed")
            await asyncio.sleep(STATE_INTERVAL)

    async def publish_state(self) -> None:
        bot = self.bot
        guild_id = await store.active_guild_id(bot.db)
        guild = bot.get_guild(guild_id) if guild_id else None
        if guild is None and bot.guilds:
            guild = bot.guilds[0]

        started_at = getattr(bot, "start_time", None)
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        activity = bot.activity
        activity_type = getattr(getattr(activity, "type", None), "name", None) or "playing"
        activity_text = getattr(activity, "name", None) or ""
        if activity_text.lower().startswith(activity_type):
            activity_text = activity_text[len(activity_type) :].strip()

        state = {
            "bot_user": str(bot.user) if bot.user else None,
            "bot_id": bot.user.id if bot.user else None,
            "ready": bot.is_ready(),
            "latency_ms": round(bot.latency * 1000) if bot.latency else None,
            "guild_count": len(bot.guilds),
            "guild_id": guild.id if guild else None,
            "guild_name": guild.name if guild else None,
            "guild_icon": guild.icon.replace(size=128, format="png").url if guild and guild.icon else None,
            "member_count": guild.member_count if guild else None,
            "channel_count": len(guild.channels) if guild else None,
            "role_count": len(guild.roles) if guild else None,
            "started_at": started_at.isoformat(timespec="seconds") if started_at else None,
            "last_activity": self._last_activity,
            "presence_status": str(getattr(bot, "status", "online")),
            "emoji_synced": emoji_registry.summary(guild)["synced"],
        }
        if activity_text:
            # Only report what Discord is really showing; never blank out the
            # presence the dashboard just applied.
            state["activity"] = describe(activity_type, activity_text)
            state["activity_type"] = activity_type
            state["activity_text"] = activity_text
        await store.write_bot_state(bot.db, state)
