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
            f"{prefix} 我的 - 查看自己的当前任务",
            f"{prefix} 历史 - 查看自己的最近任务",
            f"{prefix} 历史 失败 - 查看自己的失败任务",
            f"{prefix} 重试 123456 - 重试自己的失败任务",
            f"{prefix} 帮助 - 查看本帮助",
            "",
            "管理员：",
            f"{prefix} 管理 清空",
            f"{prefix} 管理 kill 123456",
            f"{prefix} 管理 kill 全部 123456",
            f"{prefix} 管理 插队 123456",
            f"{prefix} 管理 失败",
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


def format_my_tasks(job_positions: Sequence[tuple[AlbumQueueJob, int]]) -> str:
    """Return active jobs for the caller's current context."""

    if not job_positions:
        return "你当前没有排队或正在处理的任务。"

    lines = ["你当前的任务："]
    for index, (job, ahead_count) in enumerate(job_positions, start=1):
        if job.status == "processing":
            detail = "正在处理"
        else:
            detail = f"前面还有 {ahead_count} 个任务"
        lines.append(f"{index}. JM{job.album_id} {job.status}，{detail}")
    return "\n".join(lines)


def format_task_history(jobs: Sequence[AlbumQueueJob], *, failed_only: bool = False) -> str:
    """Return recent task history for the caller's current context."""

    if not jobs:
        return "你最近没有失败任务。" if failed_only else "你最近没有已完成、失败或取消的任务。"

    title = "你最近失败的任务：" if failed_only else "你最近的任务："
    lines = [title]
    for index, job in enumerate(jobs, start=1):
        error = "：失败原因已记录，请稍后重试或联系管理员" if job.status == "failed" else ""
        lines.append(f"{index}. JM{job.album_id} {job.status}{error}")
    return "\n".join(lines)


def format_retry_enqueued(album_id: str, ahead_count: int) -> str:
    """Return retry enqueue confirmation."""

    return f"JM{album_id} 已重新加入队列，前面还有{ahead_count}篇漫画正在处理。"


def format_retry_missing(album_id: str) -> str:
    """Return message when no retryable failure belongs to the caller."""

    return f"没有找到你在这里失败过的 JM{album_id} 任务。"


def format_retry_duplicate(album_id: str, ahead_count: int) -> str:
    """Return message when the retry target is already active."""

    return f"JM{album_id} 已经在队列中，前面还有{ahead_count}篇漫画正在处理。"


def format_admin_failed_tasks(jobs: Sequence[AlbumQueueJob]) -> str:
    """Return recent failed jobs for admins."""

    if not jobs:
        return "最近没有失败任务。"

    lines = ["最近失败任务："]
    for job in jobs:
        target = f"群 {job.group_id}" if job.destination_type == "group" else f"私聊 {job.user_id}"
        error = f"：{job.error}" if job.error else ""
        lines.append(f"#{job.id} JM{job.album_id} {target}{error}")
    return "\n".join(lines)
