from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar


T = TypeVar("T")


class DuplicateTaskError(RuntimeError):
    """Raised when the same album is already being processed."""

    def __init__(self, album_id: str):
        super().__init__(f"JM{album_id} is already running")
        self.album_id = album_id


class TaskRunner:
    """Limit job concurrency and reject duplicate in-flight album IDs."""

    def __init__(self, max_concurrency: int = 1):
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")

        self._active_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def submit(self, album_id: str, job: Callable[[], Awaitable[T]]) -> T:
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
