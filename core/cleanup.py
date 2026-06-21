from __future__ import annotations

import shutil
from pathlib import Path


def cleanup_album_files(album_dir: str | Path, pdf_path: str | Path) -> None:
    """上传成功后删除本地漫画图片目录和 PDF，避免长期占用磁盘。"""

    album = Path(album_dir)
    pdf = Path(pdf_path)

    if album.exists():
        if album.is_dir():
            shutil.rmtree(album)
        else:
            album.unlink()

    if pdf.exists():
        if pdf.is_dir():
            shutil.rmtree(pdf)
        else:
            pdf.unlink()
