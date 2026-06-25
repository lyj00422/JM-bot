from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from jmbot.core.catalog_service import AlbumDetail, SearchResultPage, get_album_detail, search_albums
from jmbot.core.job_executor import JobTimeoutError, run_with_timeout


T = TypeVar("T")


class QueryTimeoutError(TimeoutError):
    """Raised when an isolated metadata query exceeds its deadline."""


def run_query_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout_seconds: float,
    **kwargs: Any,
) -> T:
    """Run a metadata query in the existing spawn-based process isolation."""

    try:
        return run_with_timeout(func, *args, timeout_seconds=timeout_seconds, **kwargs)
    except JobTimeoutError as exc:
        raise QueryTimeoutError(str(exc)) from exc


def query_album_detail(album_id: str, *, timeout_seconds: float) -> AlbumDetail:
    return run_query_with_timeout(get_album_detail, album_id, timeout_seconds=timeout_seconds)


def query_search(keyword: str, page: int, *, timeout_seconds: float) -> SearchResultPage:
    return run_query_with_timeout(search_albums, keyword, page=page, timeout_seconds=timeout_seconds)
