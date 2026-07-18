from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from jmbot.core.catalog_service import (
    AlbumDetail,
    RankingQuery,
    RankingResult,
    SearchKind,
    SearchResult,
    get_album_detail,
    get_ranking_page,
    search_albums,
)
from jmbot.core.job_executor import JobTimeoutError, run_with_timeout
from jmbot.core.preview_service import CoverResult, PreviewResult, build_preview, download_cover


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


def query_album_search(
    keyword: str,
    *,
    kind: SearchKind,
    page: int,
    timeout_seconds: float,
) -> SearchResult:
    return run_query_with_timeout(
        search_albums,
        keyword,
        kind=kind,
        page=page,
        timeout_seconds=timeout_seconds,
    )


def query_album_ranking(query: RankingQuery, *, timeout_seconds: float) -> RankingResult:
    return run_query_with_timeout(
        get_ranking_page,
        query,
        timeout_seconds=timeout_seconds,
    )


def query_cover(album_id: str, preview_root: str | Path, *, timeout_seconds: float) -> CoverResult:
    return run_query_with_timeout(
        download_cover,
        album_id,
        preview_root,
        timeout_seconds=timeout_seconds,
    )


def query_preview(
    album_id: str,
    preview_root: str | Path,
    *,
    page_count: int,
    timeout_seconds: float,
) -> PreviewResult:
    return run_query_with_timeout(
        build_preview,
        album_id,
        preview_root,
        page_count=page_count,
        timeout_seconds=timeout_seconds,
    )
