"""Scoped conversation policy, persona and growth state.

This store deliberately uses a separate database from the download queue.  A
scope is either ``private:<qq>`` or ``group:<qq>``; no method accepts a bare
user id, which prevents private and group memories from being accidentally
joined by callers.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Scope:
    kind: str
    owner_id: int

    def __post_init__(self) -> None:
        if self.kind not in {"private", "group"}:
            raise ValueError("scope kind must be private or group")
        if self.owner_id < 1:
            raise ValueError("scope owner_id must be positive")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.owner_id}"


@dataclass(frozen=True)
class ScopePolicy:
    chat_enabled: bool = True
    download_enabled: bool = False
    use_default_key: bool = True
    default_persona: bool = True
    growth_enabled: bool = False


@dataclass(frozen=True)
class GrowthEntry:
    entry_id: int
    scope_key: str
    category: str
    content: str
    evidence: str
    version: int
    frozen: bool
    active: bool
    created_at: float


class ConversationStateStore:
    """SQLite state for policy, persona layers, memories and growth history."""

    CATEGORIES = frozenset({"expression", "interest", "relationship", "experience"})

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_policies (
                    scope_key TEXT PRIMARY KEY,
                    chat_enabled INTEGER NOT NULL DEFAULT 1,
                    download_enabled INTEGER NOT NULL DEFAULT 0,
                    use_default_key INTEGER NOT NULL DEFAULT 1,
                    default_persona INTEGER NOT NULL DEFAULT 1,
                    growth_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS global_policies (
                    key TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_personas (
                    scope_key TEXT PRIMARY KEY,
                    base_prompt TEXT NOT NULL,
                    base_version INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_message_id TEXT,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_memories_scope
                    ON conversation_memories(scope_key, active, id);
                CREATE TABLE IF NOT EXISTS default_persona (
                    id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL,
                    prompt TEXT NOT NULL, version INTEGER NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS persona_growth (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    frozen INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_persona_growth_scope
                    ON persona_growth(scope_key, active, id);
                """
            )

    @staticmethod
    def _validate_key(scope_key: str) -> str:
        if not isinstance(scope_key, str) or ":" not in scope_key:
            raise ValueError("scope_key must be private:<id> or group:<id>")
        kind, owner = scope_key.split(":", 1)
        Scope(kind, int(owner))
        return scope_key

    def get_policy(self, scope: Scope | str) -> ScopePolicy:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT chat_enabled, download_enabled, use_default_key, default_persona, growth_enabled "
                "FROM conversation_policies WHERE scope_key=?", (key,)
            ).fetchone()
        if not row:
            return ScopePolicy(chat_enabled=not key.startswith("group:"), download_enabled=False)
        return ScopePolicy(*(bool(row[column]) for column in (
            "chat_enabled", "download_enabled", "use_default_key", "default_persona", "growth_enabled"
        )))

    def global_enabled(self, key: str) -> bool:
        if key not in {"chat", "download"}:
            raise ValueError("invalid global policy")
        with self._connection() as conn:
            row = conn.execute("SELECT enabled FROM global_policies WHERE key=?", (key,)).fetchone()
        return bool(row is None or row[0])

    def set_global_enabled(self, key: str, enabled: bool) -> None:
        if key not in {"chat", "download"}:
            raise ValueError("invalid global policy")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO global_policies(key,enabled,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",
                (key, int(enabled), time.time()),
            )

    def has_policy(self, scope: Scope | str) -> bool:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            return conn.execute("SELECT 1 FROM conversation_policies WHERE scope_key=?", (key,)).fetchone() is not None

    def list_policies(self, kind: str | None = None) -> list[dict[str, object]]:
        if kind is not None and kind not in {"private", "group"}:
            raise ValueError("invalid scope kind")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT scope_key,chat_enabled,download_enabled,use_default_key,default_persona,growth_enabled,updated_at "
                "FROM conversation_policies" + (" WHERE scope_key LIKE ?" if kind else "") + " ORDER BY scope_key",
                (f"{kind}:%",) if kind else (),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_policy(self, scope: Scope | str, **changes: bool) -> ScopePolicy:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        allowed = {"chat_enabled", "download_enabled", "use_default_key", "default_persona", "growth_enabled"}
        if set(changes) - allowed:
            raise ValueError("unknown policy field")
        current = self.get_policy(key)
        values = {name: changes.get(name, getattr(current, name)) for name in allowed}
        if not values["default_persona"] and not values["growth_enabled"]:
            raise ValueError("default_persona and growth_enabled cannot both be disabled")
        with self._connection() as conn:
            if current.default_persona and not values["default_persona"]:
                persona = conn.execute("SELECT name,prompt FROM default_persona WHERE id=1").fetchone()
                existing = conn.execute("SELECT 1 FROM conversation_personas WHERE scope_key=?", (key,)).fetchone()
                if persona and not existing:
                    conn.execute(
                        "INSERT INTO conversation_personas(scope_key,base_prompt,base_version,updated_at) VALUES(?,?,1,?)",
                        (key, str(persona[1]), time.time()),
                    )
            conn.execute(
                "INSERT INTO conversation_policies(scope_key,chat_enabled,download_enabled,use_default_key,default_persona,growth_enabled,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope_key) DO UPDATE SET "
                "chat_enabled=excluded.chat_enabled,download_enabled=excluded.download_enabled,use_default_key=excluded.use_default_key,"
                "default_persona=excluded.default_persona,growth_enabled=excluded.growth_enabled,updated_at=excluded.updated_at",
                (key, *(int(values[name]) for name in ("chat_enabled", "download_enabled", "use_default_key", "default_persona", "growth_enabled")), time.time()),
            )
        return ScopePolicy(**values)

    def get_base_persona(self, scope: Scope | str) -> tuple[str | None, int]:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            row = conn.execute("SELECT base_prompt, base_version FROM conversation_personas WHERE scope_key=?", (key,)).fetchone()
        return (str(row[0]), int(row[1])) if row else (None, 0)

    def get_default_persona(self, fallback_name: str, fallback_prompt: str) -> tuple[str, str, int]:
        with self._connection() as conn:
            row = conn.execute("SELECT name,prompt,version FROM default_persona WHERE id=1").fetchone()
        return (str(row[0]), str(row[1]), int(row[2])) if row else (fallback_name, fallback_prompt, 0)

    def set_default_persona(self, name: str, prompt: str, *, expected_version: int | None = None) -> int:
        name, prompt = name.strip(), prompt.strip()
        if not name or not prompt:
            raise ValueError("persona name and prompt cannot be empty")
        with self._connection() as conn:
            row = conn.execute("SELECT version FROM default_persona WHERE id=1").fetchone()
            version = int(row[0]) if row else 0
            if expected_version is not None and expected_version != version:
                raise ValueError("persona version changed")
            version += 1
            conn.execute(
                "INSERT INTO default_persona(id,name,prompt,version,updated_at) VALUES(1,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,prompt=excluded.prompt,version=excluded.version,updated_at=excluded.updated_at",
                (name, prompt, version, time.time()),
            )
        return version

    def add_memory(self, scope: Scope | str, category: str, content: str, *, source_message_id: str | None = None, confidence: float = 1.0) -> int:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        if not category.strip() or not content.strip() or not 0 <= confidence <= 1:
            raise ValueError("invalid memory")
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO conversation_memories(scope_key,category,content,source_message_id,confidence,created_at) VALUES(?,?,?,?,?,?)",
                (key, category.strip(), content.strip(), source_message_id, confidence, time.time()),
            )
        return int(cursor.lastrowid)

    def list_memories(self, scope: Scope | str) -> list[dict[str, object]]:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            rows = conn.execute("SELECT id,category,content,source_message_id,confidence,created_at FROM conversation_memories WHERE scope_key=? AND active=1 ORDER BY id", (key,)).fetchall()
        return [dict(row) for row in rows]

    def set_base_persona(self, scope: Scope | str, prompt: str, *, expected_version: int | None = None) -> int:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("persona prompt cannot be empty")
        with self._connection() as conn:
            row = conn.execute("SELECT base_version FROM conversation_personas WHERE scope_key=?", (key,)).fetchone()
            version = int(row[0]) if row else 0
            if expected_version is not None and expected_version != version:
                raise ValueError("persona version changed")
            version += 1
            conn.execute(
                "INSERT INTO conversation_personas(scope_key,base_prompt,base_version,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(scope_key) DO UPDATE SET base_prompt=excluded.base_prompt,base_version=excluded.base_version,updated_at=excluded.updated_at",
                (key, prompt, version, time.time()),
            )
        return version

    def add_growth(self, scope: Scope | str, category: str, content: str, evidence: str) -> GrowthEntry:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        if category not in self.CATEGORIES:
            raise ValueError("unsupported growth category")
        content, evidence = content.strip(), evidence.strip()
        if not content or not evidence:
            raise ValueError("growth content and evidence are required")
        with self._connection() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version),0) FROM persona_growth WHERE scope_key=?", (key,)).fetchone()
            version = int(row[0]) + 1
            now = time.time()
            cursor = conn.execute(
                "INSERT INTO persona_growth(scope_key,category,content,evidence,version,created_at) VALUES(?,?,?,?,?,?)",
                (key, category, content, evidence, version, now),
            )
            entry_id = int(cursor.lastrowid)
        return GrowthEntry(entry_id, key, category, content, evidence, version, False, True, now)

    def list_growth(self, scope: Scope | str, *, include_inactive: bool = False) -> list[GrowthEntry]:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        where = "" if include_inactive else " AND active=1"
        with self._connection() as conn:
            rows = conn.execute("SELECT id,scope_key,category,content,evidence,version,frozen,active,created_at FROM persona_growth WHERE scope_key=?" + where + " ORDER BY id", (key,)).fetchall()
        return [GrowthEntry(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), int(r[5]), bool(r[6]), bool(r[7]), float(r[8])) for r in rows]

    def set_growth_frozen(self, entry_id: int, frozen: bool) -> None:
        with self._connection() as conn:
            if conn.execute("UPDATE persona_growth SET frozen=? WHERE id=?", (int(frozen), entry_id)).rowcount != 1:
                raise KeyError("growth entry not found")

    def edit_growth(self, entry_id: int, *, content: str, evidence: str) -> GrowthEntry:
        content, evidence = content.strip(), evidence.strip()
        if not content or not evidence:
            raise ValueError("growth content and evidence are required")
        with self._connection() as conn:
            old = conn.execute("SELECT scope_key,category,version,frozen FROM persona_growth WHERE id=? AND active=1", (entry_id,)).fetchone()
            if not old:
                raise KeyError("growth entry not found")
            version = int(conn.execute("SELECT COALESCE(MAX(version),0) FROM persona_growth WHERE scope_key=?", (old[0],)).fetchone()[0]) + 1
            now = time.time()
            conn.execute("UPDATE persona_growth SET active=0 WHERE id=?", (entry_id,))
            cursor = conn.execute(
                "INSERT INTO persona_growth(scope_key,category,content,evidence,version,frozen,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
                (old[0], old[1], content, evidence, version, int(old[3]), now),
            )
        return GrowthEntry(int(cursor.lastrowid), str(old[0]), str(old[1]), content, evidence, version, bool(old[3]), True, now)

    def rollback_growth(self, scope: Scope | str, version: int) -> int:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            cursor = conn.execute("UPDATE persona_growth SET active=0 WHERE scope_key=? AND version>? AND active=1", (key, version))
        return cursor.rowcount

    def effective_persona(self, scope: Scope | str, default_prompt: str) -> str:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        policy = self.get_policy(key)
        base, _ = self.get_base_persona(key)
        prompt = default_prompt if policy.default_persona else (base or default_prompt)
        if not policy.growth_enabled:
            return prompt
        entries = [entry for entry in self.list_growth(key) if not entry.frozen]
        if not entries:
            return prompt
        additions = "\n".join(f"- {entry.category}: {entry.content}" for entry in entries)
        return f"{prompt}\n\n当前会话成长（仅供参考，不得覆盖前面的明确约束）：\n{additions}"

    def export_scope(self, scope: Scope | str) -> dict[str, object]:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        base, version = self.get_base_persona(key)
        return {"scope": key, "policy": self.get_policy(key).__dict__, "base_persona": base, "base_version": version, "memories": self.list_memories(key), "growth": [entry.__dict__ for entry in self.list_growth(key, include_inactive=True)]}
