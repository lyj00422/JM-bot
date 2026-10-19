from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class JmCommand:
    """Parsed JM command."""

    album_id: str


JmAction = Literal[
    "request",
    "cancel",
    "queue",
    "help",
    "detail",
    "cover",
    "preview",
    "progress",
    "search",
    "ranking",
    "ranking_categories",
    "ranking_invalid",
    "favorite_add",
    "favorite_remove",
    "favorite_list",
    "my_tasks",
    "history",
    "admin_help",
    "admin_clear",
    "admin_kill",
    "admin_jump",
    "admin_failed",
    "admin_failed_delete",
    "chat_private_key",
    "chat_private_status",
    "chat_private_clear",
    "chat_clear",
    "chat_group_key_set",
    "chat_group_key_status",
    "chat_group_key_clear",
    "chat_status",
]


@dataclass(frozen=True)
class JmMessage:
    """Parsed JM request or management command."""

    action: JmAction
    album_id: str | None = None
    all_targets: bool = False
    failed_only: bool = False
    output_format: str = "pdf"
    chapters: tuple[int, ...] = ()
    search_kind: str = "site"
    search_view: str = "default"
    keyword: str | None = None
    page: int = 1
    request_id: int | None = None
    limit: int | None = None
    ranking_period: str = "all"
    ranking_category: str = "all"
    help_topic: str | None = None
    text: str | None = None
    group_id: int | None = None


_RANKING_PERIOD_ALIASES = {
    "日": "day",
    "日榜": "day",
    "周": "week",
    "周榜": "week",
    "月": "month",
    "月榜": "month",
    "总": "all",
    "总榜": "all",
}

_RANKING_CATEGORY_ALIASES = {
    "全部": "all",
    "同人": "doujin",
    "单本": "single",
    "短篇": "short",
    "其他": "another",
    "韩漫": "hanman",
    "美漫": "meiman",
    "cosplay": "doujin_cosplay",
    "3d": "3d",
    "英文站": "english_site",
}


def parse_jm_command(message: str, command_prefix: str = "/jm") -> JmCommand | None:
    """Parse `/jm <digits>` and return None for non-matching messages."""

    pattern = re.compile(rf"^\s*{re.escape(command_prefix)}\s+([0-9]+)\s*$", re.IGNORECASE)
    match = pattern.match(message)
    if match is None:
        return None

    return JmCommand(album_id=match.group(1))


