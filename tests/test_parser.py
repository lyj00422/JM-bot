import unittest

from jmbot.core.parser import parse_jm_command, parse_jm_message


class ParseJmCommandTest(unittest.TestCase):
    def test_parse_valid_jm_command(self):
        result = parse_jm_command("/jm 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "123456")

    def test_parse_allows_extra_spaces(self):
        result = parse_jm_command("  /jm   350234  ")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "350234")

    def test_parse_uses_custom_prefix(self):
        result = parse_jm_command("!jm 123456", command_prefix="!jm")

        self.assertIsNotNone(result)
        self.assertEqual(result.album_id, "123456")

    def test_parse_rejects_invalid_id(self):
        self.assertIsNone(parse_jm_command("/jm abc"))

    def test_parse_rejects_other_command(self):
        self.assertIsNone(parse_jm_command("/help 123456"))

    def test_parse_cancel_command(self):
        result = parse_jm_message("/jm 撤回 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "cancel")
        self.assertEqual(result.album_id, "123456")

    def test_parse_queue_command(self):
        result = parse_jm_message("/jm 队列")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "queue")
        self.assertIsNone(result.album_id)

    def test_parse_help_command(self):
        result = parse_jm_message("/jm 帮助")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "help")
        self.assertIsNone(result.album_id)

    def test_parse_help_alias(self):
        result = parse_jm_message("/jm help")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "help")
        self.assertIsNone(result.album_id)

    def test_parse_my_tasks_commands(self):
        for message in ["/jm 我的", "/jm my"]:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "my_tasks")
                self.assertIsNone(result.album_id)

    def test_parse_history_commands(self):
        cases = [
            ("/jm 历史", False),
            ("/jm history", False),
            ("/jm 历史 失败", True),
        ]

        for message, failed_only in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "history")
                self.assertEqual(result.failed_only, failed_only)

    def test_parse_retry_command(self):
        result = parse_jm_message("/jm 重试 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "retry")
        self.assertEqual(result.album_id, "123456")

    def test_parse_detail_commands(self):
        for message in ["/jm 看 123456", "/jm 详情 123456", "/jm info 123456"]:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "detail")
                self.assertEqual(result.album_id, "123456")

    def test_parse_cover_commands(self):
        for message in ["/jm 封面 123456", "/jm cover 123456"]:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "cover")
                self.assertEqual(result.album_id, "123456")

    def test_parse_preview_commands(self):
        for message in ["/jm 预览 123456", "/jm preview 123456"]:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "preview")
                self.assertEqual(result.album_id, "123456")

    def test_parse_download_format_commands(self):
        cases = [
            ("/jm 123456", "pdf"),
            ("/jm 123456 pdf", "pdf"),
            ("/jm 123456 zip", "zip"),
        ]
        for message, output_format in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)
                self.assertIsNotNone(result)
                self.assertEqual(result.action, "request")
                self.assertEqual(result.album_id, "123456")
                self.assertEqual(result.output_format, output_format)
                self.assertEqual(result.chapters, ())

    def test_parse_uses_configured_default_format_when_format_is_omitted(self):
        result = parse_jm_message("/jm 123456", default_format="zip")

        self.assertIsNotNone(result)
        self.assertEqual(result.output_format, "zip")

    def test_parse_chapter_selection_commands(self):
        cases = [
            ("/jm 123456 章节 1", (1,), "pdf"),
            ("/jm 123456 章节 1,3,5", (1, 3, 5), "pdf"),
            ("/jm 123456 章节 2-5", (2, 3, 4, 5), "pdf"),
            ("/jm 123456 章节 1,3-5 zip", (1, 3, 4, 5), "zip"),
        ]
        for message, chapters, output_format in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)
                self.assertIsNotNone(result)
                self.assertEqual(result.action, "request")
                self.assertEqual(result.album_id, "123456")
                self.assertEqual(result.chapters, chapters)
                self.assertEqual(result.output_format, output_format)

    def test_parse_rejects_invalid_chapter_selection(self):
        self.assertIsNone(parse_jm_message("/jm 123456 章节 0"))
        self.assertIsNone(parse_jm_message("/jm 123456 章节 5-2"))
        self.assertIsNone(parse_jm_message("/jm 123456 rar"))

    def test_parse_progress_command(self):
        result = parse_jm_message("/jm 进度 123456")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "progress")
        self.assertEqual(result.album_id, "123456")

    def test_parse_search_commands(self):
        cases = [
            ("/jm 搜索 测试关键词", "site", "测试关键词", 1),
            ("/jm 搜索 测试关键词 2", "site", "测试关键词", 2),
            ("/jm search english keyword 3", "site", "english keyword", 3),
            ("/jm 作者 柊ぽぷら", "author", "柊ぽぷら", 1),
            ("/jm 作者 柊ぽぷら 2", "author", "柊ぽぷら", 2),
        ]

        for message, search_kind, keyword, page in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "search")
                self.assertEqual(result.search_kind, search_kind)
                self.assertEqual(result.keyword, keyword)
                self.assertEqual(result.page, page)

    def test_parse_search_numeric_keyword_is_not_treated_as_page(self):
        result = parse_jm_message("/jm 搜索 271673")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "search")
        self.assertEqual(result.keyword, "271673")
        self.assertEqual(result.page, 1)

    def test_parse_rejects_empty_or_invalid_search(self):
        self.assertIsNone(parse_jm_message("/jm 搜索"))
        self.assertIsNone(parse_jm_message("/jm 搜索 测试 0"))
        self.assertIsNone(parse_jm_message("/jm 作者"))

    def test_removed_download_pagination_command_is_not_parsed(self):
        self.assertIsNone(parse_jm_message("/jm 下载 2"))

    def test_parse_favorite_commands(self):
        cases = [
            ("/jm 收藏 123456", "favorite_add", "123456"),
            ("/jm 取消收藏 123456", "favorite_remove", "123456"),
            ("/jm 我的收藏", "favorite_list", None),
            ("/jm 收藏", "favorite_list", None),
        ]

        for message, action, album_id in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, action)
                self.assertEqual(result.album_id, album_id)

    def test_parse_admin_friend_request_commands(self):
        cases = [
            ("/jm 好友申请", "friend_requests", None),
            ("/jm 同意好友 12", "friend_approve", 12),
            ("/jm 拒绝好友 12", "friend_reject", 12),
        ]

        for message, action, request_id in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, action)
                self.assertEqual(result.request_id, request_id)

    def test_parse_admin_commands(self):
        cases = [
            ("/jm 管理 清空", "admin_clear", None, False),
            ("/jm 管理 kill 123456", "admin_kill", "123456", False),
            ("/jm 管理 kill 全部 123456", "admin_kill", "123456", True),
            ("/jm 管理 插队 123456", "admin_jump", "123456", False),
        ]

        for message, action, album_id, all_targets in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, action)
                self.assertEqual(result.album_id, album_id)
                self.assertEqual(result.all_targets, all_targets)

    def test_parse_admin_failed_command(self):
        result = parse_jm_message("/jm 管理 失败")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "admin_failed")


if __name__ == "__main__":
    unittest.main()
