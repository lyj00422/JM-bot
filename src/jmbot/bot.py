from __future__ import annotations

import asyncio
import os
import random
import time
import shutil
import tempfile
from pathlib import Path
import nonebot
from nonebot import get_driver, logger, on_message, on_notice, on_request
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    NoticeEvent,
    PokeNotifyEvent,
    PrivateMessageEvent,
    FriendRequestEvent,
    GroupRequestEvent,
)
from nonebot.rule import Rule

from jmbot.core.catalog_service import RankingQuery
from jmbot.core.chat_stickers import sticker_path
from jmbot.core.chat_service import (
    CHAT_ADMIN_ID,
    ChatAPIError,
    ChatRole,
    ChatStore,
    GroupAction,
    SUPER_FACES,
    StoredMessage,
    build_group_messages,
    build_private_messages,
    extract_chat_reply,
    face_marker,
    load_chat_role,
    parse_group_action,
    request_chat_completion,
    split_chat_reply,
)
from jmbot.config import load_config, service_heartbeat_name
from jmbot.admin_app import AdminStore
from jmbot.core.conversation_state import ConversationStateStore, Scope
from jmbot.core.fair_scheduler import FairChatScheduler
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
from jmbot.core.parser import (
    JmMessage,
    parse_chat_config_message,
    is_chat_credential_message,
    is_removed_jm_admin_command,
    parse_jm_message,
)
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
from jmbot.core.process_lease import ProcessLease
_sender_lease = ProcessLease(Path(os.environ.get('JM_BOT_RUNTIME_LOCK_DIR', 'data')) / 'qq-sender.lock')
_download_lease = ProcessLease(CONFIG.queue_db_path.with_suffix('.writer.lock'))
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
SPLIT_MODE = CONFIG.chat_backend == "maibot"
if SPLIT_MODE:
    from jmbot.service_rpc import RPCClient, RemoteStore
    _service_token = os.environ["JM_BOT_SERVICE_TOKEN"]
    _chat_client = RPCClient(os.environ.get("JM_BOT_CHAT_SERVICE_URL", "http://127.0.0.1:8082"), _service_token)
    CHAT_STORE = RemoteStore(_chat_client, "credentials")
    from jmbot.resilient_admin import ResilientAdmin
    ADMIN_STORE = ResilientAdmin(os.environ.get("JM_BOT_ADMIN_SERVICE_URL", "http://127.0.0.1:8081"), _service_token)
else:
    CHAT_STORE = ChatStore(CONFIG.chat_db_path, master_key_path=CONFIG.chat_master_key_path)
    ADMIN_STORE = AdminStore(CONFIG.admin_db_path)
SERVICE_HEARTBEAT_NAME = service_heartbeat_name(CONFIG.service_role)
CONVERSATION_STORE = ConversationStateStore(os.environ.get("JM_BOT_DOWNLOAD_POLICY_DB", "data/download-policy.sqlite3")) if SPLIT_MODE else ConversationStateStore(CONFIG.conversation_db_path)
for _legacy_group_id in (() if SPLIT_MODE else CHAT_STORE.known_group_ids() | CONFIG.allowed_group_ids):
    if not CONVERSATION_STORE.has_policy(Scope("group", _legacy_group_id)):
        _is_legacy_download_group = not CONFIG.allowed_group_ids or _legacy_group_id in CONFIG.allowed_group_ids
        _was_legacy_chat_enabled = not CONFIG.allowed_group_ids or _legacy_group_id in CONFIG.allowed_group_ids
        CONVERSATION_STORE.set_policy(
            Scope("group", _legacy_group_id),
            chat_enabled=_was_legacy_chat_enabled,
            download_enabled=_is_legacy_download_group,
        )
CHAT_SCHEDULER = FairChatScheduler(
    workers=CONFIG.chat_scheduler_workers,
    per_scope_limit=CONFIG.chat_scheduler_scope_limit,
)
CHAT_ROLE = load_chat_role(CONFIG.chat_role_path)
if not SPLIT_MODE and CONVERSATION_STORE.get_default_persona("", "")[2] == 0:
    CONVERSATION_STORE.set_default_persona(CHAT_ROLE.name, CHAT_ROLE.system_prompt)
_private_chat_locks: dict[int, asyncio.Lock] = {}
_group_chat_locks: dict[int, asyncio.Lock] = {}
_group_chat_last_reply: dict[int, float] = {}
_group_chat_proactive_replies: dict[int, list[float]] = {}
_group_alias_cache: dict[int, tuple[float, set[str]]] = {}
_attention_tasks: dict[str, asyncio.Task] = {}
_attention_groups: dict[str, set[int]] = {}
_attention_examined: dict[tuple[str, int], int] = {}
DETAIL_CACHE = DetailCache(
    ttl_seconds=CONFIG.detail_cache_ttl_seconds,
    max_entries=CONFIG.query_cache_max_entries,
)
QUERY_SEMAPHORE = asyncio.Semaphore(CONFIG.query_max_concurrency)
_queue_worker_lock = asyncio.Lock()
_queue_worker_task: asyncio.Task[None] | None = None
_queue_recovered = False
LOGGER = logger


def _chat_role_for_scope(scope: Scope) -> ChatRole:
    name, default_prompt, _ = CONVERSATION_STORE.get_default_persona(
        CHAT_ROLE.name, CHAT_ROLE.system_prompt,
    )
    return ChatRole(name, CONVERSATION_STORE.effective_persona(scope, default_prompt))


def _current_default_role_name() -> str:
    return CONVERSATION_STORE.get_default_persona(CHAT_ROLE.name, CHAT_ROLE.system_prompt)[0]


def _effective_private_chat_key(user_id: int) -> tuple[str | None, str]:
    default_provider = CHAT_STORE.get_default_provider(CONFIG.chat_default_provider)
    return CHAT_STORE.effective_private_credential(
        user_id, CONFIG.chat_default_api_key, default_provider
    )


def _effective_group_chat_key(group_id: int) -> tuple[str | None, str]:
    default_provider = CHAT_STORE.get_default_provider(CONFIG.chat_default_provider)
    return CHAT_STORE.effective_group_credential(
        group_id, CONFIG.chat_default_api_key, default_provider
    )


