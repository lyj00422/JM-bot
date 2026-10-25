import json
import os
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from jmbot.admin_app import SUPER_ADMIN_ID, create_admin_app
from jmbot.native_pool import NativePool


def test_webui_token_rotates_and_never_returns_worker_credentials(tmp_path):
    pool = NativePool(tmp_path)
    identity = 'qq:99:runtime'
    pool.processes[identity] = {'process': SimpleNamespace(returncode=None),
                                'started': 100, 'token': 'worker-secret'}
    path = pool.directory(identity) / 'data' / 'webui.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'access_token': 'previous-login', 'token_source': 'temporary'}))
    os.utime(path, (90, 90))
    assert pool.webui_login()['token'] is None
    path.write_text(json.dumps({'access_token': 'current-login', 'token_source': 'temporary'}))
    assert pool.webui_login() == {'token': 'current-login', 'source': 'temporary'}
    path.write_text(json.dumps({'access_token': 'changed-login', 'token_source': 'configured'}))
    os.utime(path, (90, 90))
    assert pool.webui_login()['token'] == 'changed-login'
    assert 'worker-secret' not in str(pool.webui_login())
    pool.processes[identity]['process'].returncode = 0
    assert pool.webui_login()['token'] is None


def test_management_login_tokens_need_session_and_follow_tunnel_ports(tmp_path, monkeypatch):
    monkeypatch.setenv('JM_BOT_NAPCAT_WEBUI_TOKEN', 'napcat-login')
    native = SimpleNamespace(url='http://127.0.0.1:8082', token='service-secret')
    app = create_admin_app(admin_db=tmp_path / 'admin.db', conversation_db=tmp_path / 'state.db',
                           sticker_dir=tmp_path / 'stickers', native_client=native)
    app.state.admin_store.set_password(SUPER_ADMIN_ID, 'test-password-123', is_super=True)
    async_client = httpx.AsyncClient
    offline = False
    def respond(request):
        if request.url.path == '/internal/native/webui-login':
            assert request.headers['authorization'] == 'Bearer service-secret'
            if offline:
                return httpx.Response(503)
            return httpx.Response(200, json={'token': 'maibot-login', 'source': 'temporary'})
        return httpx.Response(200)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: async_client(transport=httpx.MockTransport(respond), **kwargs))
    with TestClient(app, base_url='http://127.0.0.1:18081') as client:
        assert client.get('/api/management/links').status_code == 401
        client.post('/api/login', json={'qq_id': SUPER_ADMIN_ID, 'password': 'test-password-123'})
        response = client.get('/api/management/links')
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()
        assert data['maibot']['login_token'] == 'maibot-login'
        assert data['napcat']['login_token'] == 'napcat-login'
        assert data['maibot']['url'] == 'http://127.0.0.1:18082'
        assert data['napcat']['url'] == 'http://127.0.0.1:18083'
        assert 'service-secret' not in response.text
        offline = True
        data = client.get('/api/management/links').json()
        assert data['maibot']['login_token'] is None
        assert data['napcat']['login_token'] == 'napcat-login'


def test_native_login_endpoint_requires_service_auth_and_does_not_use_rpc_ledger(tmp_path, monkeypatch):
    from jmbot.chat_app import create_chat_app
    for name, value in {
        'JM_BOT_SERVICE_TOKEN': 't' * 40,
        'JM_BOT_PREWARM_NATIVE': '0',
        'JM_BOT_CHAT_DATA': str(tmp_path / 'runtime'),
        'JM_BOT_CONVERSATION_DB': str(tmp_path / 'state.db'),
        'JM_BOT_CHAT_DB': str(tmp_path / 'keys.db'),
        'JM_BOT_CHAT_MASTER_KEY_PATH': str(tmp_path / 'master.key'),
        'JM_BOT_STICKER_DIR': str(tmp_path / 'stickers'),
        'JM_BOT_CHAT_ROLE': str(tmp_path / 'missing-role.json'),
        'JM_BOT_MODEL_ROUTES': str(tmp_path / 'routes.json'),
    }.items():
        monkeypatch.setenv(name, value)
    app = create_chat_app()
    monkeypatch.setattr(app.state.native_pool, 'webui_login', lambda: {'token': 'webui-secret', 'source': 'temporary'})
    with TestClient(app) as client:
        assert client.get('/internal/native/webui-login').status_code == 401
        assert client.get('/internal/native/webui-login', headers={'Authorization': 'Bearer worker-secret'}).status_code == 401
        response = client.get('/internal/native/webui-login', headers={'Authorization': 'Bearer ' + 't' * 40})
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['token'] == 'webui-secret'
    import sqlite3
    with sqlite3.connect(tmp_path / 'runtime' / 'rpc.sqlite3') as db:
        assert not db.execute("SELECT id FROM rpc_results WHERE result LIKE '%webui-secret%'").fetchall()
