"""Ticket queries and concurrency-safe ticket creation."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any, Optional

import discord

from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")

_creation_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def is_ticket_channel(bot, channel, *, include_closed: bool = False) -> bool:
    if not channel or not hasattr(channel, "id") or not getattr(bot, "db", None):
        return False
    try:
        status_clause = "status != 'deleted'" if include_closed else "status = 'open'"
        async with bot.db.execute(
            f"SELECT 1 FROM ticket_instances WHERE channel_id = ? AND {status_clause}",
            (channel.id,),
        ) as cursor:
            return await cursor.fetchone() is not None
    except Exception:
        logger.exception("Failed to check ticket channel %s", getattr(channel, "id", None))
        return False


async def get_ticket_creator(bot, channel_id: int) -> Optional[int]:
    try:
        async with bot.db.execute(
            "SELECT creator_id FROM ticket_instances WHERE channel_id = ?",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None
    except Exception:
        logger.exception("Failed to get creator for ticket channel %s", channel_id)
        return None


async def get_ticket_creator_member(bot, guild, channel_id: int):
    creator_id = await get_ticket_creator(bot, channel_id)
    if not creator_id:
        return None

    member = guild.get_member(creator_id)
    if member:
        return member

    user = bot.get_user(creator_id)
    if user:
        return user

    try:
        return await bot.fetch_user(creator_id)
    except discord.NotFound:
        logger.warning("Discord user %s no longer exists", creator_id)
    except discord.HTTPException:
        logger.exception("Failed to fetch Discord user %s", creator_id)
    return None


async def get_ticket_info(bot, channel_id: int) -> Optional[dict[str, Any]]:
    try:
        async with bot.db.execute(
            """
            SELECT creator_id, ticket_number, category, subject, description,
                   status, created_at, closed_at, claimed_by
            FROM ticket_instances
            WHERE channel_id = ?
            """,
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        keys = (
            "creator_id",
            "ticket_number",
            "category",
            "subject",
            "description",
            "status",
            "created_at",
            "closed_at",
            "claimed_by",
        )
        return dict(zip(keys, row))
    except Exception:
        logger.exception("Failed to get ticket info for channel %s", channel_id)
        return None


async def get_user_tickets(bot, guild_id: int, user_id: int) -> list[dict[str, Any]]:
    try:
        async with bot.db.execute(
            """
            SELECT channel_id, category, subject, status, ticket_number, created_at
            FROM ticket_instances
            WHERE guild_id = ? AND creator_id = ?
            ORDER BY created_at DESC
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        keys = ("channel_id", "category", "subject", "status", "ticket_number", "created_at")
        return [dict(zip(keys, row)) for row in rows]
    except Exception:
        logger.exception("Failed to list tickets for user %s in guild %s", user_id, guild_id)
        return []


async def get_user_open_tickets(bot, guild_id: int, user_id: int) -> list[dict[str, Any]]:
    return [ticket for ticket in await get_user_tickets(bot, guild_id, user_id) if ticket["status"] == "open"]


async def get_guild_ticket_stats(bot, guild_id: int) -> dict[str, int]:
    try:
        async with bot.db.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END)
            FROM ticket_instances
            WHERE guild_id = ?
            """,
            (guild_id,),
        ) as cursor:
            total, open_count, closed_count = await cursor.fetchone()
        async with bot.db.execute(
            "SELECT COUNT(*) FROM ticket_categories WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            categories = (await cursor.fetchone())[0]
        return {
            "total": total or 0,
            "open": open_count or 0,
            "closed": closed_count or 0,
            "categories": categories or 0,
        }
    except Exception:
        logger.exception("Failed to get ticket statistics for guild %s", guild_id)
        return {"total": 0, "open": 0, "closed": 0, "categories": 0}


async def get_ticket_log_channel(bot, guild_id: int):
    try:
        async with bot.db.execute(
            "SELECT log_channel_id FROM tickets WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return bot.get_channel(row[0]) if row and row[0] else None
    except Exception:
        logger.exception("Failed to get ticket log channel for guild %s", guild_id)
        return None


def _target_category(guild, category: str, configured_category):
    """The dropdown choice's own hardcoded category channel, else the configured one."""

    from utils import assistance

    channel_id = assistance.category_channel_id(category)
    if not channel_id:
        return configured_category
    return guild.get_channel(channel_id) or configured_category


def _channel_component(value: str, *, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:24] or fallback


