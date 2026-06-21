from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ApiBot(Protocol):
    """只声明本模块需要的 NoneBot Bot 能力：调用 OneBot API。"""

    async def call_api(self, action: str, **params: Any) -> Any: ...


async def ensure_group_folder(bot: ApiBot, group_id: int, folder_name: str) -> str:
    """确保 QQ 群文件中存在目标文件夹，返回该文件夹 ID。"""

    existing = await _find_group_folder(bot, group_id, folder_name)
    if existing is not None:
        return existing

    await bot.call_api(
        "create_group_file_folder",
        group_id=group_id,
        name=folder_name,
        parent_id="/",
    )

    created = await _find_group_folder(bot, group_id, folder_name)
    if created is None:
        raise RuntimeError(f"创建群文件夹失败或无法读取文件夹 ID：{folder_name}")

    return created


async def upload_group_pdf(bot: ApiBot, group_id: int, file_path: str | Path, folder_name: str) -> None:
    """把生成好的 PDF 上传到指定 QQ 群文件夹。"""

    path = Path(file_path)
    folder_id = await ensure_group_folder(bot, group_id, folder_name)
    await bot.call_api(
        "upload_group_file",
        group_id=group_id,
        file=str(path.resolve()),
        name=path.name,
        folder=folder_id,
    )


async def _find_group_folder(bot: ApiBot, group_id: int, folder_name: str) -> str | None:
    """读取群文件根目录，按文件夹名称查找 QQ 返回的 folder_id。"""

    root_files = await bot.call_api("get_group_root_files", group_id=group_id)
    folders = root_files.get("folders", []) if isinstance(root_files, dict) else []

    for folder in folders:
        if not isinstance(folder, dict):
            continue

        current_name = folder.get("folder_name") or folder.get("name")
        folder_id = folder.get("folder_id") or folder.get("id")
        if current_name == folder_name and folder_id:
            return str(folder_id)

    return None
