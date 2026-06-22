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
from jmbot.core.sender import upload_group_pdf, upload_private_pdf
from jmbot.core.task_runner import DuplicateTaskError, TaskRunner


CONFIG = load_config()
DOWNLOAD_SETTINGS = DownloadSettings(
    download_root=CONFIG.download_root,
    option_root=CONFIG.option_root,
    download_image_threads=CONFIG.download_image_threads,
    download_photo_threads=CONFIG.download_photo_threads,
    jmcomic_log=CONFIG.jmcomic_log,
    jmcomic_retry_times=CONFIG.jmcomic_retry_times,
)
TASK_RUNNER = TaskRunner(max_concurrency=CONFIG.max_concurrency)

nonebot.init(
    driver="~fastapi+~websockets",
    host=CONFIG.host,
    port=CONFIG.port,
    command_start={"/"},
    superusers={str(user_id) for user_id in CONFIG.superuser_ids},
)
get_driver().register_adapter(OneBotV11Adapter)


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

    await jm_matcher.send(f"收到 JM{command.album_id}，开始下载并生成 PDF。")

    try:
        result = await TASK_RUNNER.submit(
            command.album_id,
            lambda: asyncio.to_thread(run_album_job, command.album_id, DOWNLOAD_SETTINGS, CONFIG.output_root),
        )
    except DuplicateTaskError:
        await jm_matcher.finish(f"JM{command.album_id} 已在处理中，请稍等。")
        return
    except Exception as exc:
        await jm_matcher.finish(f"JM{command.album_id} 处理失败：{exc}")
        return

    if is_group_event:
        await upload_group_pdf(bot, event.group_id, result.pdf_path, CONFIG.group_folder_name)
    else:
        await upload_private_pdf(bot, event.user_id, result.pdf_path)

    if CONFIG.clean_after_upload:
        await asyncio.to_thread(cleanup_album_files, result.album_dir, result.pdf_path)
    await jm_matcher.finish(f"JM{command.album_id} 已上传：{result.pdf_path.name}")


def main() -> None:
    nonebot.run()


if __name__ == "__main__":
    main()
