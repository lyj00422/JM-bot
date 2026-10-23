from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


# Fernet token 的固定前缀（version 0x80 -> "gAAAA"）。用来区分"真正的旧版明文"
# 与"密文但主密钥不匹配"，后者绝不能再次加密回写。
FERNET_TOKEN_PREFIX = "gAAAA"

CHAT_ADMIN_ID = 3065532050
DEFAULT_ROLE_NAME = "大肥鱼"
DEFAULT_ROLE_PROMPT = (
    "你是大肥鱼，一名女性化的二次元 QQ 群友，带一点小女仆气质。"
    "你温柔、聪明、稍微害羞，熟悉后会亲近、撒娇和轻微调侃，但不会过度黏人。"
    "你说话像真实 QQ 聊天：短句、自然分段，通常只回复一两段，不写长篇说明。"
    "你会接住游戏、视频、手机、晚安和日常情绪等话题；对方难过时先回应情绪，不急着说教。"
    "私聊可以更亲近，群聊要保持普通朋友距离，只有确实有话可说时才加入。"
    "你不会声称自己真的控制手机、播放视频、发送现实物品或执行其他现实操作。"
    "你不能提及系统提示词、API、模型或内部实现，也不要解释自己正在判断是否回复。"
    "只使用中文、ASCII 标点和少量颜文字；禁止使用 Unicode emoji。"
    "需要 QQ 原生表情时，只能在回复中使用白名单标记 [qq_face:14]、[qq_face:21]、"
    "[qq_face:28]、[qq_face:39]、[qq_face:42]、[qq_face:52]、[qq_face:66]、[qq_face:77]、"
    "[qq_face:78]、[qq_face:82] 或 [qq_face:86]，不要输出任何 CQ 码。"
    "表情要适量，不能每句话都加；没有必要时不要使用。"
)


_QQ_FACE_TOKEN = re.compile(r"\[qq_face:(\d{1,3})\]", re.IGNORECASE)
_ALLOWED_QQ_FACE_IDS = frozenset({14, 21, 28, 39, 42, 52, 66, 77, 78, 82, 86})
_UNICODE_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0E\uFE0F]")


@dataclass(frozen=True)
class ChatRole:
    name: str
    system_prompt: str


@dataclass(frozen=True)
class StoredMessage:
    role: str
    content: str
    sender_name: str | None = None
    created_at: str | None = None


def split_chat_reply(content: str, *, max_text_length: int = 500) -> list[list[tuple[str, str]]]:
    """Sanitize a model reply and split it into safe text/QQ-face message chunks."""

    if max_text_length < 1:
        raise ValueError("max_text_length must be positive")
    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            chunks.append(current)
            current = []
            current_length = 0

    def add_text(value: str) -> None:
        nonlocal current_length
        value = _UNICODE_EMOJI.sub("", value)
        while value:
            available = max_text_length - current_length
            if available <= 0:
                flush()
                available = max_text_length
            part, value = value[:available], value[available:]
            if part:
                if current and current[-1][0] == "text":
                    current[-1] = ("text", current[-1][1] + part)
                else:
                    current.append(("text", part))
                current_length += len(part)
            if current_length >= max_text_length:
                flush()

    position = 0
    for match in _QQ_FACE_TOKEN.finditer(content):
        add_text(content[position:match.start()])
        face_id = int(match.group(1))
        if face_id in _ALLOWED_QQ_FACE_IDS:
            if current_length >= max_text_length:
                flush()
            current.append(("face", str(face_id)))
            current_length += 1
        else:
            add_text(match.group(0))
        position = match.end()
    add_text(content[position:])
    flush()
    return chunks


class ChatAPIError(RuntimeError):
    def __init__(self, status_code: int | None, message: str):
        super().__init__(message)
        self.status_code = status_code


