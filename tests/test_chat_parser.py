import unittest

from jmbot.core.parser import parse_jm_admin_message, parse_jm_message


class ChatParserTest(unittest.TestCase):
    def test_private_chat_commands(self):
        self.assertEqual(parse_jm_message("/ds sk-test").action, "chat_private_key")
        self.assertEqual(parse_jm_message("/ds 状态").action, "chat_private_status")
        self.assertEqual(parse_jm_message("/ds 清除").action, "chat_private_clear")
        self.assertEqual(parse_jm_message("/聊天 清空").action, "chat_clear")

    def test_admin_group_key_commands(self):
        set_command = parse_jm_admin_message("/jmg 群聊key 123456 sk-test")
        status_command = parse_jm_admin_message("/jmg 群聊key 123456 查看")
        clear_command = parse_jm_admin_message("/jmg 群聊key 123456 清除")

        self.assertEqual((set_command.action, set_command.group_id, set_command.text), ("chat_group_key_set", 123456, "sk-test"))
        self.assertEqual((status_command.action, status_command.group_id), ("chat_group_key_status", 123456))
        self.assertEqual((clear_command.action, clear_command.group_id), ("chat_group_key_clear", 123456))

    def test_admin_chat_switch_commands(self):
        self.assertEqual(parse_jm_admin_message("/jmg 聊天停止").action, "chat_disable")
        self.assertEqual(parse_jm_admin_message("/jmg 聊天开启").action, "chat_enable")
        self.assertEqual(parse_jm_admin_message("/jmg 聊天状态").action, "chat_status")


if __name__ == "__main__":
    unittest.main()
