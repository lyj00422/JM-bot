from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from jmbot.core.catalog_service import AlbumDetail, SearchResultPage


class SearchCacheMissError(LookupError):
    """Raised when the caller has no current search result."""


class SearchIndexError(LookupError):
    """Raised when a displayed search index does not exist."""


@dataclass(frozen=True)
class SearchScope:
    destination_type: Literal["group", "private"]
    user_id: int
    group_id: int | None = None


T = TypeVar("T")
K = TypeVar("K")


class _TtlCache(Generic[K, T]):
    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        if ttl_seconds < 1 or max_entries < 1:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._items: OrderedDict[K, tuple[float, T]] = OrderedDict()

    def put(self, key: K, value: T) -> None:
        now = self.clock()
        self._purge_expired(now)
        self._items.pop(key, None)
        self._items[key] = (now + self.ttl_seconds, value)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def get(self, key: K) -> T | None:
        now = self.clock()
        self._purge_expired(now)
        item = self._items.get(key)
        if item is None:
            return None
        _, value = item
        self._items.move_to_end(key)
        return value

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, (deadline, _) in self._items.items() if deadline <= now]
        for key in expired:
            self._items.pop(key, None)


class SearchCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 600,
        max_entries: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._cache = _TtlCache[SearchScope, SearchResultPage](
            ttl_seconds=ttl_seconds,
            max_entries=max_entries,
            clock=clock,
        )

    def put(self, scope: SearchScope, result: SearchResultPage) -> None:
        self._cache.put(scope, result)

    def resolve_album_id(self, scope: SearchScope, index: int) -> str:
        result = self._cache.get(scope)
        if result is None:
            raise SearchCacheMissError("search result missing or expired")
        if index < 1 or index > len(result.items):
            raise SearchIndexError(f"search result index out of range: {index}")
        return result.items[index - 1].album_id


class DetailCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._cache = _TtlCache[str, AlbumDetail](
            ttl_seconds=ttl_seconds,
            max_entries=max_entries,
            clock=clock,
        )

    def put(self, detail: AlbumDetail) -> None:
        self._cache.put(detail.album_id, detail)

    def get(self, album_id: str) -> AlbumDetail | None:
        return self._cache.get(album_id)
