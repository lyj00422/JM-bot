"""Inspect the locked MaiBot source for the session-scope P0 gate.

The probe is deliberately source-only: it never starts a QQ client, opens a
production database, or sends a model request. It records the extension points
that must be validated before a production cutover.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


LOCKED_COMMIT = 'c602a2780f4f4dda4a6be46d67abc1cfb1ad420c'


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.is_file() else ''


def inspect(source: Path, jmbot_root: Path) -> dict[str, object]:
    loop = _read(source / 'src/maisaka/chat_loop_service.py')
    worker = _read(jmbot_root / 'src/jmbot/native_worker.py')
    source_src = source / 'src'
    connectors = sorted(
        str(path.relative_to(source)).replace('\\', '/')
        for path in source_src.rglob('*')
        if path.is_file() and '__pycache__' not in path.parts
        and re.search(r'(connector|onebot|napcat|qq)', path.name, re.I)
    )
    hook_names = re.findall(r'name=["\']([^"\']+before_request)["\']', loop)
    return {
        'maibot_commit': LOCKED_COMMIT,
        'hooks': hook_names,
        'planner_hook_allows_abort': 'name="maisaka.planner.before_request"' in loop and 'allow_abort=False' in loop,
        'replyer_hook_allows_abort': 'name="maisaka.replyer.before_request"' in loop and 'allow_abort=False' in loop,
        'personality_reads_global_config': 'global_config.personality.personality' in loop,
        'behavior_reads_global_config': 'global_config.personality.behavior_style' in loop,
        'reply_style_reads_global_config': 'global_config.chat.reply_style' in loop,
        'worker_disables_webui': "config.webui.enabled = False" in worker,
        'worker_disables_watcher': 'config_manager.start_file_watcher = no_watcher' in worker,
        'candidate_connector_files': connectors,
    }


def main() -> None:
    source = Path(os.environ.get('MAIBOT_SOURCE', '.git/upstream/maibot')).resolve()
    jmbot_root = Path(os.environ.get('JMBOT_ROOT', '.')).resolve()
    print(json.dumps(inspect(source, jmbot_root), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
