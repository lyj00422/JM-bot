from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont, ImageOps

from jmbot.core.catalog_service import RankingResult, SearchItem, SearchResult


def build_search_result_card(
    result: SearchResult,
    cover_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    command_prefix: str = "/jm",
) -> Path:
    """Render search result items and their covers into one PNG card."""

    title = f"作者「{result.keyword}」作品" if result.kind == "author" else f"搜索「{result.keyword}」"
    return _build_result_card(
        result.items,
        cover_paths,
        output_path,
        title=title,
        page=result.page,
        page_count=max(result.page_count, result.page),
        numbers=range(1, len(result.items) + 1),
        command_prefix=command_prefix,
    )


def build_ranking_result_card(
    result: RankingResult,
    cover_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    title: str,
    command_prefix: str = "/jm",
) -> Path:
    """Render ranked items with global rank numbers and total count."""

    start = (result.page - 1) * result.query.page_size + 1
    return _build_result_card(
        result.items,
        cover_paths,
        output_path,
        title=f"{title} · 共 {result.total} 条",
        page=result.page,
        page_count=max(result.page_count, result.page),
        numbers=range(start, start + len(result.items)),
        command_prefix=command_prefix,
    )


def _build_result_card(
    source_items: Sequence[SearchItem],
    cover_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    title: str,
    page: int,
    page_count: int,
    numbers: Sequence[int] | range,
    command_prefix: str,
) -> Path:
    items = source_items[: len(cover_paths)]
    if not items:
        raise ValueError("at least one result item is required")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    row_height = 210
    width = 960
    padding = 28
    header_height = 72
    height = header_height + row_height * len(items) + padding
    image = Image.new("RGB", (width, height), (248, 248, 246))
    draw = ImageDraw.Draw(image)
    title_font = _font(30)
    body_font = _font(23)
    small_font = _font(19)
    draw.text(
        (padding, 22),
        f"{title}  第 {page} / {page_count} 页",
        fill=(28, 31, 35),
        font=title_font,
    )

    for row_index, (number, item, cover_path) in enumerate(zip(numbers, items, cover_paths)):
        top = header_height + row_index * row_height
        draw.rounded_rectangle(
            (padding, top + 10, width - padding, top + row_height - 10),
            radius=12,
            fill=(255, 255, 255),
            outline=(222, 224, 226),
        )
        image.paste(_fit_cover(cover_path, (124, 176)), (padding + 18, top + 27))

        text_x = padding + 166
        draw.text((text_x, top + 28), f"{number}. JM{item.album_id}", fill=(42, 84, 130), font=body_font)
        title_lines = wrap(item.title or item.album_id, width=28)[:2]
        for line_index, line in enumerate(title_lines):
            draw.text((text_x, top + 66 + line_index * 30), line, fill=(25, 28, 32), font=body_font)
        if item.tags:
            draw.text((text_x, top + 130), " / ".join(item.tags[:6]), fill=(91, 96, 102), font=small_font)
        draw.text(
            (text_x, top + 162),
            f"下载：{command_prefix} {item.album_id}    详情：{command_prefix} 详情 {item.album_id}",
            fill=(64, 66, 70),
            font=small_font,
        )

    image.save(output, format="PNG")
    return output


def _fit_cover(path: str | Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()
