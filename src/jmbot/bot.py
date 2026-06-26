from __future__ import annotations

import asyncio
import nonebot
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from jmbot.config import load_config
from jmbot.core.command_service import (
    QueueTarget,
    admin_clear_queue,
    admin_delete_failed_task,
    admin_failed_task_detail,
    admin_failed_tasks,
    admin_jump_album,
    admin_kill_album,
    cancel_album_request,
    deliver_cover,
    deliver_preview,
    enqueue_album_request,
    favorite_album,
    favorite_summary,
    my_tasks_summary,
    prune_history,
    progress_summary,
    query_album_detail_message,
    query_album_search_message,
    remove_favorite_album,
    queue_summary,
    task_history,
)
from jmbot.core.cleanup import cleanup_album_files
from jmbot.core.detail_cache import DetailCache
from jmbot.core.formatter import format_help
from jmbot.core.jm_service import DownloadRequest, DownloadSettings, download_directory_for_request
from jmbot.core.pdf_builder import collect_images
from jmbot.core.job_executor import run_album_job_with_timeout
from jmbot.core.message_scope import is_friend_private_message
from jmbot.core.parser import JmMessage, parse_jm_admin_message, parse_jm_message
from jmbot.core.persistent_queue import AlbumQueueJob, PersistentAlbumQueue
from jmbot.core.queue_processor import process_next_album_job
from jmbot.core.recovery import recover_interrupted_jobs
from jmbot.core.sender import (
    send_group_image,
    send_private_image,
    upload_group_pdf,
    upload_private_pdf,
)
from jmbot.core.user_store import UserDataStore


CONFIG = load_config()
DOWNLOAD_SETTINGS = DownloadSettings(
    download_root=CONFIG.download_root,
    option_root=CONFIG.option_root,
    download_image_threads=CONFIG.download_image_threads,
    download_photo_threads=CONFIG.download_photo_threads,
    jmcomic_log=CONFIG.jmcomic_log,
    jmcomic_retry_times=CONFIG.jmcomic_retry_times,
    auto_split_pdf=CONFIG.auto_split_pdf,
    split_image_count=CONFIG.split_image_count,
    progress_mode=CONFIG.progress_mode,
)
ALBUM_QUEUE = PersistentAlbumQueue(CONFIG.queue_db_path)
USER_STORE = UserDataStore(CONFIG.user_db_path)
DETAIL_CACHE = DetailCache(
    ttl_seconds=CONFIG.detail_cache_ttl_seconds,
    max_entries=CONFIG.query_cache_max_entries,
)
QUERY_SEMAPHORE = asyncio.Semaphore(CONFIG.query_max_concurrency)
_queue_worker_lock = asyncio.Lock()
_queue_worker_task: asyncio.Task[None] | None = None
_queue_recovered = False

nonebot.init(
    driver="~fastapi+~websockets",
    host=CONFIG.host,
    port=CONFIG.port,
    command_start={"/"},
    superusers={str(user_id) for user_id in CONFIG.superuser_ids},
)
DRIVER = get_driver()
DRIVER.register_adapter(OneBotV11Adapter)


@DRIVER.on_bot_connect
async def _start_queue_worker_on_connect(bot: Bot) -> None:
    await _ensure_queue_worker(bot)


async def _is_jm_command(event: MessageEvent) -> bool:
    """Return True when the incoming QQ message is a JM command."""

    plaintext = event.get_plaintext()
    return bool(
        parse_jm_message(
            plaintext,
            CONFIG.command_prefix,
            default_format=CONFIG.default_format,
        )
        or parse_jm_admin_message(plaintext, CONFIG.admin_command_prefix)
    )


jm_matcher = on_message(rule=Rule(_is_jm_command), priority=10, block=True)


@jm_matcher.handle()
async def handle_jm_command(bot: Bot, event: MessageEvent) -> None:
    is_group_event = isinstance(event, GroupMessageEvent)
    is_friend_private_event = isinstance(event, PrivateMessageEvent) and is_friend_private_message(event)

    if not is_group_event and not is_friend_private_event:
        await jm_matcher.finish(f"请在 QQ 群或机器人好友私聊里使用 {CONFIG.command_prefix} <漫画ID>。")
        return

    if is_group_event and CONFIG.allowed_group_ids and event.group_id not in CONFIG.allowed_group_ids:
        await jm_matcher.finish("这个群还没有启用 JM 下载功能。")
        return

    command = parse_jm_message(
        event.get_plaintext(),
        CONFIG.command_prefix,
        default_format=CONFIG.default_format,
    )
    if command is None:
        command = parse_jm_admin_message(event.get_plaintext(), CONFIG.admin_command_prefix)
    if command is None:
        return

    target = _target_from_event(event)
    if target is None:
        return

    prune_history(
        ALBUM_QUEUE,
        retention_days=CONFIG.history_retention_days,
        max_per_user=CONFIG.history_max_per_user,
    )

    should_start_worker = await _handle_parsed_command(bot, event, command, target)
    if should_start_worker:
        await _ensure_queue_worker(bot)


