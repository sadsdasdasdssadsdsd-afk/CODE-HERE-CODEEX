import discord
from discord import ui
import io
import logging
from utils.helpers import generate_transcript, send_ephemeral_container
from utils.config import config
from utils.emojis import resolve_component_emoji, resolve_emojis
from utils.theme import PURPLE_PRIMARY
from views.modals import TicketModal

logger = logging.getLogger("discord")


class PersistentTicketClosedView(discord.ui.View):
    """Persistent view for ticket closed delete/reopen buttons"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            bot = interaction.client
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can delete tickets.")
                return

            await interaction.response.defer()

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT creator_id, ticket_number, category FROM ticket_instances WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                ticket_result = await cur.fetchone()

                if not ticket_result:
                    await interaction.followup.send("Ticket not found in database.", ephemeral=True)
                    return

                creator_id, ticket_number, category = ticket_result

            server_name = interaction.guild.name
            _transcript_content, transcript_file = await generate_transcript(interaction.channel)
            transcript_bytes = transcript_file.getvalue()

            if creator_id:
                from utils.tickets import get_ticket_creator_member

                creator = await get_ticket_creator_member(bot, interaction.guild, interaction.channel.id)

                if creator:
                    try:
                        file = discord.File(
                            io.BytesIO(transcript_bytes),
                            filename=f"ticket-{ticket_number:04d}-transcript.txt",
                        )

                        dm_layout = ui.LayoutView()
                        dm_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        dm_container.add_item(
                            ui.TextDisplay(resolve_emojis("# <:clipboard1:1383857546410070117> Ticket Transcript"))
                        )
                        dm_container.add_item(ui.Separator())
                        dm_container.add_item(
                            ui.TextDisplay(
                                f"Your ticket in **{server_name}** has been closed. Here is the complete transcript.\n\n"
                                f"**Category:** {category}"
                            )
                        )
                        dm_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        dm_layout.add_item(dm_container)

                        await creator.send(view=dm_layout, file=file)
                        logger.info(f"Sent transcript DM to user {creator.id}")

                    except discord.Forbidden:
                        logger.warning(f"Could not send DM to user {creator_id} - DMs disabled")
                    except Exception as dm_error:
                        logger.error(f"Error sending transcript DM: {dm_error}")

            async with bot.db.cursor() as cur:
                await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if result and result[0]:
                    log_channel = interaction.guild.get_channel(result[0])
                    if log_channel:
                        delete_time = discord.utils.utcnow()
                        creator_name = "Unknown User"
                        if creator_id:
                            from utils.tickets import get_ticket_creator_member

                            creator = await get_ticket_creator_member(bot, interaction.guild, interaction.channel.id)
                            if creator:
                                creator_name = getattr(creator, "display_name", None) or getattr(
                                    creator, "name", "Unknown User"
                                )

                        log_layout = ui.LayoutView()
                        log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        log_container.add_item(ui.TextDisplay("### Logs - Ticket Deleted"))
                        log_container.add_item(ui.Separator())
                        log_container.add_item(
                            ui.TextDisplay(
                                f"Ticket `#{ticket_number:04d}` has been deleted {discord.utils.format_dt(delete_time, 'R')}!\n\n"
                                f"**Ticket's Author:** {creator_name} ({creator_id})\n"
                                f"**Deleted By:** {interaction.user.display_name} ({interaction.user.id})\n"
                                f"**Category:** {category}"
                            )
                        )
                        log_container.add_item(ui.Separator())
                        log_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        log_layout.add_item(log_container)

                        await log_channel.send(
                            view=log_layout,
                            file=discord.File(
                                io.BytesIO(transcript_bytes),
                                filename=f"ticket-{ticket_number:04d}-transcript.txt",
                            ),
                        )

            await interaction.channel.delete(reason=f"Ticket #{ticket_number:04d} deleted by {interaction.user}")

        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            try:
                await send_ephemeral_container(interaction, f"Error deleting ticket: {str(e)}")
            except Exception:
                pass

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.success, custom_id="ticket_reopen_btn")
    async def reopen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            bot = interaction.client
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can reopen tickets.")
                return

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT creator_id FROM ticket_instances WHERE channel_id = ?", (interaction.channel.id,)
                )
                result = await cur.fetchone()
                creator_id = result[0] if result else None

            if creator_id:
                creator = interaction.guild.get_member(creator_id)
                if creator:
                    try:
                        await interaction.channel.set_permissions(creator, send_messages=True, view_channel=True)
                        logger.info(f"Restored send_messages permission for user {creator_id} in ticket channel")
                    except Exception as perm_error:
                        logger.warning(f"Could not restore permissions for user: {perm_error}")

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "UPDATE ticket_instances SET status = 'open', closed_at = NULL WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                await bot.db.commit()

            reopen_layout = ui.LayoutView()
            reopen_container = ui.Container(accent_color=PURPLE_PRIMARY)
            reopen_container.add_item(ui.TextDisplay("### Ticket Reopened"))
            reopen_container.add_item(ui.Separator())
            reopen_container.add_item(ui.TextDisplay(f"Reopened by {interaction.user.mention}"))
            reopen_layout.add_item(reopen_container)

            await interaction.response.send_message(view=reopen_layout)

            await interaction.message.delete()

        except Exception as e:
            logger.error(f"Error reopening ticket: {e}")
            await send_ephemeral_container(interaction, f"Error reopening ticket: {str(e)}")


class TicketConfirmationLayoutView(ui.LayoutView):
    def __init__(self, bot, category, guild_id, user_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.category = category
        self.guild_id = guild_id
        self.user_id = user_id
        self.setup_content()

    def setup_content(self):
        self.container = ui.Container(accent_colour=PURPLE_PRIMARY)
        self.add_item(self.container)

        self.container.add_item(ui.TextDisplay("### Support Rules"))
        self.container.add_item(ui.Separator())

        self.container.add_item(ui.TextDisplay(resolve_emojis(config.TICKET_RULES_TEXT)))
        self.container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="confirm_open_ticket"),
            ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_ticket"),
        )

        button_row.children[0].callback = self.confirm_callback
        button_row.children[1].callback = self.cancel_callback

        self.container.add_item(button_row)

    async def confirm_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        modal = TicketModal(self.bot, self.category, self.guild_id)
        await interaction.response.send_modal(modal)
        self.stop()

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        await interaction.response.edit_message(view=None, content="Ticket creation cancelled.")
        self.stop()



class TicketControlView(discord.ui.View):
    """Simple backward-compatible ticket control view for persistent button handling"""

    def __init__(self, bot, ticket_data=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data or {}

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_claim_btn")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(interaction, "Ticket system is not configured.")
                    return

                from utils.database import user_has_support_role

                has_support_access = await user_has_support_role(self.bot, interaction.user)

                if not has_support_access:
                    await send_ephemeral_container(interaction, "Only support staff can claim tickets.")
                    return

                await cur.execute(
                    "SELECT claimed_by, creator_id FROM ticket_instances WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                claim_result = await cur.fetchone()

                if not claim_result:
                    await send_ephemeral_container(interaction, "Ticket not found.")
                    return

                claimed_by, creator_id = claim_result

                if claimed_by:
                    if claimed_by == interaction.user.id:
                        await send_ephemeral_container(interaction, "You have already claimed this ticket.")
                        return
                    claimer = interaction.guild.get_member(claimed_by)
                    await send_ephemeral_container(
                        interaction,
                        f"This ticket is already claimed by {claimer.mention if claimer else 'another agent'}.",
                    )
                    return

                await cur.execute(
                    """
                    UPDATE ticket_instances
                    SET claimed_by = ?
                    WHERE channel_id = ? AND claimed_by IS NULL AND status = 'open'
                    """,
                    (interaction.user.id, interaction.channel.id),
                )
                if cur.rowcount == 0:
                    await send_ephemeral_container(
                        interaction,
                        "Another agent claimed this ticket at the same time.",
                    )
                    return
                await self.bot.db.commit()

            ticket_creator = interaction.guild.get_member(creator_id) if creator_id else None
            creator_mention = (
                ticket_creator.mention if ticket_creator else f"<@{creator_id}>" if creator_id else "Ticket Creator"
            )

            await interaction.response.send_message(
                f"{creator_mention} your ticket has been claimed by {interaction.user.mention}", ephemeral=False
            )

        except Exception as e:
            logger.error(f"Error claiming ticket: {e}")
            await send_ephemeral_container(interaction, f"Error claiming ticket: {str(e)}")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT creator_id, ticket_number, category FROM ticket_instances WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                ticket_result = await cur.fetchone()
                if not ticket_result:
                    await send_ephemeral_container(interaction, "Ticket not found.")
                    return
                creator_id, ticket_number, category = ticket_result

            from utils.database import user_has_support_role

            is_creator = creator_id == interaction.user.id
            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_creator or is_support or is_admin):
                await send_ephemeral_container(interaction, "You don't have permission to close this ticket.")
                return

            await interaction.response.defer()

            ticket_data = {
                "creator_id": creator_id,
                "ticket_number": ticket_number,
                "category": category,
                "subject": "N/A",
            }

            success, message = await close_ticket_channel(self.bot, interaction, ticket_data)
            if not success:
                await interaction.followup.send(f"Error closing ticket: {message}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in close button: {e}")
            await send_ephemeral_container(interaction, f"Error: {str(e)}")


class TicketControlLayout(ui.LayoutView):
    """Components v2 ticket control panel"""

    def __init__(self, bot, ticket_data, user):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data
        self.user = user

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self._build_layout()
        self.add_item(self.container)

    def _build_layout(self):
        """Build the ticket control panel layout"""
        category = self.ticket_data.get("category", "General")
        self.container.add_item(ui.TextDisplay(f"# {category} Ticket"))

        self.container.add_item(ui.Separator())

        welcome_text = (
            f"**Welcome** {self.user.mention}\n**Category:** {category}\n\nOur support team will assist you shortly."
        )

        welcome_section = ui.Section(accessory=ui.Thumbnail(media=self.user.display_avatar.url))
        welcome_section.add_item(ui.TextDisplay(welcome_text))
        self.container.add_item(welcome_section)

        self.container.add_item(ui.Separator())

        if config.TICKET_BANNER_URL:
            gallery = ui.MediaGallery()
            gallery.add_item(media=config.TICKET_BANNER_URL)
            self.container.add_item(gallery)
            self.container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_claim_btn"),
            ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn"),
        )
        button_row.children[0].callback = self.claim_callback
        button_row.children[1].callback = self.close_callback
        self.container.add_item(button_row)

    async def claim_callback(self, interaction: discord.Interaction):
        """Handle claim ticket button"""
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(interaction, "Ticket system is not configured.")
                    return

                support_role_id = result[0]
                support_role = interaction.guild.get_role(support_role_id)

                from utils.database import user_has_support_role

                has_support_access = await user_has_support_role(self.bot, interaction.user)

                if not has_support_access:
                    await interaction.response.send_message(
                        f"Only support staff can claim tickets.\nRequired role: {support_role.mention if support_role else 'Support Role'}",
                        ephemeral=True,
                    )
                    return

                await cur.execute(
                    "SELECT claimed_by FROM ticket_instances WHERE channel_id = ?", (interaction.channel.id,)
                )
                claim_result = await cur.fetchone()

                if not claim_result:
                    await send_ephemeral_container(interaction, "Ticket not found.")
                    return

                if claim_result[0]:
                    if claim_result[0] == interaction.user.id:
                        await send_ephemeral_container(interaction, "You have already claimed this ticket.")
                        return

                    claimer = interaction.guild.get_member(claim_result[0])
                    await interaction.response.send_message(
                        f"This ticket is already claimed by {claimer.mention if claimer else 'another agent'}.",
                        ephemeral=True,
                    )
                    return

                await cur.execute(
                    """
                    UPDATE ticket_instances
                    SET claimed_by = ?
                    WHERE channel_id = ? AND claimed_by IS NULL AND status = 'open'
                    """,
                    (interaction.user.id, interaction.channel.id),
                )
                if cur.rowcount == 0:
                    await send_ephemeral_container(
                        interaction,
                        "Another agent claimed this ticket at the same time.",
                    )
                    return
                await self.bot.db.commit()

            ticket_creator_id = self.ticket_data.get("creator_id")
            ticket_creator = interaction.guild.get_member(ticket_creator_id)

            if ticket_creator:
                creator_mention = ticket_creator.mention
            elif ticket_creator_id:
                creator_mention = f"<@{ticket_creator_id}>"
            else:
                creator_mention = "Ticket Creator"

            await interaction.response.send_message(
                f"{creator_mention} your ticket has been claimed by {interaction.user.mention}", ephemeral=False
            )

            async def get_ticket_log_channel(bot, guild_id):
                async with bot.db.cursor() as cur:
                    await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (guild_id,))
                    result = await cur.fetchone()
                    return bot.get_channel(result[0]) if result and result[0] else None

            current_time = discord.utils.utcnow()
            log_channel = await get_ticket_log_channel(self.bot, interaction.guild.id)
            if log_channel:
                from utils.tickets import get_ticket_info

                ticket_info = await get_ticket_info(self.bot, interaction.channel.id)

                ticket_number_display = f"#{ticket_info['ticket_number']:04d}" if ticket_info else "#0000"

                claim_log_layout = ui.LayoutView()
                claim_log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                claim_log_container.add_item(ui.TextDisplay("# Logs - Ticket Claimed"))
                claim_log_container.add_item(ui.Separator())
                claim_log_container.add_item(
                    ui.TextDisplay(
                        f"Ticket `{ticket_number_display}` claimed {discord.utils.format_dt(current_time, 'R')}\n\n"
                        f"**Channel:** {interaction.channel.mention}\n"
                        f"**Claimed By:** {interaction.user.display_name}\n"
                        f"**Category:** {ticket_info.get('category', 'Unknown') if ticket_info else 'Unknown'}"
                    )
                )
                claim_log_layout.add_item(claim_log_container)
                await log_channel.send(view=claim_log_layout)

        except Exception as e:
            logger.error(f"Error claiming ticket: {e}")
            await send_ephemeral_container(interaction, f"Error claiming ticket: {str(e)}")

    async def close_callback(self, interaction: discord.Interaction):
        """Handle close ticket button"""
        try:
            from utils.database import user_has_support_role

            is_creator = self.ticket_data.get("creator_id") == interaction.user.id
            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_creator or is_support or is_admin):
                await send_ephemeral_container(interaction, "You don't have permission to close this ticket.")
                return

            await interaction.response.defer()

            success, message = await close_ticket_channel(self.bot, interaction, self.ticket_data)
            if not success:
                await interaction.followup.send(f"Error closing ticket: {message}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in close button: {e}")
            await send_ephemeral_container(interaction, f"Error: {str(e)}")



class TicketClosedLayout(ui.LayoutView):
    """Layout shown when a ticket is closed with Delete/Reopen buttons"""

    def __init__(self, bot, ticket_data):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self._build_layout()
        self.add_item(self.container)

    def _build_layout(self):
        self.container.add_item(ui.TextDisplay("### Ticket Closed"))
        self.container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn"),
            ui.Button(label="Reopen", style=discord.ButtonStyle.success, custom_id="ticket_reopen_btn"),
        )
        button_row.children[0].callback = self.delete_callback
        button_row.children[1].callback = self.reopen_callback
        self.container.add_item(button_row)

    async def delete_callback(self, interaction: discord.Interaction):
        try:
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can delete tickets.")
                return

            await interaction.response.defer()

            ticket_number = self.ticket_data.get("ticket_number", 0)
            creator_id = self.ticket_data.get("creator_id")
            category = self.ticket_data.get("category", "Unknown")
            server_name = interaction.guild.name

            _transcript_content, transcript_file = await generate_transcript(interaction.channel)
            transcript_bytes = transcript_file.getvalue()

            if creator_id:
                from utils.tickets import get_ticket_creator_member

                creator = await get_ticket_creator_member(self.bot, interaction.guild, interaction.channel.id)

                if creator:
                    try:
                        file = discord.File(
                            io.BytesIO(transcript_bytes),
                            filename=f"ticket-{ticket_number:04d}-transcript.txt",
                        )

                        dm_layout = ui.LayoutView()
                        dm_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        dm_container.add_item(
                            ui.TextDisplay(resolve_emojis("# <:clipboard1:1383857546410070117> Ticket Transcript"))
                        )
                        dm_container.add_item(ui.Separator())
                        dm_container.add_item(
                            ui.TextDisplay(
                                f"Your ticket in **{server_name}** has been closed. Here is the complete transcript.\n\n"
                                f"**Category:** {category}"
                            )
                        )
                        dm_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        dm_layout.add_item(dm_container)

                        await creator.send(view=dm_layout, file=file)
                        logger.info(f"Sent transcript DM to user {creator.id}")

                    except discord.Forbidden:
                        logger.warning(f"Could not send DM to user {creator_id} - DMs disabled")
                    except Exception as dm_error:
                        logger.error(f"Error sending transcript DM: {dm_error}")

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if result and result[0]:
                    log_channel = interaction.guild.get_channel(result[0])
                    if log_channel:
                        delete_time = discord.utils.utcnow()
                        creator_name = "Unknown User"
                        if creator_id:
                            from utils.tickets import get_ticket_creator_member

                            creator_member = await get_ticket_creator_member(
                                self.bot, interaction.guild, interaction.channel.id
                            )
                            if creator_member:
                                creator_name = getattr(creator_member, "display_name", None) or getattr(
                                    creator_member, "name", "Unknown User"
                                )

                        log_layout = ui.LayoutView()
                        log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        log_container.add_item(ui.TextDisplay("### Logs - Ticket Deleted"))
                        log_container.add_item(ui.Separator())
                        log_container.add_item(
                            ui.TextDisplay(
                                f"Ticket `#{ticket_number:04d}` has been deleted {discord.utils.format_dt(delete_time, 'R')}!\n\n"
                                f"**Ticket's Author:** {creator_name} ({creator_id})\n"
                                f"**Deleted By:** {interaction.user.display_name} ({interaction.user.id})\n"
                                f"**Category:** {category}"
                            )
                        )
                        log_container.add_item(ui.Separator())
                        log_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        log_layout.add_item(log_container)

                        await log_channel.send(
                            view=log_layout,
                            file=discord.File(
                                io.BytesIO(transcript_bytes),
                                filename=f"ticket-{ticket_number:04d}-transcript.txt",
                            ),
                        )

            await interaction.channel.delete(reason=f"Ticket #{ticket_number:04d} deleted by {interaction.user}")

        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            await send_ephemeral_container(interaction, f"Error deleting ticket: {str(e)}")

    async def reopen_callback(self, interaction: discord.Interaction):
        try:
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can reopen tickets.")
                return

            creator_id = self.ticket_data.get("creator_id")
            if creator_id:
                creator = interaction.guild.get_member(creator_id)
                if creator:
                    try:
                        await interaction.channel.set_permissions(creator, send_messages=True, view_channel=True)
                        logger.info(f"Restored send_messages permission for user {creator_id} in ticket channel")
                    except Exception as perm_error:
                        logger.warning(f"Could not restore permissions for user: {perm_error}")

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "UPDATE ticket_instances SET status = 'open', closed_at = NULL WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                await self.bot.db.commit()

            reopen_layout = ui.LayoutView()
            reopen_container = ui.Container(accent_color=PURPLE_PRIMARY)
            reopen_container.add_item(ui.TextDisplay("### Ticket Reopened"))
            reopen_container.add_item(ui.Separator())
            reopen_container.add_item(ui.TextDisplay(f"Reopened by {interaction.user.mention}"))
            reopen_layout.add_item(reopen_container)

            await interaction.response.send_message(view=reopen_layout)

            await interaction.message.delete()

        except Exception as e:
            logger.error(f"Error reopening ticket: {e}")
            await send_ephemeral_container(interaction, f"Error reopening ticket: {str(e)}")


async def close_ticket_channel(bot, interaction, ticket_data: dict):
    """Close a ticket channel - removes author permissions, logs, updates DB, and shows closed panel"""
    try:
        channel = interaction.channel
        creator_id = ticket_data.get("creator_id")

        from utils.tickets import get_ticket_creator_member

        creator = await get_ticket_creator_member(bot, interaction.guild, channel.id) if creator_id else None

        ticket_number = ticket_data.get("ticket_number", 0)

        async with bot.db.cursor() as cur:
            await cur.execute(
                """
                UPDATE ticket_instances
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE channel_id = ? AND status = 'open'
                """,
                (channel.id,),
            )
            if cur.rowcount == 0:
                return False, "Ticket is already closed or no longer exists."
            await bot.db.commit()

        if creator and hasattr(creator, "id"):
            try:
                await channel.set_permissions(creator, send_messages=False)
                logger.info(f"Removed send_messages permission for user {creator.id} in ticket channel")
            except Exception as perm_error:
                logger.warning(f"Could not update permissions for user: {perm_error}")

        async with bot.db.cursor() as cur:
            await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
            result = await cur.fetchone()

            if result and result[0]:
                log_channel = interaction.guild.get_channel(result[0])
                if log_channel:
                    close_time = discord.utils.utcnow()

                    creator_name = "Unknown User"
                    if creator:
                        creator_name = getattr(creator, "display_name", None) or getattr(
                            creator, "name", "Unknown User"
                        )

                    from utils.author_info import TicketClosedLogView

                    log_view = TicketClosedLogView(bot, ticket_data)

                    log_layout = ui.LayoutView()
                    log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                    log_container.add_item(ui.TextDisplay("### Logs - Ticket Closed"))
                    log_container.add_item(ui.Separator())
                    log_container.add_item(
                        ui.TextDisplay(
                            f"Ticket `#{ticket_number:04d}` has been closed {discord.utils.format_dt(close_time, 'R')}!\n\n"
                            f"**Ticket's Author:** {creator_name} ({ticket_data.get('creator_id')})\n"
                            f"**Closed By:** {interaction.user.display_name} ({interaction.user.id})\n"
                            f"**Ticket ID:** {channel.id}"
                        )
                    )
                    log_container.add_item(ui.Separator())

                    author_info_row = ui.ActionRow(
                        ui.Button(
                            label="Ticket Author Info",
                            style=discord.ButtonStyle.secondary,
                            emoji=resolve_component_emoji("<:id_icons:1384041001114407013>"),
                            custom_id=f"author_info_{ticket_data.get('creator_id')}",
                        )
                    )

                    async def author_info_callback(btn_interaction: discord.Interaction):
                        await btn_interaction.response.defer(ephemeral=True)
                        await log_view.send_author_info_container(btn_interaction)

                    author_info_row.children[0].callback = author_info_callback
                    log_container.add_item(author_info_row)

                    log_layout.add_item(log_container)

                    await log_channel.send(view=log_layout)

        closed_layout = TicketClosedLayout(bot, ticket_data)
        await channel.send(view=closed_layout)

        try:
            from utils.tickets import post_ticket_transcript

            await post_ticket_transcript(bot, interaction.guild, ticket_data, channel)
        except Exception:
            logger.exception("Could not post the transcript for channel %s", channel.id)

        return True, "Ticket closed successfully."

    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        return False, str(e)
