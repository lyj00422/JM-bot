"""Small raw-event filters that run before message decoding and media queries."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence


_CQ_CODE_PATTERN = re.compile(r"\[CQ:[^]]*\]", re.IGNORECASE)


def _raw_text(payload: Mapping[str, object]) -> str:
    message = payload.get("message")
    if isinstance(message, Sequence) and not isinstance(message, (str, bytes, bytearray)):
        parts: list[str] = []
        for segment in message:
            if not isinstance(segment, Mapping) or segment.get("type") != "text":
                continue
            data = segment.get("data")
            if isinstance(data, Mapping):
                parts.append(str(data.get("text") or ""))
        return "".join(parts)

    raw_message = payload.get("raw_message", message)
    return html.unescape(_CQ_CODE_PATTERN.sub("", str(raw_message or "")))


def is_ignored_command(payload: Mapping[str, object], prefixes: Sequence[str]) -> bool:
    """Match a complete command prefix without matching longer lookalikes."""
    text = _raw_text(payload).lstrip().casefold()
    for raw_prefix in prefixes:
        prefix = str(raw_prefix or "").strip().casefold()
        if not prefix or not text.startswith(prefix):
            continue
        if len(text) == len(prefix) or text[len(prefix)].isspace():
            return True
    return False
