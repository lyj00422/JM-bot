from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    """机器人运行配置，主要来自 scripts/start_bot.ps1 设置的环境变量。"""

    host: str
    port: int
    command_prefix: str
    download_root: Path
    output_root: Path
    option_root: Path
    max_concurrency: int
    allowed_group_ids: set[int]
    superuser_ids: set[int]
    clean_after_upload: bool
    group_folder_name: str


def parse_int_set(value: str | None) -> set[int]:
    """把类似 "10001,10002" 的环境变量解析成整数集合。"""

    if value is None or value.strip() == "":
        return set()

    result: set[int] = set()
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        result.add(int(item))

    return result


def parse_bool(value: str | None, default: bool) -> bool:
    """解析开关类环境变量，支持 true/false、yes/no、1/0 等写法。"""

    if value is None or value.strip() == "":
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise ValueError(f"Invalid boolean value: {value}")


def load_config(env: Mapping[str, str] | None = None) -> BotConfig:
    """从环境变量读取配置；测试时可以传入 env 字典来模拟环境。"""

    source = env if env is not None else os.environ
    max_concurrency = int(source.get("JM_BOT_MAX_CONCURRENCY", "1"))
    if max_concurrency < 1:
        raise ValueError("JM_BOT_MAX_CONCURRENCY must be at least 1")

    return BotConfig(
        host=source.get("JM_BOT_HOST", "127.0.0.1"),
        port=int(source.get("JM_BOT_PORT", "8080")),
        command_prefix=source.get("JM_BOT_COMMAND_PREFIX", "/jm"),
        download_root=Path(source.get("JM_BOT_DOWNLOAD_ROOT", "data/downloads")),
        output_root=Path(source.get("JM_BOT_OUTPUT_ROOT", "data/output")),
        option_root=Path(source.get("JM_BOT_OPTION_ROOT", "data/options")),
        max_concurrency=max_concurrency,
        allowed_group_ids=parse_int_set(source.get("JM_BOT_ALLOWED_GROUPS")),
        superuser_ids=parse_int_set(source.get("JM_BOT_SUPERUSERS")),
        clean_after_upload=parse_bool(source.get("JM_BOT_CLEAN_AFTER_UPLOAD"), True),
        group_folder_name=source.get("JM_BOT_GROUP_FOLDER", "JM"),
    )
