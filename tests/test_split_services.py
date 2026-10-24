import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jmbot.admin_app import AdminStore, SUPER_ADMIN_ID, create_admin_app
from jmbot.core.chat_service import ChatStore
from jmbot.core.conversation_state import ConversationStateStore
from jmbot.core.ingress import classify
from jmbot.core.ingress_queue import IngressQueue
from jmbot.domain_state import DomainState, PartialApplyError
from jmbot.migration import snapshot, verify, restore, migrate
from jmbot.service_rpc import mount_rpc, sign


def test_rpc_auth_replay_and_conflict(tmp_path):
    class Owner:
        calls = 0
        def change(self, value):
            self.calls += 1
            return value
    owner = Owner()
    app = FastAPI()
    token = 't' * 40
    mount_rpc(app, token=token, ledger=tmp_path / 'rpc.db', targets={'owner': owner}, allowed={'owner': {'change'}})
    with TestClient(app) as client:
        def invoke(value, identity='once', method='change', signature=True):
            body = json.dumps(dict(target='owner', method=method, args=[value], kwargs={})).encode()
            timestamp = str(int(time.time()))
            return client.post('/internal/rpc', content=body, headers={'X-Request-ID': identity, 'X-Timestamp': timestamp, 'X-Signature': sign(token, timestamp, identity, body) if signature else 'bad'})
        assert invoke('value', signature=False).status_code == 401
        assert invoke('value').json() == {'result': 'value'}
        assert invoke('value').json() == {'result': 'value'}
        assert owner.calls == 1
        assert invoke('changed').status_code == 409
        assert invoke('value', identity='unsupported', method='_private').status_code == 403


def test_ingress_secrets_commands_and_fairness():
    def event(text, group=None):
        return dict(post_type='message', message_type='group' if group else 'private', sub_type='normal' if group else 'friend', self_id=123, user_id=42, group_id=group, message=[{'type': 'text', 'data': {'text': text}}])
    assert classify(event('/DS school-secret')).domain == 'credential'
    assert classify(event('/jm help')).domain == 'download'
    assert classify(event('/jmg anything')).domain == 'drop'
    async def check():
        queue = IngressQueue(maxsize=4, per_scope=2)
        queue.put_nowait(event('A1', 1))
        queue.put_nowait(event('A2', 1))
        queue.put_nowait(event('A3', 1))
        queue.put_nowait(event('B', 2))
        queue.put_nowait(event('private'))
        assert queue.qsize() == 4
        assert (await queue.get())['group_id'] == 1
        assert (await queue.get())['group_id'] == 2
        assert (await queue.get())['message_type'] == 'private'
    asyncio.run(check())


def test_download_policy_does_not_need_chat_and_cross_domain_attempts_both(tmp_path):
    class Offline:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError('offline')
            return fail
    download = ConversationStateStore(tmp_path / 'download.db')
    facade = DomainState(Offline(), download)
    assert facade.set_policy('group:42', download_enabled=True).download_enabled
    with pytest.raises(PartialApplyError) as failed:
        facade.set_policy('group:42', chat_enabled=False, download_enabled=False)
    assert failed.value.domains['chat']['applied'] is None
    assert failed.value.domains['download']['applied'] is True
    assert not download.permitted('group:42', 'download')


def test_admin_csrf_session_revocation_and_role(tmp_path):
    app = create_admin_app(admin_db=tmp_path / 'admin.db', conversation_db=tmp_path / 'state.db', sticker_dir=tmp_path / 'stickers')
    store = app.state.admin_store
    store.set_password(SUPER_ADMIN_ID, 'test-password-123', is_super=True)
    with TestClient(app) as client:
        assert client.post('/api/login', json={'qq_id': SUPER_ADMIN_ID, 'password': 'test-password-123'}).status_code == 200
        assert client.put('/api/help/jm', json={'content': 'help'}).status_code == 403
        csrf = client.cookies['jmbot_csrf']
        assert client.put('/api/help/jm', json={'content': 'help'}, headers={'X-CSRF-Token': csrf}).status_code == 200
        store.set_password(SUPER_ADMIN_ID, 'changed-password-123', is_super=True)
        assert client.get('/api/me').status_code == 401
        store.set_password(42, 'other-password-123')
        assert client.post('/api/login', json={'qq_id': 42, 'password': 'other-password-123'}).status_code == 200
        csrf = client.cookies['jmbot_csrf']
        assert client.post('/api/admins', json={'qq_id': 43, 'password': 'third-password-123'}, headers={'X-CSRF-Token': csrf}).status_code == 403


