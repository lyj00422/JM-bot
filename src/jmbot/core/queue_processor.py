from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from jmbot.core.formatter import (
    format_task_canceled,
    format_task_failed,
    format_task_started,
    format_task_uploaded,
)
from jmbot.core.jm_service import AlbumJobResult
from jmbot.core.persistent_queue import AlbumQueueJob, PersistentAlbumQueue


RunAlbumJob = Callable[[str], Awaitable[AlbumJobResult]]
GroupUploader = Callable[[int, Path], Awaitable[None]]
PrivateUploader = Callable[[int, Path], Awaitable[None]]
Notifier = Callable[[AlbumQueueJob, str], Awaitable[None]]
Cleaner = Callable[[Path, Path], Awaitable[None]]
CancelChecker = Callable[[int], bool]
LOGGER = logging.getLogger(__name__)


async def process_next_album_job(
    queue: PersistentAlbumQueue,
    *,
    run_job: RunAlbumJob,
    upload_group: GroupUploader,
    upload_private: PrivateUploader,
    notify: Notifier,
    cleanup: Cleaner | None = None,
    is_canceled: CancelChecker | None = None,
) -> bool:
    """Process one queued album job and return whether a job was claimed."""

    job = queue.claim_next()
    if job is None:
        return False

    started = time.monotonic()
    LOGGER.info("Starting JM%s queue job %s for %s", job.album_id, job.id, job.destination_type)
    try:
        await _notify_safely(notify, job, format_task_started(job.album_id))
        result = await run_job(job.album_id)
        output_paths = result.output_paths
        output_names = ", ".join(path.name for path in output_paths)
        LOGGER.info("Built JM%s PDF(s) for queue job %s: %s", job.album_id, job.id, output_names)
        if is_canceled is not None and is_canceled(job.id):
            if cleanup is not None:
                for pdf_path in output_paths:
                    await cleanup(result.album_dir, pdf_path)
            await _notify_safely(notify, job, format_task_canceled(job.album_id))
            return True

        if job.destination_type == "group":
            if job.group_id is None:
                raise ValueError(f"Group job {job.id} is missing group_id")
            for pdf_path in output_paths:
                await upload_group(job.group_id, pdf_path)
        else:
            for pdf_path in output_paths:
                await upload_private(job.user_id, pdf_path)
        LOGGER.info("Uploaded JM%s PDF(s) for queue job %s: %s", job.album_id, job.id, output_names)

        if cleanup is not None:
            for pdf_path in output_paths:
                await cleanup(result.album_dir, pdf_path)

        queue.mark_done(job.id)
    except Exception as exc:
        if is_canceled is not None and is_canceled(job.id):
            await _notify_safely(notify, job, format_task_canceled(job.album_id))
            return True

        queue.mark_failed(job.id, str(exc))
        LOGGER.exception("JM%s queue job %s failed after %.1f seconds", job.album_id, job.id, time.monotonic() - started)
        await _notify_safely(notify, job, format_task_failed(job.album_id))
    else:
        LOGGER.info("Finished JM%s queue job %s in %.1f seconds", job.album_id, job.id, time.monotonic() - started)
        await _notify_safely(notify, job, format_task_uploaded(job.album_id, output_names))

    return True


async def _notify_safely(notify: Notifier, job: AlbumQueueJob, message: str) -> None:
    try:
        await notify(job, message)
    except Exception:
        return
