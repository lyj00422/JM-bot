# 2026-06-23 Download Feedback Implementation Review

## What Changed

- Moved queue summary formatting into `src/jmbot/core/formatter.py`.
- Added formatter helpers for task start, upload success, cancellation, and failure messages.
- Updated `queue_processor` so QQ users no longer see raw exception text on task failure.
- Kept raw failure details in the SQLite queue record through `queue.mark_failed(job.id, str(exc))`.
- Updated README with the new failure-message behavior.

## Why This Step

This improves the download experience without changing the downloader, jmcomic calls, OneBot upload behavior, or SQLite schema. Users get clearer and safer messages, while administrators still retain the real failure text for troubleshooting.

## Files Changed

- `src/jmbot/core/formatter.py`
- `src/jmbot/core/command_service.py`
- `src/jmbot/core/queue_processor.py`
- `tests/test_formatter.py`
- `tests/test_queue_processor.py`
- `README.md`

## Verification

- `.\.venv\Scripts\python.exe -m pytest tests/test_formatter.py tests/test_queue_processor.py -q`
  - Result: `12 passed in 0.36s`
- `.\.venv\Scripts\python.exe -m pytest`
  - Result: `59 passed in 1.09s`

## Notes For Future Chats

- User-facing failure messages should stay short and should not expose raw exceptions, paths, traceback, or upstream error text.
- Detailed failure strings are still available in `album_queue.error`.
- The next high-value download-experience feature is `/jm 我的`, `/jm 历史`, and `/jm 重试 <id>`.

## Suggested Next Step

Implement "my tasks / history / retry" as the first release-worthy user experience version.
