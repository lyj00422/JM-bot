# 2026-06-23 Aliyun Split Cache Manifest Review

## Background

After auditing both branches, the Aliyun split-PDF cache had one stability risk: retry logic reused any existing `JM<id>.part*.pdf` files. If a previous large job was terminated while generating parts, a partial set such as only `part1` could be mistaken for a complete result.

## Fix

Split PDF generation now writes `JM<id>.parts.txt` only after all PDF parts have been built successfully. Retry cache reuse requires:

- the manifest file exists;
- every listed filename is a safe basename;
- every listed part exists on disk;
- listed files match the expected `JM<id>.partN.pdf` naming pattern.

If any condition fails, the bot ignores the stale split cache and runs the download/build flow again.

## Why This Matters

Aliyun 2C2G behavior should prioritize correctness and server survival over speed. This keeps the useful retry optimization for upload-side failures, while preventing incomplete PDF sets after timeout or process termination from being uploaded as if they were complete.

## Verification

Targeted tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jm_service.py tests/test_pdf_builder.py -q
```

Result: `11 passed`.

Full release verification:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Result: `84 passed`.

This patch is release-worthy as `v0.2.6-aliyun2c2g`.
