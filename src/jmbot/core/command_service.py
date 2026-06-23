from __future__ import annotations

from dataclasses import dataclass

from jmbot.core.formatter import format_queue_summary
from jmbot.core.persistent_queue import DestinationType, DuplicateAlbumJobError, PersistentAlbumQueue


@dataclass(frozen=True)
class QueueTarget:
    """Queue delivery target extracted from a QQ message."""

    destination_type: DestinationType
    group_id: int | None
    user_id: int


def enqueue_album_request(queue: PersistentAlbumQueue, album_id: str, target: QueueTarget) -> str:
    """Enqueue a normal album request and return a user-facing reply."""

    try:
        job = _enqueue(queue, album_id, target)
    except DuplicateAlbumJobError as exc:
        ahead_count = queue.count_ahead(exc.existing_job.id)
        return f"JM{album_id} 已经在队列中，前面还有{ahead_count}篇漫画正在处理。"

    ahead_count = queue.count_ahead(job.id)
    return f"收到 JM{album_id}，已加入队列，前面还有{ahead_count}篇漫画正在处理。"


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


def _enqueue(queue: PersistentAlbumQueue, album_id: str, target: QueueTarget):
    if target.destination_type == "group":
        if target.group_id is None:
            raise ValueError("group_id is required for group target")
        return queue.enqueue_group(album_id, group_id=target.group_id, user_id=target.user_id)

    return queue.enqueue_private(album_id, user_id=target.user_id)