def _chat_endpoint(provider: str) -> str:
    env_name = {
        "ds_school": "JM_BOT_CHAT_SCHOOL_ENDPOINT",
        "ds_official": "JM_BOT_CHAT_DS_ENDPOINT",
        "glm_school": "JM_BOT_CHAT_SCHOOL_ENDPOINT",
        "glm_official": "JM_BOT_CHAT_GLM_ENDPOINT",
        "kimi_school": "JM_BOT_CHAT_SCHOOL_ENDPOINT",
        "kimi_official": "JM_BOT_CHAT_KIMI_ENDPOINT",
        "qwen_school": "JM_BOT_CHAT_SCHOOL_ENDPOINT",
        "qwen_official": "JM_BOT_CHAT_QWEN_ENDPOINT",
    }.get(provider)
    configured = os.environ.get(env_name, "").strip() if env_name else ""
    defaults = {
        "ds_official": "https://api.deepseek.com/chat/completions",
        "glm_official": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "kimi_official": "https://api.moonshot.cn/v1/chat/completions",
        "qwen_official": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    }
    return configured or (CONFIG.chat_endpoint if provider.endswith("_school") else defaults.get(provider, CONFIG.chat_endpoint))


def _chat_model(provider: str) -> str:
    env_name = {
        "ds_school": "JM_BOT_CHAT_SCHOOL_MODEL",
        "ds_official": "JM_BOT_CHAT_DS_MODEL",
        "glm_school": "JM_BOT_CHAT_SCHOOL_MODEL",
        "glm_official": "JM_BOT_CHAT_GLM_MODEL",
        "kimi_school": "JM_BOT_CHAT_SCHOOL_MODEL",
        "kimi_official": "JM_BOT_CHAT_KIMI_MODEL",
        "qwen_school": "JM_BOT_CHAT_SCHOOL_MODEL",
        "qwen_official": "JM_BOT_CHAT_QWEN_MODEL",
    }.get(provider)
    configured = os.environ.get(env_name, "").strip() if env_name else ""
    defaults = {
        "ds_official": "deepseek-chat",
        "glm_official": "glm-4-flash",
        "kimi_official": "moonshot-v1-8k",
        "qwen_official": "qwen-turbo",
    }
    return configured or (CONFIG.chat_model if provider.endswith("_school") else defaults.get(provider, CONFIG.chat_model))


def _provider_command(provider: str, argument: str) -> str:
    if provider == "ds_school":
        return f"/ds 学校 {argument}"
    if provider == "ds_official":
        return f"/ds 官方 {argument}"
    command = provider.split("_", 1)[0]
    source = " 学校" if provider.endswith("_school") else ""
    return f"/{command}{source} {argument}"

nonebot.init(
    driver="~fastapi+~websockets",
    host=CONFIG.host,
    port=CONFIG.port,
    command_start={"/"},
    superusers={str(user_id) for user_id in CONFIG.superuser_ids},
)
DRIVER = get_driver()
if SPLIT_MODE:
    from jmbot.split_adapter import SplitAdapter
    DRIVER.register_adapter(SplitAdapter)
    from jmbot.download_api import mount_download_api
    mount_download_api(DRIVER.server_app, DRIVER, ALBUM_QUEUE, CONVERSATION_STORE, _service_token, CONFIG.queue_db_path)
else:
    DRIVER.register_adapter(OneBotV11Adapter)


def _install_chat_secret_log_redaction() -> None:
    # NoneBot logs OneBot events before preprocessors/matchers run. Replace the
    # adapter's content-bearing description only for credential-setting commands.
    for event_type in (GroupMessageEvent, PrivateMessageEvent):
        original = event_type.get_event_description

        def safe_description(event: MessageEvent, original=original) -> str:
            if is_chat_credential_message(event.get_plaintext()):
                location = f"@[群:{event.group_id}]" if isinstance(event, GroupMessageEvent) else "私聊"
                return f"Credential command from {event.user_id}{location} [内容已脱敏]"
            return original(event)

        event_type.get_event_description = safe_description


_install_chat_secret_log_redaction()


@DRIVER.on_bot_connect
async def _start_queue_worker_on_connect(bot: Bot) -> None:
    await asyncio.to_thread(ADMIN_STORE.heartbeat, SERVICE_HEARTBEAT_NAME, True, f"QQ {bot.self_id} connected")
    _ensure_heartbeat_worker(bot)
    if CONFIG.service_role in {"all", "download"}:
        await _ensure_queue_worker(bot)
        _ensure_approval_worker(bot)
        _ensure_control_worker(bot)
    if CONFIG.service_role in {"all", "chat"} and CONFIG.chat_backend == "nonebot":
        await CHAT_SCHEDULER.start()
        _ensure_attention_worker(bot)


