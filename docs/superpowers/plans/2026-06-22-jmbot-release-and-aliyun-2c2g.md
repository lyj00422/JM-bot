# JMbot Release and Aliyun 2C2G Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean the generic JMbot release and create a separate Aliyun 2 vCPU / 2 GiB optimized release branch.

**Architecture:** Keep one codebase with environment-driven resource controls, then publish two branches with different deployment defaults and documentation. Runtime outputs stay outside git.

**Tech Stack:** Python 3.10+, NoneBot2, OneBot v11, jmcomic, Pillow, pytest, PowerShell startup scripts, systemd templates.

---

## File Structure

- Modify `src/jmbot/config.py`: add resource tuning fields and validation.
- Modify `src/jmbot/core/jm_service.py`: write jmcomic options from `DownloadSettings`.
- Modify `src/jmbot/bot.py`: pass new config fields to `DownloadSettings`.
- Modify `tests/test_config.py`: test resource config parsing and validation.
- Modify `tests/test_jm_service.py`: test generated jmcomic option YAML.
- Modify `scripts/start_bot.ps1`: load env files and remove private defaults.
- Modify `scripts/start_all.ps1`: load env files and remove fixed NapCat path.
- Add `scripts/local.env.example`: local private config template.
- Modify `.gitignore`: ignore env files and runtime jmcomic option files.
- Delete tracked `data/options/*.yml`: remove runtime generated files from release.
- Rewrite `README.md`: UTF-8 Chinese release and deployment guide.
- Modify `deploy/env.example`: generic deployment defaults.
- Modify `deploy/jmbot.service.example`: generic production service guidance.
- On branch `release/aliyun-2c2g`, modify `deploy/env.example`, `deploy/jmbot.service.example`, and README Aliyun notes with low-resource defaults.

### Task 1: Resource Config Tests

**Files:**
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Add tests asserting that `load_config()` parses:

```python
"JM_BOT_DOWNLOAD_IMAGE_THREADS": "3"
"JM_BOT_DOWNLOAD_PHOTO_THREADS": "1"
"JM_BOT_JMCOMIC_LOG": "false"
"JM_BOT_JMCOMIC_RETRY_TIMES": "2"
```

and rejects zero or negative values for thread and retry settings.

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: fail because `BotConfig` does not yet expose the new fields.

### Task 2: Resource Config Implementation

**Files:**
- Modify: `src/jmbot/config.py`

- [ ] **Step 1: Implement minimal config support**

Add frozen dataclass fields:

```python
download_image_threads: int
download_photo_threads: int
jmcomic_log: bool
jmcomic_retry_times: int
```

Parse them in `load_config()` and validate that thread/retry counts are at least 1.

- [ ] **Step 2: Run config tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: pass.

### Task 3: jmcomic Option Tests

**Files:**
- Modify: `tests/test_jm_service.py`

- [ ] **Step 1: Write failing test**

Update the fake download test to create `DownloadSettings` with custom values:

```python
download_image_threads=3
download_photo_threads=1
jmcomic_log=False
jmcomic_retry_times=2
```

Assert the generated option text includes:

```text
log: false
    image: 3
    photo: 1
  retry_times: 2
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_jm_service.py -v`

Expected: fail because `DownloadSettings` and `_write_option_file()` still use hard-coded values.

### Task 4: jmcomic Option Implementation

**Files:**
- Modify: `src/jmbot/core/jm_service.py`
- Modify: `src/jmbot/bot.py`

- [ ] **Step 1: Implement settings-driven option writing**

Extend `DownloadSettings` with:

```python
download_image_threads: int = 10
download_photo_threads: int = 2
jmcomic_log: bool = True
jmcomic_retry_times: int = 5
```

Pass settings into `_write_option_file()` and render those values into the YAML.

- [ ] **Step 2: Wire bot config into settings**

Construct `DownloadSettings` in `src/jmbot/bot.py` with the new `CONFIG` fields.

- [ ] **Step 3: Run jm_service tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_jm_service.py -v`

Expected: pass.

### Task 5: Generic Startup Scripts

**Files:**
- Modify: `scripts/start_bot.ps1`
- Modify: `scripts/start_all.ps1`
- Add: `scripts/local.env.example`

- [ ] **Step 1: Update scripts**

Add a small PowerShell env-file loader that reads `KEY=VALUE` lines from `.env` and `scripts/local.env`. Set only non-private defaults in `start_bot.ps1`. In `start_all.ps1`, require either `JM_BOT_NAPCAT_SCRIPT` or `JM_BOT_NAPCAT_DIR`.

- [ ] **Step 2: Add template**

Create `scripts/local.env.example` with placeholders for group IDs, superusers, and NapCat paths.

- [ ] **Step 3: Smoke-check syntax**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_bot.ps1 -Help`

Expected: PowerShell parses the file. If `-Help` is not supported, the script should fail only after parsing.

### Task 6: Repository Hygiene and Docs

**Files:**
- Modify: `.gitignore`
- Delete: `data/options/*.yml`
- Modify: `README.md`
- Modify: `deploy/env.example`
- Modify: `deploy/jmbot.service.example`

- [ ] **Step 1: Ignore runtime files**

Add `.env`, `scripts/local.env`, and `data/options/` to `.gitignore`.

- [ ] **Step 2: Delete tracked runtime options**

Remove tracked `data/options/*.yml` from git.

- [ ] **Step 3: Rewrite docs**

Rewrite README in UTF-8 Chinese with generic release instructions and Aliyun branch instructions.

- [ ] **Step 4: Run full tests**

Run: `.venv\Scripts\python.exe -m pytest`

Expected: all tests pass.

### Task 7: Publish Branches

**Files:**
- Modify on `release/aliyun-2c2g`: `deploy/env.example`
- Modify on `release/aliyun-2c2g`: `deploy/jmbot.service.example`
- Modify on `release/aliyun-2c2g`: `README.md`

- [ ] **Step 1: Commit generic release changes on `main`**

Run:

```powershell
git add -A
git commit -m "refactor: prepare generic jmbot release"
```

- [ ] **Step 2: Create Aliyun branch**

Run:

```powershell
git switch -c release/aliyun-2c2g
```

- [ ] **Step 3: Apply low-resource defaults**

Set Aliyun example defaults:

```text
JM_BOT_MAX_CONCURRENCY=1
JM_BOT_DOWNLOAD_IMAGE_THREADS=2
JM_BOT_DOWNLOAD_PHOTO_THREADS=1
JM_BOT_JMCOMIC_RETRY_TIMES=3
JM_BOT_CLEAN_AFTER_UPLOAD=true
```

- [ ] **Step 4: Run full tests**

Run: `.venv\Scripts\python.exe -m pytest`

Expected: all tests pass.

- [ ] **Step 5: Commit Aliyun branch**

Run:

```powershell
git add README.md deploy/env.example deploy/jmbot.service.example
git commit -m "chore: add aliyun 2c2g release defaults"
```

- [ ] **Step 6: Push branches**

Run:

```powershell
git push origin main
git push -u origin release/aliyun-2c2g
```

Expected: both remote branches are available.

## Self-Review

- The plan covers config, option generation, startup scripts, repo hygiene, docs, branch publishing, and tests.
- No placeholders are left for implementation decisions.
- New field names are consistent across config, service settings, tests, and docs.
