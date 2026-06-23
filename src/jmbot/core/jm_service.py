from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import jmcomic

from jmbot.core.pdf_builder import build_pdf, build_split_pdfs, collect_images


class CreateOption(Protocol):
    """Protocol for injecting jmcomic.create_option in tests."""

    def __call__(self, option_path: str): ...


class DownloadAlbum(Protocol):
    """Protocol for injecting jmcomic.download_album in tests."""

    def __call__(self, album_id: str, option): ...


@dataclass(frozen=True)
class DownloadSettings:
    """Download directories and jmcomic resource settings."""

    download_root: Path
    option_root: Path
    download_image_threads: int = 10
    download_photo_threads: int = 2
    jmcomic_log: bool = True
    jmcomic_retry_times: int = 5
    auto_split_pdf: bool = False
    split_image_count: int = 80


@dataclass(frozen=True)
class DownloadResult:
    """Result of the image download stage."""

    album_id: str
    album_dir: Path
    option_path: Path


@dataclass(frozen=True)
class AlbumJobResult:
    """Complete album job result, including generated PDF path(s)."""

    album_id: str
    album_dir: Path
    pdf_path: Path
    pdf_paths: tuple[Path, ...] = ()

    @property
    def output_paths(self) -> tuple[Path, ...]:
        """Return every PDF that should be uploaded for this job."""

        return self.pdf_paths or (self.pdf_path,)


def download_comic(
    album_id: str,
    settings: DownloadSettings,
    *,
    create_option: CreateOption = jmcomic.create_option,
    download_album: DownloadAlbum = jmcomic.download_album,
) -> DownloadResult:
    """Download one JM album and verify that image files were produced."""

    album_dir = settings.download_root / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    settings.option_root.mkdir(parents=True, exist_ok=True)

    option_path = settings.option_root / f"jm_{album_id}.yml"
    _write_option_file(option_path, album_dir, settings)

    option = create_option(str(option_path))
    download_album(album_id, option)

    if not collect_images(album_dir):
        raise FileNotFoundError(f"No downloaded images found for JM{album_id}: {album_dir}")

    return DownloadResult(album_id=album_id, album_dir=album_dir, option_path=option_path)


def create_pdf_for_album(
    album_id: str,
    album_dir: str | Path,
    output_root: str | Path,
    *,
    pdf_builder: Callable[[str | Path, str | Path], Path] = build_pdf,
) -> Path:
    """Convert a downloaded album directory into a single PDF file."""

    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    return pdf_builder(album_dir, output_dir / f"JM{album_id}.pdf")


def create_pdfs_for_album(
    album_id: str,
    album_dir: str | Path,
    output_root: str | Path,
    settings: DownloadSettings,
) -> tuple[Path, ...]:
    """Convert a downloaded album into one PDF or numbered PDF parts."""

    images = collect_images(album_dir)
    if not images:
        raise ValueError(f"No images found under {Path(album_dir)}")

    if settings.auto_split_pdf and len(images) > settings.split_image_count:
        return build_split_pdfs(
            album_dir,
            output_root,
            f"JM{album_id}",
            images_per_part=settings.split_image_count,
        )

    return (create_pdf_for_album(album_id, album_dir, output_root),)


def run_album_job(
    album_id: str,
    download_settings: DownloadSettings,
    output_root: str | Path,
    *,
    create_option: CreateOption = jmcomic.create_option,
    download_album: DownloadAlbum = jmcomic.download_album,
) -> AlbumJobResult:
    """Run the full album flow: download images, create PDF, return paths."""

    output_dir = Path(output_root)
    cached_parts = _cached_split_pdfs(output_dir, album_id) if download_settings.auto_split_pdf else ()
    if cached_parts:
        return AlbumJobResult(
            album_id=album_id,
            album_dir=download_settings.download_root / album_id,
            pdf_path=cached_parts[0],
            pdf_paths=cached_parts,
        )

    cached_pdf = output_dir / f"JM{album_id}.pdf"
    if cached_pdf.exists() and cached_pdf.is_file():
        return AlbumJobResult(album_id=album_id, album_dir=download_settings.download_root / album_id, pdf_path=cached_pdf)

    download_result = download_comic(
        album_id,
        download_settings,
        create_option=create_option,
        download_album=download_album,
    )
    pdf_paths = create_pdfs_for_album(album_id, download_result.album_dir, output_root, download_settings)
    return AlbumJobResult(
        album_id=album_id,
        album_dir=download_result.album_dir,
        pdf_path=pdf_paths[0],
        pdf_paths=pdf_paths,
    )


def _cached_split_pdfs(output_root: Path, album_id: str) -> tuple[Path, ...]:
    part_pattern = re.compile(rf"^JM{re.escape(album_id)}\.part(\d+)\.pdf$")
    indexed_parts: list[tuple[int, Path]] = []
    for path in output_root.glob(f"JM{album_id}.part*.pdf"):
        match = part_pattern.match(path.name)
        if match is not None and path.is_file():
            indexed_parts.append((int(match.group(1)), path))

    return tuple(path for _, path in sorted(indexed_parts))


def _write_option_file(option_path: Path, album_dir: Path, settings: DownloadSettings) -> None:
    """Write the jmcomic option file for this album download."""

    base_dir = album_dir.resolve().as_posix()
    log_enabled = str(settings.jmcomic_log).lower()
    option_path.write_text(
        "\n".join(
            [
                "version: '2.1'",
                f"log: {log_enabled}",
                "dir_rule:",
                "  rule: Bd",
                f"  base_dir: '{base_dir}'",
                "download:",
                "  cache: true",
                "  image:",
                "    decode: true",
                "    suffix: null",
                "  threading:",
                f"    image: {settings.download_image_threads}",
                f"    photo: {settings.download_photo_threads}",
                "client:",
                "  impl: api",
                f"  retry_times: {settings.jmcomic_retry_times}",
                "  domain: []",
                "  cache: null",
                "  postman:",
                "    type: curl_cffi",
                "    meta_data:",
                "      impersonate: chrome",
                "      headers: null",
                "      proxies: null",
                "plugins:",
                "  valid: log",
                "",
            ]
        ),
        encoding="utf-8",
    )
