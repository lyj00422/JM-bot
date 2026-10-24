import asyncio
import json

import pytest

from jmbot.core.conversation_state import ConversationStateStore
from jmbot.persona_refinery import PersonaRefinery


def setup_state(tmp_path):
    state = ConversationStateStore(tmp_path / 'state.db')
    state.set_policy('group:1', growth_enabled=True, default_persona=False)
    state.set_base_persona('group:1', '我是鱼，保留明确约束。')
    state.add_growth('group:1', 'expression', '习惯自称本鱼', '消息 1')
    state.set_policy('group:2', growth_enabled=True, default_persona=False)
    state.set_base_persona('group:2', '另一个群的人设')
    return state


def test_every_open_refines_without_applying_then_save_continues_growth(tmp_path):
    state = setup_state(tmp_path)
    calls = []
    async def generate(scope, prompt, **kwargs):
        calls.append(scope)
        assert '另一个群的人设' not in prompt
        return json.dumps({'persona': '我是鱼，自称本鱼，保留明确约束。', 'notes': ['合并表达']})
    async def check():
        refinery = PersonaRefinery(state, generate)
        revision = state.activation_revision('group:1')
        await refinery.draft('group:1')
        draft = await refinery.draft('group:1')
        assert calls == ['group:1', 'group:1']
        assert state.activation_revision('group:1') == revision
        assert not state.list_growth('group:1')[0].frozen
        state.replace_effective_persona('group:1', draft['persona'], expected_version=draft['base_version'], expected_revision=draft['effective_revision'])
        assert state.list_growth('group:1')[0].frozen
        assert state.get_policy('group:1').growth_enabled
        state.add_growth('group:1', 'interest', '喜欢新话题', '消息 2')
        assert '喜欢新话题' in state.effective_persona('group:1', '')
        assert state.get_base_persona('group:2')[0] == '另一个群的人设'
    asyncio.run(check())


@pytest.mark.parametrize('result', ['broken', '{}', '{"persona":""}', '{"persona":"ok","notes":42}'])
def test_invalid_draft_preserves_state_and_releases_lock(tmp_path, result):
    state = setup_state(tmp_path)
    async def generate(*args, **kwargs):
        return result
    async def check():
        refinery = PersonaRefinery(state, generate)
        revision = state.activation_revision('group:1')
        for _ in range(2):
            with pytest.raises(ValueError):
                await refinery.draft('group:1')
        assert state.activation_revision('group:1') == revision
    asyncio.run(check())


def test_concurrent_edit_rejects_stale_draft(tmp_path):
    state = setup_state(tmp_path)
    async def generate(*args, **kwargs):
        state.add_growth('group:1', 'expression', '新变化', '消息 2')
        return '{"persona":"旧稿"}'
    with pytest.raises(ValueError, match='有变化'):
        asyncio.run(PersonaRefinery(state, generate).draft('group:1'))
    assert state.get_base_persona('group:1')[0] == '我是鱼，保留明确约束。'


def test_178_records_compact_automatically_with_audit_history(tmp_path):
    state = setup_state(tmp_path)
    for i in range(177):
        state.add_growth('group:1', 'expression', '本鱼的重复表达' + str(i), '历史记录')
    async def generate(scope, prompt, **kwargs):
        assert len(json.loads(prompt.split('\n', 1)[1])['growth']) == 178
        return '{"summary":"自称本鱼，喜欢简短回应。"}'
    assert asyncio.run(PersonaRefinery(state, generate).compact('group:1'))
    assert len(state.list_growth('group:1')) == 178
    effective = state.effective_persona('group:1', '')
    assert '自称本鱼，喜欢简短回应。' in effective
    assert len(effective) < 2000
    first_version = state.get_persona_digest('group:1')['version']
    state.add_growth('group:1', 'expression', '新的稳定表达', '消息 3')
    assert state.get_persona_digest('group:1')['version'] == first_version
    snapshot = state.persona_refinery_input('group:1')
    assert snapshot['growth_count'] == 179
    assert snapshot['input']['summary']['summary'] == '自称本鱼，喜欢简短回应。'
    assert [item['content'] for item in snapshot['input']['growth']] == ['新的稳定表达']
    assert len(state.list_growth('group:1')) == 179
