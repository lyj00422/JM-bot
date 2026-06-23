from __future__ import annotations

from collections.abc import Sequence

from jmbot.core.persistent_queue import AlbumQueueJob


def format_help(command_prefix: str = "/jm") -> str:
    """Return compact help for commands implemented today."""

    prefix = command_prefix
    return "\n".join(
        [
            "JMbot 可用命令：",
            f"{prefix} 123456 - 加入下载队列",
            f"{prefix} 撤回 123456 - 撤回自己的排队任务",
            f"{prefix} 队列 - 查看当前队列",
            f"{prefix} 帮助 - 查看本帮助",
            "",
            "管理员：",
            f"{prefix} 管理 清空",
            f"{prefix} 管理 kill 123456",
            f"{prefix} 管理 kill 全部 123456",
            f"{prefix} 管理 插队 123456",
        ]
    )


def format_queue_summary(active_jobs: Sequence[AlbumQueueJob], *, limit: int = 10) -> str:
    """Return a compact summary for active queue jobs."""

    if not active_jobs:
        return "队列为空。"

    lines = [f"当前队列共 {len(active_jobs)} 个任务："]
    for job in active_jobs[:limit]:
        target = f"群 {job.group_id}" if job.destination_type == "group" else f"私聊 {job.user_id}"
        lines.append(f"#{job.id} JM{job.album_id} {job.status} {target}")
    if len(active_jobs) > limit:
        lines.append(f"还有 {len(active_jobs) - limit} 个未显示。")
    return "\n".join(lines)


def format_task_started(album_id: str) -> str:
    """Return the message sent when a job starts."""

    return f"开始处理 JM{album_id}。"


def format_task_uploaded(album_id: str, filename: str) -> str:
    """Return the message sent after a successful upload."""

    return f"JM{album_id} 已上传：{filename}"


def format_task_canceled(album_id: str) -> str:
    """Return the message sent when an already-canceled job stops before upload."""

    return f"JM{album_id} 已取消，不再上传。"


def format_task_failed(album_id: str) -> str:
    """Return a friendly failure message without exposing raw exception text."""

    return f"JM{album_id} 处理失败，请稍后重试或联系管理员查看原因。"
