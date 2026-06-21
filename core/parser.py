from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class JmCommand:
    """解析成功后的 JM 命令，只保存漫画 album_id。"""

    album_id: str


def parse_jm_command(message: str, command_prefix: str = "/jm") -> JmCommand | None:
    """解析 /jm <数字ID>；不符合格式时返回 None，让其它 matcher 有机会处理。"""

    pattern = re.compile(rf"^\s*{re.escape(command_prefix)}\s+([0-9]+)\s*$", re.IGNORECASE)
    match = pattern.match(message)
    if match is None:
        return None

    return JmCommand(album_id=match.group(1))
