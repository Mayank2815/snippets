<#
.SYNOPSIS
Starts the Core Emulation Engine from .venv, waits until it answers, opens the
console in the default browser and keeps the engine in the foreground so
Ctrl-C stops it. Windows only; macOS and Linux use run.sh.

    .\run.ps1              start and open http://127.0.0.1:4320/
    .\run.ps1 -NoOpen      start without opening a browser
    $env:PORT = 4321; .\run.ps1    use another port (the console follows automatically)

.NOTES
Works on Windows PowerShell 5.1 and PowerShell 7; needs no extra modules.
If Windows refuses to run it ("running scripts is disabled on this system"):

    Set-ExecutionPolicy -Scope Process Bypass
    .\run.ps1
#>
param(
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Error "run.ps1: .venv is missing - run .\install.ps1 first."
    exit 1
}

# WHY 4320: must match DEFAULT_PORT in engine/engine.py (one above task-notif's 4310).
$port = if ($env:PORT) { [int]$env:PORT } else { 4320 }
$url = "http://127.0.0.1:$port"

# netstat is on every Windows since XP and needs no module; Get-NetTCPConnection
# would be nicer but is missing on some stripped-down images.
$listening = netstat -ano -p tcp 2>$null | Select-String -Pattern "^\s*TCP\s+\S+:$port\s+\S+\s+LISTENING"
if ($listening) {
    Write-Error ("run.ps1: port $port is already in use - another engine (or something else) is listening.`n" +
                 "run.ps1: find it with:  netstat -ano | findstr :$port   (last column is the PID; Stop-Process -Id <pid>), or start with  `$env:PORT = <other>; .\run.ps1")
    exit 1
}

# Activate the venv so `python` in this window is the venv's, exactly like run.sh.
. (Join-Path $PSScriptRoot '.venv\Scripts\Activate.ps1')

$env:PORT = "$port"
$engine = Start-Process -FilePath $venvPython -ArgumentList 'engine\engine.py' -NoNewWindow -PassThru

function Stop-Engine {
    if ($engine -and -not $engine.HasExited) {
        # Ctrl-C in this window reaches the engine too (same console), and it
        # shuts down cleanly on its own; give it that chance before killing it.
        # WHY 3 s: the engine joins its loop for 2 s at most, plus a margin.
        if (-not $engine.WaitForExit(3000)) {
            Stop-Process -Id $engine.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    # WHY 10 x 1 s: ten seconds is far more than the engine needs to bind, so a
    # miss here means it crashed (its traceback is printed above), not that it is slow.
    $up = $false
    for ($try = 0; $try -lt 10; $try++) {
        try {
            $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri "$url/status"
            $up = $true
            break
        } catch {
            if ($engine.HasExited) {
                Write-Error "run.ps1: the engine exited before answering on $url - see the error above."
                exit 1
            }
            Start-Sleep -Seconds 1
        }
    }
    if (-not $up) {
        Write-Error "run.ps1: the engine did not answer on $url/status within 10 s."
        exit 1
    }

    Write-Host "Engine is up: $url/   (Ctrl-C stops it)"
    if (-not $NoOpen) {
        try { Start-Process "$url/" } catch { Write-Host "run.ps1: could not open a browser - open $url/ yourself." }
    }

    $engine.WaitForExit()
} finally {
    Stop-Engine
}
