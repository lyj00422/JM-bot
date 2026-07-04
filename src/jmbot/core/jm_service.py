from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable, Protocol

import jmcomic

from jmbot.core.pdf_builder import build_pdf, build_split_pdfs, collect_images
from jmbot.core.zip_builder import build_zip


LOGGER = logging.getLogger(__name__)


class CreateOption(Protocol):
    """Protocol for injecting jmcomic.create_option in tests."""

    def __call__(self, option_path: str): ...


class DownloadAlbum(Protocol):
    """Protocol for injecting jmcomic.download_album in tests."""

    def __call__(self, album_id: str, option, **kwargs: Any): ...


class PageCountClient(Protocol):
    def get_album_detail(self, album_id: str): ...

    def get_photo_detail(
        self,
        photo_id: str,
        fetch_album: bool = False,
        fetch_scramble_id: bool = True,
    ): ...


@dataclass(frozen=True)
class DownloadRequest:
    album_id: str
    output_format: str = "pdf"
    chapters: tuple[int, ...] = ()


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
    progress_mode: str = "stage"


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


def count_request_pages(
    request: str | DownloadRequest,
    *,
    client: PageCountClient | None = None,
) -> int:
    """Return the exact image count for the requested album chapters."""

    normalized = _normalize_request(request)
    active_client = client or jmcomic.JmOption.default().new_jm_client(impl="api", cache=None)
    album = active_client.get_album_detail(normalized.album_id)
    if not normalized.chapters:
        album_page_count = _positive_int(getattr(album, "page_count", None))
        if album_page_count is not None:
            return album_page_count

    episodes = list(getattr(album, "episode_list", ()) or ())
    selected_chapters = normalized.chapters or tuple(range(1, len(episodes) + 1))
    missing = [chapter for chapter in selected_chapters if chapter > len(episodes)]
    if missing:
        raise ValueError(f"JM{normalized.album_id} 不存在章节 {missing[0]}")

    total = 0
    for chapter in selected_chapters:
        photo_id = str(episodes[chapter - 1][0])
        photo = active_client.get_photo_detail(
            photo_id,
            fetch_album=False,
            fetch_scramble_id=True,
        )
        total += len(getattr(photo, "page_arr", ()) or ())
    return total


def download_comic(
    request: str | DownloadRequest,
    settings: DownloadSettings,
    *,
    create_option: CreateOption = jmcomic.create_option,
    download_album: DownloadAlbum = jmcomic.download_album,
    album_detail_fetcher: Callable[[str], object] | None = None,
    download_photo: Callable[[object, object], object] = jmcomic.download_photo,
    page_total_fetcher: Callable[[DownloadRequest], int] = count_request_pages,
) -> DownloadResult:
    """Download one JM album and verify that image files were produced."""

    normalized = _normalize_request(request)
    album_id = normalized.album_id
    album_dir = settings.download_root / _request_directory_name(normalized)
    album_dir.mkdir(parents=True, exist_ok=True)
    settings.option_root.mkdir(parents=True, exist_ok=True)

    option_path = settings.option_root / f"jm_{_request_directory_name(normalized)}.yml"
    _write_option_file(option_path, album_dir, settings)

    expected_total: int | None = None
    if settings.progress_mode == "precise":
        try:
            total = page_total_fetcher(normalized)
        except Exception:
            LOGGER.warning(
                "Could not count pages for JM%s precise progress",
                album_id,
                exc_info=True,
            )
        else:
            if total > 0:
                expected_total = total
                (album_dir / ".progress-total").write_text(str(expected_total), encoding="utf-8")

    option = create_option(str(option_path))
    if normalized.chapters:
        fetcher = album_detail_fetcher or _fetch_album_detail
        album = fetcher(album_id)
        episodes = list(getattr(album, "episode_list", ()) or ())
        missing = [chapter for chapter in normalized.chapters if chapter > len(episodes)]
        if missing:
            raise ValueError(f"JM{album_id} 不存在章节 {missing[0]}")
        photo_ids = [str(episodes[chapter - 1][0]) for chapter in normalized.chapters]
        raw_download_result = tuple(
            _call_download_api_without_exception_check(download_photo, photo_id, option)
            for photo_id in photo_ids
        )
    else:
        raw_download_result = _call_download_api_without_exception_check(download_album, album_id, option)

    images = collect_images(album_dir)
    if not images:
        raise FileNotFoundError(f"No downloaded images found for JM{album_id}: {album_dir}")
    if expected_total is None:
        expected_total = _expected_image_count_from_download_result(raw_download_result)
    if expected_total is not None and len(images) < expected_total:
        raise RuntimeError(f"JM{album_id} 下载不完整：已下载 {len(images)} / {expected_total} 张图片")

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
    *,
    base_name: str | None = None,
) -> tuple[Path, ...]:
    """Convert a downloaded album into one PDF or numbered PDF parts."""

    images = collect_images(album_dir)
    if not images:
        raise ValueError(f"No images found under {Path(album_dir)}")

    if settings.auto_split_pdf and len(images) > settings.split_image_count:
        return build_split_pdfs(
            album_dir,
            output_root,
            base_name or f"JM{album_id}",
            images_per_part=settings.split_image_count,
        )

    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{base_name or f'JM{album_id}'}.pdf"
    return (build_pdf(album_dir, output),)


