import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from utils.command_sync import CommandSyncCache, command_signature


def make_tree(*commands):
    return SimpleNamespace(get_commands=lambda: list(commands))


def make_command(name, description=""):
    return SimpleNamespace(name=name, description=description, parameters=[])


class CommandSyncCacheTests(unittest.TestCase):
    def test_same_commands_reuse_signature(self):
        tree = make_tree(make_command("ticket", "Open a ticket"), make_command("help", "Show help"))
        rebuilt = make_tree(make_command("help", "Show help"), make_command("ticket", "Open a ticket"))
        self.assertEqual(command_signature(tree), command_signature(rebuilt))

    def test_changed_description_invalidates_signature(self):
        self.assertNotEqual(
            command_signature(make_tree(make_command("ticket", "Open a ticket"))),
            command_signature(make_tree(make_command("ticket", "Open a support ticket"))),
        )

    def test_cache_skips_unchanged_commands(self):
        tree = make_tree(make_command("help"))
        with tempfile.TemporaryDirectory() as directory:
            cache = CommandSyncCache(Path(directory) / "command_sync.json")
            self.assertFalse(cache.is_current(tree))

            cache.remember(tree)
            self.assertTrue(cache.is_current(tree))
            self.assertFalse(cache.is_current(make_tree(make_command("help"), make_command("ping"))))

    def test_missing_cache_file_is_not_current(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CommandSyncCache(Path(directory) / "command_sync.json")
            self.assertFalse(cache.is_current(make_tree(make_command("help"))))

    def test_in_memory_database_disables_cache(self):
        cache = CommandSyncCache.next_to_database(":memory:")
        tree = make_tree(make_command("help"))
        cache.remember(tree)
        self.assertFalse(cache.is_current(tree))

    def test_cache_sits_next_to_database(self):
        cache = CommandSyncCache.next_to_database("/app/data/bot.db")
        self.assertEqual(cache.path, Path("/app/data/command_sync.json"))


if __name__ == "__main__":
    unittest.main()
