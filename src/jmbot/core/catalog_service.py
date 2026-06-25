from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jmcomic


class CatalogClient(Protocol):
    def get_album_detail(self, album_id: str): ...

    def search_site(self, keyword: str, page: int = 1): ...


@dataclass(frozen=True)
class AlbumSummary:
    album_id: str
    title: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlbumDetail:
    album_id: str
    title: str
    authors: tuple[str, ...]
    page_count: int | None
    tags: tuple[str, ...]
    chapters: tuple[str, ...]
    update_time: str | None


@dataclass(frozen=True)
class SearchResultPage:
    keyword: str
    page: int
    total: int
    items: tuple[AlbumSummary, ...]


def create_catalog_client() -> CatalogClient:
    """Create the jmcomic client used for metadata queries."""

    return jmcomic.JmOption.default().new_jm_client(impl="api", cache=None)


def get_album_detail(album_id: str, *, client: CatalogClient | None = None) -> AlbumDetail:
    entity = (client or create_catalog_client()).get_album_detail(album_id)
    return AlbumDetail(
        album_id=str(getattr(entity, "album_id", album_id)),
        title=_text_or_unknown(getattr(entity, "name", None)),
        authors=_string_tuple(getattr(entity, "authors", ())),
        page_count=_optional_int(getattr(entity, "page_count", None)),
        tags=_string_tuple(getattr(entity, "tags", ())),
        chapters=_chapter_titles(getattr(entity, "episode_list", ())),
        update_time=_optional_text(getattr(entity, "update_date", None)),
    )


def search_albums(
    keyword: str,
    *,
    page: int = 1,
    limit: int = 5,
    client: CatalogClient | None = None,
) -> SearchResultPage:
    result = (client or create_catalog_client()).search_site(keyword, page=page)
    items = tuple(_summary_from_search_item(item) for item in list(getattr(result, "content", ()))[:limit])
    return SearchResultPage(
        keyword=keyword,
        page=page,
        total=int(getattr(result, "total", len(items))),
        items=items,
    )


def _summary_from_search_item(item: Any) -> AlbumSummary:
    album_id, info = item
    info = info if isinstance(info, dict) else {}
    return AlbumSummary(
        album_id=str(album_id),
        title=_text_or_unknown(info.get("name")),
        tags=_string_tuple(info.get("tags", ())),
    )


def _chapter_titles(episodes: Any) -> tuple[str, ...]:
    titles: list[str] = []
    for episode in episodes or ():
        if not isinstance(episode, (tuple, list)) or len(episode) < 3:
            continue
        title = str(episode[2]).strip()
        if title:
            titles.append(title)
    return tuple(titles)


def _string_tuple(values: Any) -> tuple[str, ...]:
    if not values:
        return ()
    return tuple(text for value in values if (text := str(value).strip()))


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_or_unknown(value: Any) -> str:
    return _optional_text(value) or "未知"
