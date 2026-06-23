import unittest

from jmbot.core.formatter import format_help, format_queue_summary, format_task_failed, format_task_history
from jmbot.core.persistent_queue import AlbumQueueJob


class FormatterTest(unittest.TestCase):
    def test_format_help_lists_existing_user_commands(self):
        message = format_help("/jm")

        self.assertIn("/jm 123456", message)
        self.assertIn("/jm 撤回 123456", message)
        self.assertIn("/jm 队列", message)
        self.assertIn("/jm 我的", message)
        self.assertIn("/jm 历史", message)
        self.assertIn("/jm 历史 失败", message)
        self.assertIn("/jm 重试 123456", message)
        self.assertIn("/jm 帮助", message)

    def test_format_help_lists_existing_admin_commands(self):
        message = format_help("/jm")

        self.assertIn("/jm 管理 清空", message)
        self.assertIn("/jm 管理 kill 123456", message)
        self.assertIn("/jm 管理 kill 全部 123456", message)
        self.assertIn("/jm 管理 插队 123456", message)
        self.assertIn("/jm 管理 失败", message)

    def test_format_help_does_not_advertise_future_features(self):
        message = format_help("/jm")

        self.assertNotIn("搜索", message)
        self.assertNotIn("详情", message)
        self.assertNotIn("zip", message.lower())

    def test_format_queue_summary_reports_empty_queue(self):
        message = format_queue_summary([])

        self.assertEqual(message, "队列为空。")

    def test_format_queue_summary_lists_active_jobs_with_limit(self):
        jobs = [
            AlbumQueueJob(
                id=1,
                album_id="111111",
                destination_type="group",
                group_id=10001,
                user_id=42,
                status="processing",
                attempts=1,
                priority=0,
                error=None,
                created_at="2026-06-23T00:00:00.000Z",
                updated_at="2026-06-23T00:00:00.000Z",
            ),
            AlbumQueueJob(
                id=2,
                album_id="222222",
                destination_type="private",
                group_id=None,
                user_id=43,
                status="pending",
                attempts=0,
                priority=0,
                error=None,
                created_at="2026-06-23T00:00:00.000Z",
                updated_at="2026-06-23T00:00:00.000Z",
            ),
        ]

        message = format_queue_summary(jobs, limit=1)

        self.assertIn("当前队列共 2 个任务", message)
        self.assertIn("#1 JM111111 processing 群 10001", message)
        self.assertNotIn("JM222222", message)
        self.assertIn("还有 1 个未显示", message)

    def test_format_task_failed_hides_raw_error(self):
        message = format_task_failed("123456")

        self.assertIn("JM123456 处理失败", message)
        self.assertIn("联系管理员", message)
        self.assertNotIn("Traceback", message)

    def test_format_task_history_hides_failed_job_raw_error(self):
        job = AlbumQueueJob(
            id=1,
            album_id="123456",
            destination_type="private",
            group_id=None,
            user_id=42,
            status="failed",
            attempts=1,
            priority=0,
            error='RuntimeError: child failed\nTraceback (most recent call last):\n  File "C:/secret/path.py", line 1',
            created_at="2026-06-23T00:00:00.000Z",
            updated_at="2026-06-23T00:00:00.000Z",
        )

        message = format_task_history([job], failed_only=True)

        self.assertIn("JM123456 failed", message)
        self.assertIn("失败原因已记录", message)
        self.assertNotIn("RuntimeError", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("C:/secret", message)


if __name__ == "__main__":
    unittest.main()