@DRIVER.on_bot_disconnect
async def _stop_attention_on_disconnect(bot: Bot) -> None:
    await asyncio.to_thread(ADMIN_STORE.heartbeat, SERVICE_HEARTBEAT_NAME, False, "NapCat connection disconnected")
    heartbeat = _heartbeat_tasks.pop(str(bot.self_id), None)
    if heartbeat:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    task = _attention_tasks.pop(str(bot.self_id), None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    control = _control_tasks.pop(str(bot.self_id), None)
    if control:
        control.cancel()
        await asyncio.gather(control, return_exceptions=True)


@DRIVER.on_shutdown
async def _stop_attention_on_shutdown() -> None:
    tasks = list(_attention_tasks.values())
    _attention_tasks.clear()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await CHAT_SCHEDULER.close()
    approval_tasks = list(_approval_tasks.values())
    _approval_tasks.clear()
    for task in approval_tasks:
        task.cancel()
    await asyncio.gather(*approval_tasks, return_exceptions=True)
    control_tasks = list(_control_tasks.values())
    _control_tasks.clear()
    for task in control_tasks:
        task.cancel()
    await asyncio.gather(*control_tasks, return_exceptions=True)
    heartbeat_tasks = list(_heartbeat_tasks.values())
    _heartbeat_tasks.clear()
    for task in heartbeat_tasks:
        task.cancel()
    await asyncio.gather(*heartbeat_tasks, return_exceptions=True)
    _download_lease.close()
    _sender_lease.close()


async def _is_jm_command(event: MessageEvent) -> bool:
    """Return True when the incoming QQ message is a JM command."""

    if CONFIG.service_role == "chat":
        return False

    plaintext = event.get_plaintext()
    return bool(
        parse_jm_message(
            plaintext,
            CONFIG.command_prefix,
            default_format=CONFIG.default_format,
        )
    )


jm_matcher = on_message(rule=Rule(_is_jm_command), priority=10, block=True)


async def _is_ds_command(event: MessageEvent) -> bool:
    if CONFIG.service_role == "download" or CONFIG.chat_backend == "maibot":
        return False
    return parse_chat_config_message(event.get_plaintext()) is not None


ds_matcher = on_message(rule=Rule(_is_ds_command), priority=9, block=True)


@ds_matcher.handle()
async def handle_ds_command(bot: Bot, event: MessageEvent) -> None:
    command = parse_chat_config_message(event.get_plaintext())
    if command is None:
        return
    is_private = isinstance(event, PrivateMessageEvent) and is_friend_private_message(event)
    is_admin = int(event.user_id) == CHAT_ADMIN_ID or ADMIN_STORE.is_admin(int(event.user_id))

    if command.action == "chat_help":
        await ds_matcher.send(format_chat_help("/ds", topic=command.help_topic))
        return

    provider = command.provider or "ds_official"
    if command.action in {"chat_private_key", "chat_private_status", "chat_private_clear", "chat_clear"} or (
        command.action in {"chat_key_confirm", "chat_key_cancel"} and command.group_id is None
    ):
        if not is_private:
            await ds_matcher.send("请私聊机器人管理你的聊天配置和记录。")
            return
        if command.action == "chat_private_key":
            if not command.text:
                await ds_matcher.send("用法：/ds 你的 API Key；查看：/ds 状态；清除：/ds 清除")
                return
            if not CHAT_STORE.uses_default_private_key(event.user_id) and CHAT_STORE.has_scope_credentials("private", int(event.user_id)):
                CHAT_STORE.stage_credential_update(int(event.user_id), "private", int(event.user_id), provider, command.text)
                await ds_matcher.send(
                    "这个私聊已经在使用独立 Key，新的 Key 暂存 5 分钟。确认覆盖："
                    + _provider_command(provider, "确认")
                    + "；取消："
                    + _provider_command(provider, "取消")
                )
                return
            try:
                CHAT_STORE.replace_scope_credential("private", int(event.user_id), provider, command.text)
                CONVERSATION_STORE.set_policy(
                    Scope("private", int(event.user_id)), use_default_key=False,
                )
            except ValueError:
                await ds_matcher.send("API Key 不能为空。")
                return
            LOGGER.info("私聊聊天凭据已更新 user_id={} provider={}", event.user_id, provider)
            await ds_matcher.send(f"你的 {provider} API Key 已保存，已切换为独立 Key。")
        elif command.action == "chat_key_confirm":
            applied, applied_provider = CHAT_STORE.apply_pending_credential_update(int(event.user_id), "private", int(event.user_id))
            await ds_matcher.send(f"已确认覆盖，当前使用 {applied_provider} 独立 Key。" if applied else "没有可确认的 Key，或确认已过期。")
        elif command.action == "chat_key_cancel":
            removed = CHAT_STORE.cancel_pending_credential_update(int(event.user_id), "private", int(event.user_id))
            await ds_matcher.send("已取消待覆盖 Key。" if removed else "没有待覆盖的 Key。")
        elif command.action == "chat_private_status":
            if is_admin:
                state = "运行中" if CHAT_STORE.is_chat_enabled() else "已停止"
                await ds_matcher.send(f"聊天功能：{state}；管理员 QQ：3065532050；模型：{CONFIG.chat_model}")
            else:
                configured = CHAT_STORE.get_private_credential(event.user_id, provider) is not None
                inheritance = CHAT_STORE.uses_default_private_key(event.user_id)
                await ds_matcher.send(f"你的 {provider} Key：{'已配置' if configured else '未配置'}；默认 Key：{'开启' if inheritance else '关闭'}")
        elif command.action == "chat_private_clear":
            removed = CHAT_STORE.remove_private_credential(event.user_id, provider)
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
        CONVERSATION_STORE.set_global_enabled("chat", True)
        LOGGER.info("聊天功能已开启 admin_id={}", event.user_id)
        await ds_matcher.send("聊天功能已开启，下载功能一直可用。")
    elif command.action == "chat_disable":
        CHAT_STORE.set_chat_enabled(False)
        CONVERSATION_STORE.set_global_enabled("chat", False)
        LOGGER.info("聊天功能已停止 admin_id={}", event.user_id)
        await ds_matcher.send("聊天功能已停止，当前只保留下载功能。")
    elif command.action == "chat_status":
        state = "运行中" if CHAT_STORE.is_chat_enabled() else "已停止"
        await ds_matcher.send(f"聊天功能：{state}；管理员 QQ：3065532050；模型：{CONFIG.chat_model}")
    elif command.group_id is not None and command.action == "chat_group_key_set":
        if not CHAT_STORE.uses_default_group_key(command.group_id) and CHAT_STORE.has_scope_credentials("group", command.group_id):
            CHAT_STORE.stage_credential_update(int(event.user_id), "group", command.group_id, provider, command.text or "")
            target = f"{command.group_id}\\确认"
            cancel = f"{command.group_id}\\取消"
            await ds_matcher.send(
                "这个群已经在使用独立 Key，新的 Key 暂存 5 分钟。确认覆盖："
                + _provider_command(provider, target)
                + "；取消："
                + _provider_command(provider, cancel)
            )
            return
        CHAT_STORE.replace_scope_credential("group", command.group_id, provider, command.text or "")
        CONVERSATION_STORE.set_policy(
            Scope("group", int(command.group_id)), use_default_key=False,
        )
        LOGGER.info("群聊聊天凭据已更新 group_id={} provider={} admin_id={}", command.group_id, provider, event.user_id)
        await ds_matcher.send(f"群 {command.group_id} 的 {provider} Key 已保存，已切换为独立 Key。")
    elif command.action == "chat_key_confirm":
        if command.group_id is None:
            await ds_matcher.send("群 Key 的覆盖确认请在后台完成。")
            return
        applied, applied_provider = CHAT_STORE.apply_pending_credential_update(int(event.user_id), "group", command.group_id)
        await ds_matcher.send(f"已确认覆盖群 {command.group_id} 的 {applied_provider} Key。" if applied else "没有可确认的群 Key，或确认已过期。")
    elif command.action == "chat_key_cancel":
        if command.group_id is None:
            await ds_matcher.send("私聊请回复 /ds 取消。")
            return
        removed = CHAT_STORE.cancel_pending_credential_update(int(event.user_id), "group", command.group_id)
        await ds_matcher.send("已取消待覆盖群 Key。" if removed else "没有待覆盖的群 Key。")
    elif command.group_id is not None and command.action == "chat_group_key_status":
        key = CHAT_STORE.get_group_credential(command.group_id, provider)
        exhausted = CHAT_STORE.is_group_balance_exhausted(command.group_id)
        await ds_matcher.send(
            f"群 {command.group_id}：{provider} Key{'已配置' if key else '未配置'}；默认 Key：{'开启' if CHAT_STORE.uses_default_group_key(command.group_id) else '关闭'}；"
            f"余额状态：{'余额不足' if exhausted else '正常'}"
        )
    elif command.group_id is not None and command.action == "chat_group_key_clear":
        removed = CHAT_STORE.remove_group_credential(command.group_id, provider)
        LOGGER.info("群聊 API Key 已清除 group_id={} admin_id={}", command.group_id, event.user_id)
        await ds_matcher.send(f"群 {command.group_id} 的聊天 Key 已{'清除' if removed else '不存在'}。")
    elif command.action == "chat_key_confirm" and command.group_id is not None:
        applied, applied_provider = CHAT_STORE.apply_pending_credential_update(
            int(event.user_id), "group", command.group_id
        )
        await ds_matcher.send(
            f"已确认覆盖群 {command.group_id} 的 {applied_provider} Key。"
            if applied else "没有可确认的群 Key，或确认已过期。"
        )
    elif command.action == "chat_key_cancel" and command.group_id is not None:
        removed = CHAT_STORE.cancel_pending_credential_update(
            int(event.user_id), "group", command.group_id
        )
        await ds_matcher.send("已取消待覆盖群 Key。" if removed else "没有待覆盖的群 Key。")


async def _is_chat_message(event: MessageEvent) -> bool:
    if CONFIG.service_role == "download" or CONFIG.chat_backend == "maibot":
        return False
    if not CONVERSATION_STORE.global_enabled("chat"):
        return False
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
    if isinstance(event, GroupMessageEvent):
        policy = CONVERSATION_STORE.get_policy(Scope("group", int(event.group_id)))
    else:
        policy = CONVERSATION_STORE.get_policy(Scope("private", int(event.user_id)))
    if not policy.chat_enabled or policy.chat_blacklisted:
        return False
    plaintext = event.get_plaintext()
    if is_removed_jm_admin_command(plaintext, CONFIG.admin_command_prefix):
        return False
    if parse_jm_message(plaintext, CONFIG.command_prefix, default_format=CONFIG.default_format):
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
        policy = CONVERSATION_STORE.get_policy(Scope("group", int(event.group_id)))
        if not policy.chat_enabled or policy.chat_blacklisted:
            return
        text = _message_content(event).casefold()
        direct = is_at_bot(event, bot.self_id) or bool(getattr(event, "reply", None)) or any(
            alias.casefold() in text for alias in (_current_default_role_name(), *CONFIG.chat_aliases)
        )
        try:
            await CHAT_SCHEDULER.submit(
                f"group:{int(event.group_id)}",
                lambda: _handle_group_chat(bot, event),
                passive=not direct,
                timeout=CONFIG.chat_timeout_seconds + 15,
            )
        except asyncio.QueueFull:
            if direct:
                await chat_matcher.send("聊天队列正在处理其他会话，请稍后再试。")
    elif isinstance(event, PrivateMessageEvent) and is_friend_private_message(event):
        policy = CONVERSATION_STORE.get_policy(Scope("private", int(event.user_id)))
        if not policy.chat_enabled or policy.chat_blacklisted:
            return
        try:
            await CHAT_SCHEDULER.submit(
                f"private:{int(event.user_id)}",
                lambda: _handle_private_chat(bot, event),
                timeout=CONFIG.chat_timeout_seconds + 15,
            )
        except asyncio.QueueFull:
            await chat_matcher.send("聊天队列正在处理其他会话，请稍后再试。")


async def _is_group_poke(event: NoticeEvent) -> bool:
    """True when a group member pokes this bot (not another member)."""

    return (
        isinstance(event, PokeNotifyEvent)
        and event.group_id is not None
        and int(event.target_id) == int(event.self_id)
        and int(event.user_id) != int(event.self_id)
    )


poke_matcher = on_notice(rule=Rule(_is_group_poke), priority=30, block=False)


@poke_matcher.handle()
async def handle_group_poke(bot: Bot, event: PokeNotifyEvent) -> None:
    if CONFIG.service_role == "download" or CONFIG.chat_backend == "maibot":
        return
    group_id = int(event.group_id)
    if not CHAT_STORE.is_chat_enabled() or not CONVERSATION_STORE.global_enabled("chat"):
        return
    policy = CONVERSATION_STORE.get_policy(Scope("group", int(event.group_id)))
    if not policy.chat_enabled or policy.chat_blacklisted:
        return
    await _handle_group_poke(bot, event)


request_matcher = on_request(priority=5, block=False)


@request_matcher.handle()
async def capture_approval_request(event: FriendRequestEvent | GroupRequestEvent) -> None:
    if CONFIG.service_role == "chat":
        return
    if isinstance(event, FriendRequestEvent):
        await asyncio.to_thread(ADMIN_STORE.upsert_approval,
            "friend", str(event.user_id), "pending",
            {"flag": event.flag, "comment": event.comment or ""},
        )
        return
    if isinstance(event, GroupRequestEvent):
        if not CONVERSATION_STORE.has_policy(Scope("group", int(event.group_id))):
            CONVERSATION_STORE.set_policy(Scope("group", int(event.group_id)), chat_enabled=False, download_enabled=False)
        await asyncio.to_thread(ADMIN_STORE.upsert_approval,
            "group", str(event.group_id), "pending",
            {"flag": event.flag, "sub_type": event.sub_type, "user_id": event.user_id, "comment": event.comment or ""},
        )


_approval_tasks: dict[str, asyncio.Task[None]] = {}
_control_tasks: dict[str, asyncio.Task[None]] = {}
_heartbeat_tasks: dict[str, asyncio.Task[None]] = {}


def _ensure_heartbeat_worker(bot: Bot) -> None:
    bot_id = str(bot.self_id)
    task = _heartbeat_tasks.get(bot_id)
    if task is None or task.done():
        _heartbeat_tasks[bot_id] = asyncio.create_task(
            _heartbeat_worker(bot), name=f"service-heartbeat-{bot_id}",
        )


async def _heartbeat_worker(bot: Bot) -> None:
    try:
        while True:
            await asyncio.to_thread(ADMIN_STORE.heartbeat, SERVICE_HEARTBEAT_NAME, True, f"QQ {bot.self_id} connected")
            await asyncio.sleep(15)
    except asyncio.CancelledError:
        await asyncio.to_thread(ADMIN_STORE.heartbeat, SERVICE_HEARTBEAT_NAME, False, "NapCat connection disconnected")
        raise


def _ensure_approval_worker(bot: Bot) -> None:
    if CONFIG.service_role == "chat":
        return
    bot_id = str(bot.self_id)
    task = _approval_tasks.get(bot_id)
    if task is None or task.done():
        _approval_tasks[bot_id] = asyncio.create_task(_approval_worker(bot), name=f"approval-outbox-{bot_id}")


async def _approval_worker(bot: Bot) -> None:
    while True:
        for action in await asyncio.to_thread(ADMIN_STORE.pending_approval_actions):
            try:
                if not action.get('napcat_applied') and not await asyncio.to_thread(ADMIN_STORE.begin_approval, int(action['id'])):
                    continue
                if not action.get('napcat_applied') and action["kind"] == "friend":
                    await bot.call_api(
                        "set_friend_add_request", flag=action["flag"],
                        approve=action["decision"] == "approve",
                    )
                elif not action.get('napcat_applied'):
                    await bot.call_api(
                        "set_group_add_request", flag=action["flag"], sub_type=action["sub_type"],
                        approve=action["decision"] == "approve",
                    )
                if not action.get('napcat_applied'):
                    await asyncio.to_thread(ADMIN_STORE.acknowledge_approval, int(action['id']))
                if action["decision"] == "approve":
                    scope = Scope("private" if action["kind"] == "friend" else "group", int(action["subject_id"]))
                    if SPLIT_MODE:
                        if not await asyncio.to_thread(_chat_client.call, "state", "has_policy", scope):
                            await asyncio.to_thread(_chat_client.call, "state", "set_policy", scope, chat_enabled=action["kind"] == "friend")
                    elif not CONVERSATION_STORE.has_policy(scope):
                        CONVERSATION_STORE.set_policy(scope, chat_enabled=action["kind"] == "friend", download_enabled=False)
            except Exception as error:
                try:
                    await asyncio.to_thread(ADMIN_STORE.complete_approval_action, int(action["id"]), type(error).__name__)
                except RuntimeError:
                    pass
                LOGGER.warning("审批操作待重试 action_id={} kind={} error_type={}", action["id"], action["kind"], type(error).__name__)
            else:
                try:
                    await asyncio.to_thread(ADMIN_STORE.complete_approval_action, int(action["id"]))
                except RuntimeError:
                    pass
                LOGGER.info("审批操作已应用 action_id={} kind={} subject_id={}", action["id"], action["kind"], action["subject_id"])
        await asyncio.sleep(2)


def _ensure_control_worker(bot: Bot) -> None:
    if CONFIG.service_role == "chat":
        return
    bot_id = str(bot.self_id)
    task = _control_tasks.get(bot_id)
    if task is None or task.done():
        _control_tasks[bot_id] = asyncio.create_task(
            _control_action_worker(), name=f"admin-control-outbox-{bot_id}",
        )


async def _control_action_worker() -> None:
    while True:
        for action in await asyncio.to_thread(ADMIN_STORE.claim_control_actions):
            action_id = int(action["id"])
            payload = action["payload"]
            try:
                if action["name"] == "download_cancel":
                    job_id = int(payload["job_id"])
                    changed = ALBUM_QUEUE.admin_cancel_job(job_id)
                    result = {"job_id": job_id, "changed": changed}
                elif action["name"] == "download_clear":
                    result = {"canceled": ALBUM_QUEUE.clear_pending()}
                elif action["name"] == "download_clear_finished":
                    result = {"deleted": ALBUM_QUEUE.clear_finished()}
                elif action["name"] == "download_priority":
                    job_id = int(payload["job_id"])
                    job = ALBUM_QUEUE.move_to_front(job_id)
                    result = {"job_id": job.id, "priority": job.priority}
                elif action["name"] == "download_delete_failed":
                    job_id = int(payload["job_id"])
                    result = {"job_id": job_id, "deleted": ALBUM_QUEUE.delete_failed_job(job_id)}
                else:
                    raise ValueError("unsupported control action")
            except Exception as error:
                try:
                    await asyncio.to_thread(ADMIN_STORE.complete_control_action, action_id, error=type(error).__name__)
                except RuntimeError:
                    pass
                LOGGER.warning(
                    "后台下载操作失败 action_id={} action={} error_type={}",
                    action_id, action["name"], type(error).__name__,
                )
            else:
                try:
                    await asyncio.to_thread(ADMIN_STORE.complete_control_action, action_id, result=result)
                except RuntimeError:
                    pass
                LOGGER.info("后台下载操作已应用 action_id={} action={}", action_id, action["name"])
        await asyncio.sleep(1)


@jm_matcher.handle()
async def handle_jm_command(bot: Bot, event: MessageEvent) -> None:
    is_group_event = isinstance(event, GroupMessageEvent)
    is_friend_private_event = isinstance(event, PrivateMessageEvent) and is_friend_private_message(event)

    if not is_group_event and not is_friend_private_event:
        await jm_matcher.finish(f"请在 QQ 群或机器人好友私聊里使用 {CONFIG.command_prefix} <漫画ID>。")
        return

    if not CONVERSATION_STORE.global_enabled("download"):
        await jm_matcher.finish("JM 下载功能目前已关闭。")
        return

    scope = Scope("group", int(event.group_id)) if is_group_event else Scope("private", int(event.user_id))
    policy = CONVERSATION_STORE.get_policy(scope)
    if policy.download_blacklisted:
        # A blacklist suppresses the download domain completely and must not
        # disclose that a private/group scope is configured.
        return
    if not policy.download_enabled:
        await jm_matcher.finish("此会话尚未启用 JM 下载功能。")
        return

    command = parse_jm_message(
        event.get_plaintext(),
        CONFIG.command_prefix,
        default_format=CONFIG.default_format,
    )
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
        configured_help = ADMIN_STORE.get_help()
        if configured_help == "发送 /jm help 查看下载帮助。":
            configured_help = format_help(CONFIG.command_prefix, None, topic=command.help_topic)
        await jm_matcher.send(
            configured_help
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
    api_key, provider = _effective_private_chat_key(user_id)
    if not api_key:
        LOGGER.warning("私聊跳过：未配置私聊 Key user_id={}", user_id)
        await chat_matcher.send("当前私聊没有可用聊天 Key。可发送 /ds、/glm、/kimi 或 /qwen 配置独立 Key。")
        return

    text = _message_content(event).strip()
    LOGGER.info("私聊处理开始 user_id={} provider={} model={}", user_id, provider, _chat_model(provider))
    lock = _lock_for(_private_chat_locks, user_id)
    async with lock:
        messages = build_private_messages(
            _chat_role_for_scope(Scope("private", user_id)),
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
                endpoint=_chat_endpoint(provider),
                model=_chat_model(provider),
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
            await _compact_private_history(user_id, api_key, provider)


async def _handle_group_chat(bot: Bot, event: GroupMessageEvent) -> None:
    group_id = int(event.group_id)
    if str(event.user_id) == str(bot.self_id):
        return
    _attention_groups.setdefault(str(bot.self_id), set()).add(group_id)
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
    current_message_id = CHAT_STORE.append_group_message(
        group_id, sender_name, text, user_id=int(event.user_id), message_id=int(event.message_id),
    )

    if CHAT_STORE.is_group_balance_exhausted(group_id):
        LOGGER.warning("群聊跳过：API 余额标记不足 group_id={} mentioned={}", group_id, mentioned)
        if mentioned:
            await chat_matcher.send("大肥鱼被饿死了")
        return

    api_key, provider = _effective_group_chat_key(group_id)
    if not api_key:
        LOGGER.warning("群聊跳过：未配置群 Key group_id={} mentioned={}", group_id, mentioned)
        if mentioned:
            await chat_matcher.send("这个群还没有配置聊天 API Key。")
        return

    active = CHAT_STORE.is_group_active(group_id)
    aliases = await _group_aliases(bot, event)
    alias_match = any(alias and alias in text.casefold() for alias in aliases)
    replied_to_bot = is_reply_to_bot(event, bot.self_id)
    direct = mentioned or replied_to_bot or alias_match
    # Do not interrupt member-to-member @ / quoted exchanges without explicit address.
    if not direct and (at_targets(event) or getattr(event, "reply", None)):
        return
    LOGGER.info(
        "群聊触发判断 group_id={} mentioned={} replied_to_bot={} alias_match={} active={}",
        group_id,
        mentioned,
        replied_to_bot,
        alias_match,
        active,
    )
    if not direct and not active:
        return

    lock = _lock_for(_group_chat_locks, group_id)
    async with lock:
        if (
            not CHAT_STORE.is_chat_enabled()
            or _effective_group_chat_key(group_id) != (api_key, provider)
            or CHAT_STORE.is_group_balance_exhausted(group_id)
        ):
            return
        if (
            not direct
            and CHAT_STORE.latest_group_message_id(group_id) != current_message_id
        ):
            LOGGER.info("群聊跳过：被新消息覆盖的排队回复 group_id={} message_id={}", group_id, current_message_id)
            return
        if not direct:
            if not CHAT_STORE.is_group_active(group_id):
                return
            delay = CONFIG.group_chat_followup_cooldown_seconds - (time.time() - _group_chat_last_reply.get(group_id, 0))
            if delay > 0:
                await asyncio.sleep(delay)
            if CHAT_STORE.latest_group_message_id(group_id) != current_message_id:
                return
        history = CHAT_STORE.group_history(
            group_id, limit=CONFIG.group_chat_history_limit, max_age_seconds=CONFIG.group_chat_context_seconds,
            up_to_id=current_message_id,
        )
        # The trigger is already in history, including its platform metadata.
        messages = build_group_messages(
            _chat_role_for_scope(Scope("group", group_id)),
            history,
            sender_name,
            f"请回应消息ID={int(event.message_id)}：{text[:1200]}",
            decision_required=not direct,
            attention_mode="direct" if direct else "continuation",
        )
        try:
            LOGGER.info(
                "群聊请求 DeepSeek group_id={} model={} endpoint_host={} trigger={}",
                group_id,
                _chat_model(provider),
                _chat_endpoint(provider).split("/", 3)[2] if "://" in _chat_endpoint(provider) else "invalid",
                "at" if mentioned else "passive",
            )
            raw_reply = await asyncio.to_thread(
                request_chat_completion,
                api_key,
                messages,
                endpoint=_chat_endpoint(provider),
                model=_chat_model(provider),
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
            if error.status_code == 402 and _effective_group_chat_key(group_id) == (api_key, provider):
                CHAT_STORE.mark_group_balance_exhausted(group_id, str(error))
                if mentioned:
                    await chat_matcher.send("大肥鱼被饿死了")
            elif mentioned:
                await chat_matcher.send(_group_error_message(error))
            return

        if not CHAT_STORE.is_chat_enabled() or _effective_group_chat_key(group_id) != (api_key, provider):
            return
        if not direct and CHAT_STORE.latest_group_message_id(group_id) != current_message_id:
            return
        _attention_examined[(str(bot.self_id), group_id)] = current_message_id
        delivered = await _deliver_group_action(
            bot, group_id, parse_group_action(raw_reply, direct=direct), history,
            current_user_id=int(event.user_id),
            reply_message_id=int(event.message_id) if direct else None,
        )
        LOGGER.info(
            "群聊互动完成 group_id={} trigger={} delivered={}",
            group_id, "direct" if direct else "continuation", delivered,
        )


async def _group_member_name(bot: Bot, group_id: int, user_id: int) -> str:
    try:
        result = await bot.call_api("get_group_member_info", group_id=group_id, user_id=user_id)
    except Exception as error:
        LOGGER.warning(
            "获取群成员昵称失败 group_id={} user_id={} error_type={}",
            group_id, user_id, type(error).__name__,
        )
        return str(user_id)
    if isinstance(result, dict):
        name = str(result.get("card") or result.get("nickname") or "").strip()
        if name:
            return name
    return str(user_id)


async def _handle_group_poke(bot: Bot, event: PokeNotifyEvent) -> None:
    """A poke at the bot wakes it and opens the active window immediately."""

    group_id = int(event.group_id)
    user_id = int(event.user_id)
    if str(user_id) == str(bot.self_id):
        return
    api_key, provider = _effective_group_chat_key(group_id)
    if not api_key:
        LOGGER.info("群聊戳一戳忽略：未配置群 Key group_id={}", group_id)
        return
    if CHAT_STORE.is_group_balance_exhausted(group_id):
        LOGGER.info("群聊戳一戳忽略：API 余额标记不足 group_id={}", group_id)
        return

    # 被戳一戳属于三种主动唤醒之一：即使模型请求失败也先打开活跃期。
    CHAT_STORE.set_group_active_until(group_id, time.time() + CONFIG.group_chat_active_seconds)
    LOGGER.info("群聊戳一戳唤醒 group_id={} user_id={}", group_id, user_id)

    lock = _lock_for(_group_chat_locks, group_id)
    async with lock:
        if (
            not CHAT_STORE.is_chat_enabled()
            or _effective_group_chat_key(group_id) != (api_key, provider)
            or CHAT_STORE.is_group_balance_exhausted(group_id)
        ):
            return
        if time.time() - _group_chat_last_reply.get(group_id, 0.0) < CONFIG.group_chat_reply_cooldown_seconds:
            LOGGER.info("群聊戳一戳冷却中 group_id={} user_id={}", group_id, user_id)
            return
        sender_name = await _group_member_name(bot, group_id, user_id)
        current_message_id = CHAT_STORE.append_group_message(
            group_id, sender_name, "（戳了戳你）", user_id=user_id,
        )
        history = CHAT_STORE.group_history(
            group_id,
            limit=CONFIG.group_chat_history_limit,
            max_age_seconds=CONFIG.group_chat_context_seconds,
            up_to_id=current_message_id,
        )
        messages = build_group_messages(
            _chat_role_for_scope(Scope("group", group_id)),
            history,
            sender_name,
            f"（{sender_name} 戳了戳你）",
            decision_required=False,
            attention_mode="direct",
        )
        try:
            raw_reply = await asyncio.to_thread(
                request_chat_completion,
                api_key,
                messages,
                endpoint=_chat_endpoint(provider),
                model=_chat_model(provider),
                timeout_seconds=CONFIG.chat_timeout_seconds,
                proxy=CONFIG.chat_proxy,
                json_output=True,
            )
        except ChatAPIError as error:
            _attention_examined[(str(bot.self_id), group_id)] = current_message_id
            LOGGER.error(
                "群聊戳一戳 DeepSeek 请求失败 group_id={} status_code={} error_type={}",
                group_id, error.status_code, type(error).__name__,
            )
            if error.status_code == 402 and _effective_group_chat_key(group_id) == (api_key, provider):
                CHAT_STORE.mark_group_balance_exhausted(group_id, str(error))
            return

        if not CHAT_STORE.is_chat_enabled() or _effective_group_chat_key(group_id) != (api_key, provider):
            return
        _attention_examined[(str(bot.self_id), group_id)] = current_message_id
        delivered = await _deliver_group_action(
            bot, group_id, parse_group_action(raw_reply, direct=True), history,
            current_user_id=user_id,
        )
        LOGGER.info(
            "群聊戳一戳互动完成 group_id={} user_id={} delivered={}",
            group_id, user_id, delivered,
        )


async def _compact_private_history(user_id: int, api_key: str, provider: str) -> None:
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
            endpoint=_chat_endpoint(provider),
            model=_chat_model(provider),
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
    current_name = _current_default_role_name().casefold()
    if cached and cached[0] > now and current_name in cached[1]:
        return cached[1]
    aliases = {current_name, *(alias.casefold() for alias in CONFIG.chat_aliases)}
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


def _ensure_attention_worker(bot: Bot) -> None:
    if CONFIG.service_role == "download" or CONFIG.chat_backend == "maibot" or not CONFIG.group_chat_observe_enabled:
        return
    bot_id = str(bot.self_id)
    task = _attention_tasks.get(bot_id)
    if task is None or task.done():
        _attention_tasks[bot_id] = asyncio.create_task(_attention_loop(bot))


async def _attention_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(random.uniform(
            CONFIG.group_chat_observe_min_seconds, CONFIG.group_chat_observe_max_seconds,
        ))
        for group_id in tuple(_attention_groups.get(str(bot.self_id), ())):
            try:
                await _observe_group(bot, group_id)
            except Exception:
                LOGGER.exception("群聊随机观察失败 group_id={}", group_id)


async def _observe_group(bot: Bot, group_id: int) -> None:
    if not CONFIG.group_chat_observe_enabled or not CHAT_STORE.is_chat_enabled():
        return
    policy_scope = Scope("group", group_id)
    policy = CONVERSATION_STORE.get_policy(policy_scope)
    # Use the same scope gate as the message matcher.  An unknown group has a
    # disabled default policy, so it must not trigger proactive model calls just
    # because it remains in the in-memory attention set.
    if not CONVERSATION_STORE.global_enabled("chat") or not policy.chat_enabled or policy.chat_blacklisted:
        return
    lock = _lock_for(_group_chat_locks, group_id)
    if lock.locked():
        return
    async with lock:
        # 活跃期内的普通消息已由接话逻辑逐条判断，观察任务不再重复请求模型。
        if CHAT_STORE.is_group_active(group_id):
            return
        if CHAT_STORE.is_group_balance_exhausted(group_id) or not _group_reply_allowed(group_id):
            return
        api_key, provider = _effective_group_chat_key(group_id)
        if not api_key:
            return
        cursor = CHAT_STORE.latest_group_message_id(group_id)
        key = (str(bot.self_id), group_id)
        if cursor is None or cursor <= _attention_examined.get(key, 0):
            return
        history = CHAT_STORE.group_history(
            group_id, limit=CONFIG.group_chat_history_limit,
            max_age_seconds=min(CONFIG.group_chat_context_seconds, 180),
        )
        _attention_examined[key] = cursor
        users = [item for item in history if item.role == "user" and item.user_id != int(bot.self_id)]
        if not users:
            return
        current = users[-1]
        messages = build_group_messages(
            _chat_role_for_scope(Scope("group", group_id)), history, "观察提示", "判断是否参与上面最新话题或进行互动；没有必要就保持沉默。",
            attention_mode="observe",
        )
    # A background model request must not hold up explicit mentions. Validate
    # its snapshot again under the group lock before performing any actions.
    try:
        raw = await asyncio.to_thread(
            request_chat_completion, api_key, messages, endpoint=_chat_endpoint(provider),
            model=_chat_model(provider), timeout_seconds=CONFIG.chat_timeout_seconds,
            proxy=CONFIG.chat_proxy, json_output=True,
        )
    except ChatAPIError as error:
        if error.status_code == 402 and _effective_group_chat_key(group_id) == (api_key, provider):
            CHAT_STORE.mark_group_balance_exhausted(group_id)
        LOGGER.warning("群聊观察请求失败 group_id={} status_code={}", group_id, error.status_code)
        return
    async with lock:
        if (
            not CHAT_STORE.is_chat_enabled()
            or _effective_group_chat_key(group_id) != (api_key, provider)
            or CHAT_STORE.latest_group_message_id(group_id) != cursor
            or not _group_reply_allowed(group_id)
        ):
            return
        delivered = await _deliver_group_action(
            bot, group_id, parse_group_action(raw), history, current_user_id=current.user_id,
        )
        if delivered:
            _record_group_proactive_reply(group_id)


def _chat_reply_messages(reply: str, reply_message_id: int | None = None):
    from nonebot.adapters.onebot.v11 import MessageSegment

    first = True
    for chunk in split_chat_reply(reply, max_text_length=CONFIG.chat_reply_max_length):
        message = Message()
        stored_parts = []
        for kind, value in chunk:
            if kind == "sticker":
                path = sticker_path(value)
                if path is None:
                    continue
                try:
                    image = path.read_bytes()
                except OSError:
                    LOGGER.warning("聊天表情包读取失败 sticker_id={}", value)
                    continue
                # Bytes work when NapCat and the bot do not share a filesystem.
                message += MessageSegment.image(image)
                stored_parts.append(f"[sticker:{value}]")
            elif kind == "face":
                message += MessageSegment.face(int(value))
                stored_parts.append(f"[qq_face:{value}]")
            else:
                message += MessageSegment.text(value)
                stored_parts.append(value)
        if message:
            if first and reply_message_id is not None:
                message = Message(MessageSegment.reply(reply_message_id)) + message
            first = False
            yield message, "".join(stored_parts)


async def _deliver_group_action(
    bot: Bot, group_id: int, action: GroupAction, history: list[StoredMessage], *,
    current_user_id: int | None, reply_message_id: int | None = None,
) -> bool:
    delivered = False
    for index, (message, stored_text) in enumerate(_chat_reply_messages(action.reply, reply_message_id)):
        if index:
            await asyncio.sleep(0.8)
        result = await bot.call_api("send_group_msg", group_id=group_id, message=message)
        platform_id = result.get("message_id") if isinstance(result, dict) else None
        CHAT_STORE.append_group_message(
            group_id, CHAT_ROLE.name, stored_text, user_id=int(bot.self_id),
            message_id=platform_id, role="assistant",
        )
        delivered = True
        CHAT_STORE.set_group_active_until(group_id, time.time() + CONFIG.group_chat_active_seconds)
        _group_chat_last_reply[group_id] = time.time()
    eligible = {
        item.message_id: item for item in history
        if item.role == "user" and item.message_id is not None and item.user_id != int(bot.self_id)
    }
    if action.reaction and action.reaction[0] in eligible and action.reaction[1] in SUPER_FACES:
        message_id, emoji_id = action.reaction
        if not CHAT_STORE.interaction_sent(group_id, message_id, "reaction"):
            try:
                await bot.call_api("set_msg_emoji_like", message_id=message_id, emoji_id=str(emoji_id), set=True)
                CHAT_STORE.record_interaction(group_id, message_id, "reaction")
                delivered = True
            except Exception as error:
                LOGGER.warning("群聊表情回应失败 group_id={} error_type={}", group_id, type(error).__name__)
    current = next((item for item in reversed(history) if item.role == "user"), None)
    if (
        action.poke and current_user_id is not None and current_user_id != int(bot.self_id)
        and current and current.user_id == current_user_id and current.message_id is not None
        and not CHAT_STORE.interaction_sent(group_id, current.message_id, "poke")
    ):
        try:
            await bot.call_api("group_poke", group_id=group_id, user_id=current_user_id)
            CHAT_STORE.record_interaction(group_id, current.message_id, "poke")
            delivered = True
        except Exception as error:
            LOGGER.warning("群聊戳一戳失败 group_id={} error_type={}", group_id, type(error).__name__)
    if delivered:
        CHAT_STORE.set_group_active_until(group_id, time.time() + CONFIG.group_chat_active_seconds)
        _group_chat_last_reply[group_id] = time.time()
    return delivered


async def _send_chat_reply(matcher, reply: str, *, reply_message_id: int | None = None) -> None:
    for index, (message, _) in enumerate(_chat_reply_messages(reply, reply_message_id)):
        if index:
            await asyncio.sleep(0.8)
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

    if CONFIG.service_role == "chat":
        return

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
            is_canceled=_job_canceled,
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
            cancel_checker=lambda: _job_canceled(job.id),
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
    if _job_canceled(job.id):
        return
    if job.destination_type == "group":
        if job.group_id is None:
            raise ValueError(f"Group job {job.id} is missing group_id")
        await bot.call_api("send_group_msg", group_id=job.group_id, message=message)
    else:
        await bot.call_api("send_private_msg", user_id=job.user_id, message=message)


async def _cleanup_job_files(album_dir, pdf_path) -> None:
    await asyncio.to_thread(cleanup_album_files, album_dir, pdf_path)


def _job_canceled(job_id: int) -> bool:
    if ALBUM_QUEUE.is_canceled(job_id):
        return True
    try:
        job = ALBUM_QUEUE.get_job(job_id)
        if job is None:
            return True
        scope = Scope("group", job.group_id) if job.destination_type == "group" else Scope("private", job.user_id)
        if not CONVERSATION_STORE.permitted(scope, "download"):
            ALBUM_QUEUE.admin_cancel_job(job_id)
            return True
        return False
    except Exception:
        return True


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
