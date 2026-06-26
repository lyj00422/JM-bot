import tempfile
import unittest
from pathlib import Path

from jmbot.core.user_store import UserDataStore


class UserDataStoreTest(unittest.TestCase):
    def test_add_remove_and_list_favorites(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserDataStore(Path(tmp) / "user.sqlite3")

            first = store.add_favorite(42, "111111")
            duplicate = store.add_favorite(42, "111111")
            store.add_favorite(42, "222222")
            store.add_favorite(99, "333333")
            favorites = store.list_favorites(42)
            removed = store.remove_favorite(42, "111111")
            missing = store.remove_favorite(42, "999999")
            after_remove = store.list_favorites(42)

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual([item.album_id for item in favorites], ["222222", "111111"])
        self.assertTrue(removed)
        self.assertFalse(missing)
        self.assertEqual([item.album_id for item in after_remove], ["222222"])

    def test_records_and_decides_friend_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserDataStore(Path(tmp) / "user.sqlite3")

            record = store.record_friend_request(user_id=123, flag="flag-a", comment="hello")
            same = store.record_friend_request(user_id=123, flag="flag-a", comment="new")
            pending = store.list_friend_requests(status="pending")
            decided = store.decide_friend_request(record.id, approve=True, admin_user_id=42)
            second_decision = store.decide_friend_request(record.id, approve=False, admin_user_id=42)

        self.assertEqual(record.id, same.id)
        self.assertEqual(same.comment, "new")
        self.assertEqual(len(pending), 1)
        self.assertIsNotNone(decided)
        self.assertEqual(decided.status, "approved")
        self.assertEqual(decided.admin_user_id, 42)
        self.assertIsNone(second_decision)


if __name__ == "__main__":
    unittest.main()
