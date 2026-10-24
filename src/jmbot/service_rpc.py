"""Authenticated loopback RPC. Only the owning process opens business databases."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from contextlib import closing

from fastapi import FastAPI, HTTPException, Request


def encode(value):
    if is_dataclass(value):
        if hasattr(value, 'key'):
            return value.key
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        import base64
        return {'__bytes__': base64.b64encode(value).decode()}
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(type(value).__name__)


def sign(token: str, timestamp: str, request_id: str, payload: bytes) -> str:
    return hmac.new(token.encode(), timestamp.encode() + b'\n' + request_id.encode() + b'\n' + payload, hashlib.sha256).hexdigest()


class RPCClient:
    def __init__(self, url: str, token: str, *, timeout: float = 10):
        address = urlsplit(url)
        if address.scheme != 'http' or address.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('service RPC must use loopback HTTP')
        if len(token) < 32:
            raise ValueError('JM_BOT_SERVICE_TOKEN must contain at least 32 characters')
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout

    def call(self, target: str, method: str, *args, request_id: str | None = None, **kwargs):
        payload = json.dumps({'target': target, 'method': method, 'args': args, 'kwargs': kwargs}, default=encode, ensure_ascii=False).encode()
        identity = request_id or uuid.uuid4().hex
        timestamp = str(int(time.time()))
        request = urllib.request.Request(self.url + '/internal/rpc', data=payload, headers={
            'Content-Type': 'application/json', 'X-Request-ID': identity, 'X-Timestamp': timestamp,
            'X-Signature': sign(self.token, timestamp, identity, payload),
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            # Never include request bodies or upstream exception strings (keys).
            raise RuntimeError('business service unavailable; change is not confirmed applied') from None
        if 'error' in result:
            error = result['error']
            if error['type'] == 'ValueError':
                raise ValueError(error['message'])
            if error['type'] == 'KeyError':
                raise KeyError(error['message'])
            raise RuntimeError(error['message'])
        return result['result']


class RemoteStore:
    def __init__(self, client: RPCClient, target: str):
        self.client, self.target = client, target

    def __getattr__(self, method):
        def invoke(*args, **kwargs):
            result = self.client.call(self.target, method, *args, **kwargs)
            if method in {'get_policy', 'set_policy', 'add_growth', 'edit_growth'}:
                return SimpleNamespace(**result)
            if method == 'list_growth':
                return [SimpleNamespace(**entry) for entry in result]
            return result
        return invoke


def mount_rpc(app: FastAPI, *, token: str, ledger: str | Path, targets: dict, allowed: dict[str, set[str]]) -> None:
    if len(token) < 32:
        raise ValueError('service token must contain at least 32 characters')
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(ledger)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS rpc_results(id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL)')

    @app.post('/internal/rpc')
    async def rpc(request: Request):
        import asyncio
        chunks, length = [], 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > 48 * 1024 * 1024:
                raise HTTPException(413)
            chunks.append(chunk)
        payload = b''.join(chunks)
        identity = request.headers.get('x-request-id', '')
        timestamp = request.headers.get('x-timestamp', '')
        try:
            timely = abs(time.time() - int(timestamp)) <= 60
        except ValueError:
            timely = False
        if not timely or not identity or len(identity) > 100 or not hmac.compare_digest(request.headers.get('x-signature', ''), sign(token, timestamp, identity, payload)):
            raise HTTPException(401, 'invalid service authentication')
        try:
            body = json.loads(payload)
            target, method = body['target'], body['method']
            if not isinstance(target, str) or not isinstance(method, str) or not isinstance(body.get('args', []), list) or not isinstance(body.get('kwargs', {}), dict):
                raise ValueError()
            if method not in allowed.get(target, set()):
                raise HTTPException(403, 'unsupported service operation')
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(400, 'invalid RPC envelope') from None

        def execute():
            digest = hashlib.sha256(payload).hexdigest()
            with closing(sqlite3.connect(ledger, timeout=15)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                cached = conn.execute('SELECT digest,result FROM rpc_results WHERE id=?', (identity,)).fetchone()
                if cached:
                    if cached[0] != digest:
                        raise HTTPException(409, 'request ID reused with different content')
                    result = json.loads(cached[1])
                    if result.get('pending'):
                        raise HTTPException(409, 'operation pending or outcome unknown; inspect applied state before retry')
                    return result
                # Reserve before the side effect. A crash leaves an uncertain
                # outcome, never an automatic replay of a QQ send/approval.
                conn.execute('INSERT INTO rpc_results VALUES(?,?,?,?)', (identity, digest, '{"pending":true}', time.time()))
            # Never hold the ledger lock across a business or cross-service call.
            try:
                value = getattr(targets[target], method)(*body.get('args', []), **body.get('kwargs', {}))
                result = {'result': json.loads(json.dumps(value, default=encode, ensure_ascii=False))}
            except (ValueError, KeyError) as exc:
                result = {'error': {'type': type(exc).__name__, 'message': 'invalid, stale or conflicting service operation'}}
            except Exception:
                result = {'error': {'type': 'RuntimeError', 'message': 'service operation failed'}}
            with closing(sqlite3.connect(ledger, timeout=15)) as conn, conn:
                conn.execute('UPDATE rpc_results SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), identity))
            return result
        return await asyncio.to_thread(execute)

    @app.get('/internal/health')
    def health():
        return {'ok': True, 'targets': list(targets)}
