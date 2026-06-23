# 2026-06-23 Aliyun Split PDF Review

## Background

Aliyun 2 vCPU / 2 GiB servers can stall on very large JM albums when one job downloads, decodes, builds one huge PDF, and uploads it as a single file. The previous stability pass added isolated child-process execution, hard job timeout, lifecycle logs, and `img2pdf` as the preferred PDF backend. This pass keeps that direction and adds automatic PDF splitting for the Aliyun optimized branch.

## Decision

Default behavior on `release/aliyun-2c2g` is now:

- `JM_BOT_AUTO_SPLIT_PDF=true`
- `JM_BOT_SPLIT_IMAGE_COUNT=80`

When an album has more images than the threshold, the bot builds numbered PDF parts such as `JM123456.part1.pdf`, `JM123456.part2.pdf`, then uploads each part in order. It does not merge those parts into one large PDF by default, because merge would recreate the memory and disk I/O pressure this branch is trying to avoid.

## Code Changes

- `src/jmbot/config.py`: added split config and Aliyun defaults.
- `src/jmbot/core/pdf_builder.py`: added `build_split_pdfs` and shared the img2pdf/Pillow build path.
- `src/jmbot/core/jm_service.py`: `DownloadSettings` carries split config; `AlbumJobResult` can expose multiple output paths; `run_album_job` chooses single PDF or part PDFs after download.
- `src/jmbot/core/jm_service.py`: retries can reuse existing `JM<id>.part*.pdf` files when split mode is enabled, so an upload-side failure does not force another full download.
- `src/jmbot/core/queue_processor.py`: uploads, cleans, logs, and notifies all output PDFs for a job.
- `src/jmbot/bot.py`: passes split config into download settings.
- `deploy/env.example` and `README.md`: documented Aliyun defaults and deployment behavior.

## User Experience

For normal albums, users still receive one PDF. For large albums, users receive multiple PDFs in order. The final success message lists the generated filenames, so users can see that the result was intentionally split rather than partially missing. If the files already exist from a previous failed upload attempt, retrying can reuse them instead of downloading the album again.

## Verification

Targeted tests passed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_pdf_builder.py tests/test_queue_processor.py tests/test_jm_service.py -q
```

Result at this checkpoint: `24 passed, 5 subtests passed`.

Full test suite passed before release:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Result: `83 passed`.

This change is release-worthy as `v0.2.5` for the Aliyun optimized branch.
