import asyncio
import tempfile
import unittest
from pathlib import Path

from jmbot.core.command_service import (
    QueueTarget,
    admin_clear_queue,
    admin_delete_failed_task,
    admin_failed_task_detail,
    admin_failed_tasks,
    admin_jump_album,
    admin_kill_album,
    cancel_album_request,
    enqueue_album_request,
    favorite_album,
    favorite_summary,
    deliver_cover,
    deliver_preview,
    query_album_detail_message,
    query_album_ranking_reply,
    query_album_search_reply,
    query_album_search_message,
    search_reply_options,
    my_tasks_summary,
    remove_favorite_album,
    progress_summary,
    task_history,
)
from jmbot.core.parser import JmMessage
from jmbot.core.catalog_service import AlbumDetail, RankingQuery, RankingResult, SearchItem, SearchResult
from jmbot.core.detail_cache import DetailCache
from jmbot.core.preview_service import CoverResult, PreviewResult
from jmbot.core.persistent_queue import PersistentAlbumQueue
from jmbot.core.user_store import UserDataStore


class CommandServiceTest(unittest.TestCase):
    def test_enqueue_album_request_persists_format_and_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)

            message = enqueue_album_request(
                queue,
                "111111",
                target,
                output_format="zip",
                chapters=(1, 3),
            )
            job = queue.list_jobs()[0]

        self.assertIn("ZIP", message)
        self.assertEqual(job.output_format, "zip")
        self.assertEqual(job.chapters, (1, 3))

    def test_progress_summary_is_scoped_to_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)
            other = QueueTarget(destination_type="group", group_id=10002, user_id=42)
            job = queue.enqueue_group("111111", group_id=10001, user_id=99)
            queue.update_progress(job.id, stage="downloading", current=3, total=10)

            visible = progress_summary(queue, "111111", target, mode="precise")
            hidden = progress_summary(queue, "111111", other, mode="precise")

        self.assertIn("3 / 10", visible)
        self.assertIn("没有找到", hidden)

    def test_progress_summary_lists_all_variants_and_hides_counts_in_stage_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            pdf_job = queue.enqueue_private("111111", user_id=42)
            zip_job = queue.enqueue_private(
                "111111",
                user_id=42,
                output_format="zip",
                chapters=(1, 3),
            )
            queue.update_progress(pdf_job.id, stage="downloading", current=3, total=10)
            queue.update_progress(zip_job.id, stage="building", current=5, total=5)

            message = progress_summary(queue, "111111", target, mode="stage")

        self.assertIn("PDF 全部章节", message)
        self.assertIn("ZIP 章节1,3", message)
        self.assertNotIn("3 / 10", message)
        self.assertNotIn("5 / 5", message)

    def test_progress_summary_without_id_uses_latest_active_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            queue.enqueue_private("111111", user_id=42)
            latest = queue.enqueue_private("222222", user_id=42)
            queue.update_progress(latest.id, stage="building", current=4, total=4)

            message = progress_summary(queue, None, target, mode="stage")

        self.assertIn("JM222222 当前进度", message)
        self.assertNotIn("JM111111 当前进度", message)

    def test_progress_summary_without_id_includes_every_active_job_for_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            queue.enqueue_private("111111", user_id=42)
            queue.enqueue_private("222222", user_id=42)
            queue.enqueue_private("333333", user_id=99)

            message = progress_summary(queue, None, target, mode="stage")

        self.assertIn("JM111111", message)
        self.assertIn("JM222222", message)
        self.assertNotIn("JM333333", message)

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

    def test_admin_jump_finds_non_default_format_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            target = QueueTarget(destination_type="private", group_id=None, user_id=42)
            zip_job = queue.enqueue_private("111111", user_id=42, output_format="zip")

            message = admin_jump_album(queue, "111111", target)
            jobs = queue.list_jobs()

        self.assertIn("已插队", message)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, zip_job.id)
        self.assertEqual(jobs[0].output_format, "zip")

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
            failed_message = task_history(queue, target, failed_only=True, limit=1)
            other_message = task_history(queue, other, failed_only=True)

        self.assertIn("JM111111", all_message)
        self.assertIn("JM222222", all_message)
        self.assertNotIn("JM333333", all_message)
        self.assertIn("JM222222", failed_message)
        self.assertNotIn("JM111111", failed_message)
        self.assertIn(f"#{failed.id}", failed_message)
        self.assertIn("JM333333", other_message)

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

    def test_admin_failed_detail_and_delete_by_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PersistentAlbumQueue(Path(tmp) / "queue.sqlite3")
            failed = queue.enqueue_private("111111", user_id=42)
            queue.mark_failed(failed.id, "secret upstream failure")

            detail = admin_failed_task_detail(queue, failed.id)
            deleted = admin_delete_failed_task(queue, failed.id)
            missing = admin_failed_task_detail(queue, failed.id)

        self.assertIn("#", detail)
        self.assertIn("secret upstream failure", detail)
        self.assertIn("已删除失败记录", deleted)
        self.assertIn("没有找到失败任务", missing)

    def test_favorite_commands_are_user_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = UserDataStore(Path(tmp) / "user.sqlite3")
            target = QueueTarget(destination_type="group", group_id=10001, user_id=42)
            other = QueueTarget(destination_type="group", group_id=10001, user_id=99)

            added = favorite_album(store, "111111", target)
            duplicate = favorite_album(store, "111111", target)
            favorite_album(store, "222222", other)
            summary = favorite_summary(store, target)
            removed = remove_favorite_album(store, "111111", target)
            empty = favorite_summary(store, target)

        self.assertIn("已收藏", added)
        self.assertIn("已经收藏", duplicate)
        self.assertIn("JM111111", summary)
        self.assertNotIn("JM222222", summary)
        self.assertIn("已取消收藏", removed)
        self.assertIn("还没有收藏", empty)

class CatalogCommandServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_detail_query_uses_cache_after_first_call(self):
        cache = DetailCache()
        semaphore = asyncio.Semaphore(1)
        calls = []

        def query(album_id, *, timeout_seconds):
            calls.append((album_id, timeout_seconds))
            return AlbumDetail(album_id, "标题", (), 10, (), (), None)

        first = await query_album_detail_message(
            "123456", cache, semaphore, timeout_seconds=20, query_func=query
        )
        second = await query_album_detail_message(
            "123456", cache, semaphore, timeout_seconds=20, query_func=query
        )

        self.assertIn("标题：标题", first)
        self.assertEqual(second, first)
        self.assertEqual(calls, [("123456", 20)])

    async def test_query_failure_returns_safe_message(self):
        def query(album_id, *, timeout_seconds):
            raise RuntimeError("secret upstream failure")

        message = await query_album_detail_message(
            "123456",
            DetailCache(),
            asyncio.Semaphore(1),
            timeout_seconds=20,
            query_func=query,
        )

        self.assertEqual(message, "详情查询暂时不可用，请稍后重试。")
        self.assertNotIn("secret", message)

    async def test_search_query_returns_formatted_results(self):
        semaphore = asyncio.Semaphore(1)
        calls = []

        def query(keyword, *, kind, page, timeout_seconds):
            calls.append((keyword, kind, page, timeout_seconds))
            return SearchResult(
                keyword,
                kind,
                page,
                2,
                12,
                (SearchItem("111111", "搜索结果", ("中文",)),),
            )

        message = await query_album_search_message(
            "测试",
            kind="site",
            page=2,
            semaphore=semaphore,
            timeout_seconds=20,
            command_prefix="/jm",
            query_func=query,
        )

        self.assertEqual(calls, [("测试", "site", 2, 20)])
        self.assertIn("搜索「测试」", message)
        self.assertIn("JM111111 搜索结果", message)

    async def test_ranking_reply_card_text_and_failure(self):
        query = RankingQuery(keyword="全彩", period="week", category="hanman")
        result = RankingResult(
            query,
            1,
            2,
            6,
            tuple(SearchItem(str(111110 + index), f"Title {index}", ()) for index in range(1, 6)),
        )

        def success(value, *, timeout_seconds):
            self.assertEqual((value, timeout_seconds), (query, 20))
            return result

        card = await query_album_ranking_reply(
            query,
            view="default",
            semaphore=asyncio.Semaphore(1),
            timeout_seconds=20,
            query_func=success,
        )
        text = await query_album_ranking_reply(
            query,
            view="text",
            semaphore=asyncio.Semaphore(1),
            timeout_seconds=20,
            query_func=success,
        )

        self.assertEqual(card.cover_album_ids, ("111111", "111112", "111113", "111114", "111115"))
        self.assertEqual(text.cover_album_ids, ())
        self.assertIn("1. JM111111", card.message)
        self.assertIn("1. JM111111", text.message)

        def failure(value, *, timeout_seconds):
            raise RuntimeError("secret upstream failure")

        failed = await query_album_ranking_reply(
            query,
            view="default",
            semaphore=asyncio.Semaphore(1),
            timeout_seconds=20,
            query_func=failure,
        )

        self.assertEqual(failed.message, "排行榜查询暂时不可用，请稍后重试。")
        self.assertIsNone(failed.result)
        self.assertNotIn("secret", failed.message)

    async def test_author_search_reply_limits_to_five_and_includes_cover_ids(self):
        semaphore = asyncio.Semaphore(1)

        def query(keyword, *, kind, page, timeout_seconds):
            return SearchResult(
                keyword,
                kind,
                page,
                2,
                6,
                tuple(SearchItem(str(111110 + index), f"Title {index}", ()) for index in range(1, 7)),
            )

        reply = await query_album_search_reply(
            "author-name",
            kind="author",
            page=1,
            semaphore=semaphore,
            timeout_seconds=20,
            command_prefix="/jm",
            query_func=query,
            cover_limit=5,
        )

        self.assertEqual(reply.cover_album_ids, ("111111", "111112", "111113", "111114", "111115"))
        self.assertEqual([item.album_id for item in reply.card_items], ["111111", "111112", "111113", "111114", "111115"])
        self.assertIn("JM111115", reply.message)
        self.assertNotIn("JM111116", reply.message)

    def test_author_search_options_default_to_card_and_text_mode_skips_covers(self):
        image_limit, image_cover_limit = search_reply_options(
            JmMessage(action="search", search_kind="author", keyword="author-name")
        )
        text_limit, text_cover_limit = search_reply_options(
            JmMessage(action="search", search_kind="author", keyword="author-name", search_view="text")
        )
        site_limit, site_cover_limit = search_reply_options(
            JmMessage(action="search", search_kind="site", keyword="keyword")
        )

        self.assertEqual((image_limit, image_cover_limit), (5, 5))
        self.assertEqual((text_limit, text_cover_limit), (None, 0))
        self.assertEqual((site_limit, site_cover_limit), (None, 0))

    async def test_author_search_reply_paginates_five_items_across_source_pages(self):
        semaphore = asyncio.Semaphore(1)
        calls = []

        def query(keyword, *, kind, page, timeout_seconds):
            calls.append(page)
            start = (page - 1) * 10 + 1
            return SearchResult(
                keyword,
                kind,
                page,
                3,
                24,
                tuple(SearchItem(f"{index:06d}", f"Title {index}", ()) for index in range(start, start + 10)),
            )

        reply = await query_album_search_reply(
            "author-name",
            kind="author",
            page=3,
            semaphore=semaphore,
            timeout_seconds=20,
            command_prefix="/jm",
            query_func=query,
            cover_limit=5,
        )

        self.assertEqual(calls, [1, 2])
        self.assertEqual(reply.cover_album_ids, ("000011", "000012", "000013", "000014", "000015"))
        self.assertIsNotNone(reply.card_result)
        self.assertEqual(reply.card_result.page, 3)
        self.assertEqual(reply.card_result.page_count, 5)
        self.assertEqual(reply.card_result.total, 24)

    async def test_search_failure_returns_safe_message(self):
        def query(keyword, *, kind, page, timeout_seconds):
            raise RuntimeError("secret upstream failure")

        message = await query_album_search_message(
            "测试",
            kind="author",
            page=1,
            semaphore=asyncio.Semaphore(1),
            timeout_seconds=20,
            query_func=query,
        )

        self.assertEqual(message, "搜索查询暂时不可用，请稍后重试。")
        self.assertNotIn("secret", message)

    async def test_deliver_cover_sends_image_and_cleans_media_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "271673"
            media_dir.mkdir()
            cover_path = media_dir / "cover.jpg"
            cover_path.write_bytes(b"cover")
            delivered = []

            def query(album_id, preview_root, *, timeout_seconds):
                return CoverResult(album_id, media_dir, cover_path)

            async def deliver(path):
                self.assertTrue(semaphore.locked())
                delivered.append(path)

            semaphore = asyncio.Semaphore(1)
            message = await deliver_cover(
                "271673",
                Path(tmp),
                semaphore,
                deliver=deliver,
                timeout_seconds=20,
                query_func=query,
            )

            self.assertIsNone(message)
            self.assertEqual(delivered, [cover_path])
            self.assertFalse(media_dir.exists())

    async def test_deliver_preview_uploads_pdf_and_cleans_media_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "271673"
            media_dir.mkdir()
            page_path = media_dir / "page-001.jpg"
            pdf_path = media_dir / "JM271673（预览）.pdf"
            page_path.write_bytes(b"page")
            pdf_path.write_bytes(b"pdf")
            delivered = []

            def query(album_id, preview_root, *, page_count, timeout_seconds):
                return PreviewResult(album_id, media_dir, (page_path,), pdf_path)

            async def deliver(path):
                self.assertTrue(semaphore.locked())
                delivered.append(path)

            semaphore = asyncio.Semaphore(1)
            message = await deliver_preview(
                "271673",
                Path(tmp),
                semaphore,
                deliver=deliver,
                page_count=10,
                timeout_seconds=20,
                query_func=query,
            )

            self.assertIsNone(message)
            self.assertEqual(delivered, [pdf_path])
            self.assertFalse(media_dir.exists())

    async def test_deliver_preview_returns_safe_error(self):
        def query(album_id, preview_root, *, page_count, timeout_seconds):
            raise RuntimeError("secret upstream failure")

        async def deliver(path):
            raise AssertionError("delivery should not run")

        message = await deliver_preview(
            "271673",
            Path("preview"),
            asyncio.Semaphore(1),
            deliver=deliver,
            page_count=10,
            timeout_seconds=20,
            query_func=query,
        )

        self.assertEqual(message, "JM271673 预览生成失败，请稍后重试。")
        self.assertNotIn("secret", message)


if __name__ == "__main__":
    unittest.main()
