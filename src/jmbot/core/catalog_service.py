from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import jmcomic


class CatalogClient(Protocol):
    def get_album_detail(self, album_id: str): ...
    def search_site(self, search_query: str, page: int = 1): ...
    def search_author(self, search_query: str, page: int = 1): ...


@dataclass(frozen=True)
class AlbumDetail:
    album_id: str
    title: str
    authors: tuple[str, ...]
    page_count: int | None
    tags: tuple[str, ...]
    chapters: tuple[str, ...]
    update_time: str | None


SearchKind = Literal["site", "author"]


@dataclass(frozen=True)
class SearchItem:
    album_id: str
    title: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    keyword: str
    kind: SearchKind
    page: int
    page_count: int
    total: int
    items: tuple[SearchItem, ...]


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
    kind: SearchKind = "site",
    page: int = 1,
    client: CatalogClient | None = None,
) -> SearchResult:
    if page < 1:
        raise ValueError("page must be at least 1")
    if kind not in {"site", "author"}:
        raise ValueError("kind must be site or author")

    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        raise ValueError("keyword is required")

    search_client = client or create_catalog_client()
    raw_page = (
        search_client.search_author(normalized_keyword, page=page)
        if kind == "author"
        else search_client.search_site(normalized_keyword, page=page)
    )

    return SearchResult(
        keyword=normalized_keyword,
        kind=kind,
        page=page,
        page_count=_non_negative_int(getattr(raw_page, "page_count", 0)),
        total=_non_negative_int(getattr(raw_page, "total", 0)),
        items=_search_items(getattr(raw_page, "content", ())),
    )


def _search_items(content: Any) -> tuple[SearchItem, ...]:
    items: list[SearchItem] = []
    for raw_item in content or ():
        if not isinstance(raw_item, (tuple, list)) or len(raw_item) < 2:
            continue
        album_id = str(raw_item[0]).strip()
        info = raw_item[1] if isinstance(raw_item[1], dict) else {}
        title = _optional_text(info.get("name")) or album_id
        if not album_id:
            continue
        items.append(
            SearchItem(
                album_id=album_id,
                title=title,
                tags=_string_tuple(info.get("tags", ())),
            )
        )
    return tuple(items)


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
        parsed = None if value is None else int(value)
        return parsed if parsed and parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else 0
    except (TypeError, ValueError):
        return 0


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "0" else None


def _text_or_unknown(value: Any) -> str:
    return _optional_text(value) or "未知"
