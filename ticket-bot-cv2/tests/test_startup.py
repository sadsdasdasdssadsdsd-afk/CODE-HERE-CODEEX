import unittest

from main import TicketBot
from utils.config import config


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_setup_loads_all_extensions(self):
        original_values = {
            "DATABASE_PATH": config.DATABASE_PATH,
            "TRIGGERS_DATABASE_PATH": config.TRIGGERS_DATABASE_PATH,
            "AUTO_SYNC_GUILD_EMOJIS": config.AUTO_SYNC_GUILD_EMOJIS,
        }
        object.__setattr__(config, "DATABASE_PATH", ":memory:")
        object.__setattr__(config, "TRIGGERS_DATABASE_PATH", ":memory:")
        object.__setattr__(config, "AUTO_SYNC_GUILD_EMOJIS", False)

        bot = TicketBot()
        try:
            await bot._async_setup_hook()
            await bot.setup_hook()

            # ai_control, modules (card commands) and welcome (join greeting)
            self.assertEqual(len(bot.extensions), 8)
            # The panel is hardcoded now, so setup and category commands are gone.
            self.assertGreaterEqual(len(list(bot.walk_commands())), 31)
            self.assertGreaterEqual(len(bot.tree.get_commands()), 17)
        finally:
            await bot.close()
            for name, value in original_values.items():
                object.__setattr__(config, name, value)


if __name__ == "__main__":
    unittest.main()
