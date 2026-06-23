import tempfile
import unittest
from pathlib import Path

from jmbot.core.command_service import (
    QueueTarget,
    admin_clear_queue,
    admin_jump_album,
    admin_kill_album,
    cancel_album_request,
    enqueue_album_request,
)
from jmbot.core.persistent_queue import PersistentAlbumQueue


class CommandServiceTest(unittest.TestCase):
    def test_enqueue_duplicate_reports_existing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)

            first = enqueue_album_request(queue, "111111", target)
            duplicate = enqueue_album_request(queue, "111111", target)

        self.assertIn("已加入队列", first)
        self.assertIn("已经在队列中", duplicate)

    def test_cancel_album_request_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            enqueue_album_request(queue, "111111", target)

            canceled = cancel_album_request(queue, "111111", target)
            missing = cancel_album_request(queue, "222222", target)

        self.assertIn("已撤回", canceled)
        self.assertIn("没有找到", missing)

    def test_admin_clear_kill_and_jump_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)
            other_target = QueueTarget(destination_type="group", group_id=10002, user_id=43)
            enqueue_album_request(queue, "111111", target)
            enqueue_album_request(queue, "111111", other_target)

            killed_current = admin_kill_album(queue, "111111", target, all_targets=False)
            killed_all = admin_kill_album(queue, "111111", target, all_targets=True)
            jump_message = admin_jump_album(queue, "222222", target)
            clear_message = admin_clear_queue(queue)

        self.assertIn("已取消 1 个", killed_current)
        self.assertIn("已取消 1 个", killed_all)
        self.assertIn("已插队", jump_message)
        self.assertIn("已清空", clear_message)


if __name__ == "__main__":
    unittest.main()
