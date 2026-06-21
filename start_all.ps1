param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

# Fixed launcher paths. The desktop .bat only calls this script.
$napcatDir = "D:\Downlock-of-Edge\NapCat.Shell.Windows.OneKey\NapCat.44498.Shell"
$napcatScript = Join-Path $napcatDir "napcat.quick.bat"
$botDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$botScript = Join-Path $botDir "start_bot.ps1"
$botPort = 8080
$logDir = Join-Path $env:USERPROFILE "Desktop\JM\logs"
$logPath = Join-Path $logDir ("JMbot-start-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$transcriptStarted = $false

function Write-Step {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White)

    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -ForegroundColor $Color
}

function Test-LocalPort {
    param([int]$Port)

    # Use the listening port to avoid starting duplicate JMbot processes.
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $connection
}

function Wait-Until {
    param(
        [scriptblock]$Condition,
        [int]$TimeoutSeconds,
        [int]$IntervalMilliseconds = 500
    )

    # Poll for a real condition instead of relying on a fixed sleep.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) {
            return $true
        }
        Start-Sleep -Milliseconds $IntervalMilliseconds
    }

    return (& $Condition)
}

function Get-NapCatWebUICredential {
    # Exchange the local WebUI token for a short-lived NapCat credential.
    $webuiConfigPath = Join-Path $napcatDir "versions\9.9.26-44498\resources\app\napcat\config\webui.json"
    if (-not (Test-Path -LiteralPath $webuiConfigPath)) {
        return $null
    }

    $webui = Get-Content -Raw -LiteralPath $webuiConfigPath | ConvertFrom-Json
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($webui.token + ".napcat")
    $hash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    $loginBody = @{ hash = $hash } | ConvertTo-Json -Compress
    $loginArgs = @{
        Uri = "http://127.0.0.1:6099/api/auth/login"
        Method = "Post"
        ContentType = "application/json"
        Body = $loginBody
        TimeoutSec = 5
    }
    $login = Invoke-RestMethod @loginArgs
    return $login.data.Credential
}

function Get-NapCatOneBotStatus {
    # Read the live OneBot config. NapCat returns "Not Login" before QQ login.
    try {
        $credential = Get-NapCatWebUICredential
        if (-not $credential) {
            return "WebUI config not found"
        }

        $headers = @{ Authorization = "Bearer $credential" }
        $configArgs = @{
            Uri = "http://127.0.0.1:6099/api/OB11Config/GetConfig"
            Method = "Post"
            Headers = $headers
            ContentType = "application/json"
            Body = "{}"
            TimeoutSec = 5
        }
        $response = Invoke-RestMethod @configArgs
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
    # A reverse WebSocket connection shows up as an Established connection on 8080.
    $connections = Get-NetTCPConnection -LocalPort $botPort -State Established -ErrorAction SilentlyContinue
    return $null -ne $connections
}

try {
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir | Out-Null
    }

    # Write launcher logs to Desktop\JM\logs for later troubleshooting.
    Start-Transcript -Path $logPath -Append | Out-Null
    $transcriptStarted = $true

    Write-Step "JM bot one-click launcher" Cyan
    Write-Step "Log file: $logPath" DarkGray

    if (-not (Test-Path -LiteralPath $napcatScript)) {
        throw "NapCat launcher was not found: $napcatScript"
    }
    if (-not (Test-Path -LiteralPath $botScript)) {
        throw "JMbot launcher was not found: $botScript"
    }

    # Start JMbot before NapCat to avoid NapCat hitting an empty 8080 port.
    if (Test-LocalPort -Port $botPort) {
        Write-Step "JMbot is already listening on 127.0.0.1:$botPort." Yellow
    } else {
        Write-Step "Starting JMbot..." Green
        $botStartArgs = @(
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            $botScript
        )
        Start-Process -FilePath "powershell.exe" -ArgumentList $botStartArgs -WorkingDirectory $botDir

        $botReady = Wait-Until -TimeoutSeconds 20 -Condition {
            Test-LocalPort -Port $botPort
        }
        if ($botReady) {
            Write-Step "JMbot is listening on 127.0.0.1:$botPort." Green
        } else {
            Write-Step "JMbot has not opened port $botPort yet. Look at the new JMbot PowerShell window for errors." Yellow
        }
    }

    # NapCat logs into QQ and forwards messages to JMbot through OneBot.
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

    # Final chain check: QQ login status plus OneBot connection status.
    $oneBotStatus = Get-NapCatOneBotStatus
    if ($oneBotStatus -eq "Not Login") {
        Write-Step "NapCat has started, but the QQ bot account is not logged in yet." Red
        Write-Step "Log in to the NapCat/QQ window first, then run this launcher again." Yellow
    } elseif ($oneBotStatus -ne "OK") {
        Write-Step "NapCat OneBot status check: $oneBotStatus" Yellow
    } elseif (Test-OneBotConnected) {
        Write-Step "NapCat is connected to JMbot through OneBot reverse WebSocket." Green
    } else {
        Write-Step "NapCat is logged in, but OneBot is not connected yet. Wait 30 seconds or restart NapCat." Yellow
    }

    Write-Host ""
    Write-Step "Use /jm 1949101 in QQ group 1053512745 to test." Cyan
    Write-Step "Keep the NapCat and JMbot windows open while using the bot." Cyan
}
catch {
    Write-Host ""
    Write-Step ("Launcher failed: {0}" -f $_.Exception.Message) Red
    Write-Step "Open the log file above and send the last red/yellow lines if you need me to diagnose it." Yellow
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
