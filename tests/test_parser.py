import unittest

from jmbot.core.parser import parse_jm_admin_message, parse_jm_command, parse_jm_message


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
            ("/jm 历史", False, None),
            ("/jm history", False, None),
            ("/jm 历史 5", False, 5),
            ("/jm history 6", False, 6),
            ("/jm 历史 失败", True, None),
            ("/jm 历史 失败 2", True, 2),
        ]

        for message, failed_only, limit in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "history")
                self.assertEqual(result.failed_only, failed_only)
                self.assertEqual(result.limit, limit)

    def test_retry_command_is_removed(self):
        self.assertIsNone(parse_jm_message("/jm 重试 123456"))

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

    def test_parse_progress_without_id(self):
        result = parse_jm_message("/jm 进度")

        self.assertIsNotNone(result)
        self.assertEqual(result.action, "progress")
        self.assertIsNone(result.album_id)

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
                self.assertEqual(result.search_view, "default")

    def test_parse_author_text_search_commands(self):
        cases = [
            ("/jm 作者 文本 柊ぽぷら", "柊ぽぷら", 1),
            ("/jm 作者 文本 柊ぽぷら 2", "柊ぽぷら", 2),
        ]

        for message, keyword, page in cases:
            with self.subTest(message=message):
                result = parse_jm_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, "search")
                self.assertEqual(result.search_kind, "author")
                self.assertEqual(result.search_view, "text")
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

    def test_parse_ranking_defaults_and_free_filter_order(self):
        default = parse_jm_message("/jm 排行")
        first = parse_jm_message("/jm 排行 全彩 韩漫 月榜 2")
        second = parse_jm_message("/jm 排行 韩漫 全彩 月榜 2")

        self.assertIsNotNone(default)
        self.assertEqual(default.action, "ranking")
        self.assertEqual(default.ranking_period, "all")
        self.assertEqual(default.ranking_category, "all")
        self.assertEqual(default.page, 1)
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        self.assertEqual(first.keyword, "全彩")
        self.assertEqual(first.ranking_category, "hanman")
        self.assertEqual(first.ranking_period, "month")
        self.assertEqual(first.page, 2)

    def test_parse_ranking_aliases_views_and_errors(self):
        self.assertEqual(parse_jm_message("/jm 排行 日").ranking_period, "day")
        self.assertEqual(parse_jm_message("/jm 排行 周榜").ranking_period, "week")
        self.assertEqual(parse_jm_message("/jm 排行 COSPLAY 周榜").ranking_category, "doujin_cosplay")
        self.assertEqual(parse_jm_message("/jm 排行 3d 总").ranking_category, "3d")

        text = parse_jm_message("/jm 排行 文本 韩漫 周榜")
        categories = parse_jm_message("/jm 排行 类别")
        nonleading_text = parse_jm_message("/jm 排行 韩漫 文本 周榜")

        self.assertEqual(text.search_view, "text")
        self.assertEqual(categories.action, "ranking_categories")
        self.assertEqual((nonleading_text.search_view, nonleading_text.keyword), ("default", "文本"))
        for message in ("/jm 排行 日榜 周榜", "/jm 排行 韩漫 美漫", "/jm 排行 0"):
            with self.subTest(message=message):
                self.assertEqual(parse_jm_message(message).action, "ranking_invalid")

    def test_parse_layered_and_admin_help(self):
        self.assertIsNone(parse_jm_message("/jm help").help_topic)
        self.assertEqual(parse_jm_message("/jm 帮助 排行").help_topic, "排行")
        self.assertEqual(parse_jm_message("/jm 帮助 unknown").help_topic, "unknown")
        self.assertEqual(parse_jm_admin_message("/jmg 帮助").action, "admin_help")
        self.assertEqual(parse_jm_admin_message("/jmg help").action, "admin_help")

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

    def test_friend_request_commands_are_removed(self):
        self.assertIsNone(parse_jm_message("/jm 好友申请"))
        self.assertIsNone(parse_jm_message("/jm 同意好友 12"))
        self.assertIsNone(parse_jm_message("/jm 拒绝好友 12"))

    def test_legacy_jm_admin_commands_are_removed(self):
        self.assertIsNone(parse_jm_message("/jm 管理 清空"))
        self.assertIsNone(parse_jm_message("/jm 管理 kill 123456"))
        self.assertIsNone(parse_jm_message("/jm 管理 kill 全部 123456"))
        self.assertIsNone(parse_jm_message("/jm 管理 插队 123456"))
        self.assertIsNone(parse_jm_message("/jm 管理 失败"))

    def test_parse_jmg_admin_commands(self):
        cases = [
            ("/jmg 清空", "admin_clear", None, None, False),
            ("/jmg kill 123456", "admin_kill", "123456", None, False),
            ("/jmg kill 全部 123456", "admin_kill", "123456", None, True),
            ("/jmg 插队 123456", "admin_jump", "123456", None, False),
        ]

        for message, action, album_id, request_id, all_targets in cases:
            with self.subTest(message=message):
                result = parse_jm_admin_message(message)

                self.assertIsNotNone(result)
                self.assertEqual(result.action, action)
                self.assertEqual(result.album_id, album_id)
                self.assertEqual(result.request_id, request_id)
                self.assertEqual(result.all_targets, all_targets)

    def test_parse_jmg_failed_commands(self):
        list_result = parse_jm_admin_message("/jmg 失败")
        recent_result = parse_jm_admin_message("/jmg 失败 最近 2")
        detail_result = parse_jm_admin_message("/jmg 失败 21")
        delete_result = parse_jm_admin_message("/jmg 失败 删除 21")

        self.assertIsNotNone(list_result)
        self.assertEqual(list_result.action, "admin_failed")
        self.assertIsNone(list_result.request_id)
        self.assertIsNone(list_result.limit)
        self.assertIsNotNone(recent_result)
        self.assertEqual(recent_result.action, "admin_failed")
        self.assertEqual(recent_result.limit, 2)
        self.assertIsNotNone(detail_result)
        self.assertEqual(detail_result.action, "admin_failed")
        self.assertEqual(detail_result.request_id, 21)
        self.assertIsNotNone(delete_result)
        self.assertEqual(delete_result.action, "admin_failed_delete")
        self.assertEqual(delete_result.request_id, 21)


if __name__ == "__main__":
    unittest.main()
