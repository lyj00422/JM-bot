import pytest
from jmbot.core.ingress import classify
from jmbot.native_emoji import EmojiOwnership, validate_image


@pytest.mark.parametrize('sub_type', ['friend', 'group', 'other'])
def test_private_messages_including_temporary_sessions_are_chat(sub_type):
    event = {'post_type': 'message', 'message_type': 'private', 'sub_type': sub_type,
             'self_id': 99, 'user_id': 42, 'group_id': 1, 'message': 'hello'}
    route = classify(event)
    assert route.domain == 'chat'
    assert route.scope == 'private:42'


@pytest.mark.parametrize('payload,expected', [
    ({'post_type': 'message', 'message_type': 'private', 'sub_type': 'group', 'group_id': 1, 'user_id': 42}, 'private:42'),
    ({'post_type': 'message', 'message_type': 'group', 'group_id': 1, 'user_id': 42}, 'group:1'),
    ({'post_type': 'notice', 'group_id': 1, 'user_id': 42}, 'group:1'),
])
def test_official_connector_scopes_temporary_private_messages(monkeypatch, payload, expected):
    import importlib
    import sys
    import types
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / 'plugins' / 'maibot-team.snowluma-adapter'
    package_name = 'scope_router_test'
    for name, directory in [(package_name, root), (package_name + '.runtime', root / 'runtime')]:
        package = types.ModuleType(name)
        package.__path__ = [str(directory)]
        monkeypatch.setitem(sys.modules, name, package)
    for suffix, attributes in {
        'config': {'QQPluginSettings': object},
        'profile': {'PROFILE_BY_TYPE': {}, 'probe_client_profile': None},
        'types': {'QQPayloadDict': dict},
        'runtime.bundle': {'QQRuntimeBundle': object},
    }.items():
        module = types.ModuleType(package_name + '.' + suffix)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    router = importlib.import_module(package_name + '.runtime.router').QQEventRouter
    assert router._message_scope(payload) == expected


@pytest.mark.parametrize('kind,segments,expected', [
    ('private', [{'type': 'text', 'data': {'text': '鱼，在吗'}}], False),
    ('group', [{'type': 'reply', 'data': {'id': '1'}}], False),
    ('group', [{'type': 'text', 'data': {'text': '大肥鱼'}}], False),
    ('group', [{'type': 'at', 'data': {'qq': '43'}}], False),
    ('group', [{'type': 'at', 'data': {'qq': '99'}}], True),
])
def test_only_explicit_at_is_prioritized(kind, segments, expected):
    event = {'post_type': 'message', 'message_type': kind, 'sub_type': 'friend', 'self_id': 99, 'user_id': 42, 'group_id': 1, 'message': segments}
    assert classify(event).domain == 'chat'
    assert classify(event).direct is expected


@pytest.mark.parametrize(('text', 'domain'), [
    ('/jm 12345', 'download'),
    ('/jmg 清空', 'download'),
    ('/ds key-value', 'chat'),
    ('普通聊天', 'chat'),
])
def test_only_download_commands_are_claimed_by_nonebot(text, domain):
    event = {'post_type': 'message', 'message_type': 'group', 'self_id': 99, 'user_id': 42,
             'group_id': 1, 'message': [{'type': 'text', 'data': {'text': text}}]}
    assert classify(event).domain == domain


def test_dual_ws_forward_connector_filter_matches_only_complete_jm_commands():
    import importlib.util
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "maibot-team.snowluma-adapter"
        / "ingress_filter.py"
    )
    spec = importlib.util.spec_from_file_location("snowluma_ingress_filter", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    def payload(text):
        return {"message": [{"type": "text", "data": {"text": text}}]}

    prefixes = ["/jm", "/jmg"]
    assert module.is_ignored_command(payload("  /JM 12345"), prefixes)
    assert module.is_ignored_command(payload("/jmg clear"), prefixes)
    assert not module.is_ignored_command(payload("/jmatrix is ordinary text"), prefixes)
    assert not module.is_ignored_command(payload("hello /jm 12345"), prefixes)


def test_emoji_provenance_survives_restart_without_group_sharing(tmp_path):
    from PIL import Image
    import io
    data = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(data, format='PNG')
    path = tmp_path / 'ownership.db'
    digest = EmojiOwnership(path).record('group:1', data.getvalue())
    reopened = EmojiOwnership(path)
    assert reopened.hashes('group:1') == {digest}
    assert reopened.hashes('group:2') == set()
    with pytest.raises(ValueError, match='byte budget'):
        validate_image(b'x' * (8 * 1024 * 1024 + 1))
