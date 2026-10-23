from __future__ import annotations

from typing import Any


def is_friend_private_message(event: Any) -> bool:
    """Return True for OneBot private messages sent by QQ friends."""

    return getattr(event, "message_type", None) == "private" and getattr(event, "sub_type", None) == "friend"


def message_segments(event: Any) -> list[Any]:
    """Return the message segments exactly as the adapter received them.

    NoneBot's OneBot v11 adapter preprocesses ``event.message`` in
    ``Bot.handle_event`` before any matcher rule runs: ``_check_reply`` drops the
    ``reply`` segment (keeping only ``event.reply`` and setting ``to_me``) and
    ``_check_at_me`` pops a leading/trailing ``@bot`` segment (setting
    ``to_me``). Both callbacks leave ``event.original_message`` untouched, so
    anything that still needs to see the raw ``@`` / ``reply`` must read it.
    """

    original = getattr(event, "original_message", None)
    if original is not None:
        return list(original)
    return list(event.get_message())


def at_targets(event: Any) -> list[str]:
    """Return every QQ id mentioned with an ``@`` segment."""

    targets: list[str] = []
    for segment in message_segments(event):
        if getattr(segment, "type", "") != "at":
            continue
        data = getattr(segment, "data", {}) or {}
        targets.append(str(data.get("qq", "")))
    return targets


def is_at_bot(event: Any, bot_id: Any) -> bool:
    """Return True when the message ``@``s the bot.

    The adapter strips the ``@bot`` segment before rules run, so the parsed
    ``to_me`` flag is checked as well; the adapter only sets it for ``@``s,
    replies to the bot (group) and every private message.
    """

    target = str(bot_id)
    if target in at_targets(event):
        return True
    return bool(getattr(event, "to_me", False))


def is_reply_to_bot(event: Any, bot_id: Any) -> bool:
    """Return True when the message quotes/replies to one of the bot's messages."""

    target = str(bot_id)
    reply = getattr(event, "reply", None)
    sender = getattr(reply, "sender", None)
    sender_id = getattr(sender, "user_id", None)
    if sender_id is not None and str(sender_id) == target:
        return True
    for segment in message_segments(event):
        if getattr(segment, "type", "") != "reply":
            continue
        data = getattr(segment, "data", {}) or {}
        for key in ("sender_id", "user_id"):
            value = data.get(key)
            if value is not None and str(value) == target:
                return True
    return False
