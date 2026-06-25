from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

from jmbot.core.catalog_service import AlbumDetail


class DetailCache:
    """Small in-memory TTL cache for album details."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ):
        if ttl_seconds < 1 or max_entries < 1:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._items: OrderedDict[str, tuple[float, AlbumDetail]] = OrderedDict()

    def put(self, detail: AlbumDetail) -> None:
        now = self.clock()
        self._purge_expired(now)
        self._items.pop(detail.album_id, None)
        self._items[detail.album_id] = (now + self.ttl_seconds, detail)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def get(self, album_id: str) -> AlbumDetail | None:
        now = self.clock()
        self._purge_expired(now)
        item = self._items.get(album_id)
        if item is None:
            return None
        _, detail = item
        self._items.move_to_end(album_id)
        return detail

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, (deadline, _) in self._items.items() if deadline <= now]
        for key in expired:
            self._items.pop(key, None)
