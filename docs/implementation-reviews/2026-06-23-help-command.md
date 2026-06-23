# 2026-06-23 Help Command Implementation Review

## What Changed

- Added `/jm 帮助` and `/jm help`.
- Added `src/jmbot/core/formatter.py` for compact help text.
- Updated README command documentation.
- Added local git protection for `map.md`.

## Why This Step First

This was chosen as the first roadmap step because it improves the user experience without touching downloads, jmcomic calls, SQLite schema, queue persistence, or OneBot upload behavior. It also creates a small formatter surface for later command replies.

## Files Changed

- `src/jmbot/core/parser.py`
- `src/jmbot/core/formatter.py`
- `src/jmbot/bot.py`
- `tests/test_parser.py`
- `tests/test_formatter.py`
- `README.md`
- `.git/info/exclude`

## Verification

- `.\.venv\Scripts\python.exe -m pytest tests/test_parser.py tests/test_formatter.py -q`
  - Result: `13 passed, 4 subtests passed in 0.03s`
- `.\.venv\Scripts\python.exe -m pytest`
  - Result: `55 passed in 1.12s`

## Notes For Future Chats

- `map.md` is a local roadmap and must not be committed.
- Help text should only advertise commands that are already implemented.
- `formatter.py` is intentionally narrow for now; do not turn it into a large formatting framework before a second real use case.

## Suggested Next Step

Implement the first P0 architecture improvement: move queue summary formatting into `formatter.py`, then consider `/jm 看 <id>` or `/jm 搜索` after a jmcomic adapter is in place.
