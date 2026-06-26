from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jmbot.core.catalog_service import AlbumDetail, SearchKind, SearchResult
from jmbot.core.detail_cache import DetailCache
from jmbot.core.formatter import (
    format_album_detail,
    format_admin_failed_tasks,
    format_my_tasks,
    format_query_failed,
    format_queue_summary,
    format_retry_duplicate,
    format_retry_enqueued,
    format_retry_missing,
    format_search_result,
    format_task_history,
)
from jmbot.core.persistent_queue import DestinationType, DuplicateAlbumJobError, PersistentAlbumQueue
from jmbot.core.preview_service import CoverResult, PreviewResult
from jmbot.core.query_executor import query_album_detail, query_album_search, query_cover, query_preview


LOGGER = logging.getLogger(__name__)
DetailQuery = Callable[..., AlbumDetail]
SearchQuery = Callable[..., SearchResult]
CoverQuery = Callable[..., CoverResult]
PreviewQuery = Callable[..., PreviewResult]
MediaDeliverer = Callable[[Path], Awaitable[None]]


@dataclass(frozen=True)
class QueueTarget:
    """Queue delivery target extracted from a QQ message."""

    destination_type: DestinationType
    group_id: int | None
    user_id: int


def enqueue_album_request(
    queue: PersistentAlbumQueue,
    album_id: str,
    target: QueueTarget,
    *,
    output_format: str = "pdf",
    chapters: tuple[int, ...] = (),
) -> str:
    """Enqueue a normal album request and return a user-facing reply."""

    try:
        job = _enqueue(queue, album_id, target, output_format=output_format, chapters=chapters)
    except DuplicateAlbumJobError as exc:
        ahead_count = queue.count_ahead(exc.existing_job.id)
        return f"JM{album_id} 已经在队列中，前面还有{ahead_count}篇漫画正在处理。"

    ahead_count = queue.count_ahead(job.id)
    format_label = output_format.upper()
    chapter_label = f"，章节 {','.join(map(str, chapters))}" if chapters else ""
    return f"收到 JM{album_id}（{format_label}{chapter_label}），已加入队列，前面还有{ahead_count}篇漫画正在处理。"


async def query_album_detail_message(
    album_id: str,
    cache: DetailCache,
    semaphore: asyncio.Semaphore,
    *,
    timeout_seconds: int,
    command_prefix: str = "/jm",
    query_func: DetailQuery = query_album_detail,
) -> str:
    """Fetch and format album details without blocking the event loop."""

    cached = cache.get(album_id)
    if cached is not None:
        return format_album_detail(cached, command_prefix)

    try:
        async with semaphore:
            detail = await asyncio.to_thread(query_func, album_id, timeout_seconds=timeout_seconds)
    except Exception:
        LOGGER.exception("Album detail query failed for JM%s", album_id)
        return format_query_failed("详情")

    cache.put(detail)
    return format_album_detail(detail, command_prefix)


async def query_album_search_message(
    keyword: str,
    *,
    kind: SearchKind,
    page: int,
    semaphore: asyncio.Semaphore,
    timeout_seconds: int,
    command_prefix: str = "/jm",
    query_func: SearchQuery = query_album_search,
) -> str:
    """Fetch and format album search results without blocking the event loop."""

    try:
        async with semaphore:
            result = await asyncio.to_thread(
                query_func,
                keyword,
                kind=kind,
                page=page,
                timeout_seconds=timeout_seconds,
            )
    except Exception:
        LOGGER.exception("Album search query failed for %s page %s kind %s", keyword, page, kind)
        return format_query_failed("搜索")

    return format_search_result(result, command_prefix)


async def deliver_cover(
    album_id: str,
    preview_root: Path,
    semaphore: asyncio.Semaphore,
    *,
    deliver: MediaDeliverer,
    timeout_seconds: int,
    query_func: CoverQuery = query_cover,
) -> str | None:
    """Fetch, send, and clean one cover image."""

    try:
        async with semaphore:
            result = await asyncio.to_thread(
                query_func,
                album_id,
                preview_root,
                timeout_seconds=timeout_seconds,
            )
            await deliver(result.cover_path)
            await asyncio.to_thread(shutil.rmtree, result.media_dir, True)
        return None
    except Exception:
        LOGGER.exception("Cover delivery failed for JM%s", album_id)
        return f"JM{album_id} 封面获取失败，请稍后重试。"


async def deliver_preview(
    album_id: str,
    preview_root: Path,
    semaphore: asyncio.Semaphore,
    *,
    deliver: MediaDeliverer,
    page_count: int,
    timeout_seconds: int,
    query_func: PreviewQuery = query_preview,
) -> str:
    """Build, upload, and clean one preview PDF."""

    try:
        async with semaphore:
            result = await asyncio.to_thread(
                query_func,
                album_id,
                preview_root,
                page_count=page_count,
                timeout_seconds=timeout_seconds,
            )
            await deliver(result.pdf_path)
            await asyncio.to_thread(shutil.rmtree, result.media_dir, True)
        return f"JM{album_id} 预览已上传。"
    except Exception:
        LOGGER.exception("Preview delivery failed for JM%s", album_id)
        return f"JM{album_id} 预览生成失败，请稍后重试。"


def cancel_album_request(queue: PersistentAlbumQueue, album_id: str, target: QueueTarget) -> str:
    """Cancel a normal user's own pending request."""

    result = queue.cancel_pending_for_user(
        album_id,
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
    )
    if result.status == "canceled":
        return f"JM{album_id} 已撤回。"
    if result.status == "processing":
        return f"JM{album_id} 已经开始处理，无法撤回，请联系管理员 kill。"
    return f"没有找到你在这里提交的 JM{album_id} 排队任务。"


