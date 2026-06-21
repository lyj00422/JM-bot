from __future__ import annotations

from typing import Any


def is_friend_private_message(event: Any) -> bool:
    """Return True for OneBot private messages sent by QQ friends."""

    return getattr(event, "message_type", None) == "private" and getattr(event, "sub_type", None) == "friend"
