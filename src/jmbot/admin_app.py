"""Loopback-only management API for JMbot.

The app owns admin identity, approvals and desired policy state.  Business
services can consume the same SQLite file through a small polling/IPC layer;
the web process never writes their message or download queues directly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import asyncio
import urllib.parse
import tempfile
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
RELEASE_VERSION = os.environ.get("JM_BOT_RELEASE_VERSION", "0.3.21")
ALLOWED_PROVIDERS = {
    "ds_school", "ds_official", "glm_school", "glm_official",
    "kimi_school", "kimi_official", "qwen_school", "qwen_official",
}


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
                CREATE TABLE IF NOT EXISTS control_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
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
            if 'napcat_applied' not in action_columns:
                conn.execute('ALTER TABLE approval_actions ADD COLUMN napcat_applied INTEGER NOT NULL DEFAULT 0')
            if 'sending' not in action_columns:
                conn.execute('ALTER TABLE approval_actions ADD COLUMN sending INTEGER NOT NULL DEFAULT 0')
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
            conn.execute("DELETE FROM admin_sessions WHERE qq_id=?", (qq_id,))
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
            conn.execute("DELETE FROM admin_sessions WHERE qq_id=?", (qq_id,))
            conn.execute("UPDATE admins SET enabled=0,updated_at=? WHERE qq_id=?", (time.time(), qq_id))

    def upsert_approval(self, kind: str, subject_id: str, status: str, payload: dict[str, Any] | None = None) -> None:
        if kind not in {"friend", "group"} or status not in {"pending", "approved", "rejected"}:
            raise ValueError("invalid approval")
        with self._connection() as conn:
            old = conn.execute("SELECT payload,status FROM approvals WHERE kind=? AND subject_id=?", (kind, subject_id)).fetchone()
            if old and status == "pending" and json.loads(old[0]).get("flag") == (payload or {}).get("flag"):
                return
            conn.execute(
                "INSERT INTO approvals(kind,subject_id,status,payload,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(kind,subject_id) DO UPDATE SET status=excluded.status,payload=excluded.payload,updated_at=excluded.updated_at",
                (kind, subject_id, status, json.dumps(payload or {}, ensure_ascii=False), time.time()),
            )

    def queue_approval_action(self, kind: str, subject_id: str, flag: str, decision: str, sub_type: str = "") -> int:
        if kind not in {"friend", "group"} or decision not in {"approve", "reject"}:
            raise ValueError("invalid approval action")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT id,decision FROM approval_actions WHERE kind=? AND subject_id=? AND flag=? ORDER BY id DESC LIMIT 1", (kind, subject_id, flag)).fetchone()
            if old:
                if old[1] != decision:
                    raise ValueError('approval already has a different decision')
                return int(old[0])
            cursor = conn.execute(
                "INSERT INTO approval_actions(kind,subject_id,flag,decision,sub_type,created_at) VALUES(?,?,?,?,?,?)",
                (kind, subject_id, flag, decision, sub_type, time.time()),
            )
            conn.execute('UPDATE approvals SET status=?,updated_at=? WHERE kind=? AND subject_id=?', ('approved' if decision == 'approve' else 'rejected', time.time(), kind, subject_id))
            return int(cursor.lastrowid)

    def pending_approval_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id,kind,subject_id,flag,decision,sub_type,napcat_applied FROM approval_actions "
                "WHERE applied_at IS NULL AND (sending=0 OR napcat_applied=1) AND next_retry_at<=? ORDER BY id LIMIT ?",
                (time.time(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_approval(self, action_id: int):
        with self._connection() as conn:
            conn.execute('UPDATE approval_actions SET napcat_applied=1 WHERE id=?', (action_id,))

    def begin_approval(self, action_id: int):
        with self._connection() as conn:
            return conn.execute('UPDATE approval_actions SET sending=1,last_error=? WHERE id=? AND sending=0 AND napcat_applied=0', ('NapCat outcome unknown until acknowledged; reconcile before retry', action_id)).rowcount == 1

    def reconcile_approval(self, action_id: int, applied: bool):
        with self._connection() as conn:
            conn.execute('UPDATE approval_actions SET napcat_applied=?,sending=0,last_error=NULL,next_retry_at=0 WHERE id=? AND applied_at IS NULL', (int(applied), action_id))

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
                    "SELECT applied_at,last_error,id,sending,napcat_applied FROM approval_actions WHERE kind=? AND subject_id=? ORDER BY id DESC LIMIT 1",
                    (data["kind"], data["subject_id"]),
                ).fetchone()
            data["apply_state"] = (
                "applied" if action and action[0] else
                "failed" if action and action[1] else
                "pending" if action else "not_submitted"
            )
            data["apply_error"] = str(action[1]) if action and action[1] else None
            data['action_id'] = int(action[2]) if action else None
            data['outcome_unknown'] = bool(action and action[3] and not action[4] and not action[0])
            result.append(data)
        return result

    def set_help(self, content: str) -> None:
        with self._connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS help_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,created_at REAL NOT NULL)')
            conn.execute('INSERT INTO help_versions(content,created_at) VALUES(?,?)', (content, time.time()))
            conn.execute("INSERT INTO help_text(key,content,updated_at) VALUES('jm',?,?) ON CONFLICT(key) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at", (content, time.time()))

    def help_versions(self):
        with self._connection() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS help_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,created_at REAL NOT NULL)')
            return [dict(row) for row in conn.execute('SELECT * FROM help_versions ORDER BY id DESC LIMIT 100')]

    def restore_help(self, version: int):
        versions = self.help_versions()
        item = next((item for item in versions if item['id'] == version), None)
        if item is None:
            raise ValueError('help version not found')
        self.set_help(item['content'])

    def get_help(self) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT content FROM help_text WHERE key='jm'").fetchone()
        return str(row[0]) if row else "发送 /jm help 查看下载帮助。"

    def queue_control_action(self, name: str, payload: dict[str, Any] | None = None) -> int:
        if name not in {"download_cancel", "download_clear", "download_clear_finished", "download_priority", "download_delete_failed"}:
            raise ValueError("unsupported control action")
        now = time.time()
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO control_actions(name,payload,created_at,updated_at) VALUES(?,?,?,?)",
                (name, json.dumps(payload or {}, ensure_ascii=False), now, now),
            )
        return int(cursor.lastrowid)

    def claim_control_actions(self, limit: int = 10) -> list[dict[str, Any]]:
        now = time.time()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,name,payload FROM control_actions "
                "WHERE status='pending' OR (status='processing' AND updated_at<?) ORDER BY id LIMIT ?",
                (now - 60, limit),
            ).fetchall()
            for row in rows:
                conn.execute("UPDATE control_actions SET status='processing',updated_at=? WHERE id=?", (now, int(row[0])))
        return [{"id": int(row[0]), "name": str(row[1]), "payload": json.loads(row[2])} for row in rows]

    def control_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id,name,payload,status,result,error,created_at,updated_at FROM control_actions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        values = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            item["result"] = json.loads(item["result"] or "{}")
            values.append(item)
        return values

    def complete_control_action(self, action_id: int, *, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE control_actions SET status=?,result=?,error=?,updated_at=? WHERE id=?",
                ("failed" if error else "done", json.dumps(result or {}, ensure_ascii=False), error, time.time(), action_id),
            )

class LoginBody(BaseModel):
    qq_id: int
    password: str


class PolicyBody(BaseModel):
    expected_version: int | None = None
    chat_enabled: bool | None = None
    download_enabled: bool | None = None
    use_default_key: bool | None = None
    default_persona: bool | None = None
    growth_enabled: bool | None = None
    chat_blacklisted: bool | None = None
    download_blacklisted: bool | None = None


class GlobalPolicyBody(BaseModel):
    enabled: bool


class RemarkBody(BaseModel):
    remark: str = Field(default="", max_length=60)


class PersonaBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    expected_version: int | None = None


class PersonaFieldsBody(BaseModel):
    personality: str = Field(min_length=1, max_length=20000)
    behavior_style: str = Field(default="", max_length=12000)
    reply_style: str = Field(default="", max_length=12000)
    seed_source: str = Field(default="imported", pattern="^(a|b|imported)$")
    seed_source_revision: str = Field(default="", max_length=200)
    seed_snapshot: dict[str, object] | None = None
    expected_version: int | None = None
    rebase: bool = False


class EffectivePersonaBody(PersonaBody):
    expected_revision: str | None = None


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


def create_admin_app(*, admin_db: str | Path, conversation_db: str | Path, sticker_dir: str | Path, status_provider=None, chat_store=None, queue_db_path: str | Path | None = None, default_provider: str = "ds_school", fallback_default_key_configured: bool = False, state_store=None, sticker_store=None, download_client=None, native_client=None) -> FastAPI:
    admins = AdminStore(admin_db)
    state = state_store if state_store is not None else ConversationStateStore(conversation_db)
    stickers = sticker_store if sticker_store is not None else StickerImporter(sticker_dir)
    app = FastAPI(title="JMbot 管理后台", docs_url="/api/docs")
    app.state.admin_store = admins
    from jmbot.napcat_admin import NapCatAdmin
    napcat_admin = NapCatAdmin()

    @app.exception_handler(RuntimeError)
    async def unavailable(request: Request, exc: RuntimeError):
        return JSONResponse({"detail": "业务服务未确认全部应用，请刷新各域状态后重试", "applied": None, "domains": getattr(exc, 'domains', {})}, status_code=503)

    @app.middleware("http")
    async def protect_browser_requests(request: Request, call_next):
        # Loopback HTTP is intentional for SSH tunnels. Require a session-bound
        # CSRF token as well as SameSite cookies for every authenticated write.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urllib.parse.urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
            session = request.cookies.get("jmbot_session")
            if session and request.url.path != "/api/login":
                expected = hashlib.sha256(("csrf:" + session).encode()).hexdigest()
                if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
                    return JSONResponse({"detail": "CSRF 校验失败，请重新登录"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
            actor = admins.session_admin(request.cookies.get("jmbot_session"))
            if actor:
                with admins._connection() as conn:
                    conn.execute("INSERT INTO admin_audit(qq_id,action,target,created_at) VALUES(?,?,?,?)",
                                 (actor, request.method, request.url.path, time.time()))
        return response

    @app.on_event("startup")
    async def start_admin_heartbeat() -> None:
        async def heartbeat_loop() -> None:
            while True:
                admins.heartbeat("jmbot-admin", True, "管理后台运行中")
                from jmbot.domain_state import DomainState
                if isinstance(state, DomainState):
                    await asyncio.to_thread(state.reconcile)
                await asyncio.sleep(15)

        app.state.heartbeat_task = asyncio.create_task(heartbeat_loop(), name="admin-service-heartbeat")

    @app.on_event("shutdown")
    async def stop_admin_heartbeat() -> None:
        task = getattr(app.state, "heartbeat_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        admins.heartbeat("jmbot-admin", False, "管理后台已停止")

    def require_admin(request: Request) -> int:
        qq_id = admins.session_admin(request.cookies.get("jmbot_session"))
        if qq_id is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return qq_id

    def require_super(qq_id: int = Depends(require_admin)) -> int:
        if not admins.is_super(qq_id):
            raise HTTPException(status_code=403, detail="只有超级管理员可以管理管理员账号")
        return qq_id

    def _napcat_view(state: dict, *, refreshed: bool = False) -> dict:
        account = os.environ.get("JM_BOT_QQ_ACCOUNT", "").strip()
        view = dict(state)
        view["account"] = account
        if not state.get("configured"):
            view["message"] = "尚未配置 NapCat WebUI 地址或 token，无法读取登录状态。"
            return view
        if state.get("isLogin"):
            # NapCat keeps returning the previous, expired QR payload after a
            # successful login; it is meaningless while the account is online.
            view["qrcodeurl"] = None
            view["loginError"] = None
            if state.get("isOffline"):
                view["message"] = f"QQ（{account}）已登录，但当前处于离线状态，请检查网络；无需重新扫码。"
            elif refreshed:
                view["message"] = f"QQ（{account}）已登录，无需二维码。登录失效后再点「获取二维码」即可。"
            else:
                view["message"] = f"QQ（{account}）已登录，无需二维码；登录失效后可点「获取二维码」重新扫码登录。"
        else:
            view["message"] = "QQ 未登录，请用手机 QQ 扫描下方二维码登录。"
            reason = str(state.get("loginError") or "").strip()
            if reason:
                view["message"] += f"（NapCat 提示：{reason}）"
        return view

    @app.get('/api/napcat/login')
    def napcat_login(_: int = Depends(require_admin)):
        return _napcat_view(napcat_admin.login_state())

    @app.post('/api/napcat/login/refresh')
    def napcat_refresh(_: int = Depends(require_admin)):
        try:
            return _napcat_view(napcat_admin.login_state(refresh=True), refreshed=True)
        except RuntimeError:
            # NapCat answers "QQ Is Logined" when a refresh is requested while
            # the account is online; report the real state instead of an error.
            return _napcat_view(napcat_admin.login_state(), refreshed=True)

    @app.get('/api/napcat/login/qr')
    def napcat_qr(_: int = Depends(require_admin)):
        import io
        import qrcode
        state = napcat_admin.login_state()
        if state.get("isLogin"):
            raise HTTPException(409, "QQ 已登录，无需二维码")
        value = state.get("qrcodeurl")
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise HTTPException(409, "暂无可用二维码，请先点「获取二维码」")
        output = io.BytesIO()
        qrcode.make(value).save(output, format='PNG')
        return Response(output.getvalue(), media_type='image/png')

    @app.get('/api/native/status')
    def native_status(_: int = Depends(require_admin)):
        if native_client is None:
            return {'enabled': False}
        return native_client.call('native', 'status')

    @app.get('/api/model-routes')
    def read_model_routes(_: int = Depends(require_admin)):
        if native_client is None:
            raise HTTPException(409, '原生聊天服务未配置')
        return native_client.call('routes', 'read')

    @app.put('/api/model-routes')
    def write_model_routes(body: dict, _: int = Depends(require_admin)):
        if native_client is None:
            raise HTTPException(409, '原生聊天服务未配置')
        try:
            return native_client.call('routes', 'write', body['document'], body['expected_version'])
        except (KeyError, ValueError):
            raise HTTPException(409, '模型路由无效或版本变化，请重新读取') from None

    @app.post('/api/native/memory/{account}/{kind}/{owner}')
    def native_memory(account: int, kind: str, owner: int, body: dict, _: int = Depends(require_admin)):
        if native_client is None:
            raise HTTPException(409, '原生聊天服务未配置')
        scope = Scope(kind, owner)
        return native_client.call('native', 'memory', f'qq:{account}:{scope.key}', body.get('action', 'list'), **body.get('arguments', {}))

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        template = Path(__file__).with_name("templates") / "admin.html"
        # The console is an inline single-page app. Never let a desktop tunnel
        # reopen a stale cached copy after a release; the launcher also adds a
        # release query parameter for browsers that ignore response headers.
        return Response(
            template.read_text(encoding="utf-8"),
            media_type="text/html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "X-JMbot-Release": RELEASE_VERSION},
        )

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
        response.set_cookie("jmbot_csrf", hashlib.sha256(("csrf:" + token).encode()).hexdigest(), samesite="strict", max_age=3600)
        return response

    @app.post("/api/logout")
    async def logout(request: Request) -> Response:
        admins.revoke_session(request.cookies.get("jmbot_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("jmbot_session")
        response.delete_cookie("jmbot_csrf")
        return response

    @app.get("/api/status")
    async def status(_: int = Depends(require_admin)) -> dict[str, Any]:
        value = status_provider() if status_provider else admins.service_status()
        return {"ok": True, "services": value, "super_admin": SUPER_ADMIN_ID}

    @app.get('/api/management/links')
    async def management_links(request: Request, _: int = Depends(require_admin)) -> dict[str, Any]:
        """Return the real native management surfaces for the SSH connector.

        The desktop script forwards three consecutive local ports: the JMbot
        console, MaiBot WebUI and NapCat WebUI.  Deriving links from the
        request port keeps fallback ports working without exposing a server
        loopback address that the browser cannot reach.
        """
        import httpx
        admin_port = request.url.port or int(os.environ.get('JM_BOT_ADMIN_PORT', '8081'))
        base = f'http://127.0.0.1:{admin_port}'
        native_port, napcat_port = admin_port + 1, admin_port + 2
        async def probe(port: int, path: str = '/') -> bool:
            try:
                async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                    response = await client.get(f'http://127.0.0.1:{port}{path}')
                    return response.status_code < 500
            except httpx.HTTPError:
                return False
        native_ok, napcat_ok = await asyncio.gather(probe(8001), probe(6099))
        login = {'token': None, 'source': None}
        if native_ok and native_client is not None:
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                    response = await client.get(
                        native_client.url + '/internal/native/webui-login',
                        headers={'Authorization': 'Bearer ' + native_client.token})
                    response.raise_for_status()
                    value = response.json()
                    if isinstance(value, dict) and isinstance(value.get('token'), str):
                        login = {'token': value['token'], 'source': value.get('source')}
            except (httpx.HTTPError, ValueError):
                pass
        official = os.environ.get('JM_BOT_OFFICIAL_CONNECTOR_ENABLED', '').lower() in {'1', 'true', 'yes', 'on'}
        return {
            'console': {'label': 'JMbot 联合控制台', 'url': base, 'available': True},
            'maibot': {'label': 'MaiBot 原生 WebUI', 'url': f'http://127.0.0.1:{native_port}', 'available': native_ok,
                       'login_token': login['token'], 'token_source': login['source'],
                       'server_port': 8001, 'detail': '原生 runtime 尚未启动；收到授权消息后会自动启动' if not native_ok else '原生 WebUI 在线'},
            'napcat': {'label': 'NapCat 原生 WebUI', 'url': f'http://127.0.0.1:{napcat_port}', 'available': napcat_ok,
                       'login_token': napcat_admin.token or None, 'token_source': 'configured',
                       'server_port': 6099, 'detail': '等待 SSH 转发或 NapCat WebUI' if not napcat_ok else 'NapCat WebUI 在线'},
            'chat_transport': 'official_snowluma' if official else 'legacy_native_worker',
            'official_connector_enabled': official,
        }

    @app.get("/api/download/actions")
    async def download_actions(_: int = Depends(require_admin)) -> list[dict[str, Any]]:
        return admins.control_actions()

    @app.post("/api/roster/group/{group_id}/remark")
    async def set_group_remark(group_id: int, body: RemarkBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        if download_client is None:
            raise HTTPException(status_code=503, detail="下载服务未连接")
        await asyncio.to_thread(download_client.call, "qq", "set_group_remark", group_id, body.remark)
        return {"ok": True}

    @app.post("/api/roster/friend/{user_id}/remark")
    async def set_friend_remark(user_id: int, body: RemarkBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        if download_client is None:
            raise HTTPException(status_code=503, detail="下载服务未连接")
        await asyncio.to_thread(download_client.call, "qq", "set_friend_remark", user_id, body.remark)
        return {"ok": True}

    @app.get("/api/roster")
    async def qq_roster(_: int = Depends(require_admin)) -> dict[str, Any]:
        if download_client is None:
            raise HTTPException(status_code=503, detail="下载服务未连接")
        data = dict(await asyncio.to_thread(download_client.call, "qq", "roster"))
        account = os.environ.get("JM_BOT_QQ_ACCOUNT", "").strip()
        data["account"] = account
        if account:
            data["friends"] = [friend for friend in data.get("friends", []) if str(friend.get("user_id")) != account]
        return data

    @app.get("/api/download/queue")
    async def download_queue(_: int = Depends(require_admin)) -> list[dict[str, Any]]:
        if download_client is not None:
            return await asyncio.to_thread(download_client.call, "download", "snapshot")
        if queue_db_path is None or not Path(queue_db_path).exists():
            return []
        uri = Path(queue_db_path).resolve().as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT id,album_id,destination_type,group_id,user_id,status,attempts,priority,error,created_at,updated_at,output_format,chapters,stage,progress_current,progress_total,started_at "
                    "FROM album_queue ORDER BY id DESC LIMIT 200"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise HTTPException(status_code=503, detail="读取下载队列失败") from exc
        return [dict(row) for row in rows]

    @app.post("/api/download/queue/{job_id}/cancel")
    async def cancel_download_job(job_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        if queue_db_path is None:
            raise HTTPException(status_code=503, detail="下载队列未连接")
        action_id = admins.queue_control_action("download_cancel", {"job_id": job_id})
        return {"ok": True, "pending": True, "action_id": action_id}

    @app.post("/api/download/queue/clear")
    async def clear_download_queue(_: int = Depends(require_admin)) -> dict[str, Any]:
        if queue_db_path is None:
            raise HTTPException(status_code=503, detail="下载队列未连接")
        action_id = admins.queue_control_action("download_clear")
        return {"ok": True, "pending": True, "action_id": action_id}

    @app.post("/api/download/queue/clear-finished")
    async def clear_finished_downloads(_: int = Depends(require_admin)) -> dict[str, Any]:
        if queue_db_path is None:
            raise HTTPException(status_code=503, detail="下载队列未连接")
        action_id = admins.queue_control_action("download_clear_finished")
        return {"ok": True, "pending": True, "action_id": action_id}

    @app.post("/api/download/queue/{job_id}/priority")
    async def prioritize_download_job(job_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        if queue_db_path is None:
            raise HTTPException(status_code=503, detail="下载队列未连接")
        action_id = admins.queue_control_action("download_priority", {"job_id": job_id})
        return {"ok": True, "pending": True, "action_id": action_id}

    @app.delete("/api/download/queue/{job_id}/failed")
    async def delete_failed_download(job_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        if queue_db_path is None:
            raise HTTPException(status_code=503, detail="下载队列未连接")
        action_id = admins.queue_control_action("download_delete_failed", {"job_id": job_id})
        return {"ok": True, "pending": True, "action_id": action_id}

    @app.get("/api/me")
    async def me(qq_id: int = Depends(require_admin)) -> dict[str, Any]:
        return {"qq_id": qq_id, "is_super": admins.is_super(qq_id)}

    @app.get("/api/credentials")
    def credentials(_: int = Depends(require_admin)) -> dict[str, Any]:
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
    def set_default_credential(provider: str, body: CredentialBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        if provider not in ALLOWED_PROVIDERS or chat_store is None:
            raise HTTPException(status_code=400, detail="服务商无效或凭据服务未启动")
        chat_store.set_default_credential(provider, body.api_key)
        chat_store.set_default_provider(provider)
        return {"ok": True, "provider": provider}

    @app.put("/api/credentials/{kind}/{owner_id}/{provider}")
    def set_scope_credential(kind: str, owner_id: int, provider: str, body: CredentialBody, _: int = Depends(require_admin)) -> dict[str, Any]:
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
        # A scope has one active independent credential.  Replacing it through
        # the backend must have the same semantics as the QQ command: remove
        # stale provider entries, switch the active provider, and disable
        # default-key inheritance atomically from the caller's perspective.
        chat_store.replace_scope_credential(kind, owner_id, provider, body.api_key)
        state.set_policy(Scope(kind, owner_id), use_default_key=False)
        return {"ok": True, "scope": f"{kind}:{owner_id}", "provider": provider, "uses_default": False}

    @app.get("/api/admins")
    async def list_admins(_: int = Depends(require_admin)) -> list[dict[str, Any]]:
        return admins.list_admins()

    @app.post("/api/admins")
    async def add_admin(body: LoginBody, _: int = Depends(require_super)) -> dict[str, Any]:
        admins.set_password(body.qq_id, body.password, is_super=body.qq_id == SUPER_ADMIN_ID)
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

    @app.post('/api/approvals/actions/{action_id}/reconcile')
    def reconcile_approval(action_id: int, body: GlobalPolicyBody, _: int = Depends(require_admin)):
        admins.reconcile_approval(action_id, body.enabled)
        return {'ok': True}

    @app.post("/api/approvals/{kind}/{subject_id}/{decision}")
    def decide_approval(kind: str, subject_id: str, decision: str, qq_id: int = Depends(require_admin)) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise HTTPException(status_code=400, detail="invalid decision")
        try:
            submissions = admins.approvals("pending")
            matched = next((item for item in submissions if item["kind"] == kind and item["subject_id"] == subject_id), None)
            if not matched or not matched["payload"].get("flag"):
                raise ValueError("approval request is missing or already handled")
            admins.queue_approval_action(kind, subject_id, str(matched["payload"]["flag"]), "approve" if decision == "approved" else "reject", str(matched["payload"].get("sub_type", "")))
            # Authorization is applied by the business worker only after NapCat
            # has acknowledged the approval, never when the button is clicked.
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid approval subject") from exc
        return {"ok": True, "applied": False, "pending_service_apply": True, "actor": qq_id}

    @app.get("/api/scopes/{kind}/{owner_id}")
    def get_scope(kind: str, owner_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        scope = Scope(kind, owner_id)
        data = state.export_scope(scope)
        if chat_store is not None:
            data["policy"]["use_default_key"] = (
                chat_store.uses_default_private_key(owner_id)
                if kind == "private" else chat_store.uses_default_group_key(owner_id)
            )
        data["effective_persona"] = state.effective_persona(
            scope, state.get_default_persona("大肥鱼", "")[1],
        )
        return data

    @app.get("/api/scopes")
    def list_scopes(kind: str | None = None, _: int = Depends(require_admin)) -> list[dict[str, object]]:
        try:
            scopes = state.list_policies(kind)
            if chat_store is not None:
                for row in scopes:
                    scope_kind, owner = str(row["scope_key"]).split(":", 1)
                    row["use_default_key"] = (
                        chat_store.uses_default_private_key(int(owner))
                        if scope_kind == "private" else chat_store.uses_default_group_key(int(owner))
                    )
            return scopes
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/policies/global")
    def get_global_policy(_: int = Depends(require_admin)):
        result = {}
        for domain in ('chat', 'download'):
            try:
                result[domain] = state.global_enabled(domain)
            except RuntimeError:
                result[domain] = None
        from jmbot.domain_state import DomainState
        result['deliveries'] = state.delivery_status() if isinstance(state, DomainState) else []
        return result

    @app.post('/api/policies/disable-all')
    def disable_all(_: int = Depends(require_admin)):
        outcomes = {}
        for domain in ('chat', 'download'):
            try:
                state.set_global_enabled(domain, False)
                outcomes[domain] = {'desired': False, 'applied': True}
            except Exception:
                outcomes[domain] = {'desired': False, 'applied': None}
        return {'domains': outcomes, 'applied': all(item['applied'] is True for item in outcomes.values())}

    @app.get('/api/download/scopes')
    def download_scopes(_: int = Depends(require_admin)):
        return getattr(state, 'download', state).list_policies()

    @app.patch("/api/policies/global/{kind}")
    def patch_global_policy(kind: str, body: GlobalPolicyBody, _: int = Depends(require_admin)) -> dict[str, bool]:
        try:
            state.set_global_enabled(kind, body.enabled)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {kind: body.enabled}

    @app.get("/api/persona/default")
    def get_default_persona(_: int = Depends(require_admin)) -> dict[str, Any]:
        name, prompt, version = state.get_default_persona("大肥鱼", "")
        return {"name": name, "prompt": prompt, "version": version}

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "release": RELEASE_VERSION}

    @app.put("/api/persona/default")
    def put_default_persona(body: GlobalPersonaBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            version = state.set_default_persona(body.name, body.prompt, expected_version=body.expected_version)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"version": version}

    @app.patch("/api/scopes/{kind}/{owner_id}/policy")
    def patch_policy(kind: str, owner_id: int, body: PolicyBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        changes = {key: value for key, value in body.dict().items() if value is not None}
        expected_version = changes.pop("expected_version", None)
        scope = Scope(kind, owner_id)
        if changes and all(key.startswith('download_') for key in changes):
            policy = state.set_policy(scope, **changes)
            store = getattr(state, 'download', state)
            return {"policy": policy.__dict__, "policy_version": store.policy_version(scope), "applied": True}
        current = getattr(state, 'chat', state).get_policy(scope)
        requested_default = changes.get("use_default_key", current.use_default_key)
        if chat_store is not None:
            requested_default = (
                chat_store.uses_default_private_key(owner_id)
                if kind == "private" else chat_store.uses_default_group_key(owner_id)
            )
            if "use_default_key" in changes:
                requested_default = bool(changes["use_default_key"])
        requested_chat = changes.get("chat_enabled", current.chat_enabled)
        try:
            policy = state.set_policy(scope, expected_version=expected_version, **changes)
            if chat_store is not None and "use_default_key" in changes:
                if kind == "private":
                    chat_store.set_private_default_inheritance(owner_id, bool(changes["use_default_key"]))
                else:
                    chat_store.set_group_default_inheritance(owner_id, bool(changes["use_default_key"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"policy": policy.__dict__, "policy_version": state.policy_version(scope), "applied": True}

    @app.put("/api/scopes/{kind}/{owner_id}/persona")
    def put_persona(kind: str, owner_id: int, body: PersonaBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            version = state.set_base_persona(Scope(kind, owner_id), body.prompt, expected_version=body.expected_version)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"version": version, "effective_persona": state.effective_persona(
            Scope(kind, owner_id), state.get_default_persona("大肥鱼", "")[1],
        )}

    @app.put("/api/scopes/{kind}/{owner_id}/persona/fields")
    def put_persona_fields(kind: str, owner_id: int, body: PersonaFieldsBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        scope = Scope(kind, owner_id)
        snapshot = body.seed_snapshot or {
            "personality": body.personality,
            "behavior_style": body.behavior_style,
            "reply_style": body.reply_style,
        }
        try:
            version = state.set_persona_fields(
                scope,
                personality=body.personality,
                behavior_style=body.behavior_style,
                reply_style=body.reply_style,
                seed_source=body.seed_source,
                seed_source_revision=body.seed_source_revision,
                seed_snapshot=snapshot,
                expected_version=body.expected_version,
                rebase=body.rebase,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        data = state.export_scope(scope)
        return {
            "version": version,
            "persona_fields": data.get("persona_fields", {}),
            "effective_persona_fields": data.get("effective_persona_fields", {}),
            "effective_persona": state.effective_persona(scope, state.get_default_persona("大肥鱼", "")[1]),
        }

    @app.post('/api/scopes/{kind}/{owner_id}/persona/refine')
    def refine_persona(kind: str, owner_id: int, _: int = Depends(require_admin)):
        if native_client is None:
            raise HTTPException(409, '原生聊天服务未配置，无法调用人设提炼模型')
        from jmbot.service_rpc import RPCClient
        scope = Scope(kind, owner_id)
        return RPCClient(native_client.url, native_client.token, timeout=190).call('persona_draft', 'generate', scope.key)

    @app.put("/api/scopes/{kind}/{owner_id}/persona/effective")
    def put_effective_persona(kind: str, owner_id: int, body: EffectivePersonaBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        # The editor shows the persona that is actually in effect (base plus
        # growth). Saving bakes that text into the scope base and freezes the
        # growth entries already merged into it, so nothing is applied twice.
        scope = Scope(kind, owner_id)
        try:
            version = state.replace_effective_persona(
                scope, body.prompt, expected_version=body.expected_version, expected_revision=body.expected_revision,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"version": version, "effective_persona": state.effective_persona(
            scope, state.get_default_persona("大肥鱼", "")[1],
        )}

    @app.post("/api/scopes/{kind}/{owner_id}/growth")
    def add_growth(kind: str, owner_id: int, body: GrowthBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            entry = state.add_growth(Scope(kind, owner_id), body.category, body.content, body.evidence)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": entry.__dict__}

    @app.post("/api/growth/{entry_id}/freeze")
    def freeze_growth(entry_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            state.set_growth_frozen(entry_id, True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.delete("/api/growth/{entry_id}/freeze")
    def unfreeze_growth(entry_id: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            state.set_growth_frozen(entry_id, False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.patch("/api/growth/{entry_id}")
    def edit_growth(entry_id: int, body: GrowthEditBody, _: int = Depends(require_admin)) -> dict[str, Any]:
        try:
            entry = state.edit_growth(entry_id, content=body.content, evidence=body.evidence)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": entry.__dict__}

    @app.post("/api/scopes/{kind}/{owner_id}/growth/rollback/{version}")
    def rollback_growth(kind: str, owner_id: int, version: int, _: int = Depends(require_admin)) -> dict[str, Any]:
        return {"deactivated": state.rollback_growth(Scope(kind, owner_id), version)}

    @app.get("/api/stickers/template")
    async def sticker_template(_: int = Depends(require_admin)) -> Response:
        content = await asyncio.to_thread(stickers.template_bytes)
        if isinstance(content, dict):
            import base64
            content = base64.b64decode(content["__bytes__"])
        return Response(content, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=sticker-template.zip"})

    @app.post("/api/stickers/import")
    async def sticker_import(request: Request, _: int = Depends(require_admin)) -> dict[str, Any]:
        raise HTTPException(409, '请先预览，再通过确认接口提交表情包')

    @app.post('/api/stickers/preview')
    async def preview_stickers(request: Request, _: int = Depends(require_admin)):
        import base64
        content = await request.body()
        if not content or len(content) > 32 * 1024 * 1024:
            raise HTTPException(413, '上传包为空或超过 32 MiB')
        try:
            return await asyncio.to_thread(stickers.preview_bytes, base64.b64encode(content).decode())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/api/stickers/confirm')
    async def confirm_stickers(request: Request, _: int = Depends(require_admin)):
        body = await request.json()
        try:
            return await asyncio.to_thread(stickers.confirm_preview, body['preview_token'], body['sha256'], body.get('scope', 'global'), body.get('edits'))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, '无效或过期的预览，请重新上传') from None

    @app.post('/api/stickers/draft')
    def draft_stickers(body: dict, _: int = Depends(require_admin)):
        if native_client is None:
            raise HTTPException(409, '原生聊天服务未配置')
        from jmbot.service_rpc import RPCClient
        client = RPCClient(native_client.url, native_client.token, timeout=100)
        return client.call('draft', 'generate', body['preview_token'], body['sha256'], body['scope'])

    @app.get('/api/stickers')
    def sticker_catalog(_: int = Depends(require_admin)):
        return stickers.catalog()

    @app.patch('/api/stickers/{sticker_id}')
    def review_sticker(sticker_id: int, body: dict, _: int = Depends(require_admin)):
        try:
            return stickers.review(sticker_id, body)
        except ValueError:
            raise HTTPException(400, '表情审核字段或作用域无效') from None

    @app.get('/api/help/jm/versions')
    async def help_versions(_: int = Depends(require_admin)):
        return admins.help_versions()

    @app.post('/api/help/jm/restore/{version}')
    async def restore_help(version: int, _: int = Depends(require_admin)):
        try:
            admins.restore_help(version)
        except ValueError:
            raise HTTPException(404, '帮助版本不存在') from None
        return {'ok': True, 'content': admins.get_help()}

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
    split = os.environ.get("JM_BOT_CHAT_BACKEND") == "maibot"
    options = {}
    if split:
        from jmbot.service_rpc import RPCClient, RemoteStore
        service_token = os.environ["JM_BOT_SERVICE_TOKEN"]
        chat_client = RPCClient(os.environ.get("JM_BOT_CHAT_SERVICE_URL", "http://127.0.0.1:8082"), service_token, timeout=35)
        from jmbot.domain_state import DomainState
        download_client = RPCClient(os.environ.get("JM_BOT_DOWNLOAD_SERVICE_URL", "http://127.0.0.1:8080"), service_token)
        options = {"state_store": DomainState(RemoteStore(chat_client, "state"), RemoteStore(download_client, "download_state"), admin_db.with_name('policy-delivery.sqlite3')),
                   "sticker_store": RemoteStore(chat_client, "stickers"),
                   "download_client": download_client, "native_client": chat_client}
        chat_store = RemoteStore(chat_client, "credentials")
    else:
        chat_store = ChatStore(os.environ.get("JM_BOT_CHAT_DB", "data/chat.sqlite3"),
                               master_key_path=os.environ.get("JM_BOT_CHAT_MASTER_KEY_PATH", "data/chat-master.key"))
    app = create_admin_app(
        admin_db=admin_db,
        conversation_db=os.environ.get("JM_BOT_CONVERSATION_DB", "data/conversation.sqlite3"),
        sticker_dir=os.environ.get("JM_BOT_STICKER_DIR", "data/stickers"),
        chat_store=chat_store,
        queue_db_path=os.environ.get("JM_BOT_QUEUE_DB", "data/queue.sqlite3"),
        default_provider=os.environ.get("JM_BOT_CHAT_DEFAULT_PROVIDER", "ds_school").strip().lower(),
        fallback_default_key_configured=bool(os.environ.get("JM_BOT_CHAT_DEFAULT_API_KEY", "").strip()),
        **options,
    )
    if split:
        from jmbot.business_api import ADMIN_METHODS
        from jmbot.service_rpc import mount_rpc
        mount_rpc(app, token=service_token, ledger=admin_db.with_name("admin-rpc.sqlite3"),
                  targets={"admin": app.state.admin_store}, allowed={"admin": ADMIN_METHODS})
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("JM_BOT_ADMIN_PORT", "8081")), log_level="info")


if __name__ == "__main__":
    main()
