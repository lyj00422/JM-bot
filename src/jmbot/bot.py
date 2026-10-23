from __future__ import annotations

import asyncio
import time
import shutil
import tempfile
from pathlib import Path
import nonebot
from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, PrivateMessageEvent
from nonebot.rule import Rule

from jmbot.core.catalog_service import RankingQuery
from jmbot.core.chat_service import (
    CHAT_ADMIN_ID,
    ChatAPIError,
    ChatStore,
    build_group_messages,
    build_private_messages,
    extract_chat_reply,
    face_marker,
    load_chat_role,
    parse_group_response,
    request_chat_completion,
    split_chat_reply,
)
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
    format_chat_help,
    format_help,
    format_ranking_card_commands,
    format_ranking_categories,
    format_ranking_usage,
    ranking_title,
)
from jmbot.core.jm_service import DownloadRequest, DownloadSettings, download_directory_for_request
from jmbot.core.pdf_builder import collect_images
from jmbot.core.job_executor import run_album_job_with_timeout
from jmbot.core.message_scope import (
    at_targets,
    is_at_bot,
    is_friend_private_message,
    is_reply_to_bot,
    message_segments,
)
from jmbot.core.parser import JmMessage, parse_ds_message, parse_jm_admin_message, parse_jm_message
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
CHAT_STORE = ChatStore(CONFIG.chat_db_path, master_key_path=CONFIG.chat_master_key_path)
CHAT_ROLE = load_chat_role(CONFIG.chat_role_path)
_private_chat_locks: dict[int, asyncio.Lock] = {}
_group_chat_locks: dict[int, asyncio.Lock] = {}
_group_chat_last_reply: dict[int, float] = {}
_group_chat_proactive_replies: dict[int, list[float]] = {}
_group_alias_cache: dict[int, tuple[float, set[str]]] = {}
DETAIL_CACHE = DetailCache(
    ttl_seconds=CONFIG.detail_cache_ttl_seconds,
    max_entries=CONFIG.query_cache_max_entries,
)
QUERY_SEMAPHORE = asyncio.Semaphore(CONFIG.query_max_concurrency)
_queue_worker_lock = asyncio.Lock()
_queue_worker_task: asyncio.Task[None] | None = None
_queue_recovered = False
LOGGER = logger

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


async def _is_ds_command(event: MessageEvent) -> bool:
    return parse_ds_message(event.get_plaintext()) is not None


ds_matcher = on_message(rule=Rule(_is_ds_command), priority=9, block=True)


