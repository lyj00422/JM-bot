from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class JmCommand:
    """Parsed JM command."""

    album_id: str


def parse_jm_command(message: str, command_prefix: str = "/jm") -> JmCommand | None:
    """Parse `/jm <digits>` and return None for non-matching messages."""

    pattern = re.compile(rf"^\s*{re.escape(command_prefix)}\s+([0-9]+)\s*$", re.IGNORECASE)
    match = pattern.match(message)
    if match is None:
        return None

    return JmCommand(album_id=match.group(1))
