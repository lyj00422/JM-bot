import importlib.util
import sys
import types
from pathlib import Path

import pytest


_ADAPTER_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "maibot-team.snowluma-adapter"


def _load_runtime_state_module():
    package_name = "snowluma_runtime_state_test_package"
    package = types.ModuleType(package_name)
    package.__path__ = [str(_ADAPTER_ROOT)]
    sys.modules[package_name] = package

    config_module = types.ModuleType(f"{package_name}.config")

    class QQServerConfig:
        connection_id = ""

        @staticmethod
        def build_ws_url():
            return "ws://127.0.0.1:3001"

    config_module.QQServerConfig = QQServerConfig
    sys.modules[config_module.__name__] = config_module

    module_name = f"{package_name}.runtime_state"
    spec = importlib.util.spec_from_file_location(module_name, _ADAPTER_ROOT / "runtime_state.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, QQServerConfig


class _Gateway:
    def __init__(self):
        self.calls = []

    async def update_state(self, gateway_name, **kwargs):
        self.calls.append((gateway_name, kwargs))
        return True


class _Logger:
    def warning(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_heartbeat_preserves_dynamic_message_scope():
    module, config_type = _load_runtime_state_module()
    gateway = _Gateway()
    state = module.QQRuntimeStateManager(gateway, _Logger(), "snowluma_gateway")

    assert await state.report_connected("2661195769", config_type())
    assert await state.report_message_scope("group:1129150377")
    assert state.scope == "group:1129150377"

    assert await state.report_connected("2661195769", config_type(), preserve_scope=True)
    assert state.scope == "group:1129150377"
    assert [call[1]["scope"] for call in gateway.calls] == ["", "group:1129150377"]
