import tempfile
import unittest
import asyncio
import json
import sqlite3
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

from jmbot.core.chat_service import ChatStore, GroupAction, SUPER_FACES, parse_group_action, split_chat_reply
from jmbot.core.conversation_state import ConversationStateStore, Scope
from jmbot.config import load_config
from nonebot.adapters.onebot.v11 import Message, PokeNotifyEvent
import jmbot.bot as bot_module


class AttentionStoreTest(unittest.TestCase):
    def test_existing_database_migrates_without_losing_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chat.db'
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE group_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, sender_name TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))")
                connection.execute("INSERT INTO group_messages (group_id,sender_name,content) VALUES (1,'old','keep')")
                connection.commit()
            finally:
                connection.close()
            store = ChatStore(path)
            self.assertEqual(store.group_history(1)[0].content, 'keep')
            self.assertIsNone(store.group_history(1)[0].message_id)
            store.append_group_message(1, 'new', 'hello', user_id=42, message_id=101)
            self.assertEqual(len(ChatStore(path).group_history(1)), 2)

    def test_yellow_faces_are_removed_and_super_faces_survive(self):
        self.assertEqual(split_chat_reply('[qq_face:14][qq_face:21][qq_face:364]'), [[('face', '364')]])

    def test_group_metadata_survives_reopen_and_bot_does_not_supersede_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'chat.db'
            store = ChatStore(path)
            first = store.append_group_message(1, '甲', 'hi', user_id=42, message_id=101)
            store.append_group_message(1, '鱼', 'hello', user_id=99, message_id=102, role='assistant')
            store = ChatStore(path)
            history = store.group_history(1)
            self.assertEqual(history[0].message_id, 101)
            self.assertEqual(history[0].user_id, 42)
            self.assertEqual(history[1].role, 'assistant')
            self.assertEqual(store.latest_group_message_id(1), first)

    def test_action_parser_allows_interaction_only_and_ignores_invalid_targets(self):
        action = parse_group_action(json.dumps({'should_reply': True, 'reply': '', 'reaction': {'message_id': 101, 'emoji_id': 364}}))
        self.assertEqual(action, GroupAction(reaction=(101, 364)))
        self.assertEqual(parse_group_action('{"should_reply":false,"poke":true}'), GroupAction())
        self.assertIsNone(parse_group_action('{"should_reply":true,"reaction":{"message_id":101,"emoji_id":14}}').reaction)
        self.assertEqual(parse_group_action('{broken', direct=True).reply, '嗯?')
        self.assertEqual(parse_group_action('{"reply":"[qq_face:14]"}', direct=True).reply, '嗯?')

    def test_observation_config_rejects_reversed_interval(self):
        with self.assertRaises(ValueError):
            load_config({'JM_BOT_GROUP_CHAT_OBSERVE_MIN_SECONDS': '300', 'JM_BOT_GROUP_CHAT_OBSERVE_MAX_SECONDS': '120'})

    def test_poke_rule_only_matches_pokes_targeting_the_bot(self):
        def poke(target_id, user_id=42):
            return PokeNotifyEvent(
                time=0, self_id=99, post_type='notice', notice_type='notify',
                sub_type='poke', user_id=user_id, group_id=1, target_id=target_id,
            )

        from jmbot.core.ingress import classify
        self.assertEqual(classify(poke(99).model_dump()).domain, 'chat')
        self.assertFalse(classify(poke(99).model_dump()).direct)
        self.assertEqual(classify(poke(43).model_dump()).domain, 'drop')
        self.assertEqual(classify(poke(99, user_id=99).model_dump()).domain, 'drop')

    def test_persona_uses_the_same_super_face_whitelist(self):
        import re
        role = json.loads(Path('data/chat_role.json').read_text(encoding='utf-8'))
        ids = {int(value) for value in re.findall(r'\[qq_face:(\d+)\]', role['system_prompt'])}
        self.assertEqual(ids, set(SUPER_FACES))
        self.assertIn('《王者荣耀》', role['system_prompt'])
