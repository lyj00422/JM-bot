# Current Task: Failure History And Command Cleanup

## Original User Request

Reduce noisy failure messages, keep actionable public reasons visible, hide technical errors from normal users, let admins inspect technical failures by task ID, expire old failure/history records, remove retry and friend-request management, switch admin commands from `/jm 管理` to `/jmg`, improve `/jm 进度` and `/jm 队列`, update docs, commit, and release a small version.

## Confirmed Requirements

- Codex implements everything directly. Do not delegate to Claude Code.
- User commands are considered complete after this change; do not add optional roadmap features.
- Remove `/jm 重试 <id>`.
- Remove friend-request commands and friend-request event handling entirely. Do not notify admins.
- Remove legacy `/jm 管理 ...` command parsing.
- Add admin prefix `/jmg`.
- Keep Aliyun 2C2G processing concurrency at 1.
- `/jm 进度` without an ID should show the caller's most recent active job in the current group/private context.
- `/jm 队列` should show compact entries: `<id> 排队|处理中（群聊|私聊）`.
- `/jm 历史 [条数]` and `/jm 历史 失败 [条数]` should support optional recent counts; default shows all retained records.
- Failed user-visible technical messages should be brief: `JM<id> 出现技术原因，联系管理员处理 #<任务ID>。`
- Public failures such as missing album / login-only should be shown directly and not retained in the failed-history database.
- Normal users must not see raw technical failure details.
- Admins can list retained failures and inspect a failure by task ID.
- Provide admin manual deletion for retained failure records.
- Automatically delete finished history older than a configurable TTL and enforce a configurable per-user retained-history maximum.
- Update README and env examples.
- Commit changes and create a small release version.

## Confirmed Tech Stack

- Python 3.10+
- NoneBot2 + OneBot v11
- SQLite for queue/history
- pytest/unittest test suite

## Implementation Plan

- Parser:
  - Remove retry and friend-request actions.
  - Add `/jm 进度` without ID.
  - Add optional history limits.
  - Add `/jmg` admin parser for clear/kill/jump/failed list/failed detail/failed deletion.
- Queue:
  - Add job lookup/delete helpers.
  - Add latest active job lookup for `/jm 进度`.
  - Add history pruning by TTL and per-user maximum.
  - Make history list default unbounded within retained records.
- Formatter:
  - Remove retry and friend-request output.
  - Compact queue summary.
  - Add failure classification helpers.
  - Include task IDs in user failure history without raw reasons.
  - Add admin failure detail/list/delete formatting.
- Queue processor:
  - Do not persist public missing-album failures.
  - Persist technical failures and notify with task ID.
- Bot:
  - Parse `/jm` user commands and `/jmg` admin commands.
  - Remove friend request event matcher and OneBot friend-request API calls.
  - Prune history on command handling and bot startup.
- Config/docs:
  - Add admin prefix and history retention config.
  - Remove friend-request/retry docs.
  - Update help and README.

## Acceptance Criteria

- `/jm 重试 <id>` no longer parses.
- `/jm 好友申请`, `/jm 同意好友`, `/jm 拒绝好友` no longer parse.
- `/jm 管理 ...` no longer parses.
- `/jmg 清空`, `/jmg kill <id>`, `/jmg kill 全部 <id>`, `/jmg 插队 <id>`, `/jmg 失败`, `/jmg 失败 最近 <n>`, `/jmg 失败 <task_id>`, and `/jmg 失败 删除 <task_id>` parse.
- `/jm 进度` returns the latest active current-context job.
- Normal technical failure notification contains the task ID but no raw error.
- Missing-album failures notify users directly and do not remain as failed records.
- History retention pruning is covered by tests.
- Full test suite passes.
- Version is bumped from 0.3.1 to 0.3.2.

## Files Likely Involved

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
- `tests/test_formatter.py`
- `tests/test_command_service.py`
- `tests/test_queue_processor.py`
- `tests/test_user_store.py`
- `README.md`
- `deploy/env.example`
- `scripts/local.env.example`
- `pyproject.toml`

## Forbidden Changes

- Do not edit secrets, `.env`, production credentials, or server private config.
- Do not delete user runtime data.
- Do not delegate implementation to Claude Code.
- Do not add new roadmap features such as subscription, ranking, random, notes, or search-result-number download.

## Testing Requirements

- Write failing tests before production changes.
- Run focused tests for parser, queue, formatter, command service, queue processor, config, and user store.
- Run full `pytest`.

## Risks And Assumptions

- Existing SQLite databases may still contain an unused `friend_requests` table; the code will stop creating or using it, but will not drop user data.
- Aliyun 2C2G branch keeps download concurrency at 1.
- GitHub release creation depends on available local tooling and credentials; if unavailable, create a local tag/package and report the exact blocker.