@ds_matcher.handle()
async def handle_ds_command(bot: Bot, event: MessageEvent) -> None:
    command = parse_ds_message(event.get_plaintext())
    if command is None:
        return
    is_private = isinstance(event, PrivateMessageEvent) and is_friend_private_message(event)
    is_admin = int(event.user_id) == CHAT_ADMIN_ID

    if command.action == "chat_help":
        await ds_matcher.send(format_chat_help("/ds", topic=command.help_topic))
        return

    if command.action in {"chat_private_key", "chat_private_status", "chat_private_clear", "chat_clear"}:
        if not is_private:
            await ds_matcher.send("请私聊机器人管理你的聊天配置和记录。")
            return
        if command.action == "chat_private_key":
            if not command.text:
                await ds_matcher.send("用法：/ds 你的 API Key；查看：/ds 状态；清除：/ds 清除")
                return
            try:
                CHAT_STORE.set_private_key(event.user_id, command.text)
            except ValueError:
                await ds_matcher.send("API Key 不能为空。")
                return
            LOGGER.info("私聊 API Key 已更新 user_id={}", event.user_id)
            await ds_matcher.send("你的 DeepSeek API Key 已保存。")
        elif command.action == "chat_private_status":
            if is_admin:
                state = "运行中" if CHAT_STORE.is_chat_enabled() else "已停止"
                await ds_matcher.send(f"聊天功能：{state}；管理员 QQ：3065532050；模型：{CONFIG.chat_model}")
            else:
                configured = CHAT_STORE.get_private_key(event.user_id) is not None
                await ds_matcher.send(f"你的私聊 API Key：{'已配置' if configured else '未配置'}")
        elif command.action == "chat_private_clear":
            removed = CHAT_STORE.remove_private_key(event.user_id)
            await ds_matcher.send("已清除你的 API Key。" if removed else "你还没有保存 API Key。")
        else:
            CHAT_STORE.clear_private_history(event.user_id)
            await ds_matcher.send("已清空你的私聊历史。")
        return

    if not is_admin:
        await ds_matcher.send("你没有聊天管理权限。")
        return
    if not is_private:
        await ds_matcher.send("请管理员私聊机器人配置群聊 Key 或切换聊天功能。")
        return
    if command.action == "chat_enable":
        CHAT_STORE.set_chat_enabled(True)
        LOGGER.info("聊天功能已开启 admin_id={}", event.user_id)
        await ds_matcher.send("聊天功能已开启，下载功能一直可用。")
    elif command.action == "chat_disable":
        CHAT_STORE.set_chat_enabled(False)
        LOGGER.info("聊天功能已停止 admin_id={}", event.user_id)
        await ds_matcher.send("聊天功能已停止，当前只保留下载功能。")
    elif command.action == "chat_status":
        state = "运行中" if CHAT_STORE.is_chat_enabled() else "已停止"
        await ds_matcher.send(f"聊天功能：{state}；管理员 QQ：3065532050；模型：{CONFIG.chat_model}")
    elif command.group_id is not None and command.action == "chat_group_key_set":
        CHAT_STORE.set_group_key(command.group_id, command.text or "")
        LOGGER.info("群聊 API Key 已更新 group_id={} admin_id={}", command.group_id, event.user_id)
        await ds_matcher.send(f"群 {command.group_id} 的聊天 Key 已保存，余额不足状态已清除。")
    elif command.group_id is not None and command.action == "chat_group_key_status":
        key = CHAT_STORE.get_group_key(command.group_id)
        exhausted = CHAT_STORE.is_group_balance_exhausted(command.group_id)
        await ds_matcher.send(
            f"群 {command.group_id}：Key{'已配置' if key else '未配置'}；"
            f"余额状态：{'余额不足' if exhausted else '正常'}"
        )
    elif command.group_id is not None and command.action == "chat_group_key_clear":
        removed = CHAT_STORE.remove_group_key(command.group_id)
        LOGGER.info("群聊 API Key 已清除 group_id={} admin_id={}", command.group_id, event.user_id)
        await ds_matcher.send(f"群 {command.group_id} 的聊天 Key 已{'清除' if removed else '不存在'}。")


