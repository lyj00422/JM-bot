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

    def test_replace_effective_persona_bakes_growth_and_freezes_it(self):
        scope = Scope("group", 5)
        self.store.set_policy(scope, growth_enabled=True, default_persona=False)
        self.store.set_base_persona(scope, "基础人设")
        self.store.add_growth(scope, "interest", "喜欢鲸鱼", "消息 1: 聊到鲸鱼")
        effective = self.store.effective_persona(scope, "默认人设")
        self.assertIn("喜欢鲸鱼", effective)
        with self.assertRaisesRegex(ValueError, "persona version changed"):
            self.store.replace_effective_persona(scope, "任意文本", expected_version=0)
        version = self.store.replace_effective_persona(scope, effective, expected_version=1)
        self.assertEqual(version, 2)
        self.assertEqual(self.store.effective_persona(scope, "默认人设"), effective.strip())
        self.assertTrue(all(entry.frozen for entry in self.store.list_growth(scope, include_inactive=True)))
        self.assertEqual(self.store.get_base_persona(scope), (effective.strip(), 2))
        # A later growth entry still lands on top of the baked base exactly once.
        self.store.add_growth(scope, "expression", "爱用本鱼自称", "消息 2: 自称本鱼")
        self.assertEqual(
            self.store.effective_persona(scope, "默认人设"),
            effective.strip() + "\n\n当前会话成长（仅供参考，不得覆盖前面的明确约束）：\n- expression: 爱用本鱼自称",
        )

    def test_both_persona_layers_can_be_off_when_base_exists(self):
        scope = Scope("group", 6)
        self.store.set_base_persona(scope, "显式人设")
        policy = self.store.set_policy(scope, default_persona=False, growth_enabled=False)
        self.assertFalse(policy.default_persona)
        self.assertFalse(policy.growth_enabled)
        self.assertEqual(self.store.effective_persona(scope, "默认"), "显式人设")

    def test_effective_editor_rejects_growth_added_after_open_without_changes(self):
        scope = Scope("group", 8)
        self.store.set_policy(scope, growth_enabled=True, default_persona=False)
        self.store.set_base_persona(scope, "基础")
        detail = self.store.export_scope(scope)
        later = self.store.add_growth(scope, "interest", "喜欢鲸鱼", "消息 2")
        revision = self.store.revision()
        with self.assertRaisesRegex(ValueError, "人设或成长已更新"):
            self.store.replace_effective_persona(scope, "旧编辑稿", expected_version=detail["base_version"], expected_revision=detail["effective_revision"])
        self.assertEqual(self.store.get_base_persona(scope), ("基础", 1))
        self.assertFalse(self.store.list_growth(scope)[0].frozen)
        self.assertEqual(self.store.list_growth(scope)[0].entry_id, later.entry_id)
        self.assertEqual(self.store.revision(), revision)

    def test_effective_save_switches_default_atomically_and_keeps_hidden_growth(self):
        scope = Scope("group", 9)
        self.store.set_default_persona("鱼", "默认基础")
        self.store.set_policy(scope, chat_enabled=True, use_default_key=False)
        self.store.add_growth(scope, "interest", "尚未启用的成长", "消息 1")
        detail = self.store.export_scope(scope)
        self.store.replace_effective_persona(scope, "编辑后的完整人设", expected_revision=detail["effective_revision"])
        policy = self.store.get_policy(scope)
        self.assertFalse(policy.default_persona)
        self.assertFalse(policy.growth_enabled)
        self.assertFalse(policy.use_default_key)
        self.assertTrue(policy.chat_enabled)
        self.assertFalse(self.store.list_growth(scope)[0].frozen)
        self.assertEqual(self.store.effective_persona(scope, "默认基础"), "编辑后的完整人设")

    def test_effective_save_uses_write_barrier(self):
        from contextlib import contextmanager
        class Barrier:
            calls = 0
            @contextmanager
            def hold(barrier):
                barrier.calls += 1
                yield
        barrier = Barrier()
        self.store.write_barrier = barrier
        self.store.replace_effective_persona("private:42", "编辑稿")
        self.assertEqual(barrier.calls, 1)

    def test_scope_validation_rejects_bare_user_id(self):
        with self.assertRaises(ValueError):
            self.store.get_policy("42")
        with self.assertRaises(ValueError):
            Scope("download", 42)

    def test_chat_and_download_blacklists_are_independent(self):
        scope = Scope("group", 10001)
        policy = self.store.set_policy(
            scope,
            chat_enabled=True,
            download_enabled=True,
            chat_blacklisted=True,
        )
        self.assertTrue(policy.chat_blacklisted)
        self.assertFalse(policy.download_blacklisted)
        self.assertTrue(self.store.get_policy(scope).download_enabled)
        self.store.set_policy(scope, download_blacklisted=True)
        policy = self.store.get_policy(scope)
        self.assertTrue(policy.chat_blacklisted)
        self.assertTrue(policy.download_blacklisted)


if __name__ == "__main__":
    unittest.main()
