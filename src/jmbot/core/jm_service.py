from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import jmcomic

from jmbot.core.pdf_builder import build_pdf, collect_images


class CreateOption(Protocol):
    """jmcomic.create_option 的协议定义，方便测试时注入假的实现。"""

    def __call__(self, option_path: str): ...


class DownloadAlbum(Protocol):
    """jmcomic.download_album 的协议定义，避免业务代码直接绑死第三方库。"""

    def __call__(self, album_id: str, option): ...


@dataclass(frozen=True)
class DownloadSettings:
    """下载相关目录：漫画图片目录和 jmcomic 配置文件目录。"""

    download_root: Path
    option_root: Path


@dataclass(frozen=True)
class DownloadResult:
    """下载阶段的结果，不包含 PDF。"""

    album_id: str
    album_dir: Path
    option_path: Path


@dataclass(frozen=True)
class AlbumJobResult:
    """完整任务结果：下载目录和最终 PDF 路径。"""

    album_id: str
    album_dir: Path
    pdf_path: Path


def download_comic(
    album_id: str,
    settings: DownloadSettings,
    *,
    create_option: CreateOption = jmcomic.create_option,
    download_album: DownloadAlbum = jmcomic.download_album,
) -> DownloadResult:
    """调用 jmcomic 下载指定 JM 漫画，并确认目录中确实生成了图片。"""

    album_dir = settings.download_root / album_id
    album_dir.mkdir(parents=True, exist_ok=True)
    settings.option_root.mkdir(parents=True, exist_ok=True)

    option_path = settings.option_root / f"jm_{album_id}.yml"
    _write_option_file(option_path, album_dir)

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
    """把已经下载好的漫画图片目录转换为单个 PDF 文件。"""

    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    return pdf_builder(album_dir, output_dir / f"JM{album_id}.pdf")


def run_album_job(album_id: str, download_settings: DownloadSettings, output_root: str | Path) -> AlbumJobResult:
    """完整任务流水线：下载图片 -> 生成 PDF -> 返回路径给 QQ 上传逻辑。"""

    download_result = download_comic(album_id, download_settings)
    pdf_path = create_pdf_for_album(album_id, download_result.album_dir, output_root)
    return AlbumJobResult(album_id=album_id, album_dir=download_result.album_dir, pdf_path=pdf_path)


def _write_option_file(option_path: Path, album_dir: Path) -> None:
    """为每次下载生成 jmcomic 配置文件，指定保存目录、线程数和访问方式。"""

    base_dir = album_dir.resolve().as_posix()
    option_path.write_text(
        "\n".join(
            [
                "version: '2.1'",
                "log: true",
                "dir_rule:",
                "  rule: Bd",
                f"  base_dir: '{base_dir}'",
                "download:",
                "  cache: true",
                "  image:",
                "    decode: true",
                "    suffix: null",
                "  threading:",
                "    image: 10",
                "    photo: 2",
                "client:",
                "  impl: api",
                "  retry_times: 5",
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
