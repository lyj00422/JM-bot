import unittest

from jmbot.core.command_feedback import processing_ack_message
from jmbot.core.parser import JmMessage


class CommandFeedbackTest(unittest.TestCase):
    def test_returns_ack_for_commands_with_processing_time(self):
        cases = [
            (
                JmMessage(action="detail", album_id="123456"),
                "收到，正在获取 JM123456 详情。",
            ),
            (
                JmMessage(action="cover", album_id="123456"),
                "收到，正在获取 JM123456 封面。",
            ),
            (
                JmMessage(action="preview", album_id="123456"),
                "收到，正在生成 JM123456 预览。",
            ),
            (
                JmMessage(action="search", search_kind="site", keyword="关键词"),
                "收到，正在搜索：关键词。",
            ),
            (
                JmMessage(action="search", search_kind="author", keyword="作者名"),
                "收到，正在处理作者搜索：作者名。",
            ),
            (
                JmMessage(action="ranking"),
                "收到，正在查询排行榜。",
            ),
        ]

        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(processing_ack_message(command), expected)

    def test_returns_no_ack_for_immediate_commands(self):
        for command in [
            JmMessage(action="request", album_id="123456"),
            JmMessage(action="queue"),
            JmMessage(action="history"),
            JmMessage(action="help"),
        ]:
            with self.subTest(command=command):
                self.assertIsNone(processing_ack_message(command))


if __name__ == "__main__":
    unittest.main()
