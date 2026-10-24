"""Credential-only command handler; never writes message history."""
from jmbot.core.parser import parse_chat_config_message
from jmbot.core.ingress import text_of


def handle_credential(event, store, state, admins):
    command = parse_chat_config_message(text_of(event))
    if command is None:
        return '密钥命令格式无效，请私聊使用 /ds 学校 <KEY>、/ds <KEY>、/glm <KEY>、/kimi <KEY> 或 /qwen <KEY>。'
    actor = int(event['user_id'])
    if event.get('message_type') != 'private' or event.get('sub_type') != 'friend':
        return '请在好友私聊中配置密钥。'
    kind, owner = ('group', command.group_id) if command.group_id else ('private', actor)
    if kind == 'group' and not admins.is_admin(actor):
        return '只有机器人注册管理员可配置群密钥。'
    provider = command.provider or 'ds_official'
    action = command.action
    if action in {'chat_private_key', 'chat_group_key_set'}:
        key = (command.text or '').strip()
        if not key or len(key) > 4096 or any(ch.isspace() for ch in key):
            return '密钥格式无效。'
        inherits = store.uses_default_private_key(owner) if kind == 'private' else store.uses_default_group_key(owner)
        if not inherits and store.has_scope_credentials(kind, owner):
            store.stage_credential_update(actor, kind, owner, provider, key, policy_version=state.policy_version(f'{kind}:{owner}'))
            prefix = '/' + provider.split('_')[0] + (' 学校' if provider.endswith('_school') else '')
            target = str(owner) + '\\' if kind == 'group' else ''
            return f'已暂存 5 分钟；覆盖请回复 {prefix} {target}确认，放弃请回复 {prefix} {target}取消。'
        store.replace_scope_credential(kind, owner, provider, key)
        state.set_policy(f'{kind}:{owner}', use_default_key=False)
        return f'{provider} 独立密钥已保存，默认继承已关闭。'
    if action == 'chat_key_confirm':
        applied, _ = store.apply_pending_credential_update(actor, kind, owner, provider=provider, policy_version=state.policy_version(f'{kind}:{owner}'))
        if applied:
            state.set_policy(f'{kind}:{owner}', use_default_key=False)
        return '独立密钥已更新。' if applied else '确认已过期、版本变化或没有待确认密钥。'
    if action == 'chat_key_cancel':
        store.cancel_pending_credential_update(actor, kind, owner)
        return '已取消待覆盖密钥。'
    if action in {'chat_private_status', 'chat_group_key_status', 'chat_status'}:
        configured = store.has_scope_credentials(kind, owner)
        inherited = store.uses_default_private_key(owner) if kind == 'private' else store.uses_default_group_key(owner)
        return f'{kind}:{owner} 独立密钥：{"已配置" if configured else "未配置"}；默认继承：{"开" if inherited else "关"}。'
    if action in {'chat_private_clear', 'chat_group_key_clear'}:
        method = store.remove_private_credential if kind == 'private' else store.remove_group_credential
        method(owner, provider)
        return '指定服务商密钥已清除。'
    return '聊天权限、历史和全局开关请在管理后台操作。'
