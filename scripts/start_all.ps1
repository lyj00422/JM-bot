param(
    [switch]$NoPause,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$botDir = Split-Path -Parent $scriptRoot
$botScript = Join-Path $scriptRoot "start_bot.ps1"
$logDir = Join-Path $env:USERPROFILE "Desktop\JM\logs"
$logPath = Join-Path $logDir ("JMbot-start-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$transcriptStarted = $false

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

function Test-LocalPort {
    param([int]$Port)

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $connection
}

function Wait-Until {
    param(
        [scriptblock]$Condition,
        [int]$TimeoutSeconds,
        [int]$IntervalMilliseconds = 500
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) {
            return $true
        }
        Start-Sleep -Milliseconds $IntervalMilliseconds
    }

    return (& $Condition)
}

function Get-NapCatScript {
    if (-not [string]::IsNullOrWhiteSpace($env:JM_BOT_NAPCAT_SCRIPT)) {
        return $env:JM_BOT_NAPCAT_SCRIPT
    }

    if (-not [string]::IsNullOrWhiteSpace($env:JM_BOT_NAPCAT_DIR)) {
        return (Join-Path $env:JM_BOT_NAPCAT_DIR "napcat.quick.bat")
    }

    return $null
}

function Get-NapCatDir {
    param([string]$NapCatScript)

    if (-not [string]::IsNullOrWhiteSpace($env:JM_BOT_NAPCAT_DIR)) {
        return $env:JM_BOT_NAPCAT_DIR
    }

    return (Split-Path -Parent $NapCatScript)
}

function Get-EnvOrDefault {
    param([string]$Name, [string]$Default)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Get-NapCatWebUICredential {
    if ([string]::IsNullOrWhiteSpace($env:JM_BOT_NAPCAT_WEBUI_CONFIG)) {
        return $null
    }

    if (-not (Test-Path -LiteralPath $env:JM_BOT_NAPCAT_WEBUI_CONFIG)) {
        return $null
    }

    $webui = Get-Content -Raw -LiteralPath $env:JM_BOT_NAPCAT_WEBUI_CONFIG | ConvertFrom-Json
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($webui.token + ".napcat")
    $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    $loginBody = @{ hash = $hash } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Uri "http://127.0.0.1:6099/api/auth/login" -Method Post -ContentType "application/json" -Body $loginBody -TimeoutSec 5
    return $login.data.Credential
}

function Get-NapCatOneBotStatus {
    try {
        $credential = Get-NapCatWebUICredential
        if (-not $credential) {
            return "WebUI config not configured"
        }

        $headers = @{ Authorization = "Bearer $credential" }
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:6099/api/OB11Config/GetConfig" -Method Post -Headers $headers -ContentType "application/json" -Body "{}" -TimeoutSec 5
        if ($response.code -ne 0) {
            return $response.message
        }

        return "OK"
    }
    catch {
        return $_.Exception.Message
    }
}

function Test-OneBotConnected {
    param([int]$Port)

    $connections = Get-NetTCPConnection -LocalPort $Port -State Established -ErrorAction SilentlyContinue
    return $null -ne $connections
}

if ($Help) {
    Write-Host "Usage: powershell -ExecutionPolicy Bypass -File scripts/start_all.ps1 [-NoPause]"
    Write-Host "Configure NapCat with JM_BOT_NAPCAT_DIR or JM_BOT_NAPCAT_SCRIPT in scripts/local.env."
    return
}

try {
    Import-EnvFile (Join-Path $botDir ".env")
    Import-EnvFile (Join-Path $scriptRoot "local.env")

    $botPort = [int](Get-EnvOrDefault -Name "JM_BOT_PORT" -Default "8080")
    $napcatScript = Get-NapCatScript
    if ([string]::IsNullOrWhiteSpace($napcatScript)) {
        throw "NapCat path is not configured. Copy scripts/local.env.example to scripts/local.env and set JM_BOT_NAPCAT_DIR or JM_BOT_NAPCAT_SCRIPT."
    }
    if (-not (Test-Path -LiteralPath $napcatScript)) {
        throw "NapCat launcher was not found: $napcatScript"
    }
    if (-not (Test-Path -LiteralPath $botScript)) {
        throw "JMbot launcher was not found: $botScript"
    }
    $napcatDir = Get-NapCatDir -NapCatScript $napcatScript

    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir | Out-Null
    }

    Start-Transcript -Path $logPath -Append | Out-Null
    $transcriptStarted = $true

    Write-Step "JMbot one-click launcher" Cyan
    Write-Step "Log file: $logPath" DarkGray

    if (Test-LocalPort -Port $botPort) {
        Write-Step "JMbot is already listening on 127.0.0.1:$botPort." Yellow
    } else {
        Write-Step "Starting JMbot..." Green
        $botStartArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $botScript)
        Start-Process -FilePath "powershell.exe" -ArgumentList $botStartArgs -WorkingDirectory $botDir

        $botReady = Wait-Until -TimeoutSeconds 20 -Condition {
            Test-LocalPort -Port $botPort
        }
        if ($botReady) {
            Write-Step "JMbot is listening on 127.0.0.1:$botPort." Green
        } else {
            Write-Step "JMbot has not opened port $botPort yet. Check the JMbot PowerShell window." Yellow
        }
    }

    $napcatRunning = Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue
    if ($napcatRunning) {
        Write-Step "NapCat is already running." Yellow
    } else {
        Write-Step "Starting NapCat..." Green
        Start-Process -FilePath $napcatScript -WorkingDirectory $napcatDir
        $napcatStarted = Wait-Until -TimeoutSeconds 15 -Condition {
            $null -ne (Get-Process -Name "NapCatWinBootMain" -ErrorAction SilentlyContinue)
        }
        if ($napcatStarted) {
            Write-Step "NapCat process is running." Green
        } else {
            Write-Step "NapCat did not appear yet. It may still be opening QQ; check the NapCat window." Yellow
        }
    }

    $oneBotStatus = Get-NapCatOneBotStatus
    if ($oneBotStatus -eq "Not Login") {
        Write-Step "NapCat has started, but the QQ bot account is not logged in yet." Red
        Write-Step "Log in to the NapCat/QQ window first, then run this launcher again." Yellow
    } elseif ($oneBotStatus -ne "OK") {
        Write-Step "NapCat OneBot status check: $oneBotStatus" Yellow
    } elseif (Test-OneBotConnected -Port $botPort) {
        Write-Step "NapCat is connected to JMbot through OneBot reverse WebSocket." Green
    } else {
        Write-Step "NapCat is logged in, but OneBot is not connected yet. Wait 30 seconds or restart NapCat." Yellow
    }

    Write-Host ""
    $commandPrefix = Get-EnvOrDefault -Name "JM_BOT_COMMAND_PREFIX" -Default "/jm"
    Write-Step "Use $commandPrefix <album_id> in QQ to test." Cyan
    Write-Step "Keep the NapCat and JMbot windows open while using the bot." Cyan
}
catch {
    Write-Host ""
    Write-Step ("Launcher failed: {0}" -f $_.Exception.Message) Red
    Write-Step "Open the log file above and send the last red/yellow lines if you need diagnosis." Yellow
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close this launcher window"
    }
}
