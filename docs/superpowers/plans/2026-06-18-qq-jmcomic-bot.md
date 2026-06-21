# QQ JMComic Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a NoneBot2 OneBot v11 QQ group bot that accepts `/jm <album_id>`, downloads the album with `jmcomic`, converts images to PDF, and uploads the PDF to the QQ group through OneBot.

**Architecture:** Keep QQ framework code thin and place reusable behavior in `core/`. `core.parser` validates commands, `core.jm_service` wraps `jmcomic`, `core.pdf_builder` converts image trees to PDF, `core.task_runner` serializes duplicate album jobs, and `bot.py` wires these pieces to NoneBot.

**Tech Stack:** Python 3.10+, NoneBot2, nonebot-adapter-onebot v11, jmcomic, Pillow, pytest/unittest.

---

### Task 1: Parser And PDF Builder

**Files:**
- Create: `core/parser.py`
- Create: `core/pdf_builder.py`
- Test: `tests/test_parser.py`
- Test: `tests/test_pdf_builder.py`

- [x] **Step 1: Write failing parser tests**

```python
from core.parser import parse_jm_command

def test_parse_valid_jm_command():
    assert parse_jm_command("/jm 123456").album_id == "123456"

def test_parse_rejects_invalid_id():
    assert parse_jm_command("/jm abc") is None
```

- [x] **Step 2: Write failing PDF tests**

```python
from core.pdf_builder import collect_images, build_pdf

def test_collect_images_orders_nested_numeric_files(tmp_path):
    assert [p.name for p in collect_images(tmp_path)] == ["001.jpg", "002.png"]

def test_build_pdf_creates_non_empty_pdf(tmp_path):
    assert build_pdf(tmp_path, tmp_path / "out.pdf").stat().st_size > 0
```

- [x] **Step 3: Implement minimal parser and PDF builder**

Parser returns a small dataclass for valid `/jm <digits>` input. PDF builder recursively collects supported image files, natural-sorts paths, converts them to RGB with Pillow, and writes a multi-page PDF.

- [x] **Step 4: Verify**

Run: `python -m unittest tests.test_parser tests.test_pdf_builder`
Expected: all tests pass.

### Task 2: JM Service And Task Runner

**Files:**
- Create: `core/jm_service.py`
- Create: `core/task_runner.py`
- Test: `tests/test_jm_service.py`
- Test: `tests/test_task_runner.py`

- [x] **Step 1: Write failing tests**

Tests verify that `jm_service` creates a jmcomic YAML option file and calls injected downloader/PDF builder functions, and that `TaskRunner` rejects duplicate in-flight album IDs.

- [x] **Step 2: Implement minimal service and runner**

`download_comic()` writes an option YAML using `dir_rule.base_dir`, calls `jmcomic.create_option()`, calls `jmcomic.download_album()`, and returns the newest/likely album directory. `create_pdf_for_album()` delegates to `build_pdf()`. `TaskRunner.submit()` uses an `asyncio.Lock`, an active-ID set, and a semaphore.

- [x] **Step 3: Verify**

Run: `python -m unittest tests.test_jm_service tests.test_task_runner`
Expected: all tests pass.

### Task 3: NoneBot Entry, Config, Requirements, And Docs

**Files:**
- Create: `config.py`
- Create: `bot.py`
- Create: `requirements.txt`
- Create: `README.md`
- Modify: `main.py`

- [x] **Step 1: Add configuration**

Use environment variables for host, port, command prefix, data paths, concurrency, allowed group IDs, and admin IDs.

- [x] **Step 2: Add bot entry**

Create a NoneBot2 plugin in `bot.py` that handles `/jm <id>`, sends progress messages, runs `TaskRunner`, and calls OneBot `upload_group_file`.

- [x] **Step 3: Update run docs**

Document Python install, dependency install, bot launch, and the QQ-side NapCat/OneBot reverse WebSocket setup steps the user must do locally.

- [x] **Step 4: Verify**

Run: `python -m unittest discover`
Expected: all tests pass.
