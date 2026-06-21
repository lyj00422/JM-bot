from __future__ import annotations

import re
from pathlib import Path

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _natural_key(path: Path) -> list[int | str]:
    """自然排序键：让 2.jpg 排在 10.jpg 前面，避免 PDF 页码顺序错乱。"""

    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.as_posix())
    ]


def collect_images(root: str | Path) -> list[Path]:
    """递归收集漫画目录中的图片，并按自然顺序排列。"""

    root_path = Path(root)
    if not root_path.exists():
        return []

    return sorted(
        (
            path
            for path in root_path.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=_natural_key,
    )


def build_pdf(image_root: str | Path, out_pdf: str | Path) -> Path:
    """使用 Pillow 将多张图片合成为一个 PDF。"""

    images = collect_images(image_root)
    if not images:
        raise ValueError(f"No images found under {Path(image_root)}")

    output = Path(out_pdf)
    output.parent.mkdir(parents=True, exist_ok=True)

    pil_images = [_open_rgb(path) for path in images]
    try:
        first, rest = pil_images[0], pil_images[1:]
        first.save(output, "PDF", save_all=True, append_images=rest)
    finally:
        for image in pil_images:
            image.close()

    return output


def _open_rgb(path: Path) -> Image.Image:
    """Pillow 保存 PDF 需要 RGB 图片；其它模式先转换再写入。"""

    image = Image.open(path)
    if image.mode == "RGB":
        return image.copy()

    converted = image.convert("RGB")
    image.close()
    return converted
