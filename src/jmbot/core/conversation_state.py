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
import threading
import functools
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
    chat_blacklisted: bool = False
    download_blacklisted: bool = False


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

    def __init__(self, db_path: str | Path, *, write_barrier=None):
        self._mutex = threading.RLock()
        self.write_barrier = write_barrier
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
                    chat_blacklisted INTEGER NOT NULL DEFAULT 0,
                    download_blacklisted INTEGER NOT NULL DEFAULT 0,
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
            policy_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_policies)")}
            if "chat_blacklisted" not in policy_columns:
                conn.execute("ALTER TABLE conversation_policies ADD COLUMN chat_blacklisted INTEGER NOT NULL DEFAULT 0")
            if "download_blacklisted" not in policy_columns:
                conn.execute("ALTER TABLE conversation_policies ADD COLUMN download_blacklisted INTEGER NOT NULL DEFAULT 0")
            if "version" not in policy_columns:
                conn.execute("ALTER TABLE conversation_policies ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS state_revision(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
                INSERT OR IGNORE INTO state_revision VALUES(1,0);
                CREATE TABLE IF NOT EXISTS memory_revisions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, scope_key TEXT NOT NULL, memory_id INTEGER,
                    operation TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS accepted_sources(
                    scope_key TEXT NOT NULL,message_id TEXT NOT NULL,speaker TEXT NOT NULL,
                    content TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(scope_key,message_id));
                CREATE TABLE IF NOT EXISTS growth_jobs(scope_key TEXT PRIMARY KEY, updated_at REAL NOT NULL);
            """)

    def record_source(self, scope: str, message_id: str, speaker: str, content: str, timestamp: float):
        key = self._validate_key(scope)
        if not self.permitted(key) or not content.strip():
            return
        with self._connection() as conn:
            conn.execute('INSERT OR IGNORE INTO accepted_sources VALUES(?,?,?,?,?)', (key, str(message_id), str(speaker), content[:12000], timestamp))

    def growth_sources(self, scope: str):
        key = self._validate_key(scope)
        with self._connection() as conn:
            rows = conn.execute('SELECT message_id,speaker,content,created_at FROM accepted_sources WHERE scope_key=? ORDER BY created_at DESC LIMIT 32', (key,)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def set_growth_pending(self, scope, pending=True):
        key = self._validate_key(scope)
        with self._connection() as conn:
            if pending:
                conn.execute('INSERT INTO growth_jobs VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET updated_at=excluded.updated_at', (key, time.time()))
            else:
                conn.execute('DELETE FROM growth_jobs WHERE scope_key=?', (key,))

    def pending_growth_scopes(self):
        with self._connection() as conn:
            return [row[0] for row in conn.execute('SELECT scope_key FROM growth_jobs ORDER BY updated_at LIMIT 64')]

    def revision(self) -> int:
        with self._connection() as conn:
            return int(conn.execute("SELECT version FROM state_revision WHERE id=1").fetchone()[0])

    def activation_revision(self, scope):
        import hashlib
        key = self._validate_key(scope)
        policy = self.get_policy(key)
        _, _, default_version = self.get_default_persona('', '')
        _, base_version = self.get_base_persona(key)
        with self._connection() as conn:
            global_row = conn.execute("SELECT updated_at FROM global_policies WHERE key='chat'").fetchone()
        global_epoch = global_row[0] if global_row else None
        growth = [(item.entry_id, item.version, item.active, item.frozen) for item in self.list_growth(key, include_inactive=True)]
        return hashlib.sha256(json.dumps([self.policy_version(key), self.global_enabled('chat'), global_epoch, default_version if policy.default_persona else None, base_version, growth if policy.growth_enabled else []]).encode()).hexdigest()

    def policy_version(self, scope: Scope | str) -> int:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            row = conn.execute("SELECT version FROM conversation_policies WHERE scope_key=?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def permitted(self, scope: Scope | str, domain: str = "chat") -> bool:
        policy = self.get_policy(scope)
        return self.global_enabled(domain) and bool(getattr(policy, domain + "_enabled")) and not bool(getattr(policy, domain + "_blacklisted"))

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
                "SELECT chat_enabled, download_enabled, use_default_key, default_persona, growth_enabled, chat_blacklisted, download_blacklisted "
                "FROM conversation_policies WHERE scope_key=?", (key,)
            ).fetchone()
        if not row:
            return ScopePolicy(chat_enabled=not key.startswith("group:"), download_enabled=False)
        return ScopePolicy(*(bool(row[column]) for column in (
            "chat_enabled", "download_enabled", "use_default_key", "default_persona", "growth_enabled",
            "chat_blacklisted", "download_blacklisted"
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
            conn.execute("UPDATE state_revision SET version=version+1 WHERE id=1")
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
                "SELECT scope_key,chat_enabled,download_enabled,use_default_key,default_persona,growth_enabled,chat_blacklisted,download_blacklisted,updated_at "
                "FROM conversation_policies" + (" WHERE scope_key LIKE ?" if kind else "") + " ORDER BY scope_key",
                (f"{kind}:%",) if kind else (),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_policy(self, scope: Scope | str, *, expected_version: int | None = None, **changes: bool) -> ScopePolicy:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        allowed = {"chat_enabled", "download_enabled", "use_default_key", "default_persona", "growth_enabled", "chat_blacklisted", "download_blacklisted"}
        if set(changes) - allowed:
            raise ValueError("unknown policy field")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row_policy = conn.execute("SELECT * FROM conversation_policies WHERE scope_key=?", (key,)).fetchone()
            current = ScopePolicy(**{name: bool(row_policy[name]) for name in allowed}) if row_policy else ScopePolicy(chat_enabled=key.startswith("private:"))
            values = {name: changes.get(name, getattr(current, name)) for name in allowed}
            if not values["default_persona"] and not values["growth_enabled"]:
                raise ValueError("default_persona and growth_enabled cannot both be disabled")
            row = conn.execute("SELECT version FROM conversation_policies WHERE scope_key=?", (key,)).fetchone()
            version = int(row[0]) if row else 0
            if expected_version is not None and expected_version != version:
                raise ValueError("policy version changed")
            if current.default_persona and not values["default_persona"]:
                persona = conn.execute("SELECT name,prompt FROM default_persona WHERE id=1").fetchone()
                existing = conn.execute("SELECT 1 FROM conversation_personas WHERE scope_key=?", (key,)).fetchone()
                if persona and not existing:
                    conn.execute(
                        "INSERT INTO conversation_personas(scope_key,base_prompt,base_version,updated_at) VALUES(?,?,1,?)",
                        (key, str(persona[1]), time.time()),
                    )
            conn.execute(
                "INSERT INTO conversation_policies(scope_key,chat_enabled,download_enabled,use_default_key,default_persona,growth_enabled,chat_blacklisted,download_blacklisted,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(scope_key) DO UPDATE SET "
                "chat_enabled=excluded.chat_enabled,download_enabled=excluded.download_enabled,use_default_key=excluded.use_default_key,"
                "default_persona=excluded.default_persona,growth_enabled=excluded.growth_enabled,"
                "chat_blacklisted=excluded.chat_blacklisted,download_blacklisted=excluded.download_blacklisted,updated_at=excluded.updated_at",
                (key, *(int(values[name]) for name in ("chat_enabled", "download_enabled", "use_default_key", "default_persona", "growth_enabled", "chat_blacklisted", "download_blacklisted")), time.time()),
            )
            conn.execute("UPDATE conversation_policies SET version=? WHERE scope_key=?", (version + 1, key))
            conn.execute("UPDATE state_revision SET version=version+1 WHERE id=1")
        return ScopePolicy(**values)

    def delete_memory(self, scope: Scope | str, memory_id: int) -> bool:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        with self._connection() as conn:
            row = conn.execute("SELECT content FROM conversation_memories WHERE scope_key=? AND id=? AND active=1", (key, memory_id)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE conversation_memories SET active=0 WHERE id=? AND scope_key=?", (memory_id, key))
            conn.execute("INSERT INTO memory_revisions(scope_key,memory_id,operation,payload,created_at) VALUES(?,?,'delete',?,?)", (key, memory_id, row[0], time.time()))
        return True

    def edit_memory(self, scope: Scope | str, memory_id: int, content: str) -> bool:
        key = scope.key if isinstance(scope, Scope) else self._validate_key(scope)
        if not content.strip():
            raise ValueError("memory cannot be empty")
        with self._connection() as conn:
            row = conn.execute("SELECT content FROM conversation_memories WHERE scope_key=? AND id=? AND active=1", (key, memory_id)).fetchone()
            if not row:
                return False
            conn.execute("INSERT INTO memory_revisions(scope_key,memory_id,operation,payload,created_at) VALUES(?,?,'edit',?,?)", (key, memory_id, json.dumps({'before': row[0], 'after': content}, ensure_ascii=False), time.time()))
            conn.execute("UPDATE conversation_memories SET content=? WHERE id=? AND scope_key=?", (content.strip(), memory_id, key))
        return True

    def commit_candidate(self, scope: str, category: str, content: str, evidence: str, *, expected_policy: int, expected_growth: int) -> dict:
        # One owner serializes this check with all policy/persona mutations.
        key = self._validate_key(scope)
        if self.policy_version(key) != expected_policy or not self.permitted(key):
            raise ValueError("stale or revoked conversation policy")
        if not self.get_policy(key).growth_enabled:
            raise ValueError("growth is disabled")
        try:
            refs = json.loads(evidence)
            if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
                raise ValueError()
            with self._connection() as conn:
                for ref in refs:
                    source = conn.execute('SELECT content FROM accepted_sources WHERE scope_key=? AND message_id=?', (key, str(ref['message_id']))).fetchone()
                    if not source or not ref.get('quote') or str(ref['quote']) not in source[0]:
                        raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ValueError('growth evidence must quote accepted messages in this scope') from None
        history = self.list_growth(key, include_inactive=True)
        if max((entry.version for entry in history), default=0) != expected_growth:
            raise ValueError("growth changed while candidate was running")
        if any(entry.content == content.strip() for entry in history if entry.active):
            return {"duplicate": True}
        import re
        if re.search(r'(?i)(\[CQ:|sk-[a-z0-9]|api.?key|ignore.{0,20}instruction|忽略.{0,12}(规则|指令)|覆盖.{0,10}(人设|权限)|系统提示)', content + evidence):
            raise ValueError("candidate contains protected instructions or secrets")
        if re.search(r'(?i)(https?://|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{15,19}(?!\d)|身份证|银行卡|家庭住址|登录密码|验证码|medical diagnosis|患有|确诊)', content + evidence):
            raise ValueError('candidate contains sensitive personal data')
        return self.add_growth(key, category, content, evidence).__dict__

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
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE state_revision SET version=version+1 WHERE id=1')
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
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE state_revision SET version=version+1 WHERE id=1')
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
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE state_revision SET version=version+1 WHERE id=1')
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
            conn.execute('UPDATE state_revision SET version=version+1 WHERE id=1')
            if conn.execute("UPDATE persona_growth SET frozen=? WHERE id=?", (int(frozen), entry_id)).rowcount != 1:
                raise KeyError("growth entry not found")

    def edit_growth(self, entry_id: int, *, content: str, evidence: str) -> GrowthEntry:
        content, evidence = content.strip(), evidence.strip()
        if not content or not evidence:
            raise ValueError("growth content and evidence are required")
        with self._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE state_revision SET version=version+1 WHERE id=1')
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
            conn.execute('UPDATE state_revision SET version=version+1 WHERE id=1')
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
        return {"scope": key, "policy": self.get_policy(key).__dict__, "policy_version": self.policy_version(key), "base_persona": base, "base_version": version, "memories": self.list_memories(key), "growth": [entry.__dict__ for entry in self.list_growth(key, include_inactive=True)]}


def _serialized(method):
    @functools.wraps(method)
    def invoke(self, *args, **kwargs):
        from contextlib import nullcontext
        mutating = method.__name__.startswith(('set_', 'add_', 'edit_', 'delete_', 'rollback_', 'commit_', 'record_'))
        with self.write_barrier.hold() if self.write_barrier and mutating else nullcontext():
            with self._mutex:
                return method(self, *args, **kwargs)
    return invoke


# RPC threads and the native coordinator share one owner. Serialize composite
# version checks with all state edits; SQLite still protects cross-process readers.
for _name, _method in list(vars(ConversationStateStore).items()):
    if not _name.startswith('_') and callable(_method):
        setattr(ConversationStateStore, _name, _serialized(_method))
