import tempfile
import unittest
from pathlib import Path

from jmbot.core.command_service import (
    QueueTarget,
    admin_clear_queue,
    admin_failed_tasks,
    admin_jump_album,
    admin_kill_album,
    cancel_album_request,
    enqueue_album_request,
    my_tasks_summary,
    retry_failed_album,
    task_history,
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

    def test_my_tasks_summary_only_shows_current_user_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)
            other_user = QueueTarget(destination_type="group", group_id=10001, user_id=99)
            enqueue_album_request(queue, "111111", target)
            enqueue_album_request(queue, "222222", other_user)

            message = my_tasks_summary(queue, target)

        self.assertIn("你当前的任务", message)
        self.assertIn("JM111111", message)
        self.assertNotIn("JM222222", message)

    def test_task_history_filters_current_user_and_failed_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            other = QueueTarget(destination_type="private", group_id=None, user_id=99)
            done = queue.enqueue_private("111111", user_id=42)
            failed = queue.enqueue_private("222222", user_id=42)
            other_failed = queue.enqueue_private("333333", user_id=99)
            queue.mark_done(done.id)
            queue.mark_failed(failed.id, "network failed")
            queue.mark_failed(other_failed.id, "other failed")

            all_message = task_history(queue, target)
            failed_message = task_history(queue, target, failed_only=True)
            other_message = task_history(queue, other, failed_only=True)

        self.assertIn("JM111111", all_message)
        self.assertIn("JM222222", all_message)
        self.assertNotIn("JM333333", all_message)
        self.assertIn("JM222222", failed_message)
        self.assertNotIn("JM111111", failed_message)
        self.assertIn("JM333333", other_message)

    def test_retry_failed_album_requeues_own_failed_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)
            failed = queue.enqueue_group("111111", group_id=10001, user_id=42)
            queue.mark_failed(failed.id, "network failed")

            message = retry_failed_album(queue, "111111", target)
            active_jobs = queue.list_active_for_user(destination_type="group", group_id=10001, user_id=42)

        self.assertIn("已重新加入队列", message)
        self.assertEqual([job.album_id for job in active_jobs], ["111111"])

    def test_retry_failed_album_rejects_missing_or_other_user_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)
            other_failed = queue.enqueue_group("111111", group_id=10001, user_id=99)
            queue.mark_failed(other_failed.id, "other failed")

            message = retry_failed_album(queue, "111111", target)

        self.assertIn("没有找到", message)

    def test_admin_failed_tasks_lists_global_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            failed = queue.enqueue_private("111111", user_id=42)
            done = queue.enqueue_private("222222", user_id=43)
            queue.mark_failed(failed.id, "network failed")
            queue.mark_done(done.id)

            message = admin_failed_tasks(queue)

        self.assertIn("最近失败任务", message)
        self.assertIn("JM111111", message)
        self.assertNotIn("JM222222", message)


if __name__ == "__main__":
    unittest.main()
