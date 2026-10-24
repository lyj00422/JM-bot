"""Classify raw OneBot JSON before adapter logging or media decoding."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    domain: str
    scope: str | None = None
    direct: bool = False


def text_of(event: dict) -> str:
    message = event.get('message', event.get('raw_message', ''))
    if isinstance(message, list):
        return ''.join(str(segment.get('data', {}).get('text', '')) for segment in message if isinstance(segment, dict) and isinstance(segment.get('data'), dict) and segment.get('type') == 'text')
    return html.unescape(re.sub(r'\[CQ:[^]]*\]', '', str(message)))


def classify(event: dict, prefix: str = '/jm') -> Route:
    post = event.get('post_type')
    if post == 'request':
        return Route('management')
    if post == 'meta_event':
        return Route('lifecycle')
    kind = event.get('message_type')
    if post == 'notice' and event.get('sub_type') == 'poke':
        kind = 'group' if event.get('group_id') else 'private'
    if kind not in {'group', 'private'} or post not in {'message', 'notice'}:
        return Route('drop')
    if post == 'notice' and event.get('sub_type') != 'poke':
        return Route('drop')
    if kind == 'private' and post == 'message' and event.get('sub_type') != 'friend':
        return Route('drop')
    owner = event.get('group_id') if kind == 'group' else event.get('user_id')
    if not str(owner or '').isdigit() or int(owner) < 1:
        return Route('drop')
    scope = f'{kind}:{int(owner)}'
    text = text_of(event).lstrip()
    # Consume malformed credential commands too: they must never reach a model.
    if re.match(r'^/(ds|glm|kimi|qwen)', text, re.I):
        return Route('credential', scope, True)
    if re.match(r'^/jmg', text, re.I) or re.match(re.escape(prefix) + r'\s+管理(?=\s|$)', text, re.I):
        return Route('drop', scope)
    if re.match(re.escape(prefix) + r'(?=\s|$)', text, re.I):
        return Route('download', scope, True)
    if str(event.get('user_id')) == str(event.get('self_id')):
        return Route('drop', scope)
    if post == 'notice' and str(event.get('target_id')) != str(event.get('self_id')):
        return Route('drop', scope)
    message = event.get('message', [])
    mentioned = isinstance(message, list) and any(segment.get('type') == 'at' and isinstance(segment.get('data'), dict) and str(segment['data'].get('qq')) == str(event.get('self_id')) for segment in message if isinstance(segment, dict))
    return Route('chat', scope, kind == 'private' or mentioned or post == 'notice')


def account_scope(event: dict, scope: str) -> str:
    account = str(event.get('self_id', ''))
    if not account.isdigit() or int(account) < 1:
        raise ValueError('missing bot account')
    return f'qq:{account}:{scope}'
