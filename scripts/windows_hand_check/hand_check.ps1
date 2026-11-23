# The owner's hand check of Finzytrack's saves in OneDrive on Windows (S0150; BL-0283, BL-0087).
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\windows_hand_check\hand_check.ps1 [-Installed <folder>]
# It installs the engine's and the sidecar's locked environments into this download's engine\.venv and backend\.venv
# (uv sync --locked), then runs backend\scripts\windows_hand_check.py, which writes only its own scratch folders
# (README.md beside this file).
param([string]$Installed = "")
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($env:OneDrive -and $repo.StartsWith($env:OneDrive, [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "This folder is inside OneDrive. Unzip Finzytrack to a folder outside OneDrive (for example C:\ft-check) and run it from there."
    exit 2
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed. Install it (README.md, step 1), open a new PowerShell window, and run this again."
    exit 2
}
foreach ($part in @("engine", "backend")) {
    Write-Host "Installing the $part environment (uv sync --locked) ..."
    Push-Location (Join-Path $repo $part)
    try {
        uv sync --locked
        if ($LASTEXITCODE -ne 0) { Write-Host "uv sync failed in $part."; exit 2 }
    } finally {
        Pop-Location
    }
}
$checkArgs = @("-I", "scripts\windows_hand_check.py")
if ($Installed -ne "") { $checkArgs += @("--packaged", $Installed) }
Push-Location (Join-Path $repo "backend")
try {
    & .\.venv\Scripts\python.exe @checkArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
