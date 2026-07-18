from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
import nonebot
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from jmbot.core.catalog_service import RankingQuery
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
    query_album_ranking_reply,
    query_album_detail_message,
    query_album_search_reply,
    remove_favorite_album,
    queue_summary,
    search_reply_options,
    task_history,
)
from jmbot.core.cleanup import cleanup_album_files
from jmbot.core.command_feedback import processing_ack_message
from jmbot.core.detail_cache import DetailCache
from jmbot.core.formatter import (
    format_admin_help,
    format_help,
    format_ranking_card_commands,
    format_ranking_categories,
    format_ranking_usage,
    ranking_title,
)
from jmbot.core.jm_service import DownloadRequest, DownloadSettings, download_directory_for_request
from jmbot.core.pdf_builder import collect_images
from jmbot.core.job_executor import run_album_job_with_timeout
from jmbot.core.message_scope import is_friend_private_message
from jmbot.core.parser import JmMessage, parse_jm_admin_message, parse_jm_message
from jmbot.core.persistent_queue import AlbumQueueJob, PersistentAlbumQueue
from jmbot.core.query_executor import query_cover
from jmbot.core.queue_processor import process_next_album_job
from jmbot.core.recovery import recover_interrupted_jobs
from jmbot.core.sender import (
    send_group_image,
    send_private_image,
    upload_group_pdf,
    upload_private_pdf,
)
from jmbot.core.search_card import build_ranking_result_card, build_search_result_card
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

    ack_message = processing_ack_message(command)
    if ack_message is not None:
        await jm_matcher.send(ack_message)

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
        await jm_matcher.send(
            format_help(
                CONFIG.command_prefix,
                CONFIG.admin_command_prefix,
                topic=command.help_topic,
            )
        )
        return False

    if command.action == "ranking_invalid":
        await jm_matcher.send(format_ranking_usage(CONFIG.command_prefix))
        return False

    if command.action == "ranking_categories":
        await jm_matcher.send(format_ranking_categories(CONFIG.command_prefix))
        return False

    if command.action == "ranking":
        query = RankingQuery(
            keyword=command.keyword,
            period=command.ranking_period,
            category=command.ranking_category,
            page=command.page,
        )
        reply = await query_album_ranking_reply(
            query,
            view=command.search_view,
            semaphore=QUERY_SEMAPHORE,
            timeout_seconds=CONFIG.query_timeout_seconds,
            command_prefix=CONFIG.command_prefix,
        )
        if command.search_view == "text" or reply.result is None or not reply.cover_album_ids:
            await jm_matcher.send(reply.message)
            return False

        card_error = await _send_ranking_card(bot, target, reply)
        if card_error is not None:
            await jm_matcher.send(reply.message)
        else:
            await jm_matcher.send(
                format_ranking_card_commands(reply.result, CONFIG.command_prefix)
            )
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
        if message is not None:
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
        limit, cover_limit = search_reply_options(command)
        reply = await query_album_search_reply(
            command.keyword,
            kind=command.search_kind,
            page=command.page,
            semaphore=QUERY_SEMAPHORE,
            timeout_seconds=CONFIG.query_timeout_seconds,
            command_prefix=CONFIG.command_prefix,
            limit=limit,
            cover_limit=cover_limit,
        )
        if command.search_kind == "author" and command.search_view != "text" and reply.card_items:
            card_error = await _send_author_search_card(bot, target, command.keyword, command.page, reply)
            if card_error is not None:
                await jm_matcher.send(card_error)
                await jm_matcher.send(reply.message)
            else:
                await jm_matcher.send(_author_search_command_text(reply))
        elif reply.cover_album_ids:
            await jm_matcher.send(reply.message)
            for album_id in reply.cover_album_ids:
                cover_error = await deliver_cover(
                    album_id,
                    CONFIG.preview_root,
                    QUERY_SEMAPHORE,
                    deliver=lambda path: _send_cover(bot, target, path),
                    timeout_seconds=CONFIG.query_timeout_seconds,
                )
            if cover_error is not None:
                await jm_matcher.send(cover_error)
        else:
            await jm_matcher.send(reply.message)
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

    if command.action == "admin_help":
        await jm_matcher.send(format_admin_help(CONFIG.admin_command_prefix))
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
        return True

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


async def _send_author_search_card(bot: Bot, target: QueueTarget, keyword: str, page: int, reply) -> str | None:
    media_dir = CONFIG.preview_root / "author-search" / f"{target.user_id}-{page}"
    try:
        await asyncio.to_thread(shutil.rmtree, media_dir, True)
        media_dir.mkdir(parents=True, exist_ok=True)
        cover_paths = []
        for album_id in reply.cover_album_ids:
            result = await asyncio.to_thread(
                query_cover,
                album_id,
                CONFIG.preview_root,
                timeout_seconds=CONFIG.query_timeout_seconds,
            )
            target_cover = media_dir / result.cover_path.name
            await asyncio.to_thread(shutil.copyfile, result.cover_path, target_cover)
            cover_paths.append(target_cover)
            await asyncio.to_thread(shutil.rmtree, result.media_dir, True)

        card_path = await asyncio.to_thread(
            build_search_result_card,
            reply.card_result,
            cover_paths,
            media_dir / "author-search.png",
            command_prefix=CONFIG.command_prefix,
        )
        await _send_cover(bot, target, card_path)
    except Exception:
        return "作者作品卡片生成失败，请稍后重试。"
    finally:
        await asyncio.to_thread(shutil.rmtree, media_dir, True)
    return None


async def _send_ranking_card(bot: Bot, target: QueueTarget, reply) -> str | None:
    media_root = CONFIG.preview_root / "ranking"
    media_root.mkdir(parents=True, exist_ok=True)
    media_dir = Path(tempfile.mkdtemp(prefix=f"{target.user_id}-", dir=media_root))
    cover_paths = []
    try:
        async with QUERY_SEMAPHORE:
            for album_id in reply.cover_album_ids:
                result = await asyncio.to_thread(
                    query_cover,
                    album_id,
                    CONFIG.preview_root,
                    timeout_seconds=CONFIG.query_timeout_seconds,
                )
                try:
                    target_cover = media_dir / f"{album_id}{result.cover_path.suffix}"
                    await asyncio.to_thread(shutil.copyfile, result.cover_path, target_cover)
                    cover_paths.append(target_cover)
                finally:
                    await asyncio.to_thread(shutil.rmtree, result.media_dir, True)

        card_path = await asyncio.to_thread(
            build_ranking_result_card,
            reply.result,
            cover_paths,
            media_dir / "ranking.png",
            title=ranking_title(reply.result.query),
            command_prefix=CONFIG.command_prefix,
        )
        await _send_cover(bot, target, card_path)
    except Exception:
        return "排行榜卡片生成失败，已切换为文本结果。"
    finally:
        await asyncio.to_thread(shutil.rmtree, media_dir, True)
        try:
            media_root.rmdir()
        except OSError:
            pass
    return None


def _author_search_command_text(reply) -> str:
    lines = ["可复制命令："]
    for item in reply.card_items:
        lines.append(f"JM{item.album_id} 下载：{CONFIG.command_prefix} {item.album_id}")
    if reply.card_result is not None and reply.card_result.page < reply.card_result.page_count:
        lines.append(f"下一页：{CONFIG.command_prefix} 作者 {reply.card_result.keyword} {reply.card_result.page + 1}")
    return "\n".join(lines)


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
            cancel_checker=lambda: ALBUM_QUEUE.is_canceled(job.id),
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
