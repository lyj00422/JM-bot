from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from jmbot.core.jm_service import AlbumJobResult
from jmbot.core.persistent_queue import AlbumQueueJob, PersistentAlbumQueue


RunAlbumJob = Callable[[str], Awaitable[AlbumJobResult]]
GroupUploader = Callable[[int, Path], Awaitable[None]]
PrivateUploader = Callable[[int, Path], Awaitable[None]]
Notifier = Callable[[AlbumQueueJob, str], Awaitable[None]]
Cleaner = Callable[[Path, Path], Awaitable[None]]


async def process_next_album_job(
    queue: PersistentAlbumQueue,
    *,
    run_job: RunAlbumJob,
    upload_group: GroupUploader,
    upload_private: PrivateUploader,
    notify: Notifier,
    cleanup: Cleaner | None = None,
) -> bool:
    """Process one queued album job and return whether a job was claimed."""

    job = queue.claim_next()
    if job is None:
        return False

    try:
        await _notify_safely(notify, job, f"开始处理 JM{job.album_id}。")
        result = await run_job(job.album_id)
        if job.destination_type == "group":
            if job.group_id is None:
                raise ValueError(f"Group job {job.id} is missing group_id")
            await upload_group(job.group_id, result.pdf_path)
        else:
            await upload_private(job.user_id, result.pdf_path)

        if cleanup is not None:
            await cleanup(result.album_dir, result.pdf_path)

        queue.mark_done(job.id)
    except Exception as exc:
        queue.mark_failed(job.id, str(exc))
        await _notify_safely(notify, job, f"JM{job.album_id} 处理失败：{exc}")
    else:
        await _notify_safely(notify, job, f"JM{job.album_id} 已上传：{result.pdf_path.name}")

    return True


async def _notify_safely(notify: Notifier, job: AlbumQueueJob, message: str) -> None:
    try:
        await notify(job, message)
    except Exception:
        return
