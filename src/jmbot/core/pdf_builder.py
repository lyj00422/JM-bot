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

    return _build_pdf_from_images(images, output)


def build_split_pdfs(
    image_root: str | Path,
    output_dir: str | Path,
    base_name: str,
    *,
    images_per_part: int,
) -> tuple[Path, ...]:
    """Build multiple numbered PDF parts from images under a root directory."""

    if images_per_part < 1:
        raise ValueError("images_per_part must be at least 1")

    images = collect_images(image_root)
    if not images:
        raise ValueError(f"No images found under {Path(image_root)}")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    for part_index, start in enumerate(range(0, len(images), images_per_part), start=1):
        chunk = images[start : start + images_per_part]
        output = output_root / f"{base_name}.part{part_index}.pdf"
        parts.append(_build_pdf_from_images(chunk, output))
    return tuple(parts)


def _build_pdf_from_images(images: list[Path], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)

    if _build_pdf_with_img2pdf(images, output):
        return output

    return _build_pdf_with_pillow(images, output)


def _build_pdf_with_img2pdf(images: list[Path], output: Path) -> bool:
    try:
        import img2pdf
    except ImportError:
        return False

    try:
        output.write_bytes(img2pdf.convert([str(path) for path in images]))
    except Exception:
        if output.exists():
            output.unlink()
        return False

    return True


def _build_pdf_with_pillow(images: list[Path], output: Path) -> Path:
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
