import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from jmbot.core.conversation_state import ConversationStateStore
from jmbot.native_pool import NativePool


def test_growth_refresh_keeps_authorization_but_rejects_stale_candidate(tmp_path):
    state = ConversationStateStore(tmp_path / 'state.db')
    scope = 'private:42'
    state.set_policy(scope, growth_enabled=True)
    lease = state.authorization_revision(scope)
    content = state.persona_revision(scope)
    candidate_revision = state.activation_revision(scope)
    entry = state.add_growth(scope, 'expression', '爱用简短句子', 'test evidence')
    assert state.authorization_revision(scope) == lease
    assert state.persona_revision(scope) != content
    assert state.activation_revision(scope) != candidate_revision
    state.edit_growth(entry.entry_id, content='喜欢简短回答', evidence='edited')
    assert state.authorization_revision(scope) == lease
    state.set_policy(scope, chat_blacklisted=True)
    assert state.authorization_revision(scope) != lease


def test_global_disable_enable_never_restores_old_lease(tmp_path):
    state = ConversationStateStore(tmp_path / 'state.db')
    lease = state.authorization_revision('private:42')
    state.set_global_enabled('chat', False)
    assert state.authorization_revision('private:42') != lease
    state.set_global_enabled('chat', True)
    assert state.authorization_revision('private:42') != lease


def test_scopes_persona_and_credentials_share_one_runtime(tmp_path):
    async def check():
        pool = NativePool(tmp_path)
        identity = 'qq:1:private:42'
        context = {'account': '1', 'gateway': 'http://local', 'revision': 'policy-1', 'token': 'token-1', 'tasks': {'vlm': {'model': 'v1'}}}
        frames = []
        async def drain():
            pass
        process = SimpleNamespace(returncode=None, stdin=SimpleNamespace(write=frames.append, drain=drain))
        runtime = pool.runtime_identity(identity)
        fingerprint = {key: context[key] for key in ('account', 'gateway')}
        pool.processes[runtime] = {
            'process': process, 'fingerprint': hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest(),
            'last_event': time.time(), 'persona_revision': 'growth-0',
        }
        async def stopped(_):
            raise RuntimeError('rotated')
        pool._stop = stopped
        await pool.deliver(identity, {'message_id': 1}, {**context, 'persona': 'new growth', 'persona_revision': 'growth-1'})
        assert len(frames) == 1
        assert pool.processes[runtime]['process'] is process
        await pool.deliver(identity, {}, {**context, 'revision': 'policy-2', 'token': 'replacement-credential'})
        peer = 'qq:1:group:99'
        await pool.deliver(peer, {}, {**context, 'token': 'peer-token'})
        assert len(pool.processes) == 1
        assert pool.scopes[identity]['context']['token'] == 'replacement-credential'
        assert pool.scopes[peer]['context']['token'] == 'peer-token'
        assert len(frames) == 3
    asyncio.run(check())


def test_refresh_rechecks_recent_delivery_and_idle_marker(tmp_path):
    async def check():
        pool = NativePool(tmp_path)
        identity = 'qq:1:private:42'
        directory = pool.root / identity.replace(':', '_')
        directory.mkdir()
        marker = directory / 'idle'
        pool.processes[identity] = {'last_event': time.time() - 20}
        stopped = []
        async def stop(value):
            stopped.append(value)
        pool._stop = stop
        assert not await pool.stop_if_idle(identity)
        marker.touch()
        # A new delivery happened after the supervisor observed the marker.
        pool.processes[identity]['last_event'] = time.time()
        assert not await pool.stop_if_idle(identity)
        pool.processes[identity]['last_event'] = time.time() - 20
        assert await pool.stop_if_idle(identity)
        assert stopped == [identity]
    asyncio.run(check())