async def _handle_parsed_command(
    bot: Bot,
    event: MessageEvent,
    command: JmMessage,
    target: QueueTarget,
) -> bool:
    if command.action == "request":
        if command.album_id is None:
            return False
        if command.output_format not in CONFIG.allowed_formats:
            await jm_matcher.send(f"当前不支持 {command.output_format.upper()} 格式。")
            return False
        await jm_matcher.send(
            enqueue_album_request(
                ALBUM_QUEUE,
                command.album_id,
                target,
                output_format=command.output_format,
                chapters=command.chapters,
            )
        )
        return True

    if command.action == "cancel":
        if command.album_id is None:
            return False
        await jm_matcher.send(cancel_album_request(ALBUM_QUEUE, command.album_id, target))
        return False

    if command.action == "queue":
        await jm_matcher.send(queue_summary(ALBUM_QUEUE))
        return False

    if command.action == "help":
        await jm_matcher.send(format_help(CONFIG.command_prefix, CONFIG.admin_command_prefix))
        return False

    if command.action == "detail":
        if command.album_id is None:
            return False
        message = await query_album_detail_message(
            command.album_id,
            DETAIL_CACHE,
            QUERY_SEMAPHORE,
            timeout_seconds=CONFIG.query_timeout_seconds,
            command_prefix=CONFIG.command_prefix,
        )
        await jm_matcher.send(message)
        cover_error = await deliver_cover(
            command.album_id,
            CONFIG.preview_root,
            QUERY_SEMAPHORE,
            deliver=lambda path: _send_cover(bot, target, path),
            timeout_seconds=CONFIG.query_timeout_seconds,
        )
        if cover_error is not None:
            await jm_matcher.send(cover_error)
        return False

    if command.action == "cover":
        if command.album_id is None:
            return False
        error = await deliver_cover(
            command.album_id,
            CONFIG.preview_root,
            QUERY_SEMAPHORE,
            deliver=lambda path: _send_cover(bot, target, path),
            timeout_seconds=CONFIG.query_timeout_seconds,
        )
        if error is not None:
            await jm_matcher.send(error)
        return False

    if command.action == "preview":
        if command.album_id is None:
            return False
        message = await deliver_preview(
            command.album_id,
            CONFIG.preview_root,
            QUERY_SEMAPHORE,
            deliver=lambda path: _upload_preview(bot, target, path),
            page_count=CONFIG.preview_page_count,
            timeout_seconds=CONFIG.preview_timeout_seconds,
        )
        await jm_matcher.send(message)
        return False

    if command.action == "progress":
        await jm_matcher.send(
            progress_summary(
                ALBUM_QUEUE,
                command.album_id,
                target,
                mode=CONFIG.progress_mode,
            )
        )
        return False

    if command.action == "search":
        if command.keyword is None:
            return False
        message = await query_album_search_message(
            command.keyword,
            kind=command.search_kind,
            page=command.page,
            semaphore=QUERY_SEMAPHORE,
            timeout_seconds=CONFIG.query_timeout_seconds,
            command_prefix=CONFIG.command_prefix,
        )
        await jm_matcher.send(message)
        return False

    if command.action == "favorite_add":
        if command.album_id is None:
            return False
        await jm_matcher.send(
            favorite_album(
                USER_STORE,
                command.album_id,
                target,
                command_prefix=CONFIG.command_prefix,
            )
        )
        return False

    if command.action == "favorite_remove":
        if command.album_id is None:
            return False
        await jm_matcher.send(remove_favorite_album(USER_STORE, command.album_id, target))
        return False

    if command.action == "favorite_list":
        await jm_matcher.send(favorite_summary(USER_STORE, target, command_prefix=CONFIG.command_prefix))
        return False

    if command.action == "my_tasks":
        await jm_matcher.send(my_tasks_summary(ALBUM_QUEUE, target))
        return False

    if command.action == "history":
        await jm_matcher.send(
            task_history(
                ALBUM_QUEUE,
                target,
                failed_only=command.failed_only,
                limit=command.limit,
            )
        )
        return False

    if event.user_id not in CONFIG.superuser_ids:
        await jm_matcher.send("你没有 JM 管理权限。")
        return False

    if command.action == "admin_clear":
        await jm_matcher.send(admin_clear_queue(ALBUM_QUEUE))
        return False

    if command.action == "admin_kill":
        if command.album_id is None:
            return False
        reply = admin_kill_album(ALBUM_QUEUE, command.album_id, target, all_targets=command.all_targets)
        await asyncio.to_thread(_cleanup_album_download_dir, command.album_id)
        await jm_matcher.send(reply)
        return False

    if command.action == "admin_jump":
        if command.album_id is None:
            return False
        await jm_matcher.send(admin_jump_album(ALBUM_QUEUE, command.album_id, target))
        return True

    if command.action == "admin_failed":
        if command.request_id is not None:
            message = admin_failed_task_detail(ALBUM_QUEUE, command.request_id)
        else:
            message = admin_failed_tasks(ALBUM_QUEUE, limit=command.limit)
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_private_msg", user_id=event.user_id, message=message)
            await jm_matcher.send("最近失败任务已私聊发送给管理员。")
        else:
            await jm_matcher.send(message)
        return False

    if command.action == "admin_failed_delete":
        if command.request_id is None:
            return False
        await jm_matcher.send(admin_delete_failed_task(ALBUM_QUEUE, command.request_id))
        return False

    return False


