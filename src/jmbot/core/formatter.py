from __future__ import annotations

from collections.abc import Sequence

from jmbot.core.catalog_service import AlbumDetail, SearchResult
from jmbot.core.persistent_queue import AlbumQueueJob
from jmbot.core.user_store import FavoriteItem, FriendRequestRecord


def format_help(command_prefix: str = "/jm") -> str:
    """Return compact help for commands implemented today."""

    prefix = command_prefix
    return "\n".join(
        [
            "JMbot 可用命令：",
            f"{prefix} 123456 - 加入下载队列",
            f"{prefix} 详情 123456 - 查看漫画详情",
            f"{prefix} 搜索 关键词 - 搜索漫画",
            f"{prefix} 作者 作者名 - 搜索作者作品",
            f"{prefix} 收藏 123456 - 收藏漫画",
            f"{prefix} 我的收藏 - 查看收藏",
            f"{prefix} 封面 123456 - 发送封面图片",
            f"{prefix} 预览 123456 - 上传前 10 页预览 PDF",
            f"{prefix} 123456 zip - 下载 ZIP",
            f"{prefix} 123456 章节 1,3-5 - 下载指定章节",
            f"{prefix} 进度 123456 - 查看任务进度",
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
            f"{prefix} 好友申请",
            f"{prefix} 同意好友 1 / 拒绝好友 1",
        ]
    )


def format_album_detail(detail: AlbumDetail, command_prefix: str = "/jm") -> str:
    """Return compact album metadata suitable for QQ."""

    lines = [
        f"JM{detail.album_id}",
        f"标题：{detail.title}",
    ]
    if detail.authors:
        lines.append(f"作者：{' / '.join(detail.authors)}")
    if detail.page_count is not None:
        lines.append(f"页数：{detail.page_count}")
    if detail.tags:
        lines.append(f"标签：{' / '.join(detail.tags[:10])}")
    if detail.chapters:
        chapters = "、".join(
            f"{index}. {title}"
            for index, title in enumerate(detail.chapters[:10], start=1)
        )
        lines.append(f"章节：{chapters}")
    if detail.update_time is not None:
        lines.append(f"更新时间：{detail.update_time}")
    lines.extend(["", f"发送 {command_prefix} {detail.album_id} 加入下载队列"])
    return "\n".join(lines)


def format_query_failed(query_name: str) -> str:
    """Return a safe query failure without exposing upstream details."""

    return f"{query_name}查询暂时不可用，请稍后重试。"


def format_search_result(result: SearchResult, command_prefix: str = "/jm") -> str:
    """Return compact search results without unreliable optional metadata."""

    prefix = command_prefix
    if not result.items:
        query_type = f"作者「{result.keyword}」作品" if result.kind == "author" else f"「{result.keyword}」"
        return f"没有搜到{query_type}，可以换个关键词再试。"

    title = (
        f"作者「{result.keyword}」作品"
        if result.kind == "author"
        else f"搜索「{result.keyword}」"
    )
    page_count = max(result.page_count, result.page)
    total = max(result.total, len(result.items))
    lines = [f"{title}：第 {result.page} / {page_count} 页，共 {total} 条"]
    for index, item in enumerate(result.items, start=1):
        title_suffix = f" {item.title}" if item.title and item.title != item.album_id else ""
        lines.append(f"{index}. JM{item.album_id}{title_suffix}")
        if item.tags:
            lines.append(f"   标签：{' / '.join(item.tags[:6])}")

    first_id = result.items[0].album_id
    lines.append("")
    lines.append(f"详情：{prefix} 详情 {first_id}；下载：{prefix} {first_id}")
    if result.page < result.page_count:
        command = "作者" if result.kind == "author" else "搜索"
        lines.append(f"下一页：{prefix} {command} {result.keyword} {result.page + 1}")
    return "\n".join(lines)


def format_favorite_list(items: Sequence[FavoriteItem], command_prefix: str = "/jm") -> str:
    if not items:
        return f"你还没有收藏漫画。发送 {command_prefix} 收藏 123456 可以收藏。"

    lines = ["我的收藏："]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. JM{item.album_id} 下载：{command_prefix} {item.album_id}")
    return "\n".join(lines)


def format_friend_request_list(records: Sequence[FriendRequestRecord], command_prefix: str = "/jm") -> str:
    if not records:
        return "当前没有待处理好友申请。"

    lines = ["待处理好友申请："]
    for record in records:
        comment = f"：{record.comment}" if record.comment else ""
        lines.append(f"#{record.id} QQ {record.user_id}{comment}")
        lines.append(f"   同意：{command_prefix} 同意好友 {record.id}；拒绝：{command_prefix} 拒绝好友 {record.id}")
    return "\n".join(lines)


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


def format_task_failed(album_id: str, error: str | None = None) -> str:
    """Return a friendly failure message without exposing raw exception text."""

    reason = _format_public_failure_reason(error)
    if reason is not None:
        return f"JM{album_id} 处理失败：{reason}"
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
        error = f"：{_format_admin_error(job.error)}" if job.error else ""
        lines.append(f"#{job.id} JM{job.album_id} {target}{error}")
    return "\n".join(lines)


def _format_admin_error(error: str) -> str:
    if "ENOENT" in error and "/opt/jmbot/data/output/" in error:
        return "NapCat 无法访问输出文件，请检查 /opt/jmbot 挂载并重建 NapCat 容器"
    public_reason = _format_public_failure_reason(error)
    if public_reason is not None:
        return public_reason
    first_line = error.strip().splitlines()[0] if error.strip() else "未知错误"
    return first_line[:160]


def _format_public_failure_reason(error: str | None) -> str | None:
    if not error:
        return None
    if "MissingAlbumPhotoException" in error or "请求的本子不存在" in error:
        return "本子不存在或仅登录用户可见，请检查 ID；如果 ID 没错，可能需要配置 jmcomic cookies。"
    return None
