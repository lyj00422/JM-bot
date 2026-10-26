import asyncio
from dataclasses import dataclass, field
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from jmbot import native_emoji
from jmbot.core.conversation_state import ConversationStateStore


@pytest.mark.parametrize('content, expected', [
    ('{"emoji_index":2,"reason":"开心"}', (2, '开心')),
    ('{"emoji_index":0,"reason":"没有合适的候选"}', (0, '没有合适的候选')),
])
def test_selection_requires_a_final_json_answer(content, expected):
    assert native_emoji.parse_selection(content, 8) == expected


@pytest.mark.parametrize('content', ['', 'still thinking', '[]', '{"emoji_index":true}',
                                     '{"emoji_index":9}', '{"emoji_index":-1}'])
def test_invalid_selection_is_not_reported_as_an_empty_sticker_library(content):
    with pytest.raises(ValueError, match='选图'):
        native_emoji.parse_selection(content, 8)


@dataclass
class Invocation:
    tool_name: str = 'reply'
    arguments: dict = field(default_factory=dict)
    call_id: str = 'reply-1'


@pytest.mark.parametrize('text_success,with_emoji,emoji_success,expected_calls', [
    (True, True, True, 1),
    (True, True, False, 1),
    (False, True, True, 0),
    (True, False, True, 0),
])
def test_reply_can_attach_one_sticker_without_replaying_delivered_text(
    text_success, with_emoji, emoji_success, expected_calls,
):
    invocation = Invocation(arguments={'msg_id': '42', 'with_emoji': with_emoji})
    text_result = SimpleNamespace(success=text_success, metadata={}, stop_after_execution=False, content='文字已发送')
    emoji_result = SimpleNamespace(success=emoji_success, metadata={'qq_delivery': {'confirmed': emoji_success}},
                                   error_message='' if emoji_success else '选图失败')
    reply = AsyncMock(return_value=text_result)
    emoji = AsyncMock(return_value=emoji_result)
    result = asyncio.run(native_emoji.reply_with_sticker(None, invocation, None,
                                                       reply_handler=reply, emoji_handler=emoji))
    assert result is text_result
    assert result.success is text_success
    assert reply.await_count == 1
    assert reply.call_args.args[1].arguments == {'msg_id': '42'}
    assert invocation.arguments['with_emoji'] is with_emoji
    assert emoji.await_count == expected_calls
    if expected_calls:
        sent = emoji.call_args.args[1]
        assert sent.tool_name == 'send_emoji'
        assert sent.call_id == 'reply-1:emoji'
        assert sent.arguments == {'msg_id': '42', 'source': 'all', 'explicit_request': False}
        assert result.metadata['attached_emoji']['success'] is emoji_success
        assert result.stop_after_execution is True


def test_confirmed_sticker_delivery_is_not_reported_as_unsent_after_a_later_failure():
    invocation = Invocation(arguments={'msg_id': '42', 'with_emoji': True})
    text = SimpleNamespace(success=True, metadata={}, stop_after_execution=False, content='文字已发送')
    emoji = SimpleNamespace(success=False, error_message='发送后的统计写入失败',
                            metadata={'qq_delivery': {'attempted': True, 'confirmed': True}})
    result = asyncio.run(native_emoji.reply_with_sticker(None, invocation, None,
        reply_handler=AsyncMock(return_value=text), emoji_handler=AsyncMock(return_value=emoji)))
    assert result.metadata['attached_emoji']['success'] is True
    assert result.content.endswith('附带表情已发送。')


@pytest.mark.parametrize('settings', [
    {'max_tokens': 8192, 'reasoning_effort': 'medium', 'temperature': 0.7},
    {'max_tokens': 16384, 'reasoning_effort': 'high', 'temperature': 0.5},
    {'max_tokens': 4096, 'thinking': {'type': 'disabled'}, 'temperature': 0.4},
])
def test_sticker_gateway_preserves_configured_model_settings(tmp_path, monkeypatch, settings):
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
    tasks = {name: {'enabled': True, 'model': name} for name in ('planner', 'replyer', 'utils')}
    tasks['emoji'] = {'enabled': True, 'model': 'glm-5.3-flash', 'visual': True}
    (tmp_path / 'routes.json').write_text(json.dumps({'providers': {
        'ds_school': {'base_url': 'https://school.test/v1', 'tasks': tasks},
    }}))
    app = create_chat_app()
    state = ConversationStateStore(tmp_path / 'state.db')
    state.set_policy('group:1', chat_enabled=True)
    pool = app.state.native_pool
    pool.close = AsyncMock()
    captured = []

    async def fetch(client, url, *, headers, body, retry):
        captured.append((url, dict(body), retry))
        return b'{"choices":[{"message":{"content":"{\\"emoji_index\\":1}"}}]}'

    monkeypatch.setattr('jmbot.model_request.fetch_model', fetch)
    with TestClient(app) as client:
        identity = pool.runtime_identity('qq:99:private:1')
        pool.processes[identity] = {
            'process': SimpleNamespace(returncode=None, pid=1), 'token': 'runtime-token',
            'started': time.time(), 'last_event': time.time(),
        }
        response = client.get('/native/policy/group/1', headers={'Authorization': 'Bearer runtime-token'})
        assert response.status_code == 200, response.text
        grant = response.json()
        response = client.post('/native/model/emoji/chat/completions',
            headers={'Authorization': 'Bearer ' + grant['token'], 'X-JM-Workload': 'sticker'},
            json={'messages': [{'role': 'user', 'content': 'choose a sticker'}], **settings})
        assert response.status_code == 200, response.text
    assert len(captured) == 1
    url, body, retry = captured[0]
    assert url == 'https://school.test/v1/chat/completions'
    assert body['model'] == 'glm-5.3-flash'
    for key, value in settings.items():
        assert body[key] == value
    assert ('thinking' in body) == ('thinking' in settings)
    assert ('reasoning_effort' in body) == ('reasoning_effort' in settings)
    assert body['response_format'] == {'type': 'json_object'}
    assert retry is True
