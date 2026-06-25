import unittest

from jmbot.core.catalog_service import AlbumDetail
from jmbot.core.formatter import (
    format_album_detail,
    format_admin_failed_tasks,
    format_help,
    format_query_failed,
    format_queue_summary,
    format_task_failed,
    format_task_history,
)
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

    def test_format_help_lists_detail_but_not_removed_search_features(self):
        message = format_help("/jm")

        self.assertIn("/jm 详情 123456", message)
        self.assertIn("/jm 封面 123456", message)
        self.assertIn("/jm 预览 123456", message)
        self.assertNotIn("搜索", message)
        self.assertNotIn("/jm 下载 1", message)
        self.assertNotIn("zip", message.lower())

    def test_format_album_detail_limits_tags_and_chapters(self):
        detail = AlbumDetail(
            album_id="123456",
            title="测试漫画",
            authors=("作者甲", "作者乙"),
            page_count=120,
            tags=tuple(f"标签{i}" for i in range(12)),
            chapters=tuple(f"第{i}话" for i in range(12)),
            update_time="2026-06-25",
        )

        message = format_album_detail(detail)

        self.assertIn("JM123456", message)
        self.assertIn("标题：测试漫画", message)
        self.assertIn("作者：作者甲 / 作者乙", message)
        self.assertIn("页数：120", message)
        self.assertIn("标签9", message)
        self.assertNotIn("标签10", message)
        self.assertIn("第9话", message)
        self.assertNotIn("第10话", message)
        self.assertIn("/jm 123456", message)

    def test_format_album_detail_omits_missing_optional_fields(self):
        detail = AlbumDetail(
            album_id="123456",
            title="测试漫画",
            authors=(),
            page_count=None,
            tags=(),
            chapters=(),
            update_time=None,
        )

        message = format_album_detail(detail)

        self.assertIn("JM123456", message)
        self.assertIn("标题：测试漫画", message)
        self.assertNotIn("作者：", message)
        self.assertNotIn("页数：", message)
        self.assertNotIn("标签：", message)
        self.assertNotIn("章节：", message)
        self.assertNotIn("更新时间：", message)
        self.assertNotIn("未知", message)

    def test_format_query_failed_hides_raw_exception(self):
        message = format_query_failed("详情")

        self.assertEqual(message, "详情查询暂时不可用，请稍后重试。")
        self.assertNotIn("Traceback", message)

    def test_admin_failed_formats_napcat_missing_mount_as_diagnosis(self):
        job = AlbumQueueJob(
            id=21,
            album_id="1020215",
            destination_type="private",
            group_id=None,
            user_id=3065532050,
            status="failed",
            attempts=1,
            priority=0,
            error="ActionFailed(retcode=1200, message=\"ENOENT: no such file or directory, open '/opt/jmbot/data/output/JM1020215.pdf'\")",
            created_at="2026-06-25T00:00:00.000Z",
            updated_at="2026-06-25T00:00:00.000Z",
        )

        message = format_admin_failed_tasks([job])

        self.assertIn("NapCat 无法访问输出文件", message)
        self.assertIn("检查 /opt/jmbot 挂载", message)
        self.assertNotIn("ActionFailed", message)

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
