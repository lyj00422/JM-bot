from types import SimpleNamespace

from jmbot.native_sessions import NativeSessions, current_scope


def test_webui_can_see_active_registered_kernel_without_chat_scope():
    sessions = NativeSessions.__new__(NativeSessions)
    kernel = object()
    sessions.contexts = {'group:123': {}}
    sessions.memory = SimpleNamespace(scope='group:123', host=object())

    token = current_scope.set(None)
    try:
        assert sessions.runtime_kernel_for_webui(lambda: kernel) is kernel
    finally:
        current_scope.reset(token)


def test_webui_cannot_see_kernel_without_active_registered_scope():
    sessions = NativeSessions.__new__(NativeSessions)
    sessions.contexts = {'group:123': {}}
    sessions.memory = SimpleNamespace(scope='group:123', host=None)
    assert sessions.runtime_kernel_for_webui(lambda: object()) is None

    sessions.memory.host = object()
    sessions.memory.scope = 'group:unregistered'
    assert sessions.runtime_kernel_for_webui(lambda: object()) is None
