import unittest

from jmbot.core.formatter import format_help


class FormatterTest(unittest.TestCase):
    def test_format_help_lists_existing_user_commands(self):
        message = format_help("/jm")

        self.assertIn("/jm 123456", message)
        self.assertIn("/jm 撤回 123456", message)
        self.assertIn("/jm 队列", message)
        self.assertIn("/jm 帮助", message)

    def test_format_help_lists_existing_admin_commands(self):
        message = format_help("/jm")

        self.assertIn("/jm 管理 清空", message)
        self.assertIn("/jm 管理 kill 123456", message)
        self.assertIn("/jm 管理 kill 全部 123456", message)
        self.assertIn("/jm 管理 插队 123456", message)

    def test_format_help_does_not_advertise_future_features(self):
        message = format_help("/jm")

        self.assertNotIn("搜索", message)
        self.assertNotIn("详情", message)
        self.assertNotIn("zip", message.lower())


if __name__ == "__main__":
    unittest.main()
