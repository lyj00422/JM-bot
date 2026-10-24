import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet

from jmbot.admin_app import AdminStore, SUPER_ADMIN_ID, create_admin_app
from jmbot.core.chat_service import ChatStore


class AdminAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.admin_db, self.conversation_db, self.stickers = root / "admin.sqlite3", root / "conversation.sqlite3", root / "stickers"
        self.chat = ChatStore(root / "chat.sqlite3", master_key=Fernet.generate_key())
        self.chat.set_default_credential("ds_school", "default-secret")
        store = AdminStore(self.admin_db)
        store.set_password(SUPER_ADMIN_ID, "a-strong-test-password", is_super=True)
        self.app = create_admin_app(admin_db=self.admin_db, conversation_db=self.conversation_db, sticker_dir=self.stickers, chat_store=self.chat)
        self.admins = AdminStore(self.admin_db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_authentication_and_super_admin_management(self):
        self.assertIsNone(self.admins.authenticate(SUPER_ADMIN_ID, "wrong-password"))
        token = self.admins.authenticate(SUPER_ADMIN_ID, "a-strong-test-password")
        self.assertIsNotNone(token)
        self.assertEqual(self.admins.session_admin(token), SUPER_ADMIN_ID)
        self.admins.set_password(42, "another-strong-password")
        self.assertFalse(self.admins.is_super(42))
        self.assertTrue(self.admins.is_admin(42))

    def test_scope_policy_and_persona_are_available(self):
        state = self.app.routes
        self.assertTrue(any(getattr(route, "path", "") == "/api/scopes/{kind}/{owner_id}/policy" for route in state))
        self.assertTrue(any(getattr(route, "path", "") == "/api/persona/default" for route in state))
        self.assertTrue(any(getattr(route, "path", "") == "/api/scopes/{kind}/{owner_id}/persona/effective" for route in state))
        self.assertTrue(any(getattr(route, "path", "") == "/api/download/queue/{job_id}/failed" for route in state))

    def test_credentials_never_return_secret(self):
        self.assertTrue(self.chat.get_default_credential("ds_school"))
        self.assertNotIn("default-secret", str(self.app.openapi()))

    def test_non_super_admin_cannot_add_admin(self):
        self.admins.set_password(42, "another-strong-password")
        self.assertFalse(self.admins.is_super(42))
        self.assertTrue(self.admins.is_admin(42))
        with self.assertRaisesRegex(ValueError, "不能删除"):
            self.admins.remove_admin(SUPER_ADMIN_ID)

    def test_download_control_actions_are_claimed_and_completed(self):
        action_id = self.admins.queue_control_action("download_delete_failed", {"job_id": 7})
        claimed = self.admins.claim_control_actions()
        self.assertEqual([(item["id"], item["name"], item["payload"]) for item in claimed], [(action_id, "download_delete_failed", {"job_id": 7})])
        self.admins.complete_control_action(action_id, result={"job_id": 7, "deleted": True})
        action = self.admins.control_actions(1)[0]
        self.assertEqual((action["status"], action["result"], action["error"]), ("done", {"job_id": 7, "deleted": True}, None))

    def test_admin_heartbeat_is_recorded(self):
        self.admins.heartbeat("jmbot-admin", True, "test")
        self.assertTrue(self.admins.service_status()["jmbot-admin"]["healthy"])


if __name__ == "__main__":
    unittest.main()
