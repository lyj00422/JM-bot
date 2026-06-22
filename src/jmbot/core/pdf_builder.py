from __future__ import annotations

import re
from pathlib import Path

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _natural_key(path: Path) -> list[int | str]:
    """Sort numeric path parts naturally, so 2.jpg comes before 10.jpg."""

    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.as_posix())
    ]


def collect_images(root: str | Path) -> list[Path]:
    """Collect supported image files recursively in natural order."""

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
    """Use Pillow to combine album images into a single PDF."""

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
    """Return a standalone RGB image object that can be safely saved to PDF."""

    image = Image.open(path)
    if image.mode == "RGB":
        return image.copy()

    converted = image.convert("RGB")
    image.close()
    return converted
