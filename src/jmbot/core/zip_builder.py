from __future__ import annotations

import zipfile
from pathlib import Path

from jmbot.core.pdf_builder import collect_images


def build_zip(
    image_root: str | Path,
    out_zip: str | Path,
    *,
    base_name: str,
) -> Path:
    """Create a reader-friendly ZIP from downloaded album images."""

    root = Path(image_root)
    images = collect_images(root)
    if not images:
        raise ValueError(f"No images found under {root}")

    output = Path(out_zip)
    output.parent.mkdir(parents=True, exist_ok=True)
    chapter_dirs = {image.parent.relative_to(root) for image in images}
    flatten = len(chapter_dirs) == 1

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in images:
            relative = Path(image.name) if flatten else image.relative_to(root)
            archive.write(image, (Path(base_name) / relative).as_posix())

    return output
