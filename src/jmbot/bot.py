from __future__ import annotations

import asyncio

import nonebot
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from jmbot.config import load_config
from jmbot.core.cleanup import cleanup_album_files
from jmbot.core.jm_service import DownloadSettings, run_album_job
from jmbot.core.message_scope import is_friend_private_message
from jmbot.core.parser import parse_jm_command
from jmbot.core.persistent_queue import AlbumQueueJob, PersistentAlbumQueue
from jmbot.core.queue_processor import process_next_album_job
from jmbot.core.sender import upload_group_pdf, upload_private_pdf


CONFIG = load_config()
DOWNLOAD_SETTINGS = DownloadSettings(
    download_root=CONFIG.download_root,
    option_root=CONFIG.option_root,
    download_image_threads=CONFIG.download_image_threads,
    download_photo_threads=CONFIG.download_photo_threads,
    jmcomic_log=CONFIG.jmcomic_log,
    jmcomic_retry_times=CONFIG.jmcomic_retry_times,
)
ALBUM_QUEUE = PersistentAlbumQueue(CONFIG.queue_db_path)
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

    return parse_jm_command(event.get_plaintext(), CONFIG.command_prefix) is not None


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

    command = parse_jm_command(event.get_plaintext(), CONFIG.command_prefix)
    if command is None:
        return

    if is_group_event:
        queued_job = ALBUM_QUEUE.enqueue_group(command.album_id, group_id=event.group_id, user_id=event.user_id)
    else:
        queued_job = ALBUM_QUEUE.enqueue_private(command.album_id, user_id=event.user_id)

    ahead_count = ALBUM_QUEUE.count_ahead(queued_job.id)
    await jm_matcher.send(f"收到 JM{command.album_id}，已加入队列，前面还有{ahead_count}篇漫画正在处理。")
    await _ensure_queue_worker(bot)


async def _ensure_queue_worker(bot: Bot) -> None:
    global _queue_recovered, _queue_worker_task

    async with _queue_worker_lock:
        if not _queue_recovered:
            ALBUM_QUEUE.recover_interrupted_jobs()
            _queue_recovered = True

        if _queue_worker_task is None or _queue_worker_task.done():
            _queue_worker_task = asyncio.create_task(_drain_album_queue(bot))


async def _drain_album_queue(bot: Bot) -> None:
    while True:
        processed = await process_next_album_job(
            ALBUM_QUEUE,
            run_job=lambda album_id: asyncio.to_thread(run_album_job, album_id, DOWNLOAD_SETTINGS, CONFIG.output_root),
            upload_group=lambda group_id, pdf_path: upload_group_pdf(
                bot, group_id, pdf_path, CONFIG.group_folder_name
            ),
            upload_private=lambda user_id, pdf_path: upload_private_pdf(bot, user_id, pdf_path),
            notify=lambda job, message: _notify_job(bot, job, message),
            cleanup=_cleanup_job_files if CONFIG.clean_after_upload else None,
        )
        if not processed:
            return


async def _notify_job(bot: Bot, job: AlbumQueueJob, message: str) -> None:
    if job.destination_type == "group":
        if job.group_id is None:
            raise ValueError(f"Group job {job.id} is missing group_id")
        await bot.call_api("send_group_msg", group_id=job.group_id, message=message)
    else:
        await bot.call_api("send_private_msg", user_id=job.user_id, message=message)


async def _cleanup_job_files(album_dir, pdf_path) -> None:
    await asyncio.to_thread(cleanup_album_files, album_dir, pdf_path)


def main() -> None:
    nonebot.run()


if __name__ == "__main__":
    main()
