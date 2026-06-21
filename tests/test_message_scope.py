import unittest
from types import SimpleNamespace

from core.message_scope import is_friend_private_message


class MessageScopeTest(unittest.TestCase):
    def test_accepts_friend_private_message(self):
        event = SimpleNamespace(message_type="private", sub_type="friend")

        self.assertTrue(is_friend_private_message(event))

    def test_rejects_non_friend_private_message(self):
        group_temp_event = SimpleNamespace(message_type="private", sub_type="group")

        self.assertFalse(is_friend_private_message(group_temp_event))

    def test_rejects_group_message(self):
        group_event = SimpleNamespace(message_type="group", sub_type="normal")

        self.assertFalse(is_friend_private_message(group_event))


if __name__ == "__main__":
    unittest.main()