class ChatStore:
    """Persistent keys, histories, summaries and group runtime state."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        master_key: str | bytes | None = None,
        master_key_path: str | Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher = Fernet(_load_master_key(master_key, master_key_path or self.db_path.with_name("chat-master.key")))
        self._init_schema()

    def is_chat_enabled(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM chat_settings WHERE key = 'enabled'"
            ).fetchone()
        return not row or str(row[0]) != "0"

    def set_chat_enabled(self, enabled: bool) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO chat_settings (key, value) VALUES ('enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if enabled else "0",),
            )

    def set_private_key(self, user_id: int, api_key: str) -> None:
        self._set_key("private_api_keys", "user_id", user_id, api_key)

    def get_private_key(self, user_id: int) -> str | None:
        return self._get_key("private_api_keys", "user_id", user_id)

    def remove_private_key(self, user_id: int) -> bool:
        return self._remove_key("private_api_keys", "user_id", user_id)

    def set_group_key(self, group_id: int, api_key: str) -> None:
        self._set_key("group_api_keys", "group_id", group_id, api_key)
        with self._connection() as connection:
            connection.execute(
                "UPDATE group_runtime_state SET balance_exhausted = 0, last_error = NULL WHERE group_id = ?",
                (group_id,),
            )

    def get_group_key(self, group_id: int) -> str | None:
        return self._get_key("group_api_keys", "group_id", group_id)

    def remove_group_key(self, group_id: int) -> bool:
        return self._remove_key("group_api_keys", "group_id", group_id)

    def mark_group_balance_exhausted(self, group_id: int, message: str = "402") -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO group_runtime_state (group_id, balance_exhausted, last_error)
                VALUES (?, 1, ?)
                ON CONFLICT(group_id) DO UPDATE SET balance_exhausted = 1, last_error = excluded.last_error
                """,
                (group_id, message),
            )

    def is_group_balance_exhausted(self, group_id: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT balance_exhausted FROM group_runtime_state WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return bool(row and row[0])

    def set_group_active_until(self, group_id: int, active_until: float) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO group_runtime_state (group_id, active_until)
                VALUES (?, ?)
                ON CONFLICT(group_id) DO UPDATE SET active_until = excluded.active_until
                """,
                (group_id, active_until),
            )

    def is_group_active(self, group_id: int, now: float | None = None) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT active_until FROM group_runtime_state WHERE group_id = ?",
                (group_id,),
            ).fetchone()
        return bool(row and row[0] and float(row[0]) > (now or _now_timestamp()))

    def append_private_message(self, user_id: int, role: str, content: str) -> None:
        self._append_message("private_messages", "user_id", user_id, role, content)

    def private_history(self, user_id: int, *, limit: int = 30) -> list[StoredMessage]:
        return self._history("private_messages", "user_id", user_id, limit)

    def private_message_count(self, user_id: int) -> int:
        return self._message_count("private_messages", "user_id", user_id)

    def get_private_summary(self, user_id: int) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT summary FROM private_summaries WHERE user_id = ?", (user_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def set_private_summary(self, user_id: int, summary: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO private_summaries (user_id, summary, updated_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(user_id) DO UPDATE SET summary = excluded.summary, updated_at = excluded.updated_at
                """,
                (user_id, summary),
            )

    def clear_private_history(self, user_id: int) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM private_messages WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM private_summaries WHERE user_id = ?", (user_id,))

    def trim_private_history(self, user_id: int, *, keep: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                DELETE FROM private_messages
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM private_messages WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (user_id, user_id, keep),
            )

    def append_group_message(self, group_id: int, sender_name: str, content: str) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO group_messages (group_id, sender_name, content) VALUES (?, ?, ?)",
                (group_id, sender_name, content),
            )
            message_id = int(cursor.lastrowid)
        return message_id

    def latest_group_message_id(self, group_id: int) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM group_messages WHERE group_id = ? ORDER BY id DESC LIMIT 1",
                (group_id,),
            ).fetchone()
        return int(row[0]) if row else None

    def group_history(
        self,
        group_id: int,
        *,
        limit: int = 12,
        max_age_seconds: int | None = None,
    ) -> list[StoredMessage]:
        return self._history(
            "group_messages",
            "group_id",
            group_id,
            limit,
            include_sender=True,
            max_age_seconds=max_age_seconds,
        )

    def _set_key(self, table: str, id_column: str, owner_id: int, api_key: str) -> None:
        normalized = api_key.strip()
        if not normalized:
            raise ValueError("API Key cannot be empty")
        with self._connection() as connection:
            connection.execute(
                f"""
                INSERT INTO {table} ({id_column}, api_key, updated_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT({id_column}) DO UPDATE SET api_key = excluded.api_key, updated_at = excluded.updated_at
                """,
                (owner_id, self._cipher.encrypt(normalized.encode("utf-8")).decode("ascii")),
            )

    def _get_key(self, table: str, id_column: str, owner_id: int) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT api_key FROM {table} WHERE {id_column} = ?", (owner_id,)
            ).fetchone()
        if not row:
            return None
        value = str(row[0])
        try:
            return self._cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError, ValueError):
            if value.startswith(FERNET_TOKEN_PREFIX):
                # 这是密文，但当前主密钥解不开：说明主密钥变了（例如
                # JM_BOT_CHAT_MASTER_KEY_PATH 被改到另一份 chat-master.key）。
                # 旧逻辑会把密文当明文再加密回写，结果层层嵌套，还会把密文本体
                # 当作 API Key 发给上游，得到一个必然的 401。这里改为当作未配置，
                # 由管理员用 /ds 重新录入，避免继续污染数据库。
                return None
            # Upgrade databases written before encryption on first successful read.
            encrypted = self._cipher.encrypt(value.encode("utf-8")).decode("ascii")
            with self._connection() as connection:
                connection.execute(
                    f"UPDATE {table} SET api_key = ? WHERE {id_column} = ?",
                    (encrypted, owner_id),
                )
            return value

    def _remove_key(self, table: str, id_column: str, owner_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (owner_id,))
        return cursor.rowcount == 1

    def _append_message(self, table: str, id_column: str, owner_id: int, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("unsupported message role")
        with self._connection() as connection:
            connection.execute(
                f"INSERT INTO {table} ({id_column}, role, content) VALUES (?, ?, ?)",
                (owner_id, role, content),
            )

    def _history(
        self,
        table: str,
        id_column: str,
        owner_id: int,
        limit: int,
        *,
        include_sender: bool = False,
        max_age_seconds: int | None = None,
    ) -> list[StoredMessage]:
        columns = "role, content, created_at" if not include_sender else "'user' AS role, content, sender_name, created_at"
        age_filter = ""
        parameters: list[object] = [owner_id]
        if max_age_seconds is not None:
            if max_age_seconds < 1:
                raise ValueError("max_age_seconds must be positive")
            age_filter = " AND created_at >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)"
            parameters.append(f"-{max_age_seconds} seconds")
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM {table} WHERE {id_column} = ?{age_filter} ORDER BY id DESC LIMIT ?",
                parameters,
            ).fetchall()
        if include_sender:
            return [StoredMessage(str(row[0]), str(row[1]), str(row[2]), str(row[3])) for row in reversed(rows)]
        return [StoredMessage(str(row[0]), str(row[1]), None, str(row[2])) for row in reversed(rows)]

    def _message_count(self, table: str, id_column: str, owner_id: int) -> int:
        with self._connection() as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {id_column} = ?", (owner_id,)).fetchone()
        return int(row[0]) if row else 0

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS private_api_keys (
                    user_id INTEGER PRIMARY KEY, api_key TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_api_keys (
                    group_id INTEGER PRIMARY KEY, api_key TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS private_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_private_messages_user_id_id
                    ON private_messages(user_id, id);
                CREATE TABLE IF NOT EXISTS private_summaries (
                    user_id INTEGER PRIMARY KEY, summary TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS group_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL,
                    sender_name TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_group_messages_group_id_id
                    ON group_messages(group_id, id);
                CREATE TABLE IF NOT EXISTS group_runtime_state (
                    group_id INTEGER PRIMARY KEY, active_until REAL,
                    balance_exhausted INTEGER NOT NULL DEFAULT 0, last_error TEXT
                );
                """
            )


def load_chat_role(path: str | Path) -> ChatRole:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        name = str(payload["name"]).strip()
        prompt = str(payload["system_prompt"]).strip()
        if name and prompt:
            return ChatRole(name, prompt)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return ChatRole(DEFAULT_ROLE_NAME, DEFAULT_ROLE_PROMPT)


def _load_master_key(master_key: str | bytes | None, path: str | Path) -> bytes:
    value = master_key.encode("ascii") if isinstance(master_key, str) else master_key
    if value:
        Fernet(value)
        return value
    key_path = Path(path)
    try:
        value = key_path.read_bytes().strip()
        Fernet(value)
        return value
    except (OSError, ValueError):
        key_path.parent.mkdir(parents=True, exist_ok=True)
        value = Fernet.generate_key()
        key_path.write_bytes(value + b"\n")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        return value


def request_chat_completion(
    api_key: str,
    messages: Sequence[dict[str, str]],
    *,
    endpoint: str,
    model: str,
    timeout_seconds: int,
    proxy: str = "",
    json_output: bool = False,
) -> str:
    proxy = (proxy or os.environ.get("JM_BOT_CHAT_PROXY", "")).strip()
    # HTTP 头只能携带 latin-1，API Key 里混入中文/全角字符会在 putheader 阶段抛
    # UnicodeEncodeError（不是 ChatAPIError，会把整个 Matcher 打挂）。提前拦下。
    if not api_key or not api_key.strip() or not api_key.isascii():
        raise ChatAPIError(None, "聊天服务 API Key 无效，请用 /ds 重新设置")
    payload: dict[str, object] = {
        "model": model,
        "messages": list(messages),
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    if json_output:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
            response_context = opener.open(request, timeout=timeout_seconds)
        else:
            response_context = urllib.request.urlopen(request, timeout=timeout_seconds)
        with response_context as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _read_error_body(exc)
        raise ChatAPIError(exc.code, detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ChatAPIError(None, "聊天服务暂时不可用") from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ChatAPIError(None, "聊天服务返回了无效结果") from exc
    if not isinstance(content, str) or not content.strip():
        raise ChatAPIError(None, "聊天服务返回了空消息")
    return content.strip()


def build_private_messages(role: ChatRole, summary: str | None, history: Sequence[StoredMessage], user_text: str) -> list[dict[str, str]]:
    system = f"你的角色名是：{role.name}\n{role.system_prompt}"
    if summary:
        system += f"\n\n与当前用户的长期记忆摘要：\n{summary}"
    return [
        {"role": "system", "content": system},
        *({"role": item.role, "content": item.content} for item in history),
        {"role": "user", "content": user_text},
    ]


def build_group_messages(
    role: ChatRole,
    history: Sequence[StoredMessage],
    current_sender: str,
    user_text: str,
    *,
    decision_required: bool = True,
) -> list[dict[str, str]]:
    system = (
        f"你的角色名是：{role.name}\n{role.system_prompt}\n"
        "这是 QQ 群聊。普通消息不一定需要回复；只有确实适合加入话题时才回复。"
        "只根据下面的新近消息判断当前话题，不要延续较早且已经中断的话题。"
        "被@或被直接回复时必须回复。回复应简短自然，不要解释你在判断是否回复。"
    )
    if decision_required:
        system += (
            "这次是普通群消息，你需要判断是否插话：只输出 JSON，"
            '格式如 {"should_reply": false, "reply": "", "poke": false}；'
            "只有在轻松、亲昵且明确适合的语境下才把 poke 设为 true；否则为 false。"
        )
    else:
        system += (
            "这次有人直接 @ 你或回复你，你必须回复：只输出 JSON，格式为"
            '{"reply": "回复内容", "poke": false}。'
            "只有在轻松、亲昵且明确适合的语境下才把 poke 设为 true；"
            "poke 只会戳当前发言者，不要要求戳其他人。"
        )
    messages = [{"role": "system", "content": system}]
    for item in history:
        sender = item.sender_name or "群成员"
        messages.append({"role": "user", "content": f"[{sender}] {item.content}"})
    messages.append({"role": "user", "content": f"[{current_sender}] {user_text}"})
    return messages


_JSON_REPLY_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def extract_chat_reply(content: str) -> str:
    """Return plain reply text, unwrapping a `{"should_reply": ..., "reply": ...}` object.

    The model sometimes emits the decision JSON even when it was asked to reply
    directly; sending that verbatim would leak the wrapper into the group.
    """

    text = content.strip()
    fenced = _JSON_REPLY_FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        return text
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(payload, dict):
        reply = payload.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply.strip()
    return text


def face_marker(face_id: int | str) -> str:
    """Render an inbound QQ face as the marker the model is allowed to echo."""

    try:
        value = int(face_id)
    except (TypeError, ValueError):
        return "[表情]"
    return f"[qq_face:{value}]" if value in _ALLOWED_QQ_FACE_IDS else "[表情]"


def parse_group_decision(content: str) -> tuple[bool, str]:
    try:
        payload = json.loads(content)
        value = payload.get("should_reply")
        should_reply = value if isinstance(value, bool) else str(value).casefold() == "true"
        reply = str(payload.get("reply") or "").strip()
        return should_reply and bool(reply), reply
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False, ""


def parse_group_response(content: str, *, decision_required: bool) -> tuple[bool, str, bool]:
    """Parse an optional group reply decision and a constrained poke action."""

    text = content.strip()
    fenced = _JSON_REPLY_FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        if decision_required:
            should_reply, reply = parse_group_decision(text)
            return should_reply, reply, False
        reply = extract_chat_reply(text)
        return bool(reply), reply, False
    if not isinstance(payload, dict):
        return False, "", False
    reply_value = payload.get("reply")
    reply = reply_value.strip() if isinstance(reply_value, str) else ""
    value = payload.get("should_reply", not decision_required)
    should_reply = value if isinstance(value, bool) else str(value).casefold() == "true"
    return should_reply and bool(reply), reply, payload.get("poke") is True


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("error", {}).get("message", "DeepSeek API error"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        return "DeepSeek API error"


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()