def test_admin_effective_persona_editor_round_trip(tmp_path):
    app = create_admin_app(admin_db=tmp_path / 'admin.db', conversation_db=tmp_path / 'state.db', sticker_dir=tmp_path / 'stickers')
    app.state.admin_store.set_password(SUPER_ADMIN_ID, 'test-password-123', is_super=True)
    state = ConversationStateStore(tmp_path / 'state.db')
    state.set_policy('group:42', growth_enabled=True, default_persona=False)
    state.set_base_persona('group:42', '基础人设')
    state.add_growth('group:42', 'interest', '喜欢鲸鱼', '消息 1: 聊到鲸鱼')
    with TestClient(app) as client:
        assert client.post('/api/login', json={'qq_id': SUPER_ADMIN_ID, 'password': 'test-password-123'}).status_code == 200
        csrf = client.cookies['jmbot_csrf']
        detail = client.get('/api/scopes/group/42').json()
        assert '喜欢鲸鱼' in detail['effective_persona']
        saved = client.put('/api/scopes/group/42/persona/effective', json={'prompt': detail['effective_persona'], 'expected_version': detail['base_version'], 'expected_revision': detail['effective_revision']}, headers={'X-CSRF-Token': csrf})
        assert saved.status_code == 200, saved.text
        refreshed = client.get('/api/scopes/group/42').json()
        assert refreshed['effective_persona'] == detail['effective_persona']
        assert all(entry['frozen'] for entry in refreshed['growth'])
        state.add_growth('group:42', 'expression', '新的表达', '消息 2')
        conflict = client.put('/api/scopes/group/42/persona/effective', json={'prompt': '过期编辑稿', 'expected_version': refreshed['base_version'], 'expected_revision': refreshed['effective_revision']}, headers={'X-CSRF-Token': csrf})
        assert conflict.status_code == 409
        assert state.get_base_persona('group:42')[0] == detail['effective_persona']
        assert not state.list_growth('group:42')[-1].frozen


def test_sqlite_wal_backup_restore_migration_and_key_check(tmp_path):
    source = tmp_path / 'live'
    source.mkdir()
    state = ConversationStateStore(source / 'conversation.sqlite3')
    state.set_policy('group:42', download_enabled=True, chat_enabled=False)
    state.set_global_enabled('download', False)
    key = Fernet.generate_key()
    (source / 'chat-master.key').write_bytes(key)
    chat = ChatStore(source / 'chat.sqlite3', master_key=key)
    chat.replace_scope_credential('private', 42, 'ds_official', 'never-print-this-secret')
    with closing(sqlite3.connect(source / 'conversation.sqlite3')) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE extra(value TEXT)')
        connection.execute('INSERT INTO extra VALUES(?)', ('uncheckpointed',))
        connection.commit()
        backup = tmp_path / 'backup'
        snapshot(source, backup)
    verify(backup)
    report = migrate(backup, tmp_path / 'migrated', apply=True)
    assert report['applied']
    assert 'never-print-this-secret' not in json.dumps(report)
    assert report == migrate(backup, tmp_path / 'migrated', apply=True)
    download = ConversationStateStore(tmp_path / 'migrated/data/download-policy.sqlite3')
    assert download.get_policy('group:42').download_enabled
    assert not download.global_enabled('download')
    restore(backup, tmp_path / 'restored')
    with closing(sqlite3.connect(tmp_path / 'restored/data/conversation.sqlite3')) as connection:
        assert connection.execute('SELECT value FROM extra').fetchone()[0] == 'uncheckpointed'
    (backup / 'data/chat-master.key').write_bytes(Fernet.generate_key())
    with pytest.raises(ValueError):
        verify(backup)


def test_scoped_activation_revision(tmp_path):
    state = ConversationStateStore(tmp_path / 'state.db')
    before = state.activation_revision('private:42')
    state.set_policy('group:99', chat_enabled=True)
    assert before == state.activation_revision('private:42')
    state.set_policy('private:42', chat_blacklisted=True)
    assert before != state.activation_revision('private:42')
    enabled = state.activation_revision('private:42')
    state.set_global_enabled('chat', False)
    state.set_global_enabled('chat', True)
    assert enabled != state.activation_revision('private:42')


def test_policy_delivery_survives_restart_and_reconciles(tmp_path):
    class Switch:
        offline = True
        def __getattr__(self, name):
            if self.offline:
                raise RuntimeError('offline')
            return getattr(chat, name)
    chat = ConversationStateStore(tmp_path / 'chat.db')
    download = ConversationStateStore(tmp_path / 'download.db')
    remote = Switch()
    journal = tmp_path / 'deliveries.db'
    with pytest.raises(PartialApplyError):
        DomainState(remote, download, journal).set_policy('group:7', chat_enabled=True)
    restored = DomainState(remote, download, journal)
    assert restored.delivery_status()[0]['applied'] is None
    remote.offline = False
    restored.reconcile()
    assert chat.permitted('group:7')
    assert restored.delivery_status()[0]['applied'] == 1
    version = chat.policy_version('group:7')
    restored.reconcile()
    assert chat.policy_version('group:7') == version