def run_album_job(
    request: str | DownloadRequest,
    download_settings: DownloadSettings,
    output_root: str | Path,
    *,
    create_option: CreateOption = jmcomic.create_option,
    download_album: DownloadAlbum = jmcomic.download_album,
    album_detail_fetcher: Callable[[str], object] | None = None,
    download_photo: Callable[[object, object], object] = jmcomic.download_photo,
    page_total_fetcher: Callable[[DownloadRequest], int] = count_request_pages,
) -> AlbumJobResult:
    """Run the full album flow: download images, create PDF, return paths."""

    normalized = _normalize_request(request)
    album_id = normalized.album_id
    base_name = _output_base_name(normalized)
    output_dir = Path(output_root)
    cached_parts = (
        _cached_split_pdfs(output_dir, base_name)
        if normalized.output_format == "pdf" and download_settings.auto_split_pdf
        else ()
    )
    if cached_parts:
        return AlbumJobResult(
            album_id=album_id,
            album_dir=download_settings.download_root / _request_directory_name(normalized),
            pdf_path=cached_parts[0],
            pdf_paths=cached_parts,
        )

    cached_output = output_dir / f"{base_name}.{normalized.output_format}"
    if cached_output.exists() and cached_output.is_file():
        return AlbumJobResult(
            album_id=album_id,
            album_dir=download_settings.download_root / _request_directory_name(normalized),
            pdf_path=cached_output,
        )

    download_result = download_comic(
        normalized,
        download_settings,
        create_option=create_option,
        download_album=download_album,
        album_detail_fetcher=album_detail_fetcher,
        download_photo=download_photo,
        page_total_fetcher=page_total_fetcher,
    )
    images = collect_images(download_result.album_dir)
    (download_result.album_dir / ".stage-building").write_text(str(len(images)), encoding="utf-8")
    if normalized.output_format == "zip":
        output = build_zip(
            download_result.album_dir,
            output_dir / f"{base_name}.zip",
            base_name=base_name,
        )
        output_paths = (output,)
    else:
        output_paths = create_pdfs_for_album(
            album_id,
            download_result.album_dir,
            output_root,
            download_settings,
            base_name=base_name,
        )
    return AlbumJobResult(
        album_id=album_id,
        album_dir=download_result.album_dir,
        pdf_path=output_paths[0],
        pdf_paths=output_paths,
    )


def _cached_split_pdfs(output_root: Path, base_name: str) -> tuple[Path, ...]:
    manifest = output_root / f"{base_name}.parts.txt"
    if not manifest.is_file():
        return ()

    part_pattern = re.compile(rf"^{re.escape(base_name)}\.part(\d+)\.pdf$")
    indexed_parts: list[tuple[int, Path]] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or Path(name).name != name:
            return ()

        match = part_pattern.match(name)
        path = output_root / name
        if match is None or not path.is_file():
            return ()

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


def _normalize_request(request: str | DownloadRequest) -> DownloadRequest:
    return DownloadRequest(request) if isinstance(request, str) else request


def _request_directory_name(request: DownloadRequest) -> str:
    return download_directory_name_for_request(request)


def download_directory_name_for_request(request: DownloadRequest) -> str:
    """Return the isolated image-directory name for a download variant."""

    if not request.chapters and request.output_format == "pdf":
        return request.album_id
    if request.chapters:
        chapter_key = "-".join(str(chapter) for chapter in request.chapters)
        return f"{request.album_id}__ch{chapter_key}_{request.output_format}"
    return f"{request.album_id}__{request.output_format}"


def download_directory_for_request(settings: DownloadSettings, request: DownloadRequest) -> Path:
    return settings.download_root / _request_directory_name(request)


def _output_base_name(request: DownloadRequest) -> str:
    if not request.chapters:
        return f"JM{request.album_id}"
    return f"JM{request.album_id}（章节{_format_chapter_selection(request.chapters)}）"


def _format_chapter_selection(chapters: tuple[int, ...]) -> str:
    parts: list[str] = []
    start = previous = chapters[0]
    for chapter in chapters[1:]:
        if chapter == previous + 1:
            previous = chapter
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = chapter
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


def _fetch_album_detail(album_id: str):
    return jmcomic.JmOption.default().new_jm_client(impl="api", cache=None).get_album_detail(album_id)


def _call_download_api_without_exception_check(download_api: Callable[..., object], target: object, option: object):
    """Let JMbot verify final image completeness instead of trusting jmcomic's partial-failure flag."""

    if _supports_check_exception(download_api):
        return download_api(target, option, check_exception=False)
    return download_api(target, option)


def _supports_check_exception(func: Callable[..., object]) -> bool:
    try:
        parameters = signature(func).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == Parameter.VAR_KEYWORD or parameter.name == "check_exception"
        for parameter in parameters
    )


def _expected_image_count_from_download_result(result: object) -> int | None:
    if result is None:
        return None
    if isinstance(result, set):
        total = sum(_entity_image_count(item[0] if isinstance(item, tuple) and item else item) for item in result)
        return total if total > 0 else None
    if isinstance(result, tuple) and result:
        return _expected_image_count_from_download_result(result[0])
    return _entity_image_count(result) or None


def _entity_image_count(entity: object) -> int:
    page_count = _positive_int(getattr(entity, "page_count", None))
    if page_count is not None:
        return page_count
    try:
        return len(entity)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
