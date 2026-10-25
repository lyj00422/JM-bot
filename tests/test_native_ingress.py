import pytest
from jmbot.core.ingress import classify
from jmbot.native_emoji import EmojiOwnership, validate_image


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
