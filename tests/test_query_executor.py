import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jmbot.core.catalog_service import (
    AlbumDetail,
    RankingQuery,
    RankingResult,
    SearchItem,
    SearchResult,
    get_ranking_page,
)
from jmbot.core.query_executor import (
    QueryTimeoutError,
    query_album_detail,
    query_album_ranking,
    query_album_search,
    query_cover,
    query_preview,
    run_query_with_timeout,
)
from jmbot.core.preview_service import CoverResult, PreviewResult


def _return_value(value):
    return value


def _raise_error():
    raise ValueError("query failed")


def _sleep(seconds):
    time.sleep(seconds)


class QueryExecutorTest(unittest.TestCase):
    def test_returns_picklable_query_result(self):
        self.assertEqual(run_query_with_timeout(_return_value, "ok", timeout_seconds=5), "ok")

    def test_propagates_child_failure_without_hanging(self):
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            run_query_with_timeout(_raise_error, timeout_seconds=5)

    def test_terminates_query_after_timeout(self):
        started = time.monotonic()

        with self.assertRaises(QueryTimeoutError):
            run_query_with_timeout(_sleep, 10, timeout_seconds=0.2)

        self.assertLess(time.monotonic() - started, 3)

    @patch("jmbot.core.query_executor.run_query_with_timeout")
    def test_detail_wrapper_uses_isolated_executor(self, run_query):
        expected = AlbumDetail("123456", "标题", (), None, (), (), None)
        run_query.return_value = expected

        result = query_album_detail("123456", timeout_seconds=20)

        self.assertEqual(result, expected)
        self.assertEqual(run_query.call_args.args[1:], ("123456",))
        self.assertEqual(run_query.call_args.kwargs, {"timeout_seconds": 20})

    @patch("jmbot.core.query_executor.run_query_with_timeout")
    def test_search_wrapper_uses_isolated_executor(self, run_query):
        expected = SearchResult(
            "测试",
            "site",
            2,
            3,
            22,
            (SearchItem("111111", "标题", ()),),
        )
        run_query.return_value = expected

        result = query_album_search("测试", kind="site", page=2, timeout_seconds=20)

        self.assertEqual(result, expected)
        self.assertEqual(run_query.call_args.args[1:], ("测试",))
        self.assertEqual(run_query.call_args.kwargs, {"kind": "site", "page": 2, "timeout_seconds": 20})

    @patch("jmbot.core.query_executor.run_query_with_timeout")
    def test_ranking_wrapper_uses_isolated_executor(self, run_query):
        query = RankingQuery(period="week", category="hanman", page=2)
        expected = RankingResult(query, 2, 3, 12, ())
        run_query.return_value = expected

        result = query_album_ranking(query, timeout_seconds=20)

        self.assertEqual(result, expected)
        run_query.assert_called_once_with(get_ranking_page, query, timeout_seconds=20)

    @patch("jmbot.core.query_executor.run_query_with_timeout")
    def test_cover_wrapper_uses_isolated_executor(self, run_query):
        expected = CoverResult("123456", Path("preview/123456"), Path("preview/123456/cover.jpg"))
        run_query.return_value = expected

        result = query_cover("123456", Path("preview"), timeout_seconds=20)

        self.assertEqual(result, expected)
        self.assertEqual(run_query.call_args.args[1:], ("123456", Path("preview")))
        self.assertEqual(run_query.call_args.kwargs, {"timeout_seconds": 20})

    @patch("jmbot.core.query_executor.run_query_with_timeout")
    def test_preview_wrapper_uses_isolated_executor(self, run_query):
        expected = PreviewResult(
            "123456",
            Path("preview/123456"),
            (Path("preview/123456/page-001.jpg"),),
            Path("preview/123456/JM123456（预览）.pdf"),
        )
        run_query.return_value = expected

        result = query_preview("123456", Path("preview"), page_count=10, timeout_seconds=20)

        self.assertEqual(result, expected)
        self.assertEqual(run_query.call_args.args[1:], ("123456", Path("preview")))
        self.assertEqual(run_query.call_args.kwargs, {"page_count": 10, "timeout_seconds": 20})

if __name__ == "__main__":
    unittest.main()
