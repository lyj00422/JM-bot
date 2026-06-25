from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import jmcomic


class CatalogClient(Protocol):
    def get_album_detail(self, album_id: str): ...


@dataclass(frozen=True)
class AlbumDetail:
    album_id: str
    title: str
    authors: tuple[str, ...]
    page_count: int | None
    tags: tuple[str, ...]
    chapters: tuple[str, ...]
    update_time: str | None


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


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text != "0" else None


def _text_or_unknown(value: Any) -> str:
    return _optional_text(value) or "未知"
