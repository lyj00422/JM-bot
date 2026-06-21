# Configure JMbot runtime. The desktop one-click launcher calls this script.
$env:JM_BOT_HOST = "127.0.0.1"
$env:JM_BOT_PORT = "8080"
$env:JM_BOT_COMMAND_PREFIX = "/jm"
$env:JM_BOT_ALLOWED_GROUPS = "1053512745"
$env:JM_BOT_SUPERUSERS = "2661195769"
$env:JM_BOT_CLEAN_AFTER_UPLOAD = "true"
$env:JM_BOT_GROUP_FOLDER = "JM"
$env:JM_BOT_MAX_CONCURRENCY = "2"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
Set-Location $projectRoot

# Prefer the project virtual environment over any system Python.
if (Test-Path ".\.venv\Scripts\python.exe") {
    .\.venv\Scripts\python.exe .\main.py
} else {
    python .\main.py
}
