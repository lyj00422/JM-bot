from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlparse

from jmbot.core.catalog_service import create_catalog_client
from jmbot.core.pdf_builder import build_pdf


class PreviewClient(Protocol):
    def download_album_cover(self, album_id: str, save_path: str, size: str = ""): ...

    def get_album_detail(self, album_id: str): ...

    def get_photo_detail(self, photo_id: str, fetch_album: bool = False, fetch_scramble_id: bool = True): ...

    def download_by_image_detail(self, image, save_path: str, decode_image: bool = True): ...


@dataclass(frozen=True)
class CoverResult:
    album_id: str
    media_dir: Path
    cover_path: Path


@dataclass(frozen=True)
class PreviewResult:
    album_id: str
    media_dir: Path
    page_paths: tuple[Path, ...]
    pdf_path: Path


def download_cover(
    album_id: str,
    preview_root: str | Path,
    *,
    client: PreviewClient | None = None,
) -> CoverResult:
    media_dir = _media_dir(preview_root, album_id, "cover")
    cover_path = media_dir / f"JM{album_id}-cover.jpg"
    (client or create_catalog_client()).download_album_cover(album_id, str(cover_path))
    if not cover_path.is_file():
        raise FileNotFoundError(f"Cover was not created for JM{album_id}")
    return CoverResult(album_id=album_id, media_dir=media_dir, cover_path=cover_path)


def build_preview(
    album_id: str,
    preview_root: str | Path,
    *,
    page_count: int = 10,
    client: PreviewClient | None = None,
    pdf_builder: Callable[[str | Path, str | Path], Path] = build_pdf,
) -> PreviewResult:
    if page_count < 1:
        raise ValueError("page_count must be at least 1")

    media_dir = _media_dir(preview_root, album_id, "preview")
    active_client = client or create_catalog_client()
    album = active_client.get_album_detail(album_id)
    episodes = list(getattr(album, "episode_list", ()) or ())
    if not episodes:
        raise ValueError(f"JM{album_id} has no chapters")

    photo_id = str(episodes[0][0])
    photo = active_client.get_photo_detail(photo_id, fetch_album=False, fetch_scramble_id=True)
    available = len(getattr(photo, "page_arr", ()) or ())
    selected = min(page_count, available)
    if selected == 0:
        raise ValueError(f"JM{album_id} first chapter has no pages")

    page_paths: list[Path] = []
    for index in range(selected):
        image = photo.create_image_detail(index)
        suffix = _image_suffix(getattr(image, "download_url", ""))
        page_path = media_dir / f"page-{index + 1:03d}{suffix}"
        active_client.download_by_image_detail(image, str(page_path), decode_image=True)
        if not page_path.is_file():
            raise FileNotFoundError(f"Preview page was not created: {page_path}")
        page_paths.append(page_path)

    pdf_path = media_dir / f"JM{album_id}（预览）.pdf"
    built = pdf_builder(media_dir, pdf_path)
    return PreviewResult(
        album_id=album_id,
        media_dir=media_dir,
        page_paths=tuple(page_paths),
        pdf_path=Path(built),
    )


def _media_dir(preview_root: str | Path, album_id: str, operation: str) -> Path:
    media_dir = Path(preview_root) / album_id / operation
    if media_dir.exists():
        shutil.rmtree(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def _image_suffix(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"} else ".jpg"
