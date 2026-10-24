import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from jmbot.core.conversation_state import ConversationStateStore
from jmbot.service_rpc import sign


def test_shared_runtime_grants_isolate_send_and_revoke_one_scope(tmp_path, monkeypatch):
    from jmbot.chat_app import create_chat_app
    for name, value in {
        'JM_BOT_SERVICE_TOKEN': 't' * 40, 'JM_BOT_QQ_ACCOUNT': '99',
        'JM_BOT_CHAT_DATA': str(tmp_path / 'runtime'),
        'JM_BOT_CONVERSATION_DB': str(tmp_path / 'state.db'),
        'JM_BOT_CHAT_DB': str(tmp_path / 'keys.db'),
        'JM_BOT_CHAT_MASTER_KEY_PATH': str(tmp_path / 'master.key'),
        'JM_BOT_STICKER_DIR': str(tmp_path / 'stickers'),
        'JM_BOT_CHAT_ROLE': str(tmp_path / 'missing-role.json'),
        'JM_BOT_MODEL_ROUTES': str(tmp_path / 'routes.json'),
        'JM_BOT_ADMIN_SERVICE_URL': 'http://127.0.0.1:1',
        'JM_BOT_CHAT_DEFAULT_API_KEY': 'fake-provider-key',
    }.items():
        monkeypatch.setenv(name, value)
    (tmp_path / 'routes.json').write_text(json.dumps({'providers': {'ds_school': {'base_url': 'https://provider.test/v1', 'tasks': {name: {'enabled': True, 'model': name} for name in ('planner', 'replyer', 'utils')}}}}))
    app = create_chat_app()
    state = ConversationStateStore(tmp_path / 'state.db')
    state.set_policy('group:1', chat_enabled=True)
    state.set_policy('group:2', chat_enabled=True)
    pool = app.state.native_pool
    captured = {}
    async def deliver(identity, event, context):
        captured[context['scope']] = context
        pool.scopes[identity] = {'context': context, 'last_event': time.time()}
        pool.processes[pool.runtime_identity(identity)] = {'process': SimpleNamespace(returncode=None, pid=1), 'token': 'runtime-token', 'started': time.time(), 'last_event': time.time()}
    pool.deliver = deliver
    pool.close = AsyncMock()
    sends = []
    from jmbot.service_rpc import RPCClient
    original_call = RPCClient.call
    def call(client, target, method, *args, **kwargs):
        if target == 'qq' and method == 'send':
            sends.append(args)
            return {'message_id': 8}
        return original_call(client, target, method, *args, **kwargs)
    monkeypatch.setattr(RPCClient, 'call', call)
    with TestClient(app) as client:
        for group in (1, 2):
            event = {'post_type': 'message', 'message_type': 'group', 'self_id': 99, 'group_id': group, 'user_id': 42, 'message_id': group, 'message': [{'type': 'text', 'data': {'text': 'hello'}}]}
            body = json.dumps({'target': 'ingress', 'method': 'accept', 'args': [event], 'kwargs': {}}).encode()
            stamp, identity = str(int(time.time())), 'event-' + str(group)
            response = client.post('/internal/rpc', content=body, headers={'X-Request-ID': identity, 'X-Timestamp': stamp, 'X-Signature': sign('t' * 40, stamp, identity, body)})
            assert response.status_code == 200
        deadline = time.monotonic() + 5
        while len(captured) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(captured) == 2
        assert len(pool.processes) == 1
        first = {'Authorization': 'Bearer ' + captured['group:1']['token']}
        second = {'Authorization': 'Bearer ' + captured['group:2']['token']}
        assert client.get('/native/context', headers=first).json()['scope'] == 'group:1'
        assert client.get('/native/context', headers=second).json()['scope'] == 'group:2'
        assert client.get('/native/status', headers={'Authorization': 'Bearer runtime-token'}).status_code == 403
        assert client.post('/native/send', headers=first, json={'message': [{'type': 'text', 'data': {'text': 'hi'}}]}).status_code == 200
        assert sends[-1][1] == 'group:1'
        state = ConversationStateStore(tmp_path / 'state.db')
        state.set_policy('group:1', chat_blacklisted=True)
        assert client.get('/native/status', headers=first).status_code == 403
        assert client.post('/native/send', headers=first, json={'message': []}).status_code == 403
        assert client.get('/native/status', headers=second).status_code == 200
