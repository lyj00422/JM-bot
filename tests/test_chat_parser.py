import unittest

from jmbot.core.parser import parse_ds_message, parse_jm_admin_message, parse_jm_message


class ChatParserTest(unittest.TestCase):
    def test_private_chat_commands(self):
        self.assertEqual(parse_ds_message("/ds sk-test").action, "chat_private_key")
        self.assertEqual(parse_ds_message("/ds 状态").action, "chat_private_status")
        self.assertEqual(parse_ds_message("/ds 清除").action, "chat_private_clear")
        self.assertEqual(parse_ds_message("/ds 清空").action, "chat_clear")
        self.assertEqual(parse_ds_message("/ds 帮助").action, "chat_help")
        self.assertIsNone(parse_jm_message("/ds 123456"))

    def test_chat_help_topics(self):
        self.assertIsNone(parse_ds_message("/ds 帮助").help_topic)
        self.assertIsNone(parse_ds_message("/ds help").help_topic)
        self.assertEqual(parse_ds_message("/ds 帮助 私聊").help_topic, "私聊")
        self.assertEqual(parse_ds_message("/ds help 群聊").help_topic, "群聊")
        self.assertEqual(parse_ds_message("/ds 帮助 管理").help_topic, "管理")
        self.assertEqual(parse_ds_message("/ds 帮助 未知").help_topic, "未知")
        # A key that merely starts with 帮助 must stay a key, not a help page.
        self.assertEqual(parse_ds_message("/ds 帮助abc").action, "chat_private_key")

    def test_admin_group_key_commands(self):
        set_command = parse_ds_message("/ds 群聊key 123456 sk-test")
        status_command = parse_ds_message("/ds 群聊key 123456 查看")
        clear_command = parse_ds_message("/ds 群聊key 123456 清除")

        self.assertEqual((set_command.action, set_command.group_id, set_command.text), ("chat_group_key_set", 123456, "sk-test"))
        self.assertEqual((status_command.action, status_command.group_id), ("chat_group_key_status", 123456))
        self.assertEqual((clear_command.action, clear_command.group_id), ("chat_group_key_clear", 123456))

    def test_group_key_command_accepts_spaced_alias(self):
        # 回归：以前 "/ds 群聊 123456 sk-test" 不匹配，会被当成私聊 Key 存下来，
        # 之后 "Bearer 群聊 ..." 触发 UnicodeEncodeError，私聊和群聊都哑掉。
        spaced = parse_ds_message("/ds 群聊 1053512745 sk-test")

        self.assertEqual((spaced.action, spaced.group_id, spaced.text), ("chat_group_key_set", 1053512745, "sk-test"))
        self.assertEqual(parse_ds_message("/ds 群聊 1053512745 查看").action, "chat_group_key_status")
        self.assertEqual(parse_ds_message("/ds 群聊 1053512745 清除").action, "chat_group_key_clear")
        # 单独 "/ds 群聊" 仍然是查看状态的开关命令。
        self.assertEqual(parse_ds_message("/ds 群聊").action, "chat_status")

    def test_admin_chat_switch_commands(self):
        self.assertEqual(parse_ds_message("/ds 停止").action, "chat_disable")
        self.assertEqual(parse_ds_message("/ds 开启").action, "chat_enable")
        self.assertEqual(parse_ds_message("/ds 全局状态").action, "chat_status")
        self.assertIsNone(parse_jm_admin_message("/jmg 聊天停止"))


if __name__ == "__main__":
    unittest.main()
