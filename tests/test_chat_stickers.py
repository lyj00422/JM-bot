import base64
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jmbot.core.chat_service import (
    ChatRole, build_group_messages, build_private_messages,
    parse_group_action, split_chat_reply,
)


def test_sticker_only_reply_and_mixed_media_are_safe():
    assert split_chat_reply('[sticker:131]') == [[('sticker', '131')]]
    assert parse_group_action('{"reply":"[sticker:131]"}', direct=True).reply == '[sticker:131]'
    chunks = split_chat_reply(
        '[sticker:../131][sticker:003][sticker:999][qq_face:14]'
        '开心[sticker:131][sticker:130][qq_face:401][qq_face:375]'
    )
    assert chunks == [[('text', '开心'), ('sticker', '131'), ('face', '401')]]


def test_unknown_sticker_cannot_leak_into_reply_or_count_as_direct_response():
    assert split_chat_reply('[sticker:https://example.com/x.jpg][sticker:999]') == []
    assert parse_group_action('{"reply":"[sticker:999]"}', direct=True).reply == '嗯?'


def test_stickers_reach_private_and_group_prompts_with_authored_context():
    role = ChatRole('大肥鱼', '角色的其他性格。')
    for messages in (
        build_private_messages(role, None, [], '你好'),
        build_group_messages(role, [], '甲', '你好'),
    ):
        prompt = messages[0]['content']
        assert '角色的其他性格。' in prompt
        assert '[sticker:012]' in prompt
        assert '[sticker:106]' in prompt
        assert '分享美食' in prompt
        assert '压力' in prompt
        assert '可以频繁' in prompt
        assert '不必每句' in prompt


def test_catalog_matches_checked_in_images_and_preserves_user_descriptions():
    from jmbot.core.chat_stickers import STICKERS, STICKER_DIR, sticker_path

    assert len(STICKERS) == 88
    assert {item.name for item in STICKER_DIR.glob('*.jpg')} == {
        item['file'] for item in STICKERS.values()
    }
    assert '比如大肥鱼发压力' in STICKERS['012']['description']
    assert '群友分享美食' in STICKERS['106']['description']
    for key, item in STICKERS.items():
        assert sticker_path(key).is_file()
        assert item['usage']
    assert sticker_path('../131') is None
    assert sticker_path('999') is None


def test_missing_image_is_filtered_even_with_valid_catalog_id():
    from jmbot.core import chat_stickers

    with patch.object(Path, 'is_file', return_value=False):
        assert chat_stickers.sticker_path('131') is None
        assert split_chat_reply('[sticker:131]') == []
