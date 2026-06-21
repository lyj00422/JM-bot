import asyncio
import unittest

from jmbot.core.task_runner import DuplicateTaskError, TaskRunner


class TaskRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_duplicate_in_flight_album(self):
        started = asyncio.Event()
        release = asyncio.Event()
        runner = TaskRunner(max_concurrency=2)

        async def job():
            started.set()
            await release.wait()
            return "ok"

        first = asyncio.create_task(runner.submit("123456", job))
        await started.wait()

        with self.assertRaises(DuplicateTaskError):
            await runner.submit("123456", job)

        release.set()
        self.assertEqual(await first, "ok")

    async def test_allows_same_album_after_completion(self):
        runner = TaskRunner(max_concurrency=1)

        async def job():
            return "ok"

        self.assertEqual(await runner.submit("123456", job), "ok")
        self.assertEqual(await runner.submit("123456", job), "ok")


if __name__ == "__main__":
    unittest.main()
