from __future__ import annotations


def format_help(command_prefix: str = "/jm") -> str:
    """Return compact help for commands implemented today."""

    prefix = command_prefix
    return "\n".join(
        [
            "JMbot 可用命令：",
            f"{prefix} 123456 - 加入下载队列",
            f"{prefix} 撤回 123456 - 撤回自己的排队任务",
            f"{prefix} 队列 - 查看当前队列",
            f"{prefix} 帮助 - 查看本帮助",
            "",
            "管理员：",
            f"{prefix} 管理 清空",
            f"{prefix} 管理 kill 123456",
            f"{prefix} 管理 kill 全部 123456",
            f"{prefix} 管理 插队 123456",
        ]
    )
