import discord
from discord.ext import commands
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from utils import dashboard_store
from utils.command_sync import CommandSyncCache
from utils.config import config
from utils.dashboard_bridge import DashboardBridge
from utils.presence import status_key

# Force UTF-8 encoding for Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("discord")


def print_bot_ready(bot_name):
    ascii_art = """
 ▄████▄   ▒░       ░  ░ ░    ░
░                  ░
"""
    print(ascii_art)
    print(f"\033[92mLogin successful logged in as {bot_name}\033[0m")


def print_error(message):
    print(f"\033[91m[ERROR] {message}\033[0m")


def print_loading(message):
    colors = ["\033[91m", "\033[93m", "\033[92m", "\033[96m", "\033[94m", "\033[95m"]
    color = colors[hash(message) % len(colors)]
    print(f"{color}◆ {message}...\033[0m")


def print_success(message):
    colors = ["\033[92m", "\033[96m", "\033[94m", "\033[95m"]
    color = colors[hash(message) % len(colors)]
    print(f"{color}✓ {message}\033[0m")


def print_rainbow_separator():
    rainbow = "\033[91m▆\033[93m▆\033[92m▆\033[96m▆\033[94m▆\033[95m▆\033[0m"
    print(f"  {rainbow * 12}")


def print_system_ready():
    print_rainbow_separator()
    print("\033[92m  System Operational\033[0m")
    print(f"\033[95m Developed by {config.DEVELOPER_NAME}\033[0m")
    print_rainbow_separator()


