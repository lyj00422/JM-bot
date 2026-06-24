import time
import unittest
from pathlib import Path

from jmbot.core.jm_service import AlbumJobResult
from jmbot.core.job_executor import JobTimeoutError, run_with_timeout


def _return_album_result(album_id: str, root: str) -> AlbumJobResult:
    base = Path(root)
    return AlbumJobResult(album_id=album_id, album_dir=base / album_id, pdf_path=base / f"JM{album_id}.pdf")


def _raise_runtime_error() -> None:
    raise RuntimeError("child failed")


def _sleep_for(seconds: float) -> None:
    time.sleep(seconds)


def _return_large_payload(size: int) -> bytes:
    return b"x" * size


class JobExecutorTest(unittest.TestCase):
    def test_returns_child_result(self):
        result = run_with_timeout(_return_album_result, "123456", "data/output", timeout_seconds=5)

        self.assertEqual(result.album_id, "123456")
        self.assertEqual(result.pdf_path, Path("data/output") / "JM123456.pdf")

    def test_raises_child_exception(self):
        with self.assertRaisesRegex(RuntimeError, "child failed"):
            run_with_timeout(_raise_runtime_error, timeout_seconds=5)

    def test_terminates_child_when_timeout_expires(self):
        started = time.monotonic()

        with self.assertRaises(JobTimeoutError):
            run_with_timeout(_sleep_for, 10, timeout_seconds=0.2)

        self.assertLess(time.monotonic() - started, 3)

    def test_receives_large_child_result_without_false_timeout(self):
        result = run_with_timeout(
            _return_large_payload,
            10_000_000,
            timeout_seconds=3,
        )

        self.assertEqual(len(result), 10_000_000)


if __name__ == "__main__":
    unittest.main()
