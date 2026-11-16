import unittest

from utils import assistance
from utils.tickets import _target_category


class FakeGuild:
    def __init__(self, channels):
        self.channels = {channel_id: object() for channel_id in channels}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class HardcodedCategoryTests(unittest.TestCase):
    def test_every_panel_choice_has_its_own_category_channel(self):
        expected = {
            "General Support": 1563801523048161319,
            "Management Support": 1563801523228778497,
            "Partnership Support": 1563801523228778500,
        }
        for category in assistance.SUPPORT_CATEGORIES:
            self.assertEqual(category.channel_id, expected[category.label])
        self.assertEqual(assistance.category_channel_id("Management Support"), 1563801523228778497)

    def test_label_lookup_ignores_case_and_unknown_labels(self):
        self.assertEqual(assistance.category_channel_id("general support"), 1563801523048161319)
        self.assertEqual(assistance.category_channel_id("Something Else"), 0)
        self.assertEqual(assistance.category_channel_id(None), 0)

    def test_ticket_goes_to_the_choice_category_instead_of_the_configured_one(self):
        configured = object()
        guild = FakeGuild({1563801523228778500})
        self.assertIs(_target_category(guild, "Partnership Support", configured), guild.channels[1563801523228778500])
        self.assertIs(_target_category(guild, "General Support", configured), configured)


if __name__ == "__main__":
    unittest.main()
