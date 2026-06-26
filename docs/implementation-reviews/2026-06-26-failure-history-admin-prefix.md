# 2026-06-26 Failure History And Admin Prefix Implementation Review

## What Changed

- Removed `/jm 重试 <id>`.
- Removed friend-request notification, storage, and remote approval/rejection commands.
- Replaced legacy `/jm 管理 ...` commands with `/jmg ...`.
- Added `/jm 进度` without an ID, which resolves to the caller's latest active current-context task.
- Added optional history limits: `/jm 历史 5` and `/jm 历史 失败 5`.
- Changed queue summary lines to `<id> 排队|处理中（群聊|私聊）`.
- Changed technical failure notices to include a task ID for admin follow-up without exposing raw errors.
- Missing/inaccessible album failures are shown directly to users and are not retained as failed queue records.
- Added retained-history pruning by day and per-user maximum.
- Added admin failure detail and deletion commands:
  - `/jmg 失败`
  - `/jmg 失败 最近 5`
  - `/jmg 失败 <任务ID>`
  - `/jmg 失败 删除 <任务ID>`
- Bumped version to `0.3.2`.

## Files Changed

- `.agent-collab/current_task.md`
- `src/jmbot/core/parser.py`
- `src/jmbot/core/persistent_queue.py`
- `src/jmbot/core/formatter.py`
- `src/jmbot/core/command_service.py`
- `src/jmbot/core/queue_processor.py`
- `src/jmbot/core/user_store.py`
- `src/jmbot/bot.py`
- `src/jmbot/config.py`
- `tests/test_parser.py`
- `tests/test_persistent_queue.py`
- `tests/test_command_service.py`
- `tests/test_queue_processor.py`
- `tests/test_formatter.py`
- `tests/test_config.py`
- `tests/test_user_store.py`
- `README.md`
- `deploy/env.example`
- `scripts/local.env.example`
- `pyproject.toml`

## Verification

Run before release:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Notes

- Existing SQLite databases may still contain an old unused `friend_requests` table. This version no longer creates or reads that table.
- The Aliyun 2C2G branch keeps `JM_BOT_MAX_CONCURRENCY=1`.
