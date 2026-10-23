import unittest

from jmbot.core.parser import (
    parse_chat_config_message,
    parse_ds_message,
    parse_jm_admin_message,
    is_removed_jm_admin_command,
    parse_jm_message,
)


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

    def test_retired_admin_prefix_is_blocked_as_a_chat_message(self):
        self.assertTrue(is_removed_jm_admin_command("/jmg 清空"))
        self.assertTrue(is_removed_jm_admin_command("  /JMG anything"))
        self.assertFalse(is_removed_jm_admin_command("/jm help"))
        self.assertFalse(is_removed_jm_admin_command("/jmgold"))

    def test_provider_and_school_key_commands(self):
        cases = {
            "/ds sk-test": ("ds_official", None, "sk-test"),
            "/ds 官方 sk-test": ("ds_official", None, "sk-test"),
            "/ds 学校 model key": ("ds_school", None, "model key"),
            "/GLM key": ("glm_official", None, "key"),
            "/kimi 12345\\secret": ("kimi_official", 12345, "secret"),
            "/qwen 12345\\secret": ("qwen_official", 12345, "secret"),
            "/ds 学校 12345\\secret": ("ds_school", 12345, "secret"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                command = parse_chat_config_message(raw)
                self.assertIsNotNone(command)
                self.assertEqual(command.action, "chat_group_key_set" if expected[1] else "chat_private_key")
                self.assertEqual((command.provider, command.group_id, command.text), expected)

    def test_malformed_explicit_group_key_is_not_a_private_key(self):
        self.assertIsNone(parse_chat_config_message("/ds 12345\\"))
        self.assertIsNone(parse_chat_config_message("/ds 学校"))
        self.assertIsNone(parse_chat_config_message("/glm 官方 key"))
        confirm = parse_chat_config_message("/ds 学校 12345\\确认")
        self.assertEqual((confirm.action, confirm.group_id, confirm.provider), ("chat_key_confirm", 12345, "ds_school"))

    def test_source_prefixed_private_control_words_are_not_saved_as_keys(self):
        for raw, provider in (
            ("/ds 学校 确认", "ds_school"),
            ("/ds 官方 取消", "ds_official"),
            ("/glm 学校 确认", "glm_school"),
            ("/kimi 学校 取消", "kimi_school"),
            ("/qwen 学校 确认", "qwen_school"),
        ):
            with self.subTest(raw=raw):
                command = parse_chat_config_message(raw)
                self.assertIsNotNone(command)
                self.assertEqual((command.action, command.provider, command.text), ("chat_key_confirm" if raw.endswith("确认") else "chat_key_cancel", provider, None))

    def test_source_prefixed_private_status_and_help_are_controls(self):
        status = parse_chat_config_message("/ds 学校 状态")
        self.assertEqual((status.action, status.provider), ("chat_private_status", "ds_school"))
        help_command = parse_chat_config_message("/qwen 学校 帮助 群聊")
        self.assertEqual((help_command.action, help_command.provider, help_command.help_topic), ("chat_help", "qwen_school", "群聊"))


if __name__ == "__main__":
    unittest.main()
