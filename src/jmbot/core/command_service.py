from __future__ import annotations

import asyncio
import logging
import math
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jmbot.core.catalog_service import AlbumDetail, SearchItem, SearchKind, SearchResult
from jmbot.core.detail_cache import DetailCache
from jmbot.core.formatter import (
    format_album_detail,
    format_admin_failed_deleted,
    format_admin_failed_detail,
    format_admin_failed_tasks,
    format_favorite_list,
    format_my_tasks,
    format_query_failed,
    format_queue_summary,
    format_search_result,
    format_task_history,
)
from jmbot.core.persistent_queue import DestinationType, DuplicateAlbumJobError, PersistentAlbumQueue
from jmbot.core.preview_service import CoverResult, PreviewResult
from jmbot.core.query_executor import query_album_detail, query_album_search, query_cover, query_preview
from jmbot.core.parser import JmMessage
from jmbot.core.user_store import UserDataStore


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


@dataclass(frozen=True)
class SearchReply:
    """Formatted search reply plus cover IDs that should be sent with it."""

    message: str
    cover_album_ids: tuple[str, ...] = ()
    card_items: tuple[SearchItem, ...] = ()
    card_result: SearchResult | None = None


def search_reply_options(command: JmMessage) -> tuple[int | None, int]:
    """Return text limit and cover count for the requested search display."""

    if command.action == "search" and command.search_kind == "author" and command.search_view != "text":
        return 5, 5
    return None, 0


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


def favorite_album(
    store: UserDataStore,
    album_id: str,
    target: QueueTarget,
    *,
    command_prefix: str = "/jm",
) -> str:
    if store.add_favorite(target.user_id, album_id):
        return f"JM{album_id} 已收藏。发送 {command_prefix} 我的收藏 可以查看。"
    return f"JM{album_id} 已经收藏过了。"


def remove_favorite_album(store: UserDataStore, album_id: str, target: QueueTarget) -> str:
    if store.remove_favorite(target.user_id, album_id):
        return f"JM{album_id} 已取消收藏。"
    return f"你的收藏里没有 JM{album_id}。"


def favorite_summary(store: UserDataStore, target: QueueTarget, *, command_prefix: str = "/jm") -> str:
    return format_favorite_list(store.list_favorites(target.user_id), command_prefix)


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


async def query_album_search_reply(
    keyword: str,
    *,
    kind: SearchKind,
    page: int,
    semaphore: asyncio.Semaphore,
    timeout_seconds: int,
    command_prefix: str = "/jm",
    query_func: SearchQuery = query_album_search,
    limit: int | None = None,
    cover_limit: int = 0,
) -> SearchReply:
    """Fetch and format album search results, optionally returning cover IDs."""

    try:
        async with semaphore:
            if kind == "author" and cover_limit > 0:
                result = await asyncio.to_thread(
                    _query_author_search_card_page,
                    query_func,
                    keyword,
                    kind,
                    page,
                    cover_limit,
                    timeout_seconds,
                )
            else:
                result = await asyncio.to_thread(
                    query_func,
                    keyword,
                    kind=kind,
                    page=page,
                    timeout_seconds=timeout_seconds,
                )
    except Exception:
        LOGGER.exception("Album search query failed for %s page %s kind %s", keyword, page, kind)
        return SearchReply(format_query_failed("搜索"))

    effective_limit = limit if limit is not None else (cover_limit or None)
    card_items = result.items[:cover_limit] if cover_limit else ()
    cover_album_ids = tuple(item.album_id for item in card_items)
    card_result = (
        SearchResult(
            keyword=result.keyword,
            kind=result.kind,
            page=result.page,
            page_count=result.page_count,
            total=result.total,
            items=card_items,
        )
        if card_items
        else None
    )
    return SearchReply(
        format_search_result(result, command_prefix, limit=effective_limit),
        cover_album_ids,
        card_items,
        card_result,
    )


