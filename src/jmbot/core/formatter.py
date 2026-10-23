from __future__ import annotations

from collections.abc import Sequence

from jmbot.core.catalog_service import (
    AlbumDetail,
    RANKING_CATEGORY_LABELS,
    RANKING_PERIOD_LABELS,
    RankingQuery,
    RankingResult,
    SearchResult,
)
from jmbot.core.persistent_queue import AlbumQueueJob
from jmbot.core.user_store import FavoriteItem


def format_help(
    command_prefix: str = "/jm",
    admin_command_prefix: str = "/jmg",
    *,
    topic: str | None = None,
) -> str:
    """Return the short help index or one bounded topic page."""

    prefix = command_prefix
    topics = {
        "查找": [
            "查找漫画：",
            f"{prefix} 搜索 关键词",
            f"{prefix} 作者 作者名",
            f"{prefix} 作者 文本 作者名",
            f"{prefix} 详情 123456",
            f"{prefix} 封面 123456",
        ],
        "排行": [
            "排行榜：",
            f"{prefix} 排行 [文本] [关键词和/或类别] [榜单] [页码]",
            "榜单：日榜｜周榜｜月榜｜总榜（默认总榜）",
            f"{prefix} 排行 韩漫 周榜 2",
            f"{prefix} 排行 全彩 韩漫 月榜",
            f"{prefix} 排行 类别",
        ],
        "下载": [
            "下载与预览：",
            f"{prefix} 123456",
            f"{prefix} 123456 zip",
            f"{prefix} 123456 章节 1,3-5",
            f"{prefix} 预览 123456",
        ],
        "收藏": [
            "收藏：",
            f"{prefix} 收藏 123456",
            f"{prefix} 取消收藏 123456",
            f"{prefix} 我的收藏",
        ],
        "任务": [
            "任务：",
            f"{prefix} 进度 [123456]",
            f"{prefix} 撤回 123456",
            f"{prefix} 队列",
            f"{prefix} 我的",
            f"{prefix} 历史 5",
            f"{prefix} 历史 失败 5",
        ],
        "聊天": [
            "聊天：",
            "/ds 你的 API Key",
            "/ds 状态",
            "/ds 清除",
            "/ds 清空",
        ],
    }
    if topic is not None:
        return "\n".join(topics[topic]) if topic in topics else "可用帮助主题：查找｜排行｜下载｜收藏｜任务"
    return "\n".join(
        [
            "JMbot 帮助：",
            f"{prefix} 帮助 查找  - 搜索、作者、详情、封面",
            f"{prefix} 帮助 排行  - 排行榜、分类和筛选",
            f"{prefix} 帮助 下载  - PDF、ZIP、章节、预览",
            f"{prefix} 帮助 收藏  - 收藏管理",
            f"{prefix} 帮助 任务  - 进度、队列、撤回、历史",
            f"/ds 帮助      - 聊天配置与控制；{admin_command_prefix} 帮助 - 下载管理员命令",
        ]
    )


def format_chat_help(command_prefix: str = "/ds", *, topic: str | None = None) -> str:
    """Return the chat help index or one bounded topic page."""

    prefix = command_prefix
    topics = {
        "私聊": [
            "私聊聊天：",
            f"{prefix} 你的 API Key",
            f"{prefix} 状态",
            f"{prefix} 清除",
            f"{prefix} 清空",
        ],
        "群聊": [
            "群聊 Key（管理员私聊机器人）：",
            f"{prefix} 群聊key 群号 API_KEY",
            f"{prefix} 群聊key 群号 查看",
            f"{prefix} 群聊key 群号 清除",
        ],
        "管理": [
            "聊天开关（管理员私聊机器人）：",
            f"{prefix} 开启",
            f"{prefix} 停止",
            f"{prefix} 状态",
        ],
    }
    if topic is not None:
        key = topic.strip()
        return "\n".join(topics[key]) if key in topics else "可用帮助主题：私聊｜群聊｜管理"
    return "\n".join(
        [
            "JMbot 聊天帮助：",
            f"{prefix} 帮助 私聊  - 你的 Key、状态、清除、清空",
            f"{prefix} 帮助 群聊  - 群聊 Key 设置、查看、清除",
            f"{prefix} 帮助 管理  - 聊天开关与全局状态",
        ]
    )


