import unittest

from jmbot.core.parser import parse_jm_command, parse_jm_message


class ParseJmCommandTest(unittest.TestCase):
    def test_parse_valid_jm_command(self):
        result = parse_jm_command("/jm 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "123456")

    def test_parse_allows_extra_spaces(self):
        result = parse_jm_command("  /jm   350234  ")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "350234")

    def test_parse_uses_custom_prefix(self):
        result = parse_jm_command("!jm 123456", command_prefix="!jm")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "123456")

    def test_parse_rejects_invalid_id(self):
        self.assertIsNone(parse_jm_command("/jm abc"))

    def test_parse_rejects_other_command(self):
        self.assertIsNone(parse_jm_command("/help 123456"))

    def test_parse_cancel_command(self):
        result = parse_jm_message("/jm 撤回 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "cancel")
        self.assertEqual(result.album_id, "123456")

    def test_parse_queue_command(self):
        result = parse_jm_message("/jm 队列")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "queue")
        self.assertIsNone(result.album_id)

    def test_parse_admin_commands(self):
        cases = [
            ("/jm 管理 清空", "admin_clear", None, False),
            ("/jm 管理 kill 123456", "admin_kill", "123456", False),
            ("/jm 管理 kill 全部 123456", "admin_kill", "123456", True),
            ("/jm 管理 插队 123456", "admin_jump", "123456", False),
        ]

        for message, action, album_id, all_targets in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, action)
                self.assertEqual(result.album_id, album_id)
                self.assertEqual(result.all_targets, all_targets)


if __name__ == "__main__":
    unittest.main()
