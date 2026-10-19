import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jmbot.core.chat_service import (
    ChatRole,
    ChatStore,
    StoredMessage,
    build_group_messages,
    build_private_messages,
    parse_group_decision,
    request_chat_completion,
)


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class ChatServiceTest(unittest.TestCase):
    def test_store_isolates_keys_history_and_group_balance_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp) / "chat.sqlite3")
            store.set_private_key(42, "private-key")
            store.set_group_key(10001, "group-key")
            store.append_private_message(42, "user", "你好")
            store.append_private_message(42, "assistant", "你好呀")
            store.append_group_message(10001, "小明", "大家好")
            store.mark_group_balance_exhausted(10001)

            self.assertEqual(store.get_private_key(42), "private-key")
            self.assertIsNone(store.get_private_key(99))
            self.assertEqual(store.private_history(42)[0].content, "你好")
            self.assertEqual(store.group_history(10001)[0].sender_name, "小明")
            self.assertTrue(store.is_group_balance_exhausted(10001))
            store.set_group_key(10001, "new-key")
            self.assertFalse(store.is_group_balance_exhausted(10001))

    def test_chat_enabled_defaults_on_and_persists_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp) / "chat.sqlite3")
            self.assertTrue(store.is_chat_enabled())
            store.set_chat_enabled(False)
            self.assertFalse(store.is_chat_enabled())
            reopened = ChatStore(Path(tmp) / "chat.sqlite3")
            self.assertFalse(reopened.is_chat_enabled())

    def test_private_and_group_messages_include_role_and_context(self):
        role = ChatRole("大肥鱼喵", "保持简短")
        private = build_private_messages(
            role,
            "用户喜欢晚安",
            [StoredMessage("user", "你好")],
            "今天怎么样",
        )
        group = build_group_messages(
            role,
            [StoredMessage("user", "大家好", "小明")],
            "小红",
            "你在吗",
        )
        self.assertIn("用户喜欢晚安", private[0]["content"])
        self.assertEqual(private[-1]["content"], "今天怎么样")
        self.assertIn("[小明] 大家好", group[1]["content"])
        self.assertIn("[小红] 你在吗", group[-1]["content"])

    def test_parse_group_decision_rejects_false_or_invalid_json(self):
        self.assertEqual(parse_group_decision('{"should_reply": false, "reply": "不要发"}'), (False, "不要发"))
        self.assertEqual(parse_group_decision('{"should_reply": "true", "reply": "发"}'), (True, "发"))
        self.assertEqual(parse_group_decision("not-json"), (False, ""))

    @patch("jmbot.core.chat_service.urllib.request.urlopen")
    def test_request_disables_thinking_and_supports_json_output(self, urlopen):
        urlopen.return_value = _Response(
            {"choices": [{"message": {"content": json.dumps({"should_reply": True})}}]}
        )
        result = request_chat_completion(
            "sk-test",
            [{"role": "user", "content": "你好"}],
            endpoint="https://example.test/chat/completions",
            model="deepseek-flash",
            timeout_seconds=5,
            json_output=True,
        )
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(result, '{"should_reply": true}')
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