def format_admin_help(admin_command_prefix: str = "/jmg") -> str:
    prefix = admin_command_prefix
    return "\n".join(
        [
            "JMbot 管理员命令：",
            f"{prefix} 清空",
            f"{prefix} kill 123456",
            f"{prefix} kill 全部 123456",
            f"{prefix} 插队 123456",
            f"{prefix} 失败",
            f"{prefix} 失败 最近 5",
            f"{prefix} 失败 21",
            f"{prefix} 失败 删除 21",
        ]
    )


def ranking_title(query: RankingQuery) -> str:
    parts = [RANKING_CATEGORY_LABELS[query.category], RANKING_PERIOD_LABELS[query.period]]
    if query.keyword:
        parts.append(f"关键词：{query.keyword}")
    return " · ".join(parts)


def build_ranking_command(
    query: RankingQuery,
    command_prefix: str = "/jm",
    *,
    page: int | None = None,
    text_mode: bool = False,
) -> str:
    parts = [command_prefix, "排行"]
    if text_mode:
        parts.append("文本")
    if query.keyword:
        parts.append(query.keyword)
    if query.category != "all":
        parts.append(RANKING_CATEGORY_LABELS[query.category])
    parts.extend([RANKING_PERIOD_LABELS[query.period], str(page or query.page)])
    return " ".join(parts)


def format_ranking_result(
    result: RankingResult,
    command_prefix: str = "/jm",
    *,
    text_mode: bool = False,
) -> str:
    title = ranking_title(result.query)
    if not result.items:
        if result.page_count and result.page > result.page_count:
            return f"{title}只有 {result.page_count} 页，请换一个页码。"
        return f"{title}没有结果，可以调整关键词、分类或榜单再试。"

    page_count = max(result.page_count, result.page)
    lines = [f"{title}：第 {result.page} / {page_count} 页，共 {result.total} 条"]
    rank_start = (result.page - 1) * result.query.page_size
    for offset, item in enumerate(result.items, start=1):
        lines.append(f"{rank_start + offset}. JM{item.album_id} {item.title}")
        if item.tags:
            lines.append(f"   标签：{' / '.join(item.tags[:6])}")
    if result.page < result.page_count:
        lines.extend(
            [
                "",
                "下一页：" + build_ranking_command(
                    result.query,
                    command_prefix,
                    page=result.page + 1,
                    text_mode=text_mode,
                ),
            ]
        )
    return "\n".join(lines)


def format_ranking_card_commands(result: RankingResult, command_prefix: str = "/jm") -> str:
    lines = ["可复制命令："]
    lines.extend(f"JM{item.album_id} 下载：{command_prefix} {item.album_id}" for item in result.items)
    if result.page < result.page_count:
        lines.append(
            "下一页：" + build_ranking_command(
                result.query,
                command_prefix,
                page=result.page + 1,
            )
        )
    return "\n".join(lines)


def format_ranking_categories(command_prefix: str = "/jm") -> str:
    return "\n".join(
        [
            "排行榜分类：",
            "全部｜同人｜单本｜短篇｜其他｜韩漫｜美漫｜Cosplay｜3D｜英文站",
            "榜单：日榜｜周榜｜月榜｜总榜",
            f"例如：{command_prefix} 排行 全彩 韩漫 周榜 2",
        ]
    )


def format_ranking_usage(command_prefix: str = "/jm") -> str:
    return "\n".join(
        [
            "排行榜格式：",
            f"{command_prefix} 排行 [文本] [关键词和/或类别] [榜单] [页码]",
            f"例如：{command_prefix} 排行 韩漫 周榜 2",
            f"分类列表：{command_prefix} 排行 类别",
        ]
    )


