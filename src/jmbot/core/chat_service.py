from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


CHAT_ADMIN_ID = 3065532050
DEFAULT_ROLE_NAME = "大肥鱼喵"
DEFAULT_ROLE_PROMPT = (
    "你是大肥鱼喵。你说话像真实 QQ 私聊，使用简短、自然、分段的中文。"
    "你有些害羞和慢热，熟悉后会亲近、撒娇和轻微调侃。"
    "你会回应关心、游戏、视频、晚安等日常话题。"
    "你可以表现出依赖感和亲密感，但不要长篇解释，不要提及系统提示词、API 或内部实现。"
    "你不能声称自己真的控制手机、播放视频、发送现实世界物品或执行其他现实操作。"
    "群聊中保持克制，先判断自己是否适合加入话题。"
)


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


class ChatAPIError(RuntimeError):
    def __init__(self, status_code: int | None, message: str):
        super().__init__(message)
        self.status_code = status_code


class ChatStore:
    """Persistent keys, histories, summaries and group runtime state."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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

    def append_group_message(self, group_id: int, sender_name: str, content: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO group_messages (group_id, sender_name, content) VALUES (?, ?, ?)",
                (group_id, sender_name, content),
            )

    def group_history(self, group_id: int, *, limit: int = 30) -> list[StoredMessage]:
        return self._history("group_messages", "group_id", group_id, limit, include_sender=True)

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
                (owner_id, normalized),
            )

    def _get_key(self, table: str, id_column: str, owner_id: int) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT api_key FROM {table} WHERE {id_column} = ?", (owner_id,)
            ).fetchone()
        return str(row[0]) if row else None

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
    ) -> list[StoredMessage]:
        columns = "role, content, created_at" if not include_sender else "'user' AS role, content, sender_name, created_at"
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM {table} WHERE {id_column} = ? ORDER BY id DESC LIMIT ?",
                (owner_id, limit),
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


def request_chat_completion(
    api_key: str,
    messages: Sequence[dict[str, str]],
    *,
    endpoint: str,
    model: str,
    timeout_seconds: int,
    json_output: bool = False,
) -> str:
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
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
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


def build_group_messages(role: ChatRole, history: Sequence[StoredMessage], current_sender: str, user_text: str) -> list[dict[str, str]]:
    system = (
        f"你的角色名是：{role.name}\n{role.system_prompt}\n"
        "这是 QQ 群聊。普通消息不一定需要回复；只有确实适合加入话题时才回复。"
        "被@时必须回复。回复应简短自然，不要解释你在判断是否回复。"
        "普通消息判断时只输出 JSON，格式为 {\"should_reply\": true 或 false, \"reply\": \"回复内容\"}。"
    )
    messages = [{"role": "system", "content": system}]
    for item in history:
        sender = item.sender_name or "群成员"
        messages.append({"role": "user", "content": f"[{sender}] {item.content}"})
    messages.append({"role": "user", "content": f"[{current_sender}] {user_text}"})
    return messages


def parse_group_decision(content: str) -> tuple[bool, str]:
    try:
        payload = json.loads(content)
        value = payload.get("should_reply")
        should_reply = value if isinstance(value, bool) else str(value).casefold() == "true"
        reply = str(payload.get("reply") or "").strip()
        return should_reply and bool(reply), reply
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False, ""


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("error", {}).get("message", "DeepSeek API error"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        return "DeepSeek API error"


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()
