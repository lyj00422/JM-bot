param(
    [switch]$NoInstall,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White)

    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -ForegroundColor $Color
}

function Import-EnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) {
            return
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            return
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        if ($name -match "^[A-Za-z_][A-Za-z0-9_]*$") {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Set-DefaultEnv {
    param([string]$Name, [string]$Value)

    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Test-BotDependencies {
    param([string]$PythonExe)

    & $PythonExe -c "import nonebot, jmcomic, PIL" 2>$null
    return $LASTEXITCODE -eq 0
}

if ($Help) {
    Write-Host "Usage: powershell -ExecutionPolicy Bypass -File scripts/start_bot.ps1 [-NoInstall]"
    Write-Host "Optional config files: .env, scripts/local.env"
    return
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot

Import-EnvFile (Join-Path $projectRoot ".env")
Import-EnvFile (Join-Path $scriptRoot "local.env")

Set-DefaultEnv "JM_BOT_HOST" "127.0.0.1"
Set-DefaultEnv "JM_BOT_PORT" "8080"
Set-DefaultEnv "JM_BOT_COMMAND_PREFIX" "/jm"
Set-DefaultEnv "JM_BOT_DOWNLOAD_ROOT" "data/downloads"
Set-DefaultEnv "JM_BOT_OUTPUT_ROOT" "data/output"
Set-DefaultEnv "JM_BOT_OPTION_ROOT" "data/options"
Set-DefaultEnv "JM_BOT_CLEAN_AFTER_UPLOAD" "true"
Set-DefaultEnv "JM_BOT_GROUP_FOLDER" "JM"

Set-Location $projectRoot

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
}

if (-not (Test-BotDependencies -PythonExe $python)) {
    if ($NoInstall) {
        throw "Python dependencies are missing. Run: $python -m pip install -r requirements.txt"
    }

    Write-Step "Installing Python dependencies from requirements.txt..." Yellow
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
}

Write-Step "Starting JMbot on $env:JM_BOT_HOST`:$env:JM_BOT_PORT" Green
& $python .\main.py