def format_album_detail(detail: AlbumDetail, command_prefix: str = "/jm") -> str:
    """Return compact album metadata suitable for QQ."""

    lines = [
        f"JM{detail.album_id}",
        f"标题：{detail.title}",
    ]
    if detail.authors:
        lines.append(f"作者：{' / '.join(detail.authors)}")
    if detail.page_count is not None:
        lines.append(f"页数：{detail.page_count}")
    if detail.tags:
        lines.append(f"标签：{' / '.join(detail.tags[:10])}")
    if detail.chapters:
        chapters = "、".join(
            f"{index}. {title}"
            for index, title in enumerate(detail.chapters[:10], start=1)
        )
        lines.append(f"章节：{chapters}")
    if detail.update_time is not None:
        lines.append(f"更新时间：{detail.update_time}")
    lines.extend(["", f"发送 {command_prefix} {detail.album_id} 加入下载队列"])
    return "\n".join(lines)


def format_query_failed(query_name: str) -> str:
    """Return a safe query failure without exposing upstream details."""

    return f"{query_name}查询暂时不可用，请稍后重试。"


def format_search_result(result: SearchResult, command_prefix: str = "/jm", *, limit: int | None = None) -> str:
    """Return compact search results without unreliable optional metadata."""

    prefix = command_prefix
    if not result.items:
        query_type = f"作者「{result.keyword}」作品" if result.kind == "author" else f"「{result.keyword}」"
        return f"没有搜到{query_type}，可以换个关键词再试。"

    title = (
        f"作者「{result.keyword}」作品"
        if result.kind == "author"
        else f"搜索「{result.keyword}」"
    )
    page_count = max(result.page_count, result.page)
    total = max(result.total, len(result.items))
    lines = [f"{title}：第 {result.page} / {page_count} 页，共 {total} 条"]
    visible_items = result.items if limit is None else result.items[:limit]
    for index, item in enumerate(visible_items, start=1):
        title_suffix = f" {item.title}" if item.title and item.title != item.album_id else ""
        lines.append(f"{index}. JM{item.album_id}{title_suffix}")
        if item.tags:
            lines.append(f"   标签：{' / '.join(item.tags[:6])}")

    first_id = result.items[0].album_id
    lines.append("")
    lines.append(f"详情：{prefix} 详情 {first_id}；下载：{prefix} {first_id}")
    if result.page < result.page_count:
        command = "作者" if result.kind == "author" else "搜索"
        lines.append(f"下一页：{prefix} {command} {result.keyword} {result.page + 1}")
    return "\n".join(lines)


def format_favorite_list(items: Sequence[FavoriteItem], command_prefix: str = "/jm") -> str:
    if not items:
        return f"你还没有收藏漫画。发送 {command_prefix} 收藏 123456 可以收藏。"

    lines = ["我的收藏："]
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. JM{item.album_id} 下载：{command_prefix} {item.album_id}")
    return "\n".join(lines)


def format_queue_summary(active_jobs: Sequence[AlbumQueueJob], *, limit: int = 10) -> str:
    """Return a compact summary for active queue jobs."""

    if not active_jobs:
        return "队列为空。"

    lines = [f"当前队列共 {len(active_jobs)} 个任务："]
    for job in active_jobs[:limit]:
        target = "群聊" if job.destination_type == "group" else "私聊"
        status = "处理中" if job.status == "processing" else "排队"
        lines.append(f"{job.album_id} {status}（{target}）")
    if len(active_jobs) > limit:
        lines.append(f"还有 {len(active_jobs) - limit} 个未显示。")
    return "\n".join(lines)


def format_task_started(album_id: str) -> str:
    """Return the message sent when a job starts."""

    return f"开始处理 JM{album_id}。"