def queue_summary(queue: PersistentAlbumQueue, *, limit: int = 10) -> str:
    """Return a compact active queue summary."""

    active_jobs = [job for job in queue.list_jobs() if job.status in {"pending", "processing"}]
    return format_queue_summary(active_jobs, limit=limit)


def my_tasks_summary(queue: PersistentAlbumQueue, target: QueueTarget) -> str:
    """Return the caller's active jobs in the current context."""

    jobs = queue.list_active_for_user(
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
    )
    return format_my_tasks([(job, queue.count_ahead(job.id)) for job in jobs])


def task_history(queue: PersistentAlbumQueue, target: QueueTarget, *, failed_only: bool = False) -> str:
    """Return the caller's recent task history in the current context."""

    jobs = queue.list_history_for_user(
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
        failed_only=failed_only,
    )
    return format_task_history(jobs, failed_only=failed_only)


def retry_failed_album(queue: PersistentAlbumQueue, album_id: str, target: QueueTarget) -> str:
    """Retry the caller's latest failed job for this album in the current context."""

    failed = queue.find_latest_failed_for_user(
        album_id,
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
    )
    if failed is None:
        return format_retry_missing(album_id)

    try:
        job = _enqueue(
            queue,
            album_id,
            target,
            output_format=failed.output_format,
            chapters=failed.chapters,
        )
    except DuplicateAlbumJobError as exc:
        job = exc.existing_job
        return format_retry_duplicate(album_id, queue.count_ahead(job.id))

    return format_retry_enqueued(album_id, queue.count_ahead(job.id))


def admin_failed_tasks(queue: PersistentAlbumQueue, *, limit: int = 10) -> str:
    """Return recent failed jobs across all contexts for admins."""

    return format_admin_failed_tasks(queue.list_failed_jobs(limit=limit))


def admin_clear_queue(queue: PersistentAlbumQueue) -> str:
    """Cancel all pending jobs."""

    count = queue.clear_pending()
    return f"已清空 {count} 个待排队任务。"


def admin_kill_album(
    queue: PersistentAlbumQueue,
    album_id: str,
    target: QueueTarget,
    *,
    all_targets: bool,
) -> str:
    """Cancel active jobs for one target or all targets."""

    if all_targets:
        canceled = queue.admin_cancel_album(album_id)
    elif target.destination_type == "group":
        canceled = queue.admin_cancel_album(album_id, destination_type="group", group_id=target.group_id)
    else:
        canceled = queue.admin_cancel_album(album_id, destination_type="private", user_id=target.user_id)

    if not canceled:
        return f"没有找到可取消的 JM{album_id} 任务。"
    return f"已取消 {len(canceled)} 个 JM{album_id} 任务。"


def admin_jump_album(queue: PersistentAlbumQueue, album_id: str, target: QueueTarget) -> str:
    """Move an active target job to the front, or create one and then move it."""

    job = queue.find_active_job(
        album_id,
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
    )
    if job is None:
        job = _enqueue(queue, album_id, target)

    moved = queue.move_to_front(job.id)
    return f"JM{album_id} 已插队，任务 #{moved.id} 会优先处理。"


def progress_summary(
    queue: PersistentAlbumQueue,
    album_id: str,
    target: QueueTarget,
    *,
    mode: str,
) -> str:
    jobs = [
        job
        for job in queue.list_jobs()
        if job.album_id == album_id
        and job.status in {"pending", "processing"}
        and job.destination_type == target.destination_type
        and (
            (target.destination_type == "group" and job.group_id == target.group_id)
            or (target.destination_type == "private" and job.user_id == target.user_id)
        )
    ]
    if not jobs:
        return f"没有找到当前上下文中的 JM{album_id} 活动任务。"

    lines = [f"JM{album_id} 当前进度："]
    for index, job in enumerate(jobs, start=1):
        stage_labels = {
            "queued": "排队中",
            "starting": "准备中",
            "downloading": "下载图片",
            "building": f"生成 {job.output_format.upper()}",
            "uploading": "上传中",
        }
        chapter_label = f" 章节{','.join(map(str, job.chapters))}" if job.chapters else " 全部章节"
        lines.append(f"{index}. {job.output_format.upper()}{chapter_label}")
        lines.append(f"   阶段：{stage_labels.get(job.stage, job.stage)}")
        if mode == "precise" and job.progress_total > 0:
            lines.append(f"   已完成：{job.progress_current} / {job.progress_total} 页")
        elif mode == "precise" and job.progress_current > 0:
            lines.append(f"   已下载：{job.progress_current} 页")
        if job.started_at:
            started = datetime.fromisoformat(job.started_at.replace("Z", "+00:00"))
            seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
            minutes, seconds = divmod(seconds, 60)
            lines.append(f"   已运行：{minutes} 分 {seconds} 秒")
    return "\n".join(lines)


def _enqueue(
    queue: PersistentAlbumQueue,
    album_id: str,
    target: QueueTarget,
    *,
    output_format: str = "pdf",
    chapters: tuple[int, ...] = (),
):
    if target.destination_type == "group":
        if target.group_id is None:
            raise ValueError("group_id is required for group target")
        return queue.enqueue_group(
            album_id,
            group_id=target.group_id,
            user_id=target.user_id,
            output_format=output_format,
            chapters=chapters,
        )

    return queue.enqueue_private(
        album_id,
        user_id=target.user_id,
        output_format=output_format,
        chapters=chapters,
    )
