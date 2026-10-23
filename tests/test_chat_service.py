import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from jmbot.core.chat_service import (
    ChatAPIError,
    ChatRole,
    ChatStore,
    StoredMessage,
    build_group_messages,
    build_private_messages,
    extract_chat_reply,
    face_marker,
    parse_group_decision,
    parse_group_response,
    request_chat_completion,
    split_chat_reply,
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
    def test_unreadable_ciphertext_is_not_reencrypted_into_the_api_key(self):
        # 回归：主密钥被换掉后旧密文解不开，老逻辑会把密文再加密回写，并把它当
        # API Key 发给上游（必然 401）。现在应视为未配置，且不再改写数据库。
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "chat.sqlite3"
            store = ChatStore(db_path)
            store.set_private_key(30001, "sk-old-secret")

            rotated = ChatStore(db_path, master_key=Fernet.generate_key().decode())
            self.assertIsNone(rotated.get_private_key(30001))

            # 没有被二次包装：换回原主密钥仍能读出真正的 Key。
            self.assertEqual(store.get_private_key(30001), "sk-old-secret")

    def test_legacy_plaintext_key_is_still_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "chat.sqlite3"
            store = ChatStore(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "INSERT INTO private_api_keys (user_id, api_key, updated_at) "
                    "VALUES (30002, 'sk-legacy', '2026-01-01T00:00:00Z')"
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(store.get_private_key(30002), "sk-legacy")
            connection = sqlite3.connect(db_path)
            try:
                raw = connection.execute(
                    "SELECT api_key FROM private_api_keys WHERE user_id = 30002"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertTrue(raw.startswith("gAAAA"))

    def test_request_rejects_non_ascii_key_without_network_call(self):
        with self.assertRaises(ChatAPIError):
            request_chat_completion(
                "群聊 1053512745 sk-secret",
                [{"role": "user", "content": "hi"}],
                endpoint="https://example.invalid/v1/chat/completions",
                model="m",
                timeout_seconds=1,
            )

    def test_default_role_is_female_da_fei_yu_without_unicode_emoji(self):
        from jmbot.core.chat_service import DEFAULT_ROLE_NAME, DEFAULT_ROLE_PROMPT

        self.assertEqual(DEFAULT_ROLE_NAME, "大肥鱼")
        self.assertIn("禁止使用 Unicode emoji", DEFAULT_ROLE_PROMPT)

    def test_split_chat_reply_allows_whitelisted_qq_faces_and_removes_unicode_emoji(self):
        chunks = split_chat_reply("好呀 [qq_face:14] 😊 [qq_face:999]", max_text_length=100)

        self.assertEqual(
            chunks,
            [[("text", "好呀 "), ("face", "14"), ("text", "  [qq_face:999]")]],
        )

    def test_split_chat_reply_limits_text_length(self):
        chunks = split_chat_reply("甲" * 5 + "乙" * 5, max_text_length=5)

        self.assertEqual(chunks, [[("text", "甲" * 5)], [("text", "乙" * 5)]])

    def test_chat_store_encrypts_group_keys_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "chat.sqlite3"
            store = ChatStore(db_path)
            store.set_group_key(10001, "sk-group-secret")
            connection = sqlite3.connect(db_path)
            try:
                stored = connection.execute("SELECT api_key FROM group_api_keys").fetchone()[0]
            finally:
                connection.close()
            self.assertNotEqual(stored, "sk-group-secret")
            self.assertEqual(store.get_group_key(10001), "sk-group-secret")

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

    def test_api_keys_are_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "chat.sqlite3"
            key_path = Path(tmp) / "master.key"
            store = ChatStore(db_path, master_key_path=key_path)
            store.set_private_key(42, "sk-secret")

            connection = sqlite3.connect(db_path)
            try:
                stored = connection.execute("SELECT api_key FROM private_api_keys").fetchone()[0]
            finally:
                connection.close()
            self.assertNotEqual(stored, "sk-secret")
            self.assertEqual(store.get_private_key(42), "sk-secret")
            self.assertTrue(key_path.is_file())

    def test_chat_enabled_defaults_on_and_persists_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp) / "chat.sqlite3")
            self.assertTrue(store.is_chat_enabled())
            store.set_chat_enabled(False)
            self.assertFalse(store.is_chat_enabled())
            reopened = ChatStore(Path(tmp) / "chat.sqlite3")
            self.assertFalse(reopened.is_chat_enabled())

    def test_group_history_excludes_messages_outside_context_age_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "chat.sqlite3"
            store = ChatStore(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.executemany(
                    "INSERT INTO group_messages (group_id, sender_name, content, created_at) VALUES (?, ?, ?, ?)",
                    [
                        (10001, "旧人", "旧话题", "2020-01-01T00:00:00.000Z"),
                    ],
                )
                connection.execute(
                    "INSERT INTO group_messages (group_id, sender_name, content, created_at) "
                    "VALUES (10001, '小明', '新话题一', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-2 minutes'))"
                )
                connection.execute(
                    "INSERT INTO group_messages (group_id, sender_name, content, created_at) "
                    "VALUES (10001, '小红', '新话题二', strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 minute'))"
                )
                connection.commit()
            finally:
                connection.close()

            history = store.group_history(10001, limit=1, max_age_seconds=600)

            self.assertEqual([item.content for item in history], ["新话题二"])

    def test_latest_group_message_id_tracks_new_arrivals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp) / "chat.sqlite3")

            first_id = store.append_group_message(10001, "小明", "先说")
            second_id = store.append_group_message(10001, "小红", "后来")

            self.assertEqual(store.latest_group_message_id(10001), second_id)
            self.assertGreater(second_id, first_id)

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

    def test_group_prompt_only_asks_for_json_when_deciding(self):
        role = ChatRole("大肥鱼喵", "保持简短")
        passive = build_group_messages(role, [], "小红", "今天好热", decision_required=True)
        direct = build_group_messages(role, [], "小红", "今天好热", decision_required=False)

        self.assertIn("should_reply", passive[0]["content"])
        self.assertIn("只输出 JSON", passive[0]["content"])
        self.assertIn('"poke"', passive[0]["content"])
        self.assertIn("只输出 JSON", direct[0]["content"])
        self.assertIn('"reply"', direct[0]["content"])
        self.assertEqual(direct[-1]["content"], "[小红] 今天好热")

    def test_parse_group_response_supports_optional_poke_and_plain_text_fallback(self):
        self.assertEqual(
            parse_group_response(
                '{"should_reply": true, "reply": "戳你一下", "poke": true}',
                decision_required=True,
            ),
            (True, "戳你一下", True),
        )
        self.assertEqual(
            parse_group_response('{"reply": "来啦", "poke": false}', decision_required=False),
            (True, "来啦", False),
        )
        self.assertEqual(parse_group_response("普通文本", decision_required=False), (True, "普通文本", False))

    def test_extract_chat_reply_unwraps_decision_json(self):
        wrapped = '{"should_reply": true, "reply": "来啦来啦\\n天气自己看窗外"}'

        self.assertEqual(extract_chat_reply(wrapped), "来啦来啦\n天气自己看窗外")

    def test_extract_chat_reply_unwraps_fenced_json(self):
        wrapped = '```json\n{"should_reply": true, "reply": "在的"}\n```'

        self.assertEqual(extract_chat_reply(wrapped), "在的")

    def test_extract_chat_reply_keeps_plain_text(self):
        self.assertEqual(extract_chat_reply("  在的，怎么啦  "), "在的，怎么啦")
        self.assertEqual(extract_chat_reply('{"should_reply": true}'), '{"should_reply": true}')
        self.assertEqual(extract_chat_reply("{坏掉的 json"), "{坏掉的 json")

    def test_face_marker_uses_whitelisted_token(self):
        self.assertEqual(face_marker(14), "[qq_face:14]")
        self.assertEqual(face_marker("21"), "[qq_face:21]")
        self.assertEqual(face_marker(999), "[表情]")
        self.assertEqual(face_marker(""), "[表情]")

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

    @patch("jmbot.core.chat_service.urllib.request.build_opener")
    def test_request_uses_configured_proxy(self, build_opener):
        build_opener.return_value.open.return_value = _Response(
            {"choices": [{"message": {"content": "通过代理"}}]}
        )
        proxy = "http://127.0.0.1:8080"

        result = request_chat_completion(
            "sk-test",
            [{"role": "user", "content": "你好"}],
            endpoint="https://example.test/chat/completions",
            model="deepseek-flash",
            timeout_seconds=5,
            proxy=proxy,
        )

        self.assertEqual(result, "通过代理")
        self.assertEqual(
            build_opener.call_args.args[0].proxies,
            {"http": proxy, "https": proxy},
        )
        build_opener.return_value.open.assert_called_once()

    @patch.dict("os.environ", {"JM_BOT_CHAT_PROXY": "http://127.0.0.1:8080"})
    @patch("jmbot.core.chat_service.urllib.request.build_opener")
    def test_request_uses_proxy_from_environment(self, build_opener):
        build_opener.return_value.open.return_value = _Response(
            {"choices": [{"message": {"content": "通过代理"}}]}
        )

        result = request_chat_completion(
            "sk-test",
            [{"role": "user", "content": "你好"}],
            endpoint="https://example.test/chat/completions",
            model="deepseek-flash",
            timeout_seconds=5,
        )

        self.assertEqual(result, "通过代理")
        self.assertEqual(
            build_opener.call_args.args[0].proxies["https"],
            "http://127.0.0.1:8080",
        )


if __name__ == "__main__":
    unittest.main()
