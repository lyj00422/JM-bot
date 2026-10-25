"""Credential-only command handler; never writes message history."""
from jmbot.core.parser import parse_chat_config_message
from jmbot.core.ingress import text_of


def handle_credential(event, store, state, admins):
    # Keep the exact command family recognizable so it can be retired without
    # ever being persisted or sent to an LLM.  Credentials are now a single
    # service-level school secret managed out of band by the operator.
    del event, store, state, admins
    return '聊天密钥命令已退役；聊天统一使用服务级学校凭据，请联系管理员在服务器配置中更新。'
