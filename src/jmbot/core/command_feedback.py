from __future__ import annotations

from jmbot.core.parser import JmMessage


def processing_ack_message(command: JmMessage) -> str | None:
    """Return an immediate acknowledgment for commands that may take noticeable time."""

    if command.action == "detail" and command.album_id is not None:
        return f"收到，正在获取 JM{command.album_id} 详情。"
    if command.action == "cover" and command.album_id is not None:
        return f"收到，正在获取 JM{command.album_id} 封面。"
    if command.action == "preview" and command.album_id is not None:
        return f"收到，正在生成 JM{command.album_id} 预览。"
    if command.action == "search" and command.keyword:
        if command.search_kind == "author":
            return f"收到，正在处理作者搜索：{command.keyword}。"
        return f"收到，正在搜索：{command.keyword}。"
    if command.action == "ranking":
        return "收到，正在查询排行榜。"
    return None
