from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")


class DuplicateTaskError(RuntimeError):
    """同一本漫画已经在处理时抛出，避免重复下载同一个任务。"""

    def __init__(self, album_id: str):
        super().__init__(f"JM{album_id} is already running")
        self.album_id = album_id


class TaskRunner:
    """控制下载并发，并记录正在处理的 album_id。"""

    def __init__(self, max_concurrency: int = 1):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")

        self._active_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def submit(self, album_id: str, job: Callable[[], Awaitable[T]]) -> T:
        """提交一个异步任务；重复 album_id 会被拒绝，并发数量由 semaphore 限制。"""

        async with self._lock:
            if album_id in self._active_ids:
                raise DuplicateTaskError(album_id)
            self._active_ids.add(album_id)

        try:
            async with self._semaphore:
                return await job()
        finally:
            async with self._lock:
                self._active_ids.discard(album_id)