def test_pending_key_binds_policy_provider_and_current_credential(tmp_path):
    store = ChatStore(tmp_path / 'chat.db', master_key=Fernet.generate_key())
    store.replace_scope_credential('private', 42, 'ds_official', 'original')
    store.stage_credential_update(42, 'private', 42, 'kimi_official', 'candidate', policy_version=4)
    assert not store.apply_pending_credential_update(42, 'private', 42, provider='kimi_official', policy_version=5)[0]
    store.stage_credential_update(42, 'private', 42, 'kimi_official', 'candidate', policy_version=4)
    assert not store.apply_pending_credential_update(42, 'private', 42, provider='ds_official', policy_version=4)[0]
    store.stage_credential_update(42, 'private', 42, 'kimi_official', 'candidate', policy_version=4)
    store.replace_scope_credential('private', 42, 'ds_official', 'admin-replaced')
    assert not store.apply_pending_credential_update(42, 'private', 42, provider='kimi_official', policy_version=4)[0]
    store.stage_credential_update(42, 'private', 42, 'kimi_official', 'candidate', policy_version=4)
    assert store.apply_pending_credential_update(42, 'private', 42, provider='kimi_official', policy_version=4) == (True, 'kimi_official')
    assert not store.apply_pending_credential_update(42, 'private', 42, provider='kimi_official', policy_version=4)[0]


def test_approval_unknown_result_requires_reconciliation(tmp_path):
    store = AdminStore(tmp_path / 'admin.db')
    store.upsert_approval('friend', '42', 'approved', {'flag': 'request-1'})
    action = store.queue_approval_action('friend', '42', 'request-1', 'approve')
    assert store.queue_approval_action('friend', '42', 'request-1', 'approve') == action
    assert store.begin_approval(action)
    assert not store.begin_approval(action)
    assert not store.pending_approval_actions()
    assert store.approvals()[0]['outcome_unknown']
    store.reconcile_approval(action, True)
    assert store.pending_approval_actions()[0]['napcat_applied']
    store.complete_approval_action(action)
    assert store.approvals()[0]['apply_state'] == 'applied'


def test_growth_cancellation_preserves_durable_job(tmp_path):
    from jmbot.growth_worker import GrowthWorker
    state = ConversationStateStore(tmp_path / 'state.db')
    state.set_policy('private:42', chat_enabled=True, growth_enabled=True)
    for index in range(30):
        state.record_source('private:42', str(index), '42', 'source', time.time() + index)
    async def check():
        started = asyncio.Event()
        async def generate(*args):
            started.set()
            await asyncio.Event().wait()
        worker = GrowthWorker(state, generate)
        await worker.offer('private:42')
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert state.pending_growth_scopes() == ['private:42']
    asyncio.run(check())


def test_snapshot_rejects_path_escape(tmp_path):
    root = tmp_path / 'snapshot'
    root.mkdir()
    (root / 'snapshot.json').write_text(json.dumps({'schema': 1, 'files': {'../outside': 'x'}}))
    with pytest.raises(ValueError, match='path'):
        verify(root)


def test_chat_coordinator_starts_and_stops_without_business_peers(tmp_path, monkeypatch):
    from jmbot.chat_app import create_chat_app
    for name, value in {
        'JM_BOT_SERVICE_TOKEN': 't' * 40,
        'JM_BOT_CHAT_DATA': str(tmp_path / 'runtime'),
        'JM_BOT_CONVERSATION_DB': str(tmp_path / 'state.db'),
        'JM_BOT_CHAT_DB': str(tmp_path / 'keys.db'),
        'JM_BOT_CHAT_MASTER_KEY_PATH': str(tmp_path / 'master.key'),
        'JM_BOT_STICKER_DIR': str(tmp_path / 'stickers'),
        'JM_BOT_CHAT_ROLE': str(tmp_path / 'missing-role.json'),
        'JM_BOT_MODEL_ROUTES': str(tmp_path / 'routes.json'),
        'JM_BOT_ADMIN_SERVICE_URL': 'http://127.0.0.1:1',
    }.items():
        monkeypatch.setenv(name, value)
    with TestClient(create_chat_app()) as client:
        assert client.get('/health').json()['ok']
        assert client.get('/native/status').status_code == 403
