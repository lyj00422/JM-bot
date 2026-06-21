from __future__ import annotations

import asyncio

import nonebot
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.rule import Rule

from config import load_config
from core.cleanup import cleanup_album_files
from core.jm_service import DownloadSettings, run_album_job
from core.parser import parse_jm_command
from core.sender import upload_group_pdf
from core.task_runner import DuplicateTaskError, TaskRunner


# bot.py 是 QQ 消息进入项目后的核心入口：
# NoneBot 负责接收 OneBot 消息，本文件负责判断消息、调用下载逻辑、上传 PDF。
CONFIG = load_config()
DOWNLOAD_SETTINGS = DownloadSettings(
    download_root=CONFIG.download_root,
    option_root=CONFIG.option_root,
)
TASK_RUNNER = TaskRunner(max_concurrency=CONFIG.max_concurrency)

# 启动 NoneBot 的 FastAPI + WebSocket 驱动，NapCat 会反向连接到这里。
nonebot.init(
    driver="~fastapi+~websockets",
    host=CONFIG.host,
    port=CONFIG.port,
    command_start={"/"},
    superusers={str(user_id) for user_id in CONFIG.superuser_ids},
)
get_driver().register_adapter(OneBotV11Adapter)


async def _is_jm_command(event: MessageEvent) -> bool:
    """判断一条 QQ 消息是否是 /jm <数字ID> 命令。"""

    return parse_jm_command(event.get_plaintext(), CONFIG.command_prefix) is not None


# matcher 是 NoneBot 的消息处理器。priority 数字越小越先处理，block=True 表示命中后不再交给后续处理器。
jm_matcher = on_message(rule=Rule(_is_jm_command), priority=10, block=True)


@jm_matcher.handle()
async def handle_jm_command(bot: Bot, event: MessageEvent) -> None:
    # 这个机器人当前只服务 QQ 群消息，私聊里不执行下载。
    if not isinstance(event, GroupMessageEvent):
        await jm_matcher.finish(f"请在 QQ 群里使用 {CONFIG.command_prefix} <漫画ID>。")
        return

    # 如果配置了群白名单，只有白名单里的群可以触发下载。
    if CONFIG.allowed_group_ids and event.group_id not in CONFIG.allowed_group_ids:
        await jm_matcher.finish("这个群还没有启用 JM 下载功能。")
        return

    command = parse_jm_command(event.get_plaintext(), CONFIG.command_prefix)
    if command is None:
        return

    await jm_matcher.send(f"收到 JM{command.album_id}，开始下载并生成 PDF。")

    try:
        # jmcomic 下载和 PDF 生成都是阻塞操作，放到线程里跑，避免卡住 NoneBot 事件循环。
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

    await upload_group_pdf(bot, event.group_id, result.pdf_path, CONFIG.group_folder_name)
    if CONFIG.clean_after_upload:
        await asyncio.to_thread(cleanup_album_files, result.album_dir, result.pdf_path)
    await jm_matcher.finish(f"JM{command.album_id} 已上传：{result.pdf_path.name}")

def main() -> None:
    nonebot.run()


if __name__ == "__main__":
    main()
