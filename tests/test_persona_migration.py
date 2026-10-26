import importlib.util
from pathlib import Path

import pytest

tomlkit = pytest.importorskip("tomlkit")


def _load_tool():
    path = Path(__file__).parents[1] / "scripts" / "migrate_persona_to_maibot.py"
    spec = importlib.util.spec_from_file_location("migrate_persona_to_maibot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrate_persona_preserves_native_prompt_and_database(tmp_path):
    from jmbot.core.conversation_state import ConversationStateStore

    db_path = tmp_path / "conversation.sqlite3"
    config_path = tmp_path / "bot_config.toml"
    document = tomlkit.parse("[chat.reply_style]\nchat_prompts = []\n")
    item = tomlkit.inline_table()
    item.update({"platform": "qq", "item_id": "200", "rule_type": "private", "prompt": "native"})
    document["chat"]["reply_style"]["chat_prompts"].append(item)
    config_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    state = ConversationStateStore(db_path)
    state.set_default_persona("Mai", "默认人格")
    state.set_policy("group:100", download_enabled=False)
    state.set_policy("private:200", download_enabled=False)
    state.set_persona_fields("group:100", personality="群人格", behavior_style="群行为", reply_style="群回复")
    before = db_path.read_bytes()

    tool = _load_tool()
    result = tool.migrate(db_path, config_path)
    assert result["added"] == ["group:100"]
    assert any(item["scope"] == "private:200" for item in result["skipped"])
    assert db_path.read_bytes() == before
    assert list(tmp_path.glob("bot_config.toml.bak.*"))

    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    prompts = document["chat"]["reply_style"]["chat_prompts"]
    assert any(item["item_id"] == "100" and item["rule_type"] == "group" for item in prompts)
    assert any(item["item_id"] == "200" and item["prompt"] == "native" for item in prompts)

