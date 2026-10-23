import tempfile
import unittest
from pathlib import Path

from jmbot.core.conversation_state import ConversationStateStore, Scope


class ConversationStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConversationStateStore(Path(self.tmp.name) / "conversation.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_conversations_with_same_member_are_separate_scopes(self):
        private = Scope("private", 42)
        group_a = Scope("group", 42)
        group_b = Scope("group", 99)
        self.store.set_base_persona(private, "private persona")
        self.store.set_base_persona(group_a, "group A persona")
        self.store.set_base_persona(group_b, "group B persona")
        self.assertEqual(self.store.get_base_persona(private)[0], "private persona")
        self.assertEqual(self.store.get_base_persona(group_a)[0], "group A persona")
        self.assertEqual(self.store.get_base_persona(group_b)[0], "group B persona")

    def test_policy_requires_default_persona_or_growth(self):
        group = Scope("group", 1)
        self.assertFalse(self.store.get_policy(group).chat_enabled)
        self.assertFalse(self.store.get_policy(group).download_enabled)
        with self.assertRaisesRegex(ValueError, "cannot both be disabled"):
            self.store.set_policy(group, default_persona=False)
        policy = self.store.set_policy(group, growth_enabled=True, default_persona=False)
        self.assertFalse(policy.default_persona)
        self.assertTrue(policy.growth_enabled)

    def test_growth_keeps_evidence_version_freeze_and_rollback_history(self):
        scope = Scope("private", 42)
        self.store.set_policy(scope, growth_enabled=True)
        first = self.store.add_growth(scope, "interest", "喜欢策略游戏", "消息 101: 最近聊到《文明》")
        second = self.store.add_growth(scope, "expression", "偏好简短回复", "消息 108: 说回复太长")
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertIn("喜欢策略游戏", self.store.effective_persona(scope, "默认人设"))
        self.store.set_growth_frozen(first.entry_id, True)
        self.assertNotIn("喜欢策略游戏", self.store.effective_persona(scope, "默认人设"))
        self.assertEqual(self.store.rollback_growth(scope, 1), 1)
        history = self.store.list_growth(scope, include_inactive=True)
        self.assertEqual(len(history), 2)
        self.assertFalse(history[1].active)
        self.assertEqual(history[0].evidence, "消息 101: 最近聊到《文明》")

    def test_growth_off_disables_layer_but_retains_entries(self):
        scope = Scope("group", 7)
        self.store.set_policy(scope, growth_enabled=True)
        self.store.add_growth(scope, "relationship", "成员甲是熟人", "成员甲多次参与聊天")
        self.store.set_policy(scope, growth_enabled=False)
        self.assertEqual(self.store.effective_persona(scope, "固定默认"), "固定默认")
        self.assertEqual(len(self.store.list_growth(scope)), 1)

    def test_scope_validation_rejects_bare_user_id(self):
        with self.assertRaises(ValueError):
            self.store.get_policy("42")
        with self.assertRaises(ValueError):
            Scope("download", 42)


if __name__ == "__main__":
    unittest.main()