async def _is_chat_message(event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        targets = at_targets(event)
        if targets:
            LOGGER.info(
                "收到群聊含 @ group_id={} user_id={} bot_id={} at_targets={} segments={}",
                event.group_id,
                event.user_id,
                getattr(event, "self_id", "unknown"),
                targets,
                [getattr(segment, "type", "unknown") for segment in message_segments(event)],
            )
    if not CHAT_STORE.is_chat_enabled():
        if isinstance(event, GroupMessageEvent) and at_targets(event):
            LOGGER.warning("群聊 @ 被聊天总开关拦截 group_id={} user_id={}", event.group_id, event.user_id)
        return False
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return False
    if isinstance(event, PrivateMessageEvent) and not is_friend_private_message(event):
        return False
    plaintext = event.get_plaintext()
    if parse_jm_message(plaintext, CONFIG.command_prefix, default_format=CONFIG.default_format):
        return False
    if parse_jm_admin_message(plaintext, CONFIG.admin_command_prefix):
        return False
    if _message_content(event).strip():
        return True
    # A bare "@bot" keeps no text once the adapter strips the at segment, but it
    # is still an explicit call and must reach the group handler.
    return isinstance(event, GroupMessageEvent) and is_at_bot(event, getattr(event, "self_id", ""))


chat_matcher = on_message(rule=Rule(_is_chat_message), priority=20, block=True)


@chat_matcher.handle()
async def handle_chat_message(bot: Bot, event: MessageEvent) -> None:
    if isinstance(event, GroupMessageEvent):
        if CONFIG.allowed_group_ids and event.group_id not in CONFIG.allowed_group_ids:
            LOGGER.warning("群聊被白名单拦截 group_id={} user_id={}", event.group_id, event.user_id)
            return
        await _handle_group_chat(bot, event)
    elif isinstance(event, PrivateMessageEvent) and is_friend_private_message(event):
        await _handle_private_chat(bot, event)


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


async def _handle_private_chat(bot: Bot, event: PrivateMessageEvent) -> None:
    user_id = int(event.user_id)
    api_key = CHAT_STORE.get_private_key(user_id)
    if not api_key:
        LOGGER.warning("私聊跳过：未配置私聊 Key user_id={}", user_id)
        await chat_matcher.send("私聊需要先配置你的 DeepSeek API Key。\n发送：/ds 你的 API Key")
        return

    text = _message_content(event).strip()
    LOGGER.info("私聊处理开始 user_id={} model={}", user_id, CONFIG.chat_model)
    lock = _lock_for(_private_chat_locks, user_id)
    async with lock:
        messages = build_private_messages(
            CHAT_ROLE,
            CHAT_STORE.get_private_summary(user_id),
            CHAT_STORE.private_history(user_id, limit=30),
            text,
        )
        CHAT_STORE.append_private_message(user_id, "user", text)
        try:
            reply = await asyncio.to_thread(
                request_chat_completion,
                api_key,
                messages,
                endpoint=CONFIG.chat_endpoint,
                model=CONFIG.chat_model,
                timeout_seconds=CONFIG.chat_timeout_seconds,
                proxy=CONFIG.chat_proxy,
            )
        except ChatAPIError as error:
            LOGGER.error(
                "私聊 DeepSeek 请求失败 user_id={} status_code={} error_type={}",
                user_id,
                error.status_code,
                type(error).__name__,
            )
            await chat_matcher.send(_private_error_message(error))
            return

        CHAT_STORE.append_private_message(user_id, "assistant", reply)
        try:
            await _send_chat_reply(chat_matcher, reply)
        except Exception:
            LOGGER.exception("私聊回复发送失败 user_id={}", user_id)
            raise
        LOGGER.info("私聊回复发送成功 user_id={}", user_id)

        if CHAT_STORE.private_message_count(user_id) > 60:
            await _compact_private_history(user_id, api_key)


async def _handle_group_chat(bot: Bot, event: GroupMessageEvent) -> None:
    group_id = int(event.group_id)
    mentioned = is_at_bot(event, bot.self_id)
    text = _message_content(event).strip() or ("（只 @ 了你，没有说话）" if mentioned else "")
    sender_name = _sender_name(event)
    LOGGER.info(
        "群聊处理开始 group_id={} user_id={} bot_id={} mentioned={} segments={}",
        group_id,
        event.user_id,
        bot.self_id,
        mentioned,
        [getattr(segment, "type", "unknown") for segment in message_segments(event)],
    )
    history = CHAT_STORE.group_history(
        group_id,
        limit=CONFIG.group_chat_history_limit,
        max_age_seconds=CONFIG.group_chat_context_seconds,
    )
    current_message_id = CHAT_STORE.append_group_message(group_id, sender_name, text)

    if CHAT_STORE.is_group_balance_exhausted(group_id):
        LOGGER.warning("群聊跳过：API 余额标记不足 group_id={} mentioned={}", group_id, mentioned)
        if mentioned:
            await chat_matcher.send("大肥鱼被饿死了")
        return

    api_key = CHAT_STORE.get_group_key(group_id)
    if not api_key:
        LOGGER.warning("群聊跳过：未配置群 Key group_id={} mentioned={}", group_id, mentioned)
        if mentioned:
            await chat_matcher.send("这个群还没有配置聊天 API Key。")
        return

    active = CHAT_STORE.is_group_active(group_id)
    alias_match = _looks_like_group_candidate(text, CHAT_ROLE.name, await _group_aliases(bot, event))
    replied_to_bot = is_reply_to_bot(event, bot.self_id)
    LOGGER.info(
        "群聊触发判断 group_id={} mentioned={} replied_to_bot={} alias_match={} active={}",
        group_id,
        mentioned,
        replied_to_bot,
        alias_match,
        active,
    )
    if not mentioned and not replied_to_bot and not alias_match:
        LOGGER.info("群聊跳过：未命中 @、回复或昵称触发 group_id={}", group_id)
        return
    # @ 或回复机器人时无条件回应；昵称/关键词命中的主动回应只受冷却与频率限制。
    # 这里不能要求 active：活跃窗口只在回复成功后才写入，否则首次关键词触发
    # 永远不可能发生（群聊沉默的死锁）。
    if not mentioned and not replied_to_bot and not _group_reply_allowed(group_id):
        LOGGER.info("群聊跳过：主动回复限流 group_id={} active={}", group_id, active)
        return

    lock = _lock_for(_group_chat_locks, group_id)
    async with lock:
        if (
            not mentioned
            and not replied_to_bot
            and CHAT_STORE.latest_group_message_id(group_id) != current_message_id
        ):
            LOGGER.info("群聊跳过：被新消息覆盖的排队回复 group_id={} message_id={}", group_id, current_message_id)
            return
        messages = build_group_messages(
            CHAT_ROLE,
            history,
            sender_name,
            text,
            decision_required=not mentioned and not replied_to_bot,
        )
        try:
            LOGGER.info(
                "群聊请求 DeepSeek group_id={} model={} endpoint_host={} trigger={}",
                group_id,
                CONFIG.chat_model,
                CONFIG.chat_endpoint.split("/", 3)[2] if "://" in CONFIG.chat_endpoint else "invalid",
                "at" if mentioned else "passive",
            )
            raw_reply = await asyncio.to_thread(
                request_chat_completion,
                api_key,
                messages,
                endpoint=CONFIG.chat_endpoint,
                model=CONFIG.chat_model,
                timeout_seconds=CONFIG.chat_timeout_seconds,
                proxy=CONFIG.chat_proxy,
                json_output=True,
            )
        except ChatAPIError as error:
            LOGGER.error(
                "群聊 DeepSeek 请求失败 group_id={} status_code={} error_type={}",
                group_id,
                error.status_code,
                type(error).__name__,
            )
            if error.status_code == 402:
                CHAT_STORE.mark_group_balance_exhausted(group_id, str(error))
                if mentioned:
                    await chat_matcher.send("大肥鱼被饿死了")
            elif mentioned:
                await chat_matcher.send(_group_error_message(error))
            return

        should_reply, reply, should_poke = parse_group_response(
            raw_reply,
            decision_required=not mentioned and not replied_to_bot,
        )
        if not should_reply:
            LOGGER.info("群聊模型决定不回复 group_id={}", group_id)
            return
        CHAT_STORE.append_group_message(group_id, CHAT_ROLE.name, reply)
        CHAT_STORE.set_group_active_until(group_id, time.time() + CONFIG.group_chat_active_seconds)
        if not mentioned:
            _record_group_proactive_reply(group_id)
        _group_chat_last_reply[group_id] = time.time()
        try:
            if should_poke:
                try:
                    await bot.call_api("group_poke", group_id=group_id, user_id=int(event.user_id))
                except Exception as error:
                    LOGGER.warning(
                        "群聊戳一戳发送失败 group_id={} user_id={} error_type={}",
                        group_id,
                        event.user_id,
                        type(error).__name__,
                    )
            await _send_chat_reply(chat_matcher, reply, reply_message_id=int(event.message_id))
        except Exception:
            LOGGER.exception("群聊回复发送失败 group_id={}", group_id)
            raise
        LOGGER.info("群聊回复发送成功 group_id={} trigger={}", group_id, "at" if mentioned else "passive")


async def _compact_private_history(user_id: int, api_key: str) -> None:
    history = CHAT_STORE.private_history(user_id, limit=60)
    if len(history) <= 30:
        return
    old_messages = history[:-20]
    previous = CHAT_STORE.get_private_summary(user_id) or "无"
    transcript = "\n".join(f"{item.role}: {item.content}" for item in old_messages)
    prompt = (
        "请把下面的私聊内容压缩成一份简洁、准确、可长期使用的中文记忆摘要。"
        "保留用户偏好、重要经历、关系变化、称呼和未完成事项，不要编造。"
        f"\n已有摘要：{previous}\n新增对话：\n{transcript}"
    )
    try:
        summary = await asyncio.to_thread(
            request_chat_completion,
            api_key,
            [
                {"role": "system", "content": "你负责整理聊天记忆摘要，只输出摘要正文。"},
                {"role": "user", "content": prompt},
            ],
            endpoint=CONFIG.chat_endpoint,
            model=CONFIG.chat_model,
            timeout_seconds=CONFIG.chat_timeout_seconds,
            proxy=CONFIG.chat_proxy,
        )
    except ChatAPIError:
        return
    CHAT_STORE.set_private_summary(user_id, summary)
    CHAT_STORE.trim_private_history(user_id, keep=20)


def _private_error_message(error: ChatAPIError) -> str:
    if error.status_code == 402:
        return "你的 DeepSeek API Key 余额不足，请充值后再试。"
    if error.status_code == 401:
        return "你的 DeepSeek API Key 无效，请重新配置。"
    if error.status_code == 429:
        return "聊天请求太频繁了，请稍后再试。"
    return "聊天服务暂时不可用，请稍后再试。"


def _group_error_message(error: ChatAPIError) -> str:
    if error.status_code == 401:
        return "大肥鱼的群聊钥匙失效了，请联系管理员。"
    if error.status_code == 429:
        return "大肥鱼现在有点忙，晚点再叫我。"
    return "大肥鱼暂时没听清，稍后再试。"


def _lock_for(locks: dict[int, asyncio.Lock], owner_id: int) -> asyncio.Lock:
    lock = locks.get(owner_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[owner_id] = lock
    return lock


def _message_content(event: MessageEvent) -> str:
    parts: list[str] = []
    for segment in event.get_message():
        segment_type = getattr(segment, "type", "")
        data = getattr(segment, "data", {}) or {}
        if segment_type == "text":
            parts.append(str(data.get("text", "")))
        elif segment_type == "at":
            parts.append(f"@{data.get('qq', '')}")
        elif segment_type == "image":
            parts.append("[图片]")
        elif segment_type == "face":
            parts.append(face_marker(data.get("id", "")))
        elif segment_type == "reply":
            parts.append("[回复消息]")
        elif segment_type in {"record", "video", "file"}:
            parts.append(f"[{segment_type}]")
    return "".join(parts) or event.get_plaintext()


def _sender_name(event: GroupMessageEvent) -> str:
    sender = getattr(event, "sender", None)
    return str(
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or event.user_id
    )


async def _group_aliases(bot: Bot, event: GroupMessageEvent) -> set[str]:
    group_id = int(event.group_id)
    now = time.time()
    cached = _group_alias_cache.get(group_id)
    if cached and cached[0] > now:
        return cached[1]
    aliases = {CHAT_ROLE.name.casefold(), *(alias.casefold() for alias in CONFIG.chat_aliases)}
    try:
        result = await bot.call_api("get_group_member_info", group_id=group_id, user_id=int(bot.self_id))
        for key in ("card", "nickname"):
            value = str(result.get(key) or "").strip().casefold() if isinstance(result, dict) else ""
            if value:
                aliases.add(value)
    except Exception as error:
        LOGGER.warning(
            "获取机器人群昵称失败 group_id={} error_type={}",
            group_id,
            type(error).__name__,
        )
    _group_alias_cache[group_id] = (now + 3600, aliases)
    return aliases


def _group_reply_allowed(group_id: int) -> bool:
    now = time.time()
    last_reply = _group_chat_last_reply.get(group_id, 0.0)
    if now - last_reply < CONFIG.group_chat_reply_cooldown_seconds:
        return False
    recent = [value for value in _group_chat_proactive_replies.get(group_id, []) if now - value < 600]
    _group_chat_proactive_replies[group_id] = recent
    return len(recent) < CONFIG.group_chat_proactive_limit


def _record_group_proactive_reply(group_id: int) -> None:
    _group_chat_proactive_replies.setdefault(group_id, []).append(time.time())


def _looks_like_group_candidate(text: str, role_name: str, aliases: set[str] | None = None) -> bool:
    normalized = text.casefold()
    names = aliases or {role_name.casefold(), "大肥鱼"}
    if any(name and name in normalized for name in names):
        return True
    if any(mark in text for mark in ("？", "?")):
        return True
    return any(normalized.startswith(prefix) for prefix in ("你能", "能不能", "可以", "为什么", "怎么", "帮我"))


async def _send_chat_reply(matcher, reply: str, *, reply_message_id: int | None = None) -> None:
    for index, chunk in enumerate(split_chat_reply(reply, max_text_length=CONFIG.chat_reply_max_length)):
        if index:
            await asyncio.sleep(0.8)
        message = Message()
        if index == 0 and reply_message_id is not None:
            from nonebot.adapters.onebot.v11 import MessageSegment

            message += MessageSegment.reply(reply_message_id)
        for kind, value in chunk:
            if kind == "face":
                from nonebot.adapters.onebot.v11 import MessageSegment

                message += MessageSegment.face(int(value))
            else:
                message += value
        if message:
            await matcher.send(message)


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
