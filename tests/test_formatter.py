import unittest

from jmbot.core.catalog_service import AlbumDetail, RankingQuery, RankingResult, SearchItem, SearchResult
from jmbot.core.formatter import (
    format_album_detail,
    format_admin_help,
    format_admin_failed_detail,
    format_admin_failed_tasks,
    format_chat_help,
    format_favorite_list,
    format_help,
    format_query_failed,
    format_queue_summary,
    format_ranking_card_commands,
    format_ranking_categories,
    format_ranking_result,
    format_ranking_usage,
    format_search_result,
    format_task_failed,
    format_task_history,
    is_public_nonpersistent_failure,
)
from jmbot.core.persistent_queue import AlbumQueueJob
from jmbot.core.user_store import FavoriteItem


class FormatterTest(unittest.TestCase):
    def test_help_index_is_short_and_topic_based(self):
        message = format_help("/jm", "/jmg")

        self.assertLessEqual(len([line for line in message.splitlines() if line.strip()]), 7)
        self.assertIn("/jm 帮助 查找", message)
        self.assertIn("/jm 帮助 排行", message)
        self.assertNotIn("/jmg", message)
        self.assertNotIn("kill 123456", message)

    def test_public_help_topics_are_bounded_and_specific(self):
        expectations = {
            "查找": "/jm 搜索 关键词",
            "排行": "/jm 排行 [文本]",
            "下载": "/jm 123456 zip",
            "收藏": "/jm 我的收藏",
            "任务": "/jm 撤回 123456",
        }
        for topic, command in expectations.items():
            with self.subTest(topic=topic):
                message = format_help("/jm", "/jmg", topic=topic)
                self.assertLessEqual(len([line for line in message.splitlines() if line.strip()]), 10)
                self.assertIn(command, message)

    def test_unknown_and_admin_help_are_separate(self):
        self.assertIn("查找｜排行｜下载｜收藏｜任务", format_help(topic="unknown"))
        admin = format_admin_help("/jmg")
        self.assertLessEqual(len([line for line in admin.splitlines() if line.strip()]), 10)
        self.assertIn("/jmg kill 123456", admin)
        self.assertIn("/jmg 失败 删除 21", admin)

    def test_chat_help_index_mirrors_the_main_help_style(self):
        message = format_chat_help("/ds")

        lines = [line for line in message.splitlines() if line.strip()]
        self.assertLessEqual(len(lines), 7)
        self.assertEqual(lines[0], "JMbot 聊天帮助：")
        self.assertIn("/ds 帮助 私聊", message)
        self.assertIn("/ds 帮助 群聊", message)
        self.assertIn("/ds 帮助 管理", message)
        for line in lines[1:]:
            self.assertIn("  - ", line)
        self.assertNotIn("群聊key 群号 API_KEY", message)

    def test_chat_help_topics_are_bounded_and_specific(self):
        expectations = {
            "私聊": "/ds 你的 API Key",
            "群聊": "/ds 群聊key 群号 查看",
            "管理": "/ds 开启",
        }
        for topic, command in expectations.items():
            with self.subTest(topic=topic):
                message = format_chat_help("/ds", topic=topic)
                self.assertLessEqual(len([line for line in message.splitlines() if line.strip()]), 6)
                self.assertIn(command, message)

    def test_chat_help_unknown_topic_lists_topics(self):
        self.assertEqual(format_chat_help("/ds", topic="未知"), "可用帮助主题：私聊｜群聊｜管理")

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

    def test_format_search_result_lists_items_with_pagination(self):
        result = SearchResult(
            keyword="柊ぽぷら",
            kind="author",
            page=1,
            page_count=3,
            total=25,
            items=(
                SearchItem("111111", "作者作品一", ("中文", "全彩")),
                SearchItem("222222", "作者作品二", ()),
            ),
        )

        message = format_search_result(result, "/jm")

        self.assertIn("作者「柊ぽぷら」作品", message)
        self.assertIn("第 1 / 3 页", message)
        self.assertIn("共 25 条", message)
        self.assertIn("1. JM111111 作者作品一", message)
        self.assertIn("标签：中文 / 全彩", message)
        self.assertIn("2. JM222222 作者作品二", message)
        self.assertIn("/jm 详情 111111", message)
        self.assertIn("下一页：/jm 作者 柊ぽぷら 2", message)
        self.assertNotIn("页数：", message)
        self.assertNotIn("更新时间：", message)
        self.assertNotIn("未知", message)

    def test_format_author_search_result_limits_items_to_five(self):
        result = SearchResult(
            keyword="author-name",
            kind="author",
            page=1,
            page_count=2,
            total=6,
            items=tuple(SearchItem(str(111110 + index), f"Title {index}", ()) for index in range(1, 7)),
        )

        message = format_search_result(result, "/jm", limit=5)

        self.assertIn("JM111115", message)
        self.assertNotIn("JM111116", message)

    def test_format_empty_search_result_is_short_and_actionable(self):
        result = SearchResult(
            keyword="不存在",
            kind="site",
            page=1,
            page_count=0,
            total=0,
            items=(),
        )

        message = format_search_result(result, "/jm")

        self.assertIn("没有搜到「不存在」", message)
        self.assertIn("换个关键词", message)
        self.assertNotIn("未知", message)

    def test_ranking_result_uses_global_rank_and_preserves_filters(self):
        query = RankingQuery(keyword="全彩", period="week", category="hanman", page=2)
        result = RankingResult(
            query,
            2,
            3,
            12,
            (SearchItem("111111", "作品一", ("全彩",)),),
        )

        message = format_ranking_result(result, "/jm", text_mode=True)

        self.assertIn("韩漫 · 周榜 · 关键词：全彩", message)
        self.assertIn("第 2 / 3 页，共 12 条", message)
        self.assertIn("6. JM111111 作品一", message)
        self.assertIn("下一页：/jm 排行 文本 全彩 韩漫 周榜 3", message)

    def test_ranking_empty_out_of_range_categories_and_usage(self):
        empty_query = RankingQuery(keyword="不存在")
        high_query = RankingQuery(period="month", page=4)

        self.assertIn(
            "没有结果",
            format_ranking_result(RankingResult(empty_query, 1, 0, 0, ())),
        )
        self.assertIn(
            "只有 3 页",
            format_ranking_result(RankingResult(high_query, 4, 3, 15, ())),
        )
        self.assertIn("Cosplay｜3D｜英文站", format_ranking_categories("/jm"))
        self.assertIn("/jm 排行 [文本]", format_ranking_usage("/jm"))

    def test_ranking_card_commands_include_downloads_and_next_page(self):
        query = RankingQuery(period="day")
        result = RankingResult(
            query,
            1,
            2,
            6,
            (SearchItem("111111", "作品一", ()),),
        )

        message = format_ranking_card_commands(result, "/jm")

        self.assertIn("JM111111 下载：/jm 111111", message)
        self.assertIn("下一页：/jm 排行 日榜 2", message)

    def test_format_favorite_list(self):
        message = format_favorite_list(
            [
                FavoriteItem(user_id=42, album_id="111111", created_at="2026-06-26T00:00:00.000Z"),
                FavoriteItem(user_id=42, album_id="222222", created_at="2026-06-26T00:00:00.000Z"),
            ],
            "/jm",
        )

        self.assertIn("我的收藏", message)
        self.assertIn("JM111111", message)
        self.assertIn("/jm 111111", message)

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

    def test_admin_failed_formats_missing_album_without_traceback(self):
        job = AlbumQueueJob(
            id=27,
            album_id="206954",
            destination_type="private",
            group_id=None,
            user_id=3065532050,
            status="failed",
            attempts=1,
            priority=0,
            error=(
                "MissingAlbumPhotoException: 请求的本子不存在！(https://18comic.vip/album/206954/)\n"
                "原因可能为:\n"
                "1. id有误，检查你的本子id\n"
                "2. 该漫画只对登录用户可见，请配置你的cookies，或者使用移动端Client（api）\n\n"
                "Traceback (most recent call last):\n"
                "  File \"/opt/jmbot/src/jmbot/core/job_executor.py\", line 88, in _run_child\n"
                "jmcomic.jm_exception.MissingAlbumPhotoException: 请求的本子不存在！"
            ),
            created_at="2026-06-26T00:00:00.000Z",
            updated_at="2026-06-26T00:00:00.000Z",
        )

        message = format_admin_failed_tasks([job])

        self.assertIn("JM206954", message)
        self.assertIn("本子不存在或仅登录用户可见", message)
        self.assertIn("检查 ID", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("/opt/jmbot", message)
        self.assertNotIn("jm_exception", message)

    def test_admin_failed_detail_includes_raw_error_for_admin_lookup(self):
        job = AlbumQueueJob(
            id=21,
            album_id="1020215",
            destination_type="group",
            group_id=10001,
            user_id=42,
            status="failed",
            attempts=1,
            priority=0,
            error="RuntimeError: secret upstream failure\nTraceback line",
            created_at="2026-06-25T00:00:00.000Z",
            updated_at="2026-06-25T00:00:00.000Z",
        )

        message = format_admin_failed_detail(job)

        self.assertIn("#21 JM1020215", message)
        self.assertIn("群 10001", message)
        self.assertIn("secret upstream failure", message)
        self.assertIn("Traceback line", message)

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
        self.assertIn("111111 处理中（群聊）", message)
        self.assertNotIn("JM222222", message)
        self.assertIn("还有 1 个未显示", message)

    def test_format_task_failed_hides_raw_error(self):
        message = format_task_failed("123456", 17, "secret upstream failure")

        self.assertIn("JM123456 出现技术原因，联系管理员处理 #17", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("secret upstream failure", message)

    def test_format_task_failed_shows_actionable_missing_album_reason(self):
        message = format_task_failed(
            "206954",
            27,
            "MissingAlbumPhotoException: 请求的本子不存在！该漫画只对登录用户可见\nTraceback...",
        )

        self.assertIn("JM206954 处理失败", message)
        self.assertIn("本子不存在或仅登录用户可见", message)
        self.assertIn("请检查 ID", message)
        self.assertNotIn("Traceback", message)
        self.assertTrue(is_public_nonpersistent_failure("MissingAlbumPhotoException: 请求的本子不存在！"))

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

        self.assertIn("#1 JM123456 failed", message)
        self.assertNotIn("失败原因已记录", message)
        self.assertNotIn("RuntimeError", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("C:/secret", message)


if __name__ == "__main__":
    unittest.main()