def format_task_uploaded(album_id: str, filename: str) -> str:
    """Return the message sent after a successful upload."""

    return f"JM{album_id} 已上传：{filename}"


def format_task_canceled(album_id: str) -> str:
    """Return the message sent when an already-canceled job stops before upload."""

    return f"JM{album_id} 已取消，不再上传。"


def format_task_failed(album_id: str, job_id: int, error: str | None = None) -> str:
    """Return a friendly failure message without exposing raw exception text."""

    reason = _format_public_failure_reason(error)
    if reason is not None:
        return f"JM{album_id} 处理失败：{reason}"
    return f"JM{album_id} 出现技术原因，联系管理员处理 #{job_id}。"


def format_my_tasks(job_positions: Sequence[tuple[AlbumQueueJob, int]]) -> str:
    """Return active jobs for the caller's current context."""

    if not job_positions:
        return "你当前没有排队或正在处理的任务。"

    lines = ["你当前的任务："]
    for index, (job, ahead_count) in enumerate(job_positions, start=1):
        if job.status == "processing":
            detail = "正在处理"
        else:
            detail = f"前面还有 {ahead_count} 个任务"
        lines.append(f"{index}. JM{job.album_id} {job.status}，{detail}")
    return "\n".join(lines)


def format_task_history(jobs: Sequence[AlbumQueueJob], *, failed_only: bool = False) -> str:
    """Return recent task history for the caller's current context."""

    if not jobs:
        return "你最近没有失败任务。" if failed_only else "你最近没有已完成、失败或取消的任务。"

    title = "你最近失败的任务：" if failed_only else "你最近的任务："
    lines = [title]
    for index, job in enumerate(jobs, start=1):
        lines.append(f"{index}. #{job.id} JM{job.album_id} {job.status}")
    return "\n".join(lines)


def format_admin_failed_tasks(jobs: Sequence[AlbumQueueJob]) -> str:
    """Return recent failed jobs for admins."""

    if not jobs:
        return "最近没有失败任务。"

    lines = ["最近失败任务："]
    for job in jobs:
        target = f"群 {job.group_id}" if job.destination_type == "group" else f"私聊 {job.user_id}"
        error = f"：{_format_admin_error(job.error)}" if job.error else ""
        lines.append(f"#{job.id} JM{job.album_id} {target}{error}")
    return "\n".join(lines)


def format_admin_failed_detail(job: AlbumQueueJob | None) -> str:
    """Return detailed failure information for one admin-selected task."""

    if job is None or job.status != "failed":
        return "没有找到失败任务。"

    target = f"群 {job.group_id}" if job.destination_type == "group" else f"私聊 {job.user_id}"
    error = job.error or "未知错误"
    return "\n".join(
        [
            f"#{job.id} JM{job.album_id} {target}",
            f"用户：{job.user_id}",
            f"时间：{job.updated_at}",
            "失败原因：",
            error,
        ]
    )


def format_admin_failed_deleted(job_id: int, deleted: bool) -> str:
    if deleted:
        return f"已删除失败记录 #{job_id}。"
    return f"没有找到失败任务 #{job_id}。"


def is_public_nonpersistent_failure(error: str | None) -> bool:
    return _format_public_failure_reason(error) is not None


def _format_admin_error(error: str) -> str:
    if "ENOENT" in error and "/opt/jmbot/data/output/" in error:
        return "NapCat 无法访问输出文件，请检查 /opt/jmbot 挂载并重建 NapCat 容器"
    public_reason = _format_public_failure_reason(error)
    if public_reason is not None:
        return public_reason
    first_line = error.strip().splitlines()[0] if error.strip() else "未知错误"
    return first_line[:160]


def _format_public_failure_reason(error: str | None) -> str | None:
    if not error:
        return None
    if "MissingAlbumPhotoException" in error or "请求的本子不存在" in error:
        return "本子不存在或仅登录用户可见，请检查 ID；如果 ID 没错，可能需要配置 jmcomic cookies。"
    return None