def _query_author_search_card_page(
    query_func: SearchQuery,
    keyword: str,
    kind: SearchKind,
    page: int,
    page_size: int,
    timeout_seconds: int,
) -> SearchResult:
    start_index = max(page - 1, 0) * page_size
    end_index = start_index + page_size
    source_page = 1
    collected: list[SearchItem] = []
    first_result: SearchResult | None = None

    while True:
        result = query_func(keyword, kind=kind, page=source_page, timeout_seconds=timeout_seconds)
        if first_result is None:
            first_result = result
        collected.extend(result.items)

        if len(collected) >= end_index or source_page >= max(result.page_count, source_page):
            break
        source_page += 1

    if first_result is None:
        return SearchResult(keyword, kind, page, page, 0, ())

    total = first_result.total
    page_count = max(1, math.ceil(total / page_size)) if total > 0 else max(page, math.ceil(len(collected) / page_size))
    return SearchResult(
        keyword=first_result.keyword,
        kind=first_result.kind,
        page=page,
        page_count=page_count,
        total=total,
        items=tuple(collected[start_index:end_index]),
    )


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
) -> str | None:
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
        return None
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


def task_history(
    queue: PersistentAlbumQueue,
    target: QueueTarget,
    *,
    failed_only: bool = False,
    limit: int | None = None,
) -> str:
    """Return the caller's recent task history in the current context."""

    jobs = queue.list_history_for_user(
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
        failed_only=failed_only,
        limit=limit,
    )
    return format_task_history(jobs, failed_only=failed_only)


def admin_failed_tasks(queue: PersistentAlbumQueue, *, limit: int | None = None) -> str:
    """Return recent failed jobs across all contexts for admins."""

    return format_admin_failed_tasks(queue.list_failed_jobs(limit=limit))


def admin_failed_task_detail(queue: PersistentAlbumQueue, job_id: int) -> str:
    """Return raw stored failure detail for an admin-selected failed job."""

    job = queue.get_job(job_id)
    return format_admin_failed_detail(job)


def admin_delete_failed_task(queue: PersistentAlbumQueue, job_id: int) -> str:
    """Delete one stored failed job by queue task id."""

    job = queue.get_job(job_id)
    if job is None or job.status != "failed":
        return format_admin_failed_deleted(job_id, False)
    return format_admin_failed_deleted(job_id, queue.delete_job(job_id))


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
    album_id: str | None,
    target: QueueTarget,
    *,
    mode: str,
) -> str:
    if album_id is None:
        all_active_message = _progress_summary_for_all_active(queue, target, mode=mode)
        if all_active_message is not None:
            return all_active_message
        latest = queue.find_latest_active_for_user(
            destination_type=target.destination_type,
            group_id=target.group_id,
            user_id=target.user_id,
        )
        if latest is None:
            return "没有找到你在这里提交的活动任务。"
        album_id = latest.album_id

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


def _progress_summary_for_all_active(queue: PersistentAlbumQueue, target: QueueTarget, *, mode: str) -> str | None:
    jobs = queue.list_active_for_user(
        destination_type=target.destination_type,
        group_id=target.group_id,
        user_id=target.user_id,
    )
    if not jobs:
        return None
    latest = max(jobs, key=lambda job: job.id)
    ordered_jobs = [latest, *(job for job in jobs if job.id != latest.id)]

    lines = [f"JM{latest.album_id} 当前进度："]
    for index, job in enumerate(ordered_jobs, start=1):
        stage_labels = {
            "queued": "排队中",
            "starting": "准备中",
            "downloading": "下载图片",
            "building": f"生成 {job.output_format.upper()}",
            "uploading": "上传中",
        }
        chapter_label = f" 章节{','.join(map(str, job.chapters))}" if job.chapters else " 全部章节"
        lines.append(f"{index}. JM{job.album_id} {job.output_format.upper()}{chapter_label}")
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


def prune_history(queue: PersistentAlbumQueue, *, retention_days: int, max_per_user: int) -> int:
    """Prune old finished queue records according to configured retention."""

    return queue.prune_history(retention_days=retention_days, max_per_user=max_per_user)


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
