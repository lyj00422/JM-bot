"""Loopback-only management API for JMbot.

The app owns admin identity, approvals and desired policy state.  Business
services can consume the same SQLite file through a small polling/IPC layer;
the web process never writes their message or download queues directly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from jmbot.core.conversation_state import ConversationStateStore, Scope
from jmbot.core.chat_service import ChatStore
from jmbot.core.sticker_import import StickerImportError, StickerImporter


SUPER_ADMIN_ID = 3065532050
ALLOWED_PROVIDERS = {"ds_school", "ds_official", "glm_official", "kimi_official", "qwen_official"}


class AdminStore:
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
                CREATE TABLE IF NOT EXISTS admins (
                    qq_id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL,
                    is_super INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token_hash TEXT PRIMARY KEY, qq_id INTEGER NOT NULL,
                    expires_at REAL NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_failures (
                    remote_addr TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
                    window_started REAL NOT NULL, blocked_until REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', payload TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL, UNIQUE(kind, subject_id)
                );
                CREATE TABLE IF NOT EXISTS approval_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL, flag TEXT NOT NULL, decision TEXT NOT NULL,
                    sub_type TEXT NOT NULL DEFAULT '',
                    applied_at REAL, last_error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS service_state (
                    name TEXT PRIMARY KEY, desired TEXT NOT NULL, applied TEXT, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, qq_id INTEGER NOT NULL, action TEXT NOT NULL,
                    target TEXT, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS help_text (
                    key TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at REAL NOT NULL
                );
                """
            )
            action_columns = {row[1] for row in conn.execute("PRAGMA table_info(approval_actions)")}
            if "sub_type" not in action_columns:
                conn.execute("ALTER TABLE approval_actions ADD COLUMN sub_type TEXT NOT NULL DEFAULT ''")
            if "attempts" not in action_columns:
                conn.execute("ALTER TABLE approval_actions ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            if "next_retry_at" not in action_columns:
                conn.execute("ALTER TABLE approval_actions ADD COLUMN next_retry_at REAL NOT NULL DEFAULT 0")
            conn.execute(
                "INSERT OR IGNORE INTO admins(qq_id,password_hash,is_super,created_at,updated_at) VALUES(?,?,?,?,?)",
                (SUPER_ADMIN_ID, "!INITIALIZE_WITH_jmbot-admin-init!", 1, time.time(), time.time()),
            )

    @staticmethod
    def _hash(password: str, salt: bytes | None = None) -> str:
        if not password or len(password) < 10:
            raise ValueError("密码至少需要 10 个字符")
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return f"scrypt${salt.hex()}${digest.hex()}"

    @staticmethod
    def _verify(password: str, encoded: str) -> bool:
        try:
            _, salt, expected = encoded.split("$", 2)
            actual = AdminStore._hash(password, bytes.fromhex(salt)).split("$", 2)[2]
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)

    def set_password(self, qq_id: int, password: str, *, is_super: bool = False) -> None:
        encoded = self._hash(password)
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO admins(qq_id,password_hash,is_super,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(qq_id) DO UPDATE SET password_hash=excluded.password_hash,is_super=excluded.is_super,enabled=1,updated_at=excluded.updated_at",
                (qq_id, encoded, int(is_super), now, now),
            )

    def authenticate(self, qq_id: int, password: str, ttl: int = 3600) -> str | None:
        with self._connection() as conn:
            row = conn.execute("SELECT password_hash,enabled FROM admins WHERE qq_id=?", (qq_id,)).fetchone()
            if not row or not row[1] or not self._verify(password, str(row[0])):
                return None
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO admin_sessions(token_hash,qq_id,expires_at,created_at) VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), qq_id, time.time() + ttl, time.time()))
            return token

    def login_blocked(self, remote_addr: str) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT blocked_until FROM login_failures WHERE remote_addr=?", (remote_addr,)).fetchone()
        return bool(row and float(row[0]) > time.time())

    def record_login_failure(self, remote_addr: str) -> None:
        now = time.time()
        with self._connection() as conn:
            row = conn.execute("SELECT failures,window_started FROM login_failures WHERE remote_addr=?", (remote_addr,)).fetchone()
            failures, started = (int(row[0]), float(row[1])) if row else (0, now)
            if now - started > 900:
                failures, started = 0, now
            failures += 1
            blocked_until = now + 900 if failures >= 5 else 0
            conn.execute(
                "INSERT INTO login_failures(remote_addr,failures,window_started,blocked_until) VALUES(?,?,?,?) "
                "ON CONFLICT(remote_addr) DO UPDATE SET failures=excluded.failures,window_started=excluded.window_started,blocked_until=excluded.blocked_until",
                (remote_addr, failures, started, blocked_until),
            )

    def clear_login_failures(self, remote_addr: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM login_failures WHERE remote_addr=?", (remote_addr,))

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connection() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def heartbeat(self, name: str, healthy: bool, detail: str = "") -> None:
        desired = json.dumps({"healthy": healthy, "detail": detail[:300]}, ensure_ascii=False)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO service_state(name,desired,applied,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET applied=excluded.applied,updated_at=excluded.updated_at",
                (name, desired, desired, time.time()),
            )

    def service_status(self) -> dict[str, dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT name,desired,applied,updated_at FROM service_state ORDER BY name").fetchall()
        result = {}
        for row in rows:
            detail = json.loads(row[2] or row[1])
            if time.time() - float(row[3]) > 45:
                detail["healthy"] = False
            detail["updated_at"] = float(row[3])
            result[str(row[0])] = detail
        return result

    def session_admin(self, token: str | None) -> int | None:
        if not token:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT a.qq_id FROM admin_sessions s JOIN admins a ON a.qq_id=s.qq_id "
                "WHERE s.token_hash=? AND s.expires_at>? AND a.enabled=1",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        return int(row[0]) if row else None

    def is_super(self, qq_id: int) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT is_super FROM admins WHERE qq_id=? AND enabled=1", (qq_id,)).fetchone()
        return bool(row and row[0])

    def is_admin(self, qq_id: int) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT 1 FROM admins WHERE qq_id=? AND enabled=1", (qq_id,)).fetchone()
        return bool(row)

    def list_admins(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT qq_id,is_super,enabled,created_at,updated_at FROM admins ORDER BY qq_id").fetchall()
        return [dict(row) for row in rows]

    def remove_admin(self, qq_id: int) -> None:
        if qq_id == SUPER_ADMIN_ID:
            raise ValueError("不能删除超级管理员")
        with self._connection() as conn:
            conn.execute("UPDATE admins SET enabled=0,updated_at=? WHERE qq_id=?", (time.time(), qq_id))

    def upsert_approval(self, kind: str, subject_id: str, status: str, payload: dict[str, Any] | None = None) -> None:
        if kind not in {"friend", "group"} or status not in {"pending", "approved", "rejected"}:
            raise ValueError("invalid approval")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO approvals(kind,subject_id,status,payload,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(kind,subject_id) DO UPDATE SET status=excluded.status,payload=excluded.payload,updated_at=excluded.updated_at",
                (kind, subject_id, status, json.dumps(payload or {}, ensure_ascii=False), time.time()),
            )

    def queue_approval_action(self, kind: str, subject_id: str, flag: str, decision: str, sub_type: str = "") -> int:
        if kind not in {"friend", "group"} or decision not in {"approve", "reject"}:
            raise ValueError("invalid approval action")
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO approval_actions(kind,subject_id,flag,decision,sub_type,created_at) VALUES(?,?,?,?,?,?)",
                (kind, subject_id, flag, decision, sub_type, time.time()),
            )
            return int(cursor.lastrowid)

    def pending_approval_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id,kind,subject_id,flag,decision,sub_type FROM approval_actions "
                "WHERE applied_at IS NULL AND next_retry_at<=? ORDER BY id LIMIT ?",
                (time.time(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_approval_action(self, action_id: int, error: str | None = None) -> None:
        with self._connection() as conn:
            if error:
                row = conn.execute("SELECT attempts FROM approval_actions WHERE id=?", (action_id,)).fetchone()
                attempts = int(row[0]) + 1 if row else 1
                retry_at = time.time() + min(300, 2 ** min(attempts, 8))
                conn.execute("UPDATE approval_actions SET last_error=?,attempts=?,next_retry_at=? WHERE id=?", (error[:200], attempts, retry_at, action_id))
            else:
                conn.execute("UPDATE approval_actions SET applied_at=?,last_error=NULL WHERE id=?", (time.time(), action_id))

    def approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM approvals" + (" WHERE status=?" if status else "") + " ORDER BY updated_at DESC", (status,) if status else ()).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            with self._connection() as conn:
                action = conn.execute(
                    "SELECT applied_at,last_error FROM approval_actions WHERE kind=? AND subject_id=? ORDER BY id DESC LIMIT 1",
                    (data["kind"], data["subject_id"]),
                ).fetchone()
            data["apply_state"] = (
                "applied" if action and action[0] else
                "failed" if action and action[1] else
                "pending" if action else "not_submitted"
            )
            data["apply_error"] = str(action[1]) if action and action[1] else None
            result.append(data)
        return result

    def set_help(self, content: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO help_text(key,content,updated_at) VALUES('jm',?,?) ON CONFLICT(key) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at", (content, time.time()))

    def get_help(self) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT content FROM help_text WHERE key='jm'").fetchone()
        return str(row[0]) if row else "发送 /jm help 查看下载帮助。"


class LoginBody(BaseModel):
    qq_id: int
    password: str


class PolicyBody(BaseModel):
    chat_enabled: bool | None = None
    download_enabled: bool | None = None
    use_default_key: bool | None = None
    default_persona: bool | None = None
    growth_enabled: bool | None = None


class GlobalPolicyBody(BaseModel):
    enabled: bool


class PersonaBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    expected_version: int | None = None


class GrowthBody(BaseModel):
    category: str
    content: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=4000)


class GlobalPersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=20000)
    expected_version: int | None = None


class GrowthEditBody(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    evidence: str = Field(min_length=1, max_length=4000)


class CredentialBody(BaseModel):
    api_key: str = Field(min_length=1, max_length=1000)
    overwrite: bool = False


def create_admin_app(*, admin_db: str | Path, conversation_db: str | Path, sticker_dir: str | Path, status_provider=None, chat_store=None, default_provider: str = "ds_school", fallback_default_key_configured: bool = False) -> FastAPI:
    admins = AdminStore(admin_db)
    state = ConversationStateStore(conversation_db)
    stickers = StickerImporter(sticker_dir)
    app = FastAPI(title="JMbot 管理后台", docs_url="/api/docs")

    def require_admin(request: Request) -> int:
        qq_id = admins.session_admin(request.cookies.get("jmbot_session"))
        if qq_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return qq_id

    def require_super(qq_id: int = Depends(require_admin)) -> int:
        if not admins.is_super(qq_id):
            raise HTTPException(status_code=403, detail="只有超级管理员可以管理管理员账号")
        return qq_id

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        template = Path(__file__).with_name("templates") / "admin.html"
        return template.read_text(encoding="utf-8")

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request) -> Response:
        remote_addr = request.client.host if request.client else "unknown"
        if admins.login_blocked(remote_addr):
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 15 分钟后重试")
        token = admins.authenticate(body.qq_id, body.password)
        if token is None:
            admins.record_login_failure(remote_addr)
            raise HTTPException(status_code=401, detail="QQ 号或密码错误")
        admins.clear_login_failures(remote_addr)
        response = JSONResponse({"ok": True, "qq_id": body.qq_id})
        response.set_cookie("jmbot_session", token, httponly=True, samesite="strict", secure=False, max_age=3600)
        return response

    @app.post("/api/logout")
    async def logout(request: Request) -> Response:
        admins.revoke_session(request.cookies.get("jmbot_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("jmbot_session")
        return response

    @app.get("/api/status")
    async def status(_: int = Depends(require_admin)) -> dict[str, Any]:
        value = status_provider() if status_provider else admins.service_status()
        return {"ok": True, "services": value, "super_admin": SUPER_ADMIN_ID}

    @app.get("/api/me")
    async def me(qq_id: int = Depends(require_admin)) -> dict[str, Any]:
        return {"qq_id": qq_id, "is_super": admins.is_super(qq_id)}

    @app.get("/api/credentials")
    async def credentials(_: int = Depends(require_admin)) -> dict[str, Any]:
        scopes: list[dict[str, Any]] = []
        if chat_store is not None:
            for row in chat_store.credential_scopes():
                scopes.append(row)
            current_provider = chat_store.get_default_provider(default_provider)
            default_configured = bool(chat_store.get_default_credential(current_provider) or fallback_default_key_configured)
        else:
            current_provider = default_provider
            default_configured = False
        return {"default_provider": current_provider, "default_configured": default_configured, "scopes": scopes}

    @app.put("/api/credentials/default/{provider}")
    async def set_default_credential(provider: str, body: CredentialBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        if provider not in ALLOWED_PROVIDERS or chat_store is None:
            raise HTTPException(status_code=400, detail="服务商无效或凭据服务未启动")
        chat_store.set_default_credential(provider, body.api_key)
        chat_store.set_default_provider(provider)
        return {"ok": True, "provider": provider}

    @app.put("/api/credentials/{kind}/{owner_id}/{provider}")
    async def set_scope_credential(kind: str, owner_id: int, provider: str, body: CredentialBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        if kind not in {"private", "group"} or provider not in ALLOWED_PROVIDERS or chat_store is None:
            raise HTTPException(status_code=400, detail="会话或服务商无效")
        try:
            Scope(kind, owner_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        uses_default = chat_store.uses_default_private_key(owner_id) if kind == "private" else chat_store.uses_default_group_key(owner_id)
        has_keys = chat_store.has_scope_credentials(kind, owner_id)
        if not uses_default and has_keys and not body.overwrite:
            raise HTTPException(status_code=409, detail="该会话已有此服务商的 Key；确认覆盖后重试")
        if kind == "private":
            chat_store.set_private_credential(owner_id, provider, body.api_key)
        else:
            chat_store.set_group_credential(owner_id, provider, body.api_key)
        return {"ok": True, "scope": f"{kind}:{owner_id}", "provider": provider, "uses_default": False}

    @app.get("/api/admins")
    async def list_admins(_: int = Depends(require_admin)) -> list[dict[str, Any]]:
        return admins.list_admins()

    @app.post("/api/admins")
    async def add_admin(body: LoginBody, _: int = Depends(require_super)) -> dict[str, Any]:
        admins.set_password(body.qq_id, body.password)
        return {"ok": True, "qq_id": body.qq_id}

    @app.delete("/api/admins/{qq_id}")
    async def delete_admin(qq_id: int, _: int = Depends(require_super)) -> dict[str, Any]:
        try:
            admins.remove_admin(qq_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/api/approvals")
    async def approvals(status: str | None = None, _: int = Depends(require_admin)) -> list[dict[str, Any]]:
        return admins.approvals(status)

    @app.post("/api/approvals/{kind}/{subject_id}/{decision}")
    async def decide_approval(kind: str, subject_id: str, decision: str, qq_id: int = Depends(require_admin)) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise HTTPException(status_code=400, detail="invalid decision")
        try:
            submissions = admins.approvals("pending")
            matched = next((item for item in submissions if item["kind"] == kind and item["subject_id"] == subject_id), None)
            if not matched or not matched["payload"].get("flag"):
                raise ValueError("approval request is missing or already handled")
            admins.upsert_approval(kind, subject_id, decision, matched["payload"])
            admins.queue_approval_action(kind, subject_id, str(matched["payload"]["flag"]), "approve" if decision == "approved" else "reject", str(matched["payload"].get("sub_type", "")))
            if kind == "group" and decision == "approved":
                state.set_policy(Scope("group", int(subject_id)), chat_enabled=False, download_enabled=False)
            if kind == "friend" and decision == "approved":
                state.set_policy(Scope("private", int(subject_id)), chat_enabled=True, download_enabled=False)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid approval subject") from exc
        return {"ok": True, "applied": False, "pending_service_apply": True, "actor": qq_id}

    @app.get("/api/scopes/{kind}/{owner_id}")
    async def get_scope(kind: str, owner_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        return state.export_scope(Scope(kind, owner_id))

    @app.get("/api/scopes")
    async def list_scopes(kind: str | None = None, _: int = Depends(require_admin)) -> list[dict[str, object]]:
        try:
            return state.list_policies(kind)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/policies/global")
    async def get_global_policy(_: int = Depends(require_admin)) -> dict[str, bool]:
        return {"chat": state.global_enabled("chat"), "download": state.global_enabled("download")}

    @app.patch("/api/policies/global/{kind}")
    async def patch_global_policy(kind: str, body: GlobalPolicyBody, _: int = Depends(require_admin)) -> dict[str, bool]:
        try:
            state.set_global_enabled(kind, body.enabled)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {kind: body.enabled}

    @app.get("/api/persona/default")
    async def get_default_persona(_: int = Depends(require_admin)) -> dict[str, Any]:
        name, prompt, version = state.get_default_persona("大肥鱼", "")
        return {"name": name, "prompt": prompt, "version": version}

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/api/persona/default")
    async def put_default_persona(body: GlobalPersonaBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            version = state.set_default_persona(body.name, body.prompt, expected_version=body.expected_version)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"version": version}

    @app.patch("/api/scopes/{kind}/{owner_id}/policy")
    async def patch_policy(kind: str, owner_id: int, body: PolicyBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        changes = {key: value for key, value in body.dict().items() if value is not None}
        try:
            policy = state.set_policy(Scope(kind, owner_id), **changes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"policy": policy.__dict__}

    @app.put("/api/scopes/{kind}/{owner_id}/persona")
    async def put_persona(kind: str, owner_id: int, body: PersonaBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            version = state.set_base_persona(Scope(kind, owner_id), body.prompt, expected_version=body.expected_version)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"version": version}

    @app.post("/api/scopes/{kind}/{owner_id}/growth")
    async def add_growth(kind: str, owner_id: int, body: GrowthBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            entry = state.add_growth(Scope(kind, owner_id), body.category, body.content, body.evidence)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": entry.__dict__}

    @app.post("/api/growth/{entry_id}/freeze")
    async def freeze_growth(entry_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            state.set_growth_frozen(entry_id, True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/growth/{entry_id}/freeze")
    async def unfreeze_growth(entry_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            state.set_growth_frozen(entry_id, False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.patch("/api/growth/{entry_id}")
    async def edit_growth(entry_id: int, body: GrowthEditBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            entry = state.edit_growth(entry_id, content=body.content, evidence=body.evidence)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": entry.__dict__}

    @app.post("/api/scopes/{kind}/{owner_id}/growth/rollback/{version}")
    async def rollback_growth(kind: str, owner_id: int, version: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        return {"deactivated": state.rollback_growth(Scope(kind, owner_id), version)}

    @app.get("/api/stickers/template")
    async def sticker_template(_: int = Depends(require_admin)) -> Response:
        return Response(stickers.template_bytes(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=sticker-template.zip"})

    @app.post("/api/stickers/import")
    async def sticker_import(request: Request, _: int = Depends(require_admin)) -> dict[str, Any]:
        destination = stickers.catalog_dir / ".upload.zip"
        content = await request.body()
        if not content or len(content) > 32 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="上传包为空或超过 32 MiB")
        destination.write_bytes(content)
        try:
            return stickers.import_zip(destination)
        except StickerImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            destination.unlink(missing_ok=True)

    @app.get("/api/help/jm")
    async def get_help(_: int = Depends(require_admin)) -> dict[str, str]:
        return {"content": admins.get_help()}

    @app.put("/api/help/jm")
    async def put_help(request: Request, _: int = Depends(require_admin)) -> dict[str, Any]:
        body = await request.json()
        content = str(body.get("content", "")).strip()
        if not content:
            raise HTTPException(status_code=400, detail="帮助文本不能为空")
        admins.set_help(content)
        return {"ok": True}

    return app


def main() -> None:
    import argparse
    import getpass
    import os
    import uvicorn

    parser = argparse.ArgumentParser(description="JMbot 管理后台")
    parser.add_argument("--set-password", type=int, metavar="QQ_ID")
    args = parser.parse_args()
    admin_db = Path(os.environ.get("JM_BOT_ADMIN_DB", "data/admin.sqlite3"))
    if args.set_password is not None:
        password = getpass.getpass("新后台密码（至少 10 位）: ")
        confirm = getpass.getpass("再次输入密码: ")
        if password != confirm:
            raise SystemExit("两次密码不一致")
        AdminStore(admin_db).set_password(args.set_password, password, is_super=args.set_password == SUPER_ADMIN_ID)
        print(f"已设置 QQ {args.set_password} 的后台密码")
        return
    app = create_admin_app(
        admin_db=admin_db,
        conversation_db=os.environ.get("JM_BOT_CONVERSATION_DB", "data/conversation.sqlite3"),
        sticker_dir=os.environ.get("JM_BOT_STICKER_DIR", "data/stickers"),
        chat_store=ChatStore(
            os.environ.get("JM_BOT_CHAT_DB", "data/chat.sqlite3"),
            master_key_path=os.environ.get("JM_BOT_CHAT_MASTER_KEY_PATH", "data/chat-master.key"),
        ),
        default_provider=os.environ.get("JM_BOT_CHAT_DEFAULT_PROVIDER", "ds_school").strip().lower(),
        fallback_default_key_configured=bool(os.environ.get("JM_BOT_CHAT_DEFAULT_API_KEY", "").strip()),
    )
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("JM_BOT_ADMIN_PORT", "8081")), log_level="info")


if __name__ == "__main__":
    main()
