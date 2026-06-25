import time
import unittest
from unittest.mock import patch

from jmbot.core.catalog_service import AlbumDetail
from jmbot.core.query_executor import (
    QueryTimeoutError,
    query_album_detail,
    run_query_with_timeout,
)


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

if __name__ == "__main__":
    unittest.main()
