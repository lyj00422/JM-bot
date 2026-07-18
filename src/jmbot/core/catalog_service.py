from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import jmcomic


class CatalogClient(Protocol):
    def get_album_detail(self, album_id: str): ...
    def search_site(self, search_query: str, page: int = 1, **kwargs: Any): ...
    def search_author(self, search_query: str, page: int = 1): ...
    def categories_filter(self, **kwargs: Any): ...


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


RankingPeriod = Literal["day", "week", "month", "all"]
RankingCategory = Literal[
    "all",
    "doujin",
    "single",
    "short",
    "another",
    "hanman",
    "meiman",
    "doujin_cosplay",
    "3d",
    "english_site",
]

RANKING_PERIOD_LABELS = {
    "day": "日榜",
    "week": "周榜",
    "month": "月榜",
    "all": "总榜",
}

RANKING_CATEGORY_LABELS = {
    "all": "全部",
    "doujin": "同人",
    "single": "单本",
    "short": "短篇",
    "another": "其他",
    "hanman": "韩漫",
    "meiman": "美漫",
    "doujin_cosplay": "Cosplay",
    "3d": "3D",
    "english_site": "英文站",
}

_RANKING_PERIOD_PARAMS = {
    "day": "t",
    "week": "w",
    "month": "m",
    "all": "a",
}

_RANKING_CATEGORY_PARAMS = {
    "all": "0",
    "doujin": "doujin",
    "single": "single",
    "short": "short",
    "another": "another",
    "hanman": "hanman",
    "meiman": "meiman",
    "doujin_cosplay": "doujin_cosplay",
    "3d": "3D",
    "english_site": "english_site",
}


@dataclass(frozen=True)
class RankingQuery:
    keyword: str | None = None
    period: RankingPeriod = "all"
    category: RankingCategory = "all"
    page: int = 1
    page_size: int = 5


@dataclass(frozen=True)
class RankingResult:
    query: RankingQuery
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


def get_ranking_page(
    query: RankingQuery,
    *,
    client: CatalogClient | None = None,
) -> RankingResult:
    """Return one five-item Bot page from the official view ranking."""

    if query.page < 1 or query.page_size < 1:
        raise ValueError("ranking page and page_size must be at least 1")
    if query.period not in _RANKING_PERIOD_PARAMS:
        raise ValueError("unsupported ranking period")
    if query.category not in _RANKING_CATEGORY_PARAMS:
        raise ValueError("unsupported ranking category")

    catalog_client = client or create_catalog_client()
    keyword = query.keyword.strip() if query.keyword else None
    time_value = _RANKING_PERIOD_PARAMS[query.period]
    category_value = _RANKING_CATEGORY_PARAMS[query.category]

    def fetch(source_page: int):
        if keyword:
            return catalog_client.search_site(
                keyword,
                page=source_page,
                order_by="mv",
                time=time_value,
                category=category_value,
            )
        return catalog_client.categories_filter(
            page=source_page,
            time=time_value,
            category=category_value,
            order_by="mv",
        )

    first_page = fetch(1)
    total = _non_negative_int(getattr(first_page, "total", 0))
    source_size = _non_negative_int(getattr(first_page, "page_size", 0))
    if source_size == 0:
        source_size = max(len(getattr(first_page, "content", ()) or ()), 1)

    start = (query.page - 1) * query.page_size
    page_count = math.ceil(total / query.page_size) if total else 0
    if total == 0 or start >= total:
        return RankingResult(query, query.page, page_count, total, ())

    end = start + query.page_size
    first_source = start // source_size + 1
    last_source = (end - 1) // source_size + 1
    source_items: list[SearchItem] = []
    for source_page in range(first_source, last_source + 1):
        raw_page = first_page if source_page == 1 else fetch(source_page)
        source_items.extend(_search_items(getattr(raw_page, "content", ())))

    local_start = start - (first_source - 1) * source_size
    return RankingResult(
        query=query,
        page=query.page,
        page_count=page_count,
        total=total,
        items=tuple(source_items[local_start:local_start + query.page_size]),
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
