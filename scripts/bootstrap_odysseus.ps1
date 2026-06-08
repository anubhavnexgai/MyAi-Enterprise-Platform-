# Bootstrap the dedicated venv used to run vendored Odysseus instances.
#
# The MyAi bridge launches one Odysseus subprocess per tenant using this venv's
# interpreter (see app/odysseus_bridge/supervisor.py -> odysseus_python_exe).
# Giving Odysseus its own venv keeps its pinned deps from clashing with MyAi's.
#
# Usage:  pwsh scripts/bootstrap_odysseus.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$odyDir = Join-Path $root "vendor/odysseus"
$venv = Join-Path $odyDir ".venv"

if (-not (Test-Path $odyDir)) { throw "vendor/odysseus not found at $odyDir" }

if (-not (Test-Path $venv)) {
    Write-Host "Creating venv at $venv ..."
    python -m venv $venv
}

$py = Join-Path $venv "Scripts/python.exe"
Write-Host "Upgrading pip ..."
& $py -m pip install --upgrade pip wheel setuptools

Write-Host "Installing Odysseus requirements ..."
& $py -m pip install -r (Join-Path $odyDir "requirements.txt")

Write-Host "Installing optional requirements (best-effort) ..."
try { & $py -m pip install -r (Join-Path $odyDir "requirements-optional.txt") }
catch { Write-Warning "optional requirements failed (non-fatal): $_" }

Write-Host "Done. Odysseus interpreter: $py"
