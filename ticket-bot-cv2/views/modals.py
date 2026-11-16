import logging

import discord

from utils.helpers import send_ephemeral_container

logger = logging.getLogger("discord")


class TicketModal(discord.ui.Modal):
    """Collects the inquiry details used by the "New Ticket Opened" message."""

    def __init__(self, bot, category, guild_id):
        super().__init__(title=f"Create {category} Ticket", timeout=300)
        self.bot = bot
        self.category = category
        self.guild_id = guild_id

        self.reason = discord.ui.TextInput(
            label="Inquiry",
            style=discord.TextStyle.short,
            placeholder="What do you need help with?",
            max_length=120,
            required=True,
        )
        self.additional_info = discord.ui.TextInput(
            label="Additional Info",
            style=discord.TextStyle.paragraph,
            placeholder="Anything else we should know? (optional)",
            max_length=1000,
            required=False,
        )
        self.add_item(self.reason)
        self.add_item(self.additional_info)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            reason = self.reason.value.strip() if self.reason.value else "No reason provided"
            additional_info = self.additional_info.value.strip() if self.additional_info.value else ""

            from utils.database import check_user_ticket_limit
            from utils.helpers import check_rate_limit

            user_id = interaction.user.id
            if await check_rate_limit(self.bot, self.guild_id, user_id):
                await send_ephemeral_container(
                    interaction, "You're creating tickets too quickly. Please wait 60 seconds."
                )
                return

            ticket_limit_ok, current_tickets, max_tickets = await check_user_ticket_limit(
                self.bot, self.guild_id, user_id
            )
            if not ticket_limit_ok:
                await send_ephemeral_container(
                    interaction,
                    f"You have reached the maximum ticket limit ({current_tickets}/{max_tickets}). Please close existing tickets first.",
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (self.guild_id, user_id)
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction, "You are blacklisted from creating tickets in this server."
                    )
                    return

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (self.guild_id,))
                result = await cur.fetchone()
                if result and result[0]:
                    await send_ephemeral_container(
                        interaction, "The ticket system is currently under maintenance. Please try again later."
                    )
                    return

            from utils.tickets import create_ticket_channel

            try:
                success, message = await create_ticket_channel(
                    self.bot,
                    interaction.guild,
                    interaction.user,
                    None,
                    self.category,
                    reason,
                    additional_info,
                )

                await send_ephemeral_container(interaction, message)
                if not success:
                    logger.warning("Ticket creation failed for %s: %s", user_id, message)
            except Exception as ticket_error:
                logger.error(f"Error creating ticket: {ticket_error}")
                await send_ephemeral_container(interaction, f"Failed to create ticket: {str(ticket_error)}")

        except Exception as e:
            logger.error(f"Error in ticket modal submission: {e}")
            try:
                await send_ephemeral_container(
                    interaction, "An error occurred while creating your ticket. Please try again."
                )
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Modal error: {error}")
        try:
            await send_ephemeral_container(interaction, "An error occurred. Please try again.")
        except Exception:
            pass