def _target_from_event(event: MessageEvent) -> QueueTarget | None:
    if isinstance(event, GroupMessageEvent):
        return QueueTarget(destination_type="group", group_id=event.group_id, user_id=event.user_id)
    if isinstance(event, PrivateMessageEvent):
        return QueueTarget(destination_type="private", group_id=None, user_id=event.user_id)
    return None


async def _send_cover(bot: Bot, target: QueueTarget, path) -> None:
    if target.destination_type == "group":
        if target.group_id is None:
            raise ValueError("group_id is required for group cover")
        await send_group_image(bot, target.group_id, path)
    else:
        await send_private_image(bot, target.user_id, path)


async def _upload_preview(bot: Bot, target: QueueTarget, path) -> None:
    if target.destination_type == "group":
        if target.group_id is None:
            raise ValueError("group_id is required for group preview")
        await upload_group_pdf(bot, target.group_id, path, CONFIG.group_folder_name)
    else:
        await upload_private_pdf(bot, target.user_id, path)


async def _ensure_queue_worker(bot: Bot) -> None:
    global _queue_recovered, _queue_worker_task

    async with _queue_worker_lock:
        if not _queue_recovered:
            recover_interrupted_jobs(ALBUM_QUEUE, CONFIG.download_root)
            _queue_recovered = True

        if _queue_worker_task is None or _queue_worker_task.done():
            _queue_worker_task = asyncio.create_task(_drain_album_queue(bot))


async def _drain_album_queue(bot: Bot) -> None:
    while True:
        processed = await process_next_album_job(
            ALBUM_QUEUE,
            run_job=_run_queued_album_job,
            upload_group=lambda group_id, pdf_path: upload_group_pdf(
                bot, group_id, pdf_path, CONFIG.group_folder_name
            ),
            upload_private=lambda user_id, pdf_path: upload_private_pdf(bot, user_id, pdf_path),
            notify=lambda job, message: _notify_job(bot, job, message),
            cleanup=_cleanup_job_files if CONFIG.clean_after_upload else None,
            is_canceled=ALBUM_QUEUE.is_canceled,
        )
        if not processed:
            return


async def _run_queued_album_job(job: AlbumQueueJob):
    request = DownloadRequest(
        job.album_id,
        output_format=job.output_format,
        chapters=job.chapters,
    )
    album_dir = download_directory_for_request(DOWNLOAD_SETTINGS, request)
    task = asyncio.create_task(
        asyncio.to_thread(
            run_album_job_with_timeout,
            request,
            DOWNLOAD_SETTINGS,
            CONFIG.output_root,
            timeout_seconds=CONFIG.job_timeout_seconds,
        )
    )
    while not task.done():
        images = await asyncio.to_thread(collect_images, album_dir)
        marker = album_dir / ".stage-building"
        total_marker = album_dir / ".progress-total"
        if marker.is_file():
            total = len(images)
            ALBUM_QUEUE.update_progress(job.id, stage="building", current=total, total=total)
        elif CONFIG.progress_mode == "precise":
            total = _read_positive_int(total_marker)
            ALBUM_QUEUE.update_progress(
                job.id,
                stage="downloading",
                current=len(images),
                total=total,
            )
        await asyncio.sleep(0.5)
    return await task


async def _notify_job(bot: Bot, job: AlbumQueueJob, message: str) -> None:
    if job.destination_type == "group":
        if job.group_id is None:
            raise ValueError(f"Group job {job.id} is missing group_id")
        await bot.call_api("send_group_msg", group_id=job.group_id, message=message)
    else:
        await bot.call_api("send_private_msg", user_id=job.user_id, message=message)


async def _cleanup_job_files(album_dir, pdf_path) -> None:
    await asyncio.to_thread(cleanup_album_files, album_dir, pdf_path)


def _cleanup_album_download_dir(album_id: str) -> None:
    cleanup_album_files(CONFIG.download_root / album_id, CONFIG.output_root / f".missing-JM{album_id}.pdf")


def _read_positive_int(path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def main() -> None:
    nonebot.run()


if __name__ == "__main__":
    main()
