import unittest
from types import SimpleNamespace

from jmbot.core.message_scope import (
    at_targets,
    is_at_bot,
    is_friend_private_message,
    is_reply_to_bot,
    message_segments,
)

BOT_ID = 2661195769


class MessageScopeTest(unittest.TestCase):
    def test_accepts_friend_private_message(self):
        event = SimpleNamespace(message_type="private", sub_type="friend")

        self.assertTrue(is_friend_private_message(event))

    def test_rejects_non_friend_private_message(self):
        group_temp_event = SimpleNamespace(message_type="private", sub_type="group")

        self.assertFalse(is_friend_private_message(group_temp_event))

    def test_rejects_group_message(self):
        group_event = SimpleNamespace(message_type="group", sub_type="normal")

        self.assertFalse(is_friend_private_message(group_event))


def _segment(kind, **data):
    return SimpleNamespace(type=kind, data=data)


class _FakeEvent:
    """Mimics a OneBot v11 event after ``Bot.handle_event`` preprocessing."""

    def __init__(self, stripped, original, to_me=False):
        self.message = stripped
        self.original_message = original
        self.to_me = to_me

    def get_message(self):
        return self.message


class AtBotDetectionTest(unittest.TestCase):
    def test_finds_at_bot_in_original_message_after_adapter_stripped_it(self):
        event = _FakeEvent(
            stripped=[_segment("text", text="说话")],
            original=[_segment("at", qq=str(BOT_ID)), _segment("text", text=" 说话")],
            to_me=True,
        )

        self.assertEqual([segment.type for segment in event.get_message()], ["text"])
        self.assertEqual(at_targets(event), [str(BOT_ID)])
        self.assertTrue(is_at_bot(event, BOT_ID))

    def test_uses_original_message_even_without_to_me(self):
        # Different adapter versions/flags must not hide the at segment.
        event = _FakeEvent(
            stripped=[_segment("text", text="说话")],
            original=[_segment("at", qq=str(BOT_ID)), _segment("text", text=" 说话")],
        )

        self.assertTrue(is_at_bot(event, BOT_ID))

    def test_falls_back_to_to_me_when_no_at_segment_is_present(self):
        event = _FakeEvent(
            stripped=[_segment("text", text="说话")],
            original=[_segment("text", text="说话")],
            to_me=True,
        )

        self.assertFalse(at_targets(event))
        self.assertTrue(is_at_bot(event, BOT_ID))

    def test_ignores_at_of_other_members(self):
        event = _FakeEvent(
            stripped=[_segment("text", text="@3065532050 说话")],
            original=[_segment("at", qq="3065532050"), _segment("text", text=" 说话")],
        )

        self.assertEqual(at_targets(event), ["3065532050"])
        self.assertFalse(is_at_bot(event, BOT_ID))

    def test_returns_original_segments_from_real_onebot_event(self):
        event = _FakeEvent(
            stripped=[],
            original=[_segment("at", qq=str(BOT_ID)), _segment("reply", id="1")],
        )

        self.assertEqual([segment.type for segment in message_segments(event)], ["at", "reply"])


class ReplyToBotDetectionTest(unittest.TestCase):
    def test_detects_reply_resolved_by_adapter(self):
        # The adapter removes the reply segment from event.message and exposes
        # the quoted message through event.reply instead.
        event = SimpleNamespace(
            message=[_segment("text", text="说话")],
            original_message=[_segment("text", text="说话")],
            reply=SimpleNamespace(sender=SimpleNamespace(user_id=BOT_ID)),
        )

        self.assertTrue(is_reply_to_bot(event, BOT_ID))

    def test_detects_unresolved_reply_segment(self):
        event = SimpleNamespace(
            message=[_segment("text", text="说话")],
            original_message=[
                _segment("reply", id="1", sender_id=str(BOT_ID)),
                _segment("text", text="说话"),
            ],
        )

        self.assertTrue(is_reply_to_bot(event, BOT_ID))

    def test_ignores_reply_to_other_members(self):
        event = SimpleNamespace(
            message=[_segment("text", text="说话")],
            original_message=[_segment("text", text="说话")],
            reply=SimpleNamespace(sender=SimpleNamespace(user_id=3065532050)),
        )

        self.assertFalse(is_reply_to_bot(event, BOT_ID))

    def test_tolerates_events_without_reply_attribute(self):
        event = SimpleNamespace(
            message=[_segment("text", text="说话")],
            original_message=[_segment("text", text="说话")],
        )

        self.assertFalse(is_reply_to_bot(event, BOT_ID))


class MessageSegmentsFallbackTest(unittest.TestCase):
    def test_uses_get_message_when_original_message_is_absent(self):
        event = SimpleNamespace(
            message=[_segment("text", text="说话")],
            get_message=lambda: [_segment("text", text="说话")],
        )

        self.assertEqual([segment.type for segment in message_segments(event)], ["text"])


class OneBotAdapterPreprocessingTest(unittest.TestCase):
    """Guards against regressions in NoneBot's @/reply preprocessing."""

    def _group_event(self, message_text: str):
        from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

        return GroupMessageEvent(
            time=1792463935,
            self_id=BOT_ID,
            post_type="message",
            sub_type="normal",
            user_id=3065532050,
            message_type="group",
            message_id=1,
            group_id=1053512745,
            message=Message(message_text),
            raw_message=message_text,
            font=0,
            sender={"user_id": 3065532050, "nickname": "测试"},
        )

    def test_adapter_strips_at_bot_but_original_message_keeps_it(self):
        from nonebot.adapters.onebot.v11.bot import _check_at_me

        event = self._group_event(f"[CQ:at,qq={BOT_ID}] 说话")

        _check_at_me(None, event)

        self.assertTrue(event.to_me)
        self.assertEqual([segment.type for segment in event.get_message()], ["text"])
        self.assertEqual([segment.type for segment in event.original_message], ["at", "text"])
        self.assertTrue(is_at_bot(event, BOT_ID))

    def test_plain_text_mention_is_not_an_at_segment(self):
        from nonebot.adapters.onebot.v11.bot import _check_at_me

        event = self._group_event("@吃白饭大肥鱼 说话")

        _check_at_me(None, event)

        self.assertFalse(event.to_me)
        self.assertFalse(is_at_bot(event, BOT_ID))


if __name__ == "__main__":
    unittest.main()
