<#
.SYNOPSIS
Sets up the Core Emulation Engine on Windows: a private Python virtualenv in
.venv holding pynput. Safe to re-run — an existing .venv is reused and pip
only installs what is missing. macOS and Linux use install.sh instead.

.NOTES
Works on Windows PowerShell 5.1 and PowerShell 7; needs no extra modules.
If Windows refuses to run it ("running scripts is disabled on this system"),
allow scripts for this one window and try again:

    Set-ExecutionPolicy -Scope Process Bypass
    .\install.ps1
#>

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Find-Python {
    # `py -3` is the launcher every python.org install adds and is immune to
    # the Microsoft Store's "python" alias, which only opens the Store. Fall
    # back to `python` for installs that skipped the launcher.
    $candidates = @(
        @{ Exe = 'py';     Extra = @('-3') },
        @{ Exe = 'python'; Extra = @() }
    )
    foreach ($candidate in $candidates) {
        $exe = $candidate.Exe
        $extra = $candidate.Extra
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $version = & $exe @extra -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>$null
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $version -or "$version" -notmatch '^\d+\.\d+\.\d+$') { continue }
        return @{ Exe = $exe; Args = $extra; Version = [version]"$version" }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Error "install.ps1: Python 3 was not found. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), then re-run."
    exit 1
}
# WHY 3.9: the same floor as install.sh so one requirements.txt serves every platform.
if ($python.Version -lt [version]'3.9') {
    Write-Error "install.ps1: Python $($python.Version) found; Python 3.9 or newer is required."
    exit 1
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    Write-Host ".venv already exists - reusing it."
} else {
    Write-Host "Creating .venv with Python $($python.Version) ..."
    & $python.Exe @($python.Args) -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Error "install.ps1: could not create .venv - see the error above."
        exit 1
    }
}

Write-Host "Installing requirements into .venv ..."
& $venvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Error "install.ps1: pip could not upgrade itself - see the error above."; exit 1 }
& $venvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Error "install.ps1: pip could not install the requirements - see the error above."; exit 1 }

& $venvPython -c 'import pynput' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "install.ps1: pynput still cannot be imported from .venv. Delete .venv (Remove-Item -Recurse .venv) and re-run; check the pip output above."
    exit 1
}

Write-Host ""
Write-Host "Installed. Start the engine with:  .\run.ps1"
Write-Host "(No permission step is needed on Windows; the engine drives input through SendInput.)"
