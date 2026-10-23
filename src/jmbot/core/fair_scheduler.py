"""Bounded, fair scheduling for chat work.

Each conversation scope gets a small queue.  A busy group can coalesce its
pending passive work, while direct messages remain ordered.  The dispatcher
serves scopes round-robin, so one hot group cannot consume all workers.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass
class _Job:
    scope: str
    operation: Callable[[], Awaitable[T]]
    future: asyncio.Future[T]
    passive: bool


class FairChatScheduler:
    def __init__(self, *, workers: int = 2, per_scope_limit: int = 8, max_scopes: int = 1024):
        if workers < 1 or per_scope_limit < 1 or max_scopes < 1:
            raise ValueError("scheduler limits must be positive")
        self.workers = workers
        self.per_scope_limit = per_scope_limit
        self.max_scopes = max_scopes
        self._queues: dict[str, deque[_Job]] = defaultdict(deque)
        self._ready: deque[str] = deque()
        self._ready_set: set[str] = set()
        self._condition = asyncio.Condition()
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False
        self._inflight = 0
        self._busy: set[str] = set()
        self._active: dict[asyncio.Future[object], asyncio.Task[object]] = {}

    async def start(self) -> None:
        if self._tasks:
            return
        self._closed = False
        self._tasks = [asyncio.create_task(self._worker(), name=f"chat-worker-{i}") for i in range(self.workers)]

    async def close(self) -> None:
        self._closed = True
        async with self._condition:
            self._condition.notify_all()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._condition:
            for queue in self._queues.values():
                while queue:
                    job = queue.popleft()
                    if not job.future.done():
                        job.future.cancel()
            self._queues.clear()
            self._ready.clear()
            self._ready_set.clear()

    async def submit(self, scope: str, operation: Callable[[], Awaitable[T]], *, passive: bool = False, timeout: float | None = None) -> T:
        if not scope or ":" not in scope:
            raise ValueError("scope must be private:<id> or group:<id>")
        if self._closed:
            raise RuntimeError("chat scheduler is closed")
        if not self._tasks:
            await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        async with self._condition:
            if scope not in self._queues and len(self._queues) >= self.max_scopes:
                raise RuntimeError("chat scheduler scope limit reached")
            queue = self._queues[scope]
            if len(queue) >= self.per_scope_limit:
                if passive:
                    # Keep the newest passive snapshot; an old snapshot is no
                    # longer useful after a newer group message arrived.
                    old_index = next((index for index in range(len(queue) - 1, -1, -1) if queue[index].passive), None)
                    if old_index is None:
                        raise asyncio.QueueFull
                    old = queue[old_index]
                    del queue[old_index]
                    if not old.future.done():
                        old.future.set_result(None)
                else:
                    passive_index = next((index for index, item in enumerate(queue) if item.passive), None)
                    if passive_index is None:
                        raise asyncio.QueueFull
                    displaced = queue[passive_index]
                    del queue[passive_index]
                    if not displaced.future.done():
                        displaced.future.set_result(None)
            job = _Job(scope, operation, future, passive)
            if passive:
                queue.append(job)
            else:
                priority_index = next((index for index, item in enumerate(queue) if item.passive), len(queue))
                queue.insert(priority_index, job)
            if scope not in self._ready_set:
                self._ready.append(scope)
                self._ready_set.add(scope)
            self._condition.notify()
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        except (asyncio.CancelledError, TimeoutError):
            future.cancel()
            async with self._condition:
                active = self._active.get(future)
                if active:
                    active.cancel()
                else:
                    queue = self._queues.get(scope)
                    if queue:
                        self._queues[scope] = deque(job for job in queue if job.future is not future)
                        if not self._queues[scope]:
                            self._queues.pop(scope, None)
                        elif scope not in self._ready_set:
                            self._ready.append(scope)
                            self._ready_set.add(scope)
                self._condition.notify_all()
            raise

    async def _next_job(self) -> _Job | None:
        async with self._condition:
            while not self._closed:
                if not self._ready:
                    await self._condition.wait()
                    continue
                scope = self._ready.popleft()
                self._ready_set.discard(scope)
                if scope in self._busy:
                    continue
                queue = self._queues[scope]
                job = queue.popleft()
                self._busy.add(scope)
                if not queue:
                    self._queues.pop(scope, None)
                self._inflight += 1
                return job
            return None

    async def _worker(self) -> None:
        while True:
            job = await self._next_job()
            if job is None:
                if self._closed:
                    return
                continue
            if job.future.cancelled():
                async with self._condition:
                    self._inflight -= 1
                    self._busy.discard(job.scope)
                    if self._queues.get(job.scope) and job.scope not in self._ready_set:
                        self._ready.append(job.scope)
                        self._ready_set.add(job.scope)
                    self._condition.notify_all()
                continue
            try:
                operation = asyncio.create_task(job.operation())
                self._active[job.future] = operation
                result = await operation
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
            except Exception as exc:  # propagate the operation's real failure
                if not job.future.done():
                    job.future.set_exception(exc)
            else:
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                async with self._condition:
                    self._inflight -= 1
                    self._active.pop(job.future, None)
                    self._busy.discard(job.scope)
                    if self._queues.get(job.scope) and job.scope not in self._ready_set:
                        self._ready.append(job.scope)
                        self._ready_set.add(job.scope)
                    self._condition.notify_all()

    @property
    def pending_count(self) -> int:
        return sum(len(queue) for queue in self._queues.values()) + self._inflight

    def snapshot(self) -> dict[str, object]:
        return {"workers": self.workers, "pending": self.pending_count, "scopes": {scope: len(queue) for scope, queue in self._queues.items()}}
