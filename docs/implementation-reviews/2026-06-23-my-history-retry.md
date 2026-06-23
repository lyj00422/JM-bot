# 2026-06-23 My Tasks, History, And Retry Implementation Review

## What Changed

- Added `/jm 我的` and `/jm my`.
- Added `/jm 历史`, `/jm history`, and `/jm 历史 失败`.
- Added `/jm 重试 <id>`.
- Added admin-only `/jm 管理 失败`.
- Added queue query helpers that preserve user and group/private privacy boundaries.
- Updated README command documentation.

## Why This Step

This is the first release-worthy download-experience feature set. Users can now understand what they have in progress, inspect their recent outcomes, and retry their own failed tasks without asking an admin to manually recreate the request.

## Privacy Boundary

- Normal users only see jobs from their own `user_id`.
- Group queries are scoped to the current `group_id`.
- Private queries are scoped to the current private chat.
- Retry only uses the caller's latest failed task for the same album in the same context.
- `/jm 管理 失败` is global, but it is behind the existing superuser permission check.

## Files Changed

- `src/jmbot/core/parser.py`
- `src/jmbot/core/persistent_queue.py`
- `src/jmbot/core/command_service.py`
- `src/jmbot/core/formatter.py`
- `src/jmbot/bot.py`
- `tests/test_parser.py`
- `tests/test_persistent_queue.py`
- `tests/test_command_service.py`
- `README.md`

## Verification

- `.\.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_persistent_queue.py tests/test_command_service.py -q`
  - Result: `35 passed, 9 subtests passed in 1.11s`
- `.\.venv\Scripts\python.exe -m pytest`
  - Result: `72 passed in 1.58s`

## Release Recommendation

After full verification passes, this commit is suitable for release and server deployment. It adds visible user value while avoiding schema migrations and downloader changes.

## Suggested Next Step

Implement download stability protection: queue maximum length, user cooldown, and daily limits with conservative defaults for the Aliyun 2C2G branch.