class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix=config.PREFIX,
            intents=intents,
            help_command=None,
            heartbeat_timeout=60.0,
            chunk_guilds_at_startup=False,
        )

        self.db = None
        self.triggers_db = None
        self.start_time = datetime.now()
        self._ready_initialized = False
        self.guild_emoji_report = None
        self.dashboard = None
        # The presence Discord should show, kept here because discord.py re-sends
        # the presence it logged in with whenever a session has to be re-identified.
        self.desired_presence = None
        self.presence_status = None

    async def set_presence(self, activity, status):
        """Apply a presence and remember it so a reconnect keeps showing it."""

        self.desired_presence = (activity, status)
        self.presence_status = status_key(status)
        await self.change_presence(activity=activity, status=status)

    async def setup_database(self):
        from utils.ai import schema as ai_schema
        from utils.database import initialize_database
        from utils.modules import schema as modules_schema

        print_loading("Database initialization")
        await initialize_database(self)
        await ai_schema.ensure_schema(self.db)
        await modules_schema.ensure_schema(self.db)
        print_success("Database initialized")

    async def setup_guild_emojis(self, guilds=None):
        """Upload the emoji.gg pack to the servers before views and panels load."""

        from utils.emojis import sync_guild_emojis

        if not config.AUTO_SYNC_GUILD_EMOJIS:
            logger.info("Server emoji sync is disabled")
            return

        emoji_directory = Path(config.EMOJI_DIRECTORY)
        if not emoji_directory.is_absolute():
            emoji_directory = Path(__file__).resolve().parent / emoji_directory

        print_loading("Uploading emojis to the server")
        try:
            report = await asyncio.wait_for(
                sync_guild_emojis(self, guilds, asset_directory=emoji_directory / "white"),
                timeout=180.0,
            )
        except Exception:
            logger.exception("Server emoji upload failed; the panel falls back to Unicode glyphs")
            return

        self.guild_emoji_report = report.to_dict()
        print_success(
            f"Server emojis ready - {report.created} uploaded, {report.updated} refreshed, "
            f"{report.reused} already present"
        )
        if report.failures:
            logger.warning(
                "Emoji upload finished with %d failure(s): %s", len(report.failures), report.failures
            )
        if report.assets and not report.guilds:
            logger.warning("Emoji upload skipped because the bot is not in any server yet")

    async def setup_hook(self):
        try:
            await self.setup_database()

            print_loading("Loading dashboard configuration")
            await dashboard_store.ensure_schema(self.db)
            self.dashboard = DashboardBridge(self)
            await self.dashboard.refresh_config()
            print_success("Dashboard configuration loaded")

            print_loading("Loading modules")
            extensions = [
                "cogs.tickets",
                "cogs.help",
                "cogs.triggers",
                "cogs.on_mention",
                "cogs.ai_control",
                "cogs.modules",
                "cogs.welcome",
                "utils.error_handler",
            ]

            for extension in extensions:
                try:
                    await self.load_extension(extension)
                    print_success(f"✓ {extension} loaded")
                except Exception as e:
                    print_error(f"✗ Failed to load {extension}: {e}")
                    raise

            hybrid_commands = [cmd for cmd in self.commands if hasattr(cmd, "app_command")]
            print_success(f"Modules loaded - {len(hybrid_commands)} hybrid commands registered")

        except Exception as e:
            print_error(f"Setup failed: {e}")
            raise

    async def on_ready(self):
        if self._ready_initialized:
            logger.info("Discord connection resumed as %s", self.user)
            if self.desired_presence is not None:
                # A re-identified session starts from the login presence, which would
                # reveal, say, an invisible bot as online. Put the live one back.
                await self.change_presence(
                    activity=self.desired_presence[0], status=self.desired_presence[1]
                )
                logger.info("Presence restored after reconnect: %s", self.presence_status)
            return

        try:
            # Server copies of the emoji pack must exist before any view or panel
            # string is resolved, otherwise the mentions would fall back to glyphs.
            await self.setup_guild_emojis()

            print_loading("Initializing persistent views")
            from views.assistance_panel import AssistancePanelView, publish_assistance_panel
            from views.ticket_views import TicketControlView, PersistentTicketClosedView
            from utils.database import ensure_hardcoded_ticket_config

            self.add_view(TicketControlView(self, {}))

            self.add_view(PersistentTicketClosedView())

            for guild in self.guilds:
                self.add_view(AssistancePanelView(guild))

            print_success("Views registered")

            if self.dashboard is not None:
                self.dashboard.start()
                await self.dashboard.publish_state()
                print_success("Dashboard bridge online")

            print_loading("Publishing the assistance panel")
            try:
                await ensure_hardcoded_ticket_config(self)
                if await publish_assistance_panel(self):
                    print_success("Assistance panel published")
                else:
                    print("◆ Assistance panel waits for ASSISTANCE_PANEL_CHANNEL_ID")
            except Exception as e:
                print_error(f"Could not publish the assistance panel: {e}")

            print_loading("Synchronizing slash commands")
            sync_cache = CommandSyncCache.next_to_database(config.DATABASE_PATH)
            if sync_cache.is_current(self.tree):
                print_success("Commands unchanged - global sync skipped")
            else:
                try:
                    synced = await asyncio.wait_for(self.tree.sync(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Discord throttles global bulk overwrites; the commands registered by the
                    # last successful sync stay live, so a retry on the next restart is enough.
                    print("◆ Command sync timed out - commands will retry on the next restart")
                except discord.HTTPException as e:
                    if "429" in str(e):
                        print("◆ Rate limited during command sync - commands will retry on the next restart")
                    else:
                        print_error(f"Command sync failed: {e}")
                except Exception as e:
                    print_error(f"Command sync failed: {e}")
                else:
                    print_success(f"Commands synchronized ({len(synced)} commands)")
                    for cmd in synced:
                        logger.info(f"Synced command: /{cmd.name}")
                    sync_cache.remember(self.tree)

            try:
                status_type = config.BOT_STATUS_TYPE

                if status_type == "STREAMING":
                    if config.STREAM_URL:
                        activity = discord.Streaming(name=config.BOT_STATUS, url=config.STREAM_URL)
                    else:
                        activity = discord.Game(name=config.BOT_STATUS)
                        logger.warning("STREAM_URL is empty; using a playing activity instead")
                    status = discord.Status.online
                elif status_type == "PLAYING":
                    activity = discord.Game(name=config.BOT_STATUS)
                    status = discord.Status.online
                elif status_type == "WATCHING":
                    activity = discord.Activity(type=discord.ActivityType.watching, name=config.BOT_STATUS)
                    status = discord.Status.online
                elif status_type == "LISTENING":
                    activity = discord.Activity(type=discord.ActivityType.listening, name=config.BOT_STATUS)
                    status = discord.Status.online
                else:
                    activity = discord.Game(name=config.BOT_STATUS)
                    status = discord.Status.online

                if config.BOT_STATUS_TYPE == "IDLE":
                    status = discord.Status.idle
                elif config.BOT_STATUS_TYPE == "DND":
                    status = discord.Status.dnd
                elif config.BOT_STATUS_TYPE == "INVISIBLE":
                    status = discord.Status.invisible

                await self.set_presence(activity, status)
                print_success(f"Bot status set: {config.BOT_STATUS} ({status_type})")
            except Exception as status_error:
                print_error(f"Failed to set bot status: {status_error}")

            print_bot_ready(self.user.name)
            print_system_ready()
            self._ready_initialized = True

        except Exception as e:
            print_error(f"Startup error: {e}")
            if hasattr(self, "user") and self.user:
                print_bot_ready(self.user.name)

    async def on_guild_join(self, guild):
        """Upload the emoji pack as soon as the bot is invited to a new server."""

        logger.info("Joined %s; uploading the emoji pack", guild.name)
        await self.setup_guild_emojis([guild])

    async def close(self):
        print_loading("Shutting down bot")

        if self.dashboard is not None:
            try:
                await self.dashboard.stop()
            except Exception as e:
                logger.warning("Could not stop the dashboard bridge: %s", e)

        if hasattr(self, "db") and self.db:
            try:
                await self.db.close()
                print_success("Main database connection closed")
            except Exception as e:
                print_error(f"Error closing main database: {e}")

        if hasattr(self, "triggers_db") and self.triggers_db:
            try:
                await self.triggers_db.close()
                print_success("Triggers database connection closed")
            except Exception as e:
                print_error(f"Error closing triggers database: {e}")

        try:
            await super().close()
            print_success("Bot shutdown complete")
        except Exception as e:
            print_error(f"Error during bot shutdown: {e}")


bot = TicketBot()


@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Sync commands globally"""
    print_loading("Manual sync initiated...")
    try:
        synced = await bot.tree.sync()
        print_success(f"Synced {len(synced)} commands globally")
        await ctx.send(f"Synced {len(synced)} commands globally")
    except Exception as e:
        print_error(f"Sync failed: {e}")
        await ctx.send(f"Sync failed: {e}")


async def shutdown_handler():
    """Handle graceful shutdown"""
    print_loading("Shutdown signal received")
    await bot.close()


if __name__ == "__main__":
    config.setup_logging()
    if not config.TOKEN:
        logger.error("DISCORD_TOKEN is required and was not found in the environment")
        raise SystemExit(1)

    print("Bot logging in...")

    try:
        asyncio.run(bot.start(config.TOKEN))
    except discord.LoginFailure:
        print_error("Invalid bot token. Rotate it and check DISCORD_TOKEN.")
    except discord.HTTPException as e:
        # Discord's edge (Cloudflare) answers a throttled login with an HTML error
        # page; keep the console readable instead of dumping thousands of lines.
        if e.status == 429:
            print_error(
                "Discord is rate-limiting this host (Cloudflare error 1015) - not a token problem. "
                "Login will be retried later."
            )
        else:
            print_error(f"Bot failed to start: HTTP {e.status} {str(e.text or '').strip()[:160]}")
    except KeyboardInterrupt:
        print_loading("Shutdown initiated by user")
        asyncio.run(shutdown_handler())
    except Exception as e:
        print_error(f"Bot failed to start: {e}")
    finally:
        print_success("Bot process ended")