async def create_ticket_channel(
    bot,
    guild: discord.Guild,
    user: discord.Member,
    category_channel,
    category: str,
    reason: str,
    additional_info: str = "",
) -> tuple[bool, str]:
    """Create one ticket while serializing final checks for the guild.

    Discord channel creation cannot be part of a SQLite transaction. A per-guild
    lock closes the in-process race window, and a unique database index protects
    ticket numbers if another process is accidentally started.
    """
    from utils import assistance
    from utils.database import ensure_database_connection, get_additional_support_roles
    from utils.helpers import check_rate_limit

    if not await ensure_database_connection(bot):
        return False, "Database connection failed. Please try again later."

    async with _creation_locks[guild.id]:
        async with bot.db.cursor() as cursor:
            await cursor.execute(
                """
                SELECT category_id, role_id, log_channel_id, ticket_limit, maintenance_mode
                FROM tickets
                WHERE guild_id = ?
                """,
                (guild.id,),
            )
            config_row = await cursor.fetchone()
            if not config_row:
                return False, "The ticket system is not configured."

            category_id, role_id, log_channel_id, ticket_limit, maintenance = config_row
            if maintenance:
                return False, "The ticket system is currently under maintenance."

            await cursor.execute(
                "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                (guild.id, user.id),
            )
            if await cursor.fetchone():
                return False, "You are blacklisted from creating tickets in this server."

            if await check_rate_limit(bot, guild.id, user.id):
                return False, "You're creating tickets too quickly. Please wait 60 seconds."

            await cursor.execute(
                """
                SELECT COUNT(*) FROM ticket_instances
                WHERE guild_id = ? AND creator_id = ? AND status = 'open'
                """,
                (guild.id, user.id),
            )
            open_count = (await cursor.fetchone())[0]
            effective_limit = ticket_limit or 3
            if open_count >= effective_limit:
                return False, (f"You have reached the maximum ticket limit ({open_count}/{effective_limit}).")

            await cursor.execute(
                "SELECT COALESCE(MAX(ticket_number), 0) + 1 FROM ticket_instances WHERE guild_id = ?",
                (guild.id,),
            )
            ticket_number = (await cursor.fetchone())[0]

        configured_category = guild.get_channel(category_id) if category_id else None
        target_category = category_channel or _target_category(guild, category, configured_category)
        support_role = guild.get_role(role_id) if role_id else None
        additional_roles = await get_additional_support_roles(bot, guild.id)

        username = _channel_component(user.display_name, fallback="user")
        category_name = _channel_component(category, fallback="support")
        channel_name = f"{username}-{category_name}-{ticket_number:04d}"[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }
        chain_role_ids = set(assistance.ADMIN_ROLE_IDS) | set(assistance.MODERATOR_ROLE_IDS)
        chain_roles = [guild.get_role(role_id) for role_id in chain_role_ids]
        support_roles = [role for role in [support_role, *additional_roles, *chain_roles] if role]
        for role in support_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=target_category,
                overwrites=overwrites,
                topic=(f"Ticket #{ticket_number:04d} for user {user.id}: {category}")[:1024],
                reason=f"Ticket created by Discord user {user.id}",
            )
        except discord.Forbidden:
            return False, "I do not have permission to create ticket channels."
        except discord.HTTPException as exc:
            logger.exception("Discord rejected ticket channel creation")
            return False, f"Discord could not create the ticket channel: {exc}"

        try:
            async with bot.db.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO ticket_instances
                    (guild_id, channel_id, creator_id, ticket_number, category,
                     subject, description, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        guild.id,
                        channel.id,
                        user.id,
                        ticket_number,
                        category,
                        reason,
                        reason,
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO rate_limits (guild_id, user_id, last_ticket_time)
                    VALUES (?, ?, unixepoch('now'))
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET last_ticket_time = excluded.last_ticket_time
                    """,
                    (guild.id, user.id),
                )
                await bot.db.commit()
        except Exception:
            await bot.db.rollback()
            logger.exception("Ticket database insert failed; removing channel %s", channel.id)
            try:
                await channel.delete(reason="Rolling back failed ticket creation")
            except discord.HTTPException:
                logger.exception("Could not remove rolled-back ticket channel %s", channel.id)
            return False, "The ticket could not be saved. Please try again."

        try:
            from views.assistance_panel import TicketOpenedLayout, guild_icon_url

            await channel.send(
                view=TicketOpenedLayout(
                    user=user,
                    category=category,
                    inquiry=reason,
                    additional_info=additional_info,
                    thumbnail_url=guild_icon_url(guild),
                    cc_role=support_role,
                    guild=guild,
                ),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    users=False,
                    roles=[support_role] if support_role else [],
                    replied_user=False,
                ),
            )
        except discord.HTTPException:
            logger.exception("Failed to post the ticket header in channel %s", channel.id)

        try:
            from views.ticket_views import TicketControlLayout

            ticket_data = {
                "channel_id": channel.id,
                "creator_id": user.id,
                "ticket_number": ticket_number,
                "category": category,
                "reason": reason,
            }
            control_message = await channel.send(
                view=TicketControlLayout(bot, ticket_data, user),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            try:
                await control_message.pin(reason="Ticket controls")
            except discord.HTTPException:
                logger.warning("Could not pin controls in ticket channel %s", channel.id)
        except Exception:
            logger.exception("Failed to send controls in ticket channel %s", channel.id)

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        if log_channel:
            try:
                from discord import ui

                log_layout = ui.LayoutView()
                log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                log_container.add_item(ui.TextDisplay("### New Ticket Created"))
                log_container.add_item(ui.Separator())
                log_container.add_item(
                    ui.TextDisplay(
                        f"Ticket `#{ticket_number:04d}` created "
                        f"{discord.utils.format_dt(discord.utils.utcnow(), 'R')}\n\n"
                        f"**Channel:** {channel.mention} ({channel.id})\n"
                        f"**Creator:** {user.display_name} ({user.id})\n"
                        f"**Category:** {category}\n"
                        f"**Reason:** {reason}"
                    )
                )
                log_layout.add_item(log_container)
                await log_channel.send(
                    view=log_layout,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                logger.exception("Failed to log creation of ticket %s", channel.id)

        await notify_staff_of_ticket(bot, guild, role_id, ticket_number, channel, user, category)

        return True, f"Ticket #{ticket_number:04d} created in {channel.mention}"


async def notify_staff_of_ticket(bot, guild, support_role_id, ticket_number: int, channel, user, category: str) -> None:
    """Feed the dashboard activity list and ping staff in their channel."""

    from utils import assistance, dashboard_store
    from utils.emojis import resolve_component_emoji

    try:
        await dashboard_store.log_activity(
            bot.db, "ticket", f"Ticket #{ticket_number:04d} opened by {user.display_name}"
        )
    except Exception:
        logger.debug("Could not record dashboard activity for a new ticket", exc_info=True)

    if not assistance.STAFF_CHANNEL_ID:
        return
    staff_channel = guild.get_channel(assistance.STAFF_CHANNEL_ID)
    if staff_channel is None:
        return

    role = guild.get_role(support_role_id) if support_role_id else None
    chain_role_ids = set(assistance.ADMIN_ROLE_IDS) | set(assistance.MODERATOR_ROLE_IDS)
    notify_roles = [guild.get_role(role_id) for role_id in chain_role_ids]
    notify_roles = [item for item in ([role] if role else []) + notify_roles if item]
    notify_text = " ".join(item.mention for item in notify_roles)
    icon = resolve_component_emoji("<:Ticket_icons:1382703084815257610>") or "🎫"
    try:
        from discord import ui

        layout = ui.LayoutView()
        container = ui.Container(accent_color=assistance.GOLD_ACCENT)
        container.add_item(ui.TextDisplay(f"{icon} ### New ticket #{ticket_number:04d}"))
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"**Opened by:** {user.mention}\n"
                f"**Category:** {category}\n"
                f"**Channel:** {channel.mention}"
                + (f"\n**Notify:** {notify_text}" if notify_text else "")
            )
        )
        layout.add_item(container)
        await staff_channel.send(
            view=layout,
            allowed_mentions=discord.AllowedMentions(roles=notify_roles, users=[user]),
        )
    except discord.HTTPException:
        logger.warning("Could not notify the staff channel about ticket %s", ticket_number)


async def post_ticket_transcript(bot, guild, ticket_data: dict, ticket_channel) -> None:
    """Send a plain-text transcript of a closing ticket to the transcript channel."""

    import io

    from utils import assistance, dashboard_store

    ticket_number = int(ticket_data.get("ticket_number") or 0)
    try:
        await dashboard_store.log_activity(
            bot.db, "ticket", f"Ticket #{ticket_number:04d} closed and transcribed"
        )
    except Exception:
        logger.debug("Could not record dashboard activity for a closed ticket", exc_info=True)

    if not assistance.TRANSCRIPT_CHANNEL_ID or ticket_channel is None:
        return
    transcript_channel = guild.get_channel(assistance.TRANSCRIPT_CHANNEL_ID)
    if transcript_channel is None:
        return

    lines = []
    try:
        async for message in ticket_channel.history(limit=500, oldest_first=True):
            attachments = " ".join(attachment.url for attachment in message.attachments)
            content = message.content or ""
            lines.append(f"[{message.created_at:%Y-%m-%d %H:%M:%S}] {message.author}: {content} {attachments}".rstrip())
    except discord.HTTPException:
        logger.warning("Could not read the history of ticket channel %s", ticket_channel.id)
    if not lines:
        lines = ["No messages were recorded before the ticket closed."]

    payload = io.BytesIO("\n".join(lines).encode("utf-8"))
    try:
        await transcript_channel.send(
            content=(
                f"Transcript for ticket #{ticket_number:04d} "
                f"(owner {ticket_data.get('creator_id')})"
            ),
            file=discord.File(payload, filename=f"ticket-{ticket_number:04d}.txt"),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        logger.warning("Could not post the transcript for ticket %s", ticket_number)


async def close_and_delete_ticket(
    bot,
    guild,
    ticket_channel,
    ticket_data: dict[str, Any],
    *,
    actor: str,
    delete_channel: bool = True,
) -> tuple[bool, str]:
    """Close a ticket without a staff interaction.

    Used by the automated partnership review: the applicant keeps a transcript by
    DM, the log channel records the closure, and with ``delete_channel`` False the
    channel is kept (locked) so staff still have the ticket history.
    """

    if ticket_channel is None:
        return False, "The ticket channel no longer exists."

    from utils.helpers import generate_transcript, send_transcript_dm

    ticket_number = int(ticket_data.get("ticket_number") or 0)
    creator_id = ticket_data.get("creator_id")
    category = ticket_data.get("category") or "Partnership Support"

    async with bot.db.cursor() as cur:
        await cur.execute(
            "UPDATE ticket_instances SET status = 'closed', closed_at = CURRENT_TIMESTAMP "
            "WHERE channel_id = ? AND status = 'open'",
            (ticket_channel.id,),
        )
        await bot.db.commit()

    creator = await get_ticket_creator_member(bot, guild, ticket_channel.id) if creator_id else None
    if creator is not None:
        try:
            await ticket_channel.set_permissions(creator, send_messages=False)
        except Exception:
            logger.warning("Could not lock ticket channel %s for its creator", ticket_channel.id)
        try:
            _content, transcript_file = await generate_transcript(ticket_channel)
            await send_transcript_dm(creator, ticket_channel.name, transcript_file)
        except Exception:
            logger.warning("Could not send the transcript of ticket %s", ticket_number, exc_info=True)

    try:
        async with bot.db.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (guild.id,)) as cursor:
            row = await cursor.fetchone()
        log_channel = guild.get_channel(row[0]) if row and row[0] else None
        if log_channel is not None:
            from discord import ui

            author = f"<@{creator_id}> ({creator_id})" if creator_id else "Unknown user"
            layout = ui.LayoutView()
            container = ui.Container(accent_color=PURPLE_PRIMARY)
            container.add_item(ui.TextDisplay("### Logs - Ticket Closed"))
            container.add_item(ui.Separator())
            container.add_item(
                ui.TextDisplay(
                    f"Ticket `#{ticket_number:04d}` was closed "
                    f"{discord.utils.format_dt(discord.utils.utcnow(), 'R')}\n\n"
                    f"**Ticket's Author:** {author}\n"
                    f"**Closed By:** {actor}\n"
                    f"**Category:** {category}"
                )
            )
            layout.add_item(container)
            await log_channel.send(view=layout, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.warning("Could not log the closure of ticket %s", ticket_number, exc_info=True)

    try:
        await post_ticket_transcript(bot, guild, ticket_data, ticket_channel)
    except Exception:
        logger.warning("Could not post the transcript of ticket %s", ticket_number, exc_info=True)

    if not delete_channel:
        return True, f"Ticket #{ticket_number:04d} closed."

    try:
        await ticket_channel.delete(reason=actor)
    except discord.HTTPException as error:
        return False, f"Could not delete the ticket channel: {error}"
    return True, f"Ticket #{ticket_number:04d} closed and deleted."


async def get_user_open_ticket_count(bot, guild_id: int, user_id: int) -> int:
    try:
        async with bot.db.execute(
            """
            SELECT COUNT(*) FROM ticket_instances
            WHERE guild_id = ? AND creator_id = ? AND status = 'open'
            """,
            (guild_id, user_id),
        ) as cursor:
            return (await cursor.fetchone())[0]
    except Exception:
        logger.exception("Failed to count tickets for user %s in guild %s", user_id, guild_id)
        return 0


async def get_ticket_limit(bot, guild_id: int) -> int:
    try:
        async with bot.db.execute(
            "SELECT ticket_limit FROM tickets WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row and row[0] else 3
    except Exception:
        logger.exception("Failed to get ticket limit for guild %s", guild_id)
        return 3


async def check_database_connection(bot) -> bool:
    from utils.database import check_database_connection as check

    return await check(bot)
