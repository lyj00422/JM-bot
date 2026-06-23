from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class JmCommand:
    """Parsed JM command."""

    album_id: str


JmAction = Literal["request", "cancel", "queue", "admin_clear", "admin_kill", "admin_jump"]


@dataclass(frozen=True)
class JmMessage:
    """Parsed JM request or management command."""

    action: JmAction
    album_id: str | None = None
    all_targets: bool = False


def parse_jm_command(message: str, command_prefix: str = "/jm") -> JmCommand | None:
    """Parse `/jm <digits>` and return None for non-matching messages."""

    pattern = re.compile(rf"^\s*{re.escape(command_prefix)}\s+([0-9]+)\s*$", re.IGNORECASE)
    match = pattern.match(message)
    if match is None:
        return None

    return JmCommand(album_id=match.group(1))


def parse_jm_message(message: str, command_prefix: str = "/jm") -> JmMessage | None:
    """Parse JM request, user queue commands, and Chinese admin subcommands."""

    request = parse_jm_command(message, command_prefix)
    if request is not None:
        return JmMessage(action="request", album_id=request.album_id)

    prefix = re.escape(command_prefix)
    patterns: list[tuple[str, JmAction, bool]] = [
        (rf"^\s*{prefix}\s+撤回\s+([0-9]+)\s*$", "cancel", False),
        (rf"^\s*{prefix}\s+管理\s+kill\s+全部\s+([0-9]+)\s*$", "admin_kill", True),
        (rf"^\s*{prefix}\s+管理\s+kill\s+([0-9]+)\s*$", "admin_kill", False),
        (rf"^\s*{prefix}\s+管理\s+插队\s+([0-9]+)\s*$", "admin_jump", False),
    ]
    for pattern, action, all_targets in patterns:
        match = re.match(pattern, message, re.IGNORECASE)
        if match is not None:
            return JmMessage(action=action, album_id=match.group(1), all_targets=all_targets)

    if re.match(rf"^\s*{prefix}\s+队列\s*$", message, re.IGNORECASE):
        return JmMessage(action="queue")

    if re.match(rf"^\s*{prefix}\s+管理\s+清空\s*$", message, re.IGNORECASE):
        return JmMessage(action="admin_clear")

    return None