def parse_jm_message(
    message: str,
    command_prefix: str = "/jm",
    *,
    default_format: str = "pdf",
) -> JmMessage | None:
    """Parse JM request, user queue commands, and Chinese admin subcommands."""

    normalized_default = default_format.lower()
    if normalized_default not in {"pdf", "zip"}:
        raise ValueError(f"Unsupported default format: {default_format}")

    prefix = re.escape(command_prefix)
    download_match = re.match(
        rf"^\s*{prefix}\s+([0-9]+)(?:\s+(pdf|zip))?\s*$",
        message,
        re.IGNORECASE,
    )
    if download_match is not None:
        return JmMessage(
            action="request",
            album_id=download_match.group(1),
            output_format=(download_match.group(2) or normalized_default).lower(),
        )

    chapter_match = re.match(
        rf"^\s*{prefix}\s+([0-9]+)\s+章节\s+([0-9,\-\s]+?)(?:\s+(pdf|zip))?\s*$",
        message,
        re.IGNORECASE,
    )
    if chapter_match is not None:
        chapters = _parse_chapters(chapter_match.group(2))
        if not chapters:
            return None
        return JmMessage(
            action="request",
            album_id=chapter_match.group(1),
            output_format=(chapter_match.group(3) or normalized_default).lower(),
            chapters=chapters,
        )

    patterns: list[tuple[str, JmAction, bool]] = [
        (rf"^\s*{prefix}\s+撤回\s+([0-9]+)\s*$", "cancel", False),
    ]
    for pattern, action, all_targets in patterns:
        match = re.match(pattern, message, re.IGNORECASE)
        if match is not None:
            return JmMessage(action=action, album_id=match.group(1), all_targets=all_targets)

    detail_match = re.match(rf"^\s*{prefix}\s+(?:看|详情|info)\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if detail_match is not None:
        return JmMessage(action="detail", album_id=detail_match.group(1))

    cover_match = re.match(rf"^\s*{prefix}\s+(?:封面|cover)\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if cover_match is not None:
        return JmMessage(action="cover", album_id=cover_match.group(1))

    preview_match = re.match(rf"^\s*{prefix}\s+(?:预览|preview)\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if preview_match is not None:
        return JmMessage(action="preview", album_id=preview_match.group(1))

    progress_match = re.match(rf"^\s*{prefix}\s+进度\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if progress_match is not None:
        return JmMessage(action="progress", album_id=progress_match.group(1))

    if re.match(rf"^\s*{prefix}\s+进度\s*$", message, re.IGNORECASE):
        return JmMessage(action="progress")

    ranking_match = re.match(rf"^\s*{prefix}\s+排行(?:\s+(.*?))?\s*$", message, re.IGNORECASE)
    if ranking_match is not None:
        return _parse_ranking(ranking_match.group(1) or "")

    search_match = re.match(rf"^\s*{prefix}\s+(搜索|search|作者)\s+(.+?)\s*$", message, re.IGNORECASE)
    if search_match is not None:
        search_kind = "author" if search_match.group(1).lower() == "作者" else "site"
        search_view = "default"
        search_text = search_match.group(2)
        if search_kind == "author":
            author_text_match = re.match(r"^\s*文本\s+(.+?)\s*$", search_text, re.IGNORECASE)
            if author_text_match is not None:
                search_view = "text"
                search_text = author_text_match.group(1)
        keyword, page = _parse_search_keyword_and_page(search_text)
        if keyword is None:
            return None
        return JmMessage(action="search", search_kind=search_kind, search_view=search_view, keyword=keyword, page=page)

    favorite_add_match = re.match(rf"^\s*{prefix}\s+收藏\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if favorite_add_match is not None:
        return JmMessage(action="favorite_add", album_id=favorite_add_match.group(1))

    favorite_remove_match = re.match(rf"^\s*{prefix}\s+取消收藏\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if favorite_remove_match is not None:
        return JmMessage(action="favorite_remove", album_id=favorite_remove_match.group(1))

    if re.match(rf"^\s*{prefix}\s+(收藏|我的收藏)\s*$", message, re.IGNORECASE):
        return JmMessage(action="favorite_list")

    if re.match(rf"^\s*{prefix}\s+队列\s*$", message, re.IGNORECASE):
        return JmMessage(action="queue")

    help_match = re.match(rf"^\s*{prefix}\s+(?:帮助|help)(?:\s+(.+?))?\s*$", message, re.IGNORECASE)
    if help_match is not None:
        topic = help_match.group(1)
        return JmMessage(action="help", help_topic=topic.strip() if topic else None)

    api_key_match = re.match(r"^\s*/ds(?:key)?(?:\s+(.+?))?\s*$", message, re.IGNORECASE)
    if api_key_match is not None:
        value = (api_key_match.group(1) or "").strip()
        if value.casefold() in {"状态", "status"}:
            return JmMessage(action="chat_private_status")
        if value.casefold() in {"清除", "删除", "clear", "remove"}:
            return JmMessage(action="chat_private_clear")
        if value:
            return JmMessage(action="chat_private_key", text=value)
        return JmMessage(action="chat_private_key")

    if re.match(r"^\s*/聊天\s+(?:清空|重置|clear)\s*$", message, re.IGNORECASE):
        return JmMessage(action="chat_clear")

    if re.match(rf"^\s*{prefix}\s+(我的|my)\s*$", message, re.IGNORECASE):
        return JmMessage(action="my_tasks")

    failed_history_match = re.match(rf"^\s*{prefix}\s+历史\s+失败(?:\s+([0-9]+))?\s*$", message, re.IGNORECASE)
    if failed_history_match is not None:
        limit = _parse_optional_limit(failed_history_match.group(1))
        if limit == 0:
            return None
        return JmMessage(action="history", failed_only=True, limit=limit)

    history_match = re.match(rf"^\s*{prefix}\s+(?:历史|history)(?:\s+([0-9]+))?\s*$", message, re.IGNORECASE)
    if history_match is not None:
        limit = _parse_optional_limit(history_match.group(1))
        if limit == 0:
            return None
        return JmMessage(action="history", limit=limit)

    return None


def parse_jm_admin_message(message: str, admin_command_prefix: str = "/jmg") -> JmMessage | None:
    """Parse admin-only JM commands that use the dedicated `/jmg` prefix."""

    prefix = re.escape(admin_command_prefix)
    if re.match(rf"^\s*{prefix}\s+(?:帮助|help)\s*$", message, re.IGNORECASE):
        return JmMessage(action="admin_help")

    patterns: list[tuple[str, JmAction, bool]] = [
        (rf"^\s*{prefix}\s+kill\s+全部\s+([0-9]+)\s*$", "admin_kill", True),
        (rf"^\s*{prefix}\s+kill\s+([0-9]+)\s*$", "admin_kill", False),
        (rf"^\s*{prefix}\s+插队\s+([0-9]+)\s*$", "admin_jump", False),
    ]
    for pattern, action, all_targets in patterns:
        match = re.match(pattern, message, re.IGNORECASE)
        if match is not None:
            return JmMessage(action=action, album_id=match.group(1), all_targets=all_targets)

    if re.match(rf"^\s*{prefix}\s+清空\s*$", message, re.IGNORECASE):
        return JmMessage(action="admin_clear")

    failed_delete_match = re.match(rf"^\s*{prefix}\s+失败\s+删除\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if failed_delete_match is not None:
        return JmMessage(action="admin_failed_delete", request_id=int(failed_delete_match.group(1)))

    failed_recent_match = re.match(rf"^\s*{prefix}\s+失败\s+最近\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if failed_recent_match is not None:
        limit = _parse_optional_limit(failed_recent_match.group(1))
        if limit == 0:
            return None
        return JmMessage(action="admin_failed", limit=limit)

    failed_detail_match = re.match(rf"^\s*{prefix}\s+失败\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if failed_detail_match is not None:
        return JmMessage(action="admin_failed", request_id=int(failed_detail_match.group(1)))

    if re.match(rf"^\s*{prefix}\s+失败\s*$", message, re.IGNORECASE):
        return JmMessage(action="admin_failed")

    group_key_match = re.match(
        rf"^\s*{prefix}\s+群聊key\s+([0-9]+)\s+(.+?)\s*$",
        message,
        re.IGNORECASE,
    )
    if group_key_match is not None:
        group_id = int(group_key_match.group(1))
        value = group_key_match.group(2).strip()
        if value.casefold() in {"查看", "状态", "status"}:
            return JmMessage(action="chat_group_key_status", group_id=group_id)
        if value.casefold() in {"清除", "删除", "clear", "remove"}:
            return JmMessage(action="chat_group_key_clear", group_id=group_id)
        return JmMessage(action="chat_group_key_set", group_id=group_id, text=value)

    if re.match(rf"^\s*{prefix}\s+聊天状态\s*$", message, re.IGNORECASE):
        return JmMessage(action="chat_status")

    return None


def _parse_chapters(value: str) -> tuple[int, ...]:
    chapters: set[int] = set()
    for raw_part in value.replace(" ", "").split(","):
        if not raw_part:
            return ()
        if "-" in raw_part:
            bounds = raw_part.split("-", 1)
            if len(bounds) != 2 or not all(bound.isdigit() for bound in bounds):
                return ()
            start, end = map(int, bounds)
            if start < 1 or end < start:
                return ()
            chapters.update(range(start, end + 1))
        elif raw_part.isdigit():
            chapter = int(raw_part)
            if chapter < 1:
                return ()
            chapters.add(chapter)
        else:
            return ()
    return tuple(sorted(chapters))


def _parse_ranking(value: str) -> JmMessage:
    tokens = value.split()
    view = "default"
    if tokens and tokens[0].casefold() == "文本":
        view = "text"
        tokens.pop(0)

    if tokens == ["类别"]:
        return JmMessage(action="ranking_categories")

    page = 1
    if tokens and tokens[-1].isdigit():
        page = int(tokens.pop())
        if page < 1:
            return JmMessage(action="ranking_invalid")

    period = "all"
    category = "all"
    period_seen = False
    category_seen = False
    keyword_parts: list[str] = []
    for token in tokens:
        period_value = _RANKING_PERIOD_ALIASES.get(token.casefold())
        category_value = _RANKING_CATEGORY_ALIASES.get(token.casefold())
        if period_value is not None:
            if period_seen:
                return JmMessage(action="ranking_invalid")
            period = period_value
            period_seen = True
        elif category_value is not None:
            if category_seen:
                return JmMessage(action="ranking_invalid")
            category = category_value
            category_seen = True
        else:
            keyword_parts.append(token)

    return JmMessage(
        action="ranking",
        search_view=view,
        keyword=" ".join(keyword_parts) or None,
        page=page,
        ranking_period=period,
        ranking_category=category,
    )


def _parse_search_keyword_and_page(value: str) -> tuple[str | None, int]:
    text = value.strip()
    if not text:
        return None, 1

    page = 1
    page_match = re.match(r"^(.+?)\s+([0-9]+)\s*$", text)
    if page_match is not None:
        parsed_page = int(page_match.group(2))
        if parsed_page < 1:
            return None, 1
        text = page_match.group(1).strip()
        page = parsed_page

    return (text, page) if text else (None, 1)


def _parse_optional_limit(value: str | None) -> int | None:
    if value is None:
        return None
    limit = int(value)
    return limit if limit > 0 else 0
