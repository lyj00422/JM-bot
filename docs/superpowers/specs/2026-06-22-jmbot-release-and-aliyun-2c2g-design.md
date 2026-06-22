# JMbot Release and Aliyun 2C2G Design

## Goal

Prepare JMbot for two release targets:

- `main`: a clean, generic release branch suitable for normal local and server deployments.
- `release/aliyun-2c2g`: a low-resource release branch tuned for an Aliyun server with 2 vCPU, 2 GiB memory, and a 40 GiB ESSD disk.

The Aliyun branch must be pullable directly on the server with `git pull origin release/aliyun-2c2g`.

## Current Findings

- Baseline tests pass in the project virtual environment: 26 tests pass.
- README and several Chinese comments/messages are mojibake and should be rewritten as UTF-8.
- `scripts/start_bot.ps1` and `scripts/start_all.ps1` contain private QQ IDs, group IDs, and a fixed NapCat install path.
- Runtime jmcomic option files under `data/options/*.yml` are tracked in git, but they are generated per album and should not be release artifacts.
- Server resource pressure likely comes from two hard-coded settings:
  - jmcomic image download threads are fixed at `10`.
  - jmcomic photo threads are fixed at `2`.
- PDF generation currently opens and converts all images before saving, which can increase peak memory use on large albums.

## Branch Strategy

### Generic branch: `main`

`main` will contain reusable code, documentation, tests, and environment examples. It will not contain private local paths, QQ IDs, generated downloads, generated PDFs, or generated jmcomic option files.

`main` keeps resource controls configurable through environment variables, with defaults that preserve the current general behavior as much as possible.

### Aliyun branch: `release/aliyun-2c2g`

This branch will be created from the cleaned generic branch. It will keep the same code surface but ship lower-resource defaults in deployment examples and README guidance:

- one bot job at a time
- fewer jmcomic image/photo download threads
- upload cleanup enabled
- systemd memory and restart guidance for small instances

## Runtime Configuration Design

Add these environment variables:

- `JM_BOT_DOWNLOAD_IMAGE_THREADS`: jmcomic per-image download thread count.
- `JM_BOT_DOWNLOAD_PHOTO_THREADS`: jmcomic per-photo thread count.
- `JM_BOT_JMCOMIC_LOG`: enable or disable jmcomic logs.
- `JM_BOT_JMCOMIC_RETRY_TIMES`: retry count for jmcomic client.

`BotConfig` will parse and validate them. `DownloadSettings` will receive them. `_write_option_file()` will write the jmcomic option YAML from the settings instead of hard-coded values.

## Local Startup Design

PowerShell startup scripts will load optional local configuration files:

- `.env`
- `scripts/local.env`

The committed template will be `scripts/local.env.example`. The real `scripts/local.env` will be ignored by git.

`scripts/start_bot.ps1` will:

- load env files
- set safe defaults only when variables are missing
- prefer `.venv\Scripts\python.exe`
- show a clear error if dependencies are missing

`scripts/start_all.ps1` will:

- load the same env files
- require `JM_BOT_NAPCAT_DIR` or `JM_BOT_NAPCAT_SCRIPT` for NapCat
- start JMbot first, then NapCat
- avoid hard-coded private IDs and machine-specific paths

## Repository Hygiene

Remove tracked runtime option files from `data/options/*.yml`. Ignore these paths:

- `data/options/`
- `scripts/local.env`
- `.env`

Keep empty runtime directories out of git unless a future need appears.

## Testing

Add tests before implementation for:

- parsing new resource variables in `load_config()`
- rejecting invalid thread/retry values
- writing jmcomic option files with configured thread counts, retry count, and log flag

Run the full test suite after changes.

## Deployment Documentation

Rewrite README in UTF-8 Chinese. It should explain:

- local setup
- local one-click startup
- generic server deployment
- low-resource Aliyun deployment branch
- how to switch/pull the optimized branch
- what generated files are intentionally ignored
