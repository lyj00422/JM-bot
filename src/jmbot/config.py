from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    """Runtime configuration loaded from environment variables."""

    host: str
    port: int
    command_prefix: str
    download_root: Path
    output_root: Path
    option_root: Path
    queue_db_path: Path
    preview_root: Path
    max_concurrency: int
    download_image_threads: int
    download_photo_threads: int
    jmcomic_log: bool
    jmcomic_retry_times: int
    job_timeout_seconds: int
    auto_split_pdf: bool
    split_image_count: int
    query_timeout_seconds: int
    query_max_concurrency: int
    detail_cache_ttl_seconds: int
    query_cache_max_entries: int
    preview_page_count: int
    preview_timeout_seconds: int
    allowed_group_ids: set[int]
    superuser_ids: set[int]
    clean_after_upload: bool
    group_folder_name: str


def parse_int_set(value: str | None) -> set[int]:
    """Parse comma-separated integer IDs from an environment variable."""

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
    """Parse common boolean spellings from an environment variable."""

    if value is None or value.strip() == "":
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    raise ValueError(f"Invalid boolean value: {value}")


def parse_positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    value = int(source.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def load_config(env: Mapping[str, str] | None = None) -> BotConfig:
    """Load configuration from environment variables."""

    source = env if env is not None else os.environ

    return BotConfig(
        host=source.get("JM_BOT_HOST", "127.0.0.1"),
        port=int(source.get("JM_BOT_PORT", "8080")),
        command_prefix=source.get("JM_BOT_COMMAND_PREFIX", "/jm"),
        download_root=Path(source.get("JM_BOT_DOWNLOAD_ROOT", "data/downloads")),
        output_root=Path(source.get("JM_BOT_OUTPUT_ROOT", "data/output")),
        option_root=Path(source.get("JM_BOT_OPTION_ROOT", "data/options")),
        queue_db_path=Path(source.get("JM_BOT_QUEUE_DB", "data/queue.sqlite3")),
        preview_root=Path(source.get("JM_BOT_PREVIEW_ROOT", "data/preview")),
        max_concurrency=parse_positive_int(source, "JM_BOT_MAX_CONCURRENCY", 1),
        download_image_threads=parse_positive_int(source, "JM_BOT_DOWNLOAD_IMAGE_THREADS", 10),
        download_photo_threads=parse_positive_int(source, "JM_BOT_DOWNLOAD_PHOTO_THREADS", 2),
        jmcomic_log=parse_bool(source.get("JM_BOT_JMCOMIC_LOG"), True),
        jmcomic_retry_times=parse_positive_int(source, "JM_BOT_JMCOMIC_RETRY_TIMES", 5),
        job_timeout_seconds=parse_positive_int(source, "JM_BOT_JOB_TIMEOUT_SECONDS", 1800),
        auto_split_pdf=parse_bool(source.get("JM_BOT_AUTO_SPLIT_PDF"), True),
        split_image_count=parse_positive_int(source, "JM_BOT_SPLIT_IMAGE_COUNT", 80),
        query_timeout_seconds=parse_positive_int(source, "JM_BOT_QUERY_TIMEOUT_SECONDS", 20),
        query_max_concurrency=parse_positive_int(source, "JM_BOT_QUERY_MAX_CONCURRENCY", 1),
        detail_cache_ttl_seconds=parse_positive_int(source, "JM_BOT_DETAIL_CACHE_TTL_SECONDS", 300),
        query_cache_max_entries=parse_positive_int(source, "JM_BOT_QUERY_CACHE_MAX_ENTRIES", 128),
        preview_page_count=parse_positive_int(source, "JM_BOT_PREVIEW_PAGE_COUNT", 10),
        preview_timeout_seconds=parse_positive_int(source, "JM_BOT_PREVIEW_TIMEOUT_SECONDS", 180),
        allowed_group_ids=parse_int_set(source.get("JM_BOT_ALLOWED_GROUPS")),
        superuser_ids=parse_int_set(source.get("JM_BOT_SUPERUSERS")),
        clean_after_upload=parse_bool(source.get("JM_BOT_CLEAN_AFTER_UPLOAD"), True),
        group_folder_name=source.get("JM_BOT_GROUP_FOLDER", "JM"),
    )
