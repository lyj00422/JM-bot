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
    "search",
    "download_from_search",
    "my_tasks",
    "history",
    "retry",
    "admin_clear",
    "admin_kill",
    "admin_jump",
    "admin_failed",
]


@dataclass(frozen=True)
class JmMessage:
    """Parsed JM request or management command."""

    action: JmAction
    album_id: str | None = None
    all_targets: bool = False
    failed_only: bool = False
    keyword: str | None = None
    page: int | None = None
    index: int | None = None


def parse_jm_command(message: str, command_prefix: str = "/jm") -> JmCommand | None:
    """Parse `/jm <digits>` and return None for non-matching messages."""

    pattern = re.compile(rf"^\s*{re.escape(command_prefix)}\s+([0-9]+)\s*$", re.IGNORECASE)
    match = pattern.match(message)
    if match is None:
        return None

    return JmCommand(album_id=match.group(1))


def parse_jm_message(message: str, command_prefix: str = "/jm") -> JmMessage | None:
    """Parse JM request, user queue commands, and Chinese admin subcommands."""

    request = parse_jm_command(message, command_prefix)
    if request is not None:
        return JmMessage(action="request", album_id=request.album_id)

    prefix = re.escape(command_prefix)
    patterns: list[tuple[str, JmAction, bool]] = [
        (rf"^\s*{prefix}\s+撤回\s+([0-9]+)\s*$", "cancel", False),
        (rf"^\s*{prefix}\s+管理\s+kill\s+全部\s+([0-9]+)\s*$", "admin_kill", True),
        (rf"^\s*{prefix}\s+管理\s+kill\s+([0-9]+)\s*$", "admin_kill", False),
        (rf"^\s*{prefix}\s+管理\s+插队\s+([0-9]+)\s*$", "admin_jump", False),
    ]
    for pattern, action, all_targets in patterns:
        match = re.match(pattern, message, re.IGNORECASE)
        if match is not None:
            return JmMessage(action=action, album_id=match.group(1), all_targets=all_targets)

    detail_match = re.match(rf"^\s*{prefix}\s+(?:看|详情|info)\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if detail_match is not None:
        return JmMessage(action="detail", album_id=detail_match.group(1))

    search_match = re.match(rf"^\s*{prefix}\s+(?:搜索|search)\s+(.+?)\s*$", message, re.IGNORECASE)
    if search_match is not None:
        search_text = search_match.group(1).strip()
        page_match = re.match(r"^(.+?)\s+([0-9]+)$", search_text)
        if page_match is None:
            return JmMessage(action="search", keyword=search_text, page=1)

        page = int(page_match.group(2))
        if page < 1:
            return None
        return JmMessage(action="search", keyword=page_match.group(1).strip(), page=page)

    download_match = re.match(rf"^\s*{prefix}\s+下载\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if download_match is not None:
        index = int(download_match.group(1))
        return None if index < 1 else JmMessage(action="download_from_search", index=index)

    if re.match(rf"^\s*{prefix}\s+队列\s*$", message, re.IGNORECASE):
        return JmMessage(action="queue")

    if re.match(rf"^\s*{prefix}\s+(帮助|help)\s*$", message, re.IGNORECASE):
        return JmMessage(action="help")

    if re.match(rf"^\s*{prefix}\s+(我的|my)\s*$", message, re.IGNORECASE):
        return JmMessage(action="my_tasks")

    if re.match(rf"^\s*{prefix}\s+(历史|history)\s*$", message, re.IGNORECASE):
        return JmMessage(action="history")

    if re.match(rf"^\s*{prefix}\s+历史\s+失败\s*$", message, re.IGNORECASE):
        return JmMessage(action="history", failed_only=True)

    retry_match = re.match(rf"^\s*{prefix}\s+重试\s+([0-9]+)\s*$", message, re.IGNORECASE)
    if retry_match is not None:
        return JmMessage(action="retry", album_id=retry_match.group(1))

    if re.match(rf"^\s*{prefix}\s+管理\s+清空\s*$", message, re.IGNORECASE):
        return JmMessage(action="admin_clear")

    if re.match(rf"^\s*{prefix}\s+管理\s+失败\s*$", message, re.IGNORECASE):
        return JmMessage(action="admin_failed")

    return None
