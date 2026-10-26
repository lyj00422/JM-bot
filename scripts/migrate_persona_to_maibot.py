"""Copy legacy JMbot persona records into MaiBot's native chat prompts once.

This is an offline deployment tool.  The chat service never imports or calls
this module; after migration, MaiBot WebUI owns the resulting prompt entries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import tomlkit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jmbot.core.conversation_state import ConversationStateStore


MIGRATION_MARKER = "【JMbot 离线迁移的人设】"


def _ensure_table(parent, name):
    value = parent.get(name)
    if value is None:
        value = tomlkit.table()
        parent.add(name, value)
    return value


def _prompt_item_values(item):
    return {key: str(item.get(key, "") or "").strip() for key in ("platform", "item_id", "rule_type", "prompt")}


def _render_prompt(fields: dict[str, object]) -> str:
    labels = (("人格", "personality"), ("行为风格", "behavior_style"), ("回复风格", "reply_style"))
    lines = [MIGRATION_MARKER]
    for label, key in labels:
        value = str(fields.get(key, "") or "").strip()
        if value:
            lines.append(f"{label}：{value}")
    return "\n".join(lines) if len(lines) > 1 else ""


def migrate(db_path: Path, config_path: Path, *, dry_run: bool = False, backup: bool = True) -> dict[str, object]:
    if not db_path.is_file():
        raise FileNotFoundError(f"JMbot conversation database not found: {db_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"MaiBot bot_config.toml not found: {config_path}")

    state = ConversationStateStore(db_path)
    default_prompt = state.get_default_persona("", "")[1]
    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    chat = _ensure_table(document, "chat")
    reply_style = _ensure_table(chat, "reply_style")
    prompts = reply_style.get("chat_prompts")
    if prompts is None:
        prompts = tomlkit.aot()
        reply_style.add("chat_prompts", prompts)

    existing = {}
    for item in prompts:
        values = _prompt_item_values(item)
        existing[(values["platform"], values["item_id"], values["rule_type"])] = values["prompt"]

    added = []
    skipped = []
    for policy in state.list_policies():
        scope = str(policy["scope_key"])
        kind, owner = scope.split(":", 1)
        fields = state.effective_persona_fields(scope, default_prompt)
        prompt = _render_prompt(fields)
        if not prompt:
            skipped.append({"scope": scope, "reason": "empty persona"})
            continue
        key = ("qq", owner, kind)
        if key in existing:
            skipped.append({"scope": scope, "reason": "native prompt already exists"})
            continue
        item = tomlkit.inline_table()
        item.add("platform", "qq")
        item.add("item_id", owner)
        item.add("rule_type", kind)
        item.add("prompt", prompt)
        prompts.append(item)
        existing[key] = prompt
        added.append(scope)

    changed = bool(added)
    if changed and not dry_run:
        if backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(config_path, config_path.with_name(config_path.name + ".bak." + stamp))
        fd, temporary_name = tempfile.mkstemp(prefix=config_path.name + ".", suffix=".tmp", dir=config_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(tomlkit.dumps(document))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, config_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    return {"changed": changed, "dry_run": dry_run, "added": added, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate JMbot persona data into MaiBot chat.reply_style.chat_prompts")
    parser.add_argument("--db", required=True, type=Path, help="JMbot conversation.sqlite3")
    parser.add_argument("--config", required=True, type=Path, help="MaiBot instance config/bot_config.toml")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing the config")
    parser.add_argument("--no-backup", action="store_true", help="do not create a timestamped config backup")
    args = parser.parse_args(argv)
    try:
        result = migrate(args.db, args.config, dry_run=args.dry_run, backup=not args.no_backup)
    except (OSError, ValueError, tomlkit.exceptions.ParseError) as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
