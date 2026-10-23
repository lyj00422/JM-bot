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
from jmbot.config import load_config
from nonebot.adapters.onebot.v11 import Message
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

    def test_persona_uses_the_same_super_face_whitelist(self):
        import re
        role = json.loads(Path('data/chat_role.json').read_text(encoding='utf-8'))
        ids = {int(value) for value in re.findall(r'\[qq_face:(\d+)\]', role['system_prompt'])}
        self.assertEqual(ids, set(SUPER_FACES))
        self.assertIn('《王者荣耀》', role['system_prompt'])


class AttentionBotTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.tmp.name) / 'chat.db')
        self.store.set_group_key(1, 'test-key')
        self.config = replace(load_config({}), group_chat_followup_cooldown_seconds=1)
        self.patches = [patch.object(bot_module, 'CHAT_STORE', self.store), patch.object(bot_module, 'CONFIG', self.config)]
        for item in self.patches:
            item.start()
        bot_module._group_chat_locks.clear()
        bot_module._group_chat_last_reply.clear()
        bot_module._group_chat_proactive_replies.clear()
        bot_module._attention_examined.clear()
        bot_module._attention_groups.clear()
        self.bot = SimpleNamespace(self_id='99', call_api=AsyncMock(return_value={'message_id': 800}))

    async def asyncTearDown(self):
        await bot_module._stop_attention_on_shutdown()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def event(self, message_id, text, to_me=False):
        message = Message(text)
        return SimpleNamespace(group_id=1, user_id=42, message_id=message_id, to_me=to_me,
                               reply=None, sender=SimpleNamespace(card='甲'), original_message=message,
                               get_message=lambda: message, get_plaintext=lambda: text)

    async def handle(self, event, response):
        with patch.object(bot_module, '_group_aliases', AsyncMock(return_value={'大肥鱼'})), patch.object(
            bot_module, 'request_chat_completion', return_value=response
        ) as request:
            await bot_module._handle_group_chat(self.bot, event)
            return request

    async def test_wake_quotes_then_followup_does_not_quote_or_use_proactive_budget(self):
        await self.handle(self.event(101, '在吗', True), '{"reply":"来了"}')
        first = self.bot.call_api.await_args.kwargs['message']
        self.assertEqual(first[0].type, 'reply')
        self.assertEqual(first[0].data['id'], '101')
        bot_module._group_chat_last_reply[1] = 0
        bot_module._group_chat_proactive_replies[1] = [time.time()] * 5
        await self.handle(self.event(102, '刚才那个呢'), '{"should_reply":true,"reply":"这个啊"}')
        second = self.bot.call_api.await_args.kwargs['message']
        self.assertTrue(all(item.type != 'reply' for item in second))
        self.assertEqual([item.role for item in self.store.group_history(1)], ['user', 'assistant', 'user', 'assistant'])

    async def test_sleeping_ignores_generic_question_and_expired_window(self):
        request = await self.handle(self.event(101, '怎么回事?'), '{"reply":"不要发"}')
        request.assert_not_called()
        self.store.set_group_active_until(1, time.time() - 1)
        request = await self.handle(self.event(102, '然后呢'), '{"reply":"不要发"}')
        request.assert_not_called()
        self.bot.call_api.assert_not_awaited()

    async def test_member_to_member_address_does_not_trigger_active_bot(self):
        self.store.set_group_active_until(1, time.time() + 100)
        event = self.event(101, '')
        event.original_message = Message('[CQ:at,qq=43]你觉得呢')
        request = await self.handle(event, '{"should_reply":true,"reply":"不要发"}')
        request.assert_not_called()

    async def test_unrelated_followup_can_stay_silent(self):
        self.store.set_group_active_until(1, time.time() + 100)
        request = await self.handle(self.event(101, '甲和乙聊天'), '{"should_reply":false,"reply":""}')
        request.assert_called_once()
        self.bot.call_api.assert_not_awaited()

    async def test_failed_send_does_not_store_reply_or_wake(self):
        self.bot.call_api.side_effect = RuntimeError('send failed')
        with self.assertRaises(RuntimeError):
            await self.handle(self.event(101, '在吗', True), '{"reply":"来了"}')
        self.assertFalse(self.store.is_group_active(1))
        self.assertEqual(len(self.store.group_history(1)), 1)

    async def test_reactions_are_scoped_deduplicated_and_pokes_use_current_sender(self):
        self.store.append_group_message(1, '甲', 'hi', user_id=42, message_id=101)
        history = self.store.group_history(1)
        action = GroupAction(poke=True, reaction=(101, 364))
        await bot_module._deliver_group_action(self.bot, 1, action, history, current_user_id=42)
        await bot_module._deliver_group_action(self.bot, 1, action, history, current_user_id=42)
        self.assertEqual([call.args[0] for call in self.bot.call_api.await_args_list], ['set_msg_emoji_like', 'group_poke'])
        self.bot.call_api.reset_mock()
        await bot_module._deliver_group_action(self.bot, 1, GroupAction(reaction=(999, 364)), history, current_user_id=42)
        self.bot.call_api.assert_not_awaited()

    async def test_random_observation_runs_once_for_new_messages_and_stops_when_disabled(self):
        self.store.append_group_message(1, '甲', 'hi', user_id=42, message_id=101)
        response = '{"should_reply":true,"reaction":{"message_id":101,"emoji_id":364}}'
        with patch.object(bot_module, 'request_chat_completion', return_value=response) as request:
            await bot_module._observe_group(self.bot, 1)
            await bot_module._observe_group(self.bot, 1)
            request.assert_called_once()
            self.store.set_chat_enabled(False)
            self.store.append_group_message(1, '甲', 'new', user_id=42, message_id=102)
            await bot_module._observe_group(self.bot, 1)
            request.assert_called_once()
        self.bot.call_api.assert_awaited_once()

    async def test_new_message_during_generation_drops_stale_observation(self):
        self.store.append_group_message(1, '甲', 'hi', user_id=42, message_id=101)
        def request(*args, **kwargs):
            self.store.append_group_message(1, '乙', 'new topic', user_id=43, message_id=102)
            return '{"should_reply":true,"reply":"old"}'
        with patch.object(bot_module, 'request_chat_completion', side_effect=request):
            await bot_module._observe_group(self.bot, 1)
        self.bot.call_api.assert_not_awaited()

    async def test_disconnect_cancels_worker_without_duplicate_tasks(self):
        bot_module._ensure_attention_worker(self.bot)
        first = bot_module._attention_tasks['99']
        bot_module._ensure_attention_worker(self.bot)
        self.assertIs(bot_module._attention_tasks['99'], first)
        await asyncio.sleep(0)
        await bot_module._stop_attention_on_disconnect(self.bot)
        self.assertTrue(first.cancelled())

    async def test_observation_does_not_block_direct_mention(self):
        self.store.append_group_message(1, '甲', 'hi', user_id=42, message_id=101)
        started = threading.Event()
        release = threading.Event()
        def request(*args, **kwargs):
            started.set()
            release.wait(5)
            return '{"should_reply":true,"reply":"old topic"}'
        with patch.object(bot_module, 'request_chat_completion', side_effect=request):
            observer = asyncio.create_task(bot_module._observe_group(self.bot, 1))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await asyncio.wait_for(self.handle(self.event(102, '在吗', True), '{"reply":"来了"}'), timeout=2)
            finally:
                release.set()
                await observer
        self.assertEqual(self.bot.call_api.await_count, 1)

    async def test_super_face_serialization_and_unsafe_output_filtering(self):
        matcher = AsyncMock()
        await bot_module._send_chat_reply(matcher, '[qq_face:14][CQ:face,id=21][qq_face:364][qq_face:375]')
        message = matcher.send.await_args.args[0]
        self.assertEqual(len(message), 1)
        self.assertEqual(message[0].type, 'face')
        self.assertEqual(message[0].data['id'], '364')

    async def test_failed_interaction_is_not_recorded_as_delivered(self):
        self.store.append_group_message(1, '甲', 'hi', user_id=42, message_id=101)
        self.bot.call_api.side_effect = RuntimeError('unsupported')
        delivered = await bot_module._deliver_group_action(
            self.bot, 1, GroupAction(reaction=(101, 364)), self.store.group_history(1), current_user_id=42,
        )
        self.assertFalse(delivered)
        self.assertFalse(self.store.interaction_sent(1, 101, 'reaction'))
        self.assertFalse(self.store.is_group_active(1))
