import asyncio
import unittest

from jmbot.core.fair_scheduler import FairChatScheduler


class FairSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_round_robin_prevents_hot_scope_starvation(self):
        scheduler = FairChatScheduler(workers=1, per_scope_limit=4)
        await scheduler.start()
        seen = []

        async def work(scope, index):
            seen.append((scope, index))
            await asyncio.sleep(0)
            return scope

        futures = []
        for index in range(4):
            futures.append(asyncio.create_task(scheduler.submit("group:1", lambda i=index: work("group:1", i), passive=True)))
        futures.append(asyncio.create_task(scheduler.submit("private:2", lambda: work("private:2", 0))))
        await asyncio.gather(*futures)
        await scheduler.close()
        self.assertEqual(seen[0], ("group:1", 0))
        self.assertEqual(seen[1], ("private:2", 0))

    async def test_passive_jobs_coalesce_when_scope_is_full(self):
        scheduler = FairChatScheduler(workers=1, per_scope_limit=1)
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()

        first = asyncio.create_task(scheduler.submit("group:1", blocked, passive=True))
        await asyncio.sleep(0)
        second = asyncio.create_task(scheduler.submit("group:1", lambda: asyncio.sleep(0), passive=True))
        await asyncio.sleep(0)
        third = asyncio.create_task(scheduler.submit("group:1", lambda: asyncio.sleep(0), passive=True))
        await asyncio.sleep(0)
        gate.set()
        result = await asyncio.gather(first, second, third, return_exceptions=True)
        self.assertIsNone(result[1])
        self.assertEqual(scheduler.pending_count, 0)
        await scheduler.close()

    async def test_direct_work_is_rejected_when_scope_is_full(self):
        scheduler = FairChatScheduler(workers=1, per_scope_limit=1)
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()

        first = asyncio.create_task(scheduler.submit("private:1", blocked))
        await asyncio.sleep(0)
        with self.assertRaises(asyncio.QueueFull):
            await scheduler.submit("private:1", lambda: asyncio.sleep(0))
        gate.set()
        await first
        await scheduler.close()

    async def test_direct_work_evicts_passive_candidate_at_capacity(self):
        scheduler = FairChatScheduler(workers=1, per_scope_limit=1)
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()

        active = asyncio.create_task(scheduler.submit("group:1", blocked, passive=True))
        await asyncio.sleep(0)
        displaced = asyncio.create_task(scheduler.submit("group:1", lambda: asyncio.sleep(0), passive=True))
        await asyncio.sleep(0)
        direct = asyncio.create_task(scheduler.submit("group:1", lambda: asyncio.sleep(0, result="direct")))
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(active, displaced, direct, return_exceptions=True)
        self.assertIsNone(results[1])
        self.assertEqual(results[2], "direct")
        await scheduler.close()


if __name__ == "__main__":
    unittest.main()
