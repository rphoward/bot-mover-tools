# Bot Mover bundle setup (Windows PowerShell). Safe to re-run.
# Makes .venv, installs requirements.lock, falls back to requirements.txt.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Lock = Join-Path $Root "requirements.lock"
$Txt  = Join-Path $Root "requirements.txt"
if (-not (Test-Path $Lock) -and -not (Test-Path $Txt)) {
  Write-Host "No Python packages needed. Setup done."
} else {
  $Py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $Py) { $Py = Get-Command python3 -ErrorAction SilentlyContinue }
  if (-not $Py) { $Py = Get-Command py -ErrorAction SilentlyContinue }
  if (-not $Py) { throw "Python not found. Install Python 3.10+ first." }
  $Venv = Join-Path $Root ".venv"
  if (-not (Test-Path $Venv)) { & $Py.Source -m venv $Venv }
  $VenvPy = Join-Path $Venv "Scripts\python.exe"
  if (-not (Test-Path $VenvPy)) { $VenvPy = Join-Path $Venv "bin/python" }  # PowerShell on Mac/Linux
  $lockOk = $false
  if (Test-Path $Lock) {
    & $VenvPy -m pip install --disable-pip-version-check -r $Lock
    if ($LASTEXITCODE -eq 0) { $lockOk = $true; Write-Host "Installed from requirements.lock." }
  }
  if (-not $lockOk) {
    if (-not (Test-Path $Txt)) { throw "requirements.lock failed and there is no requirements.txt to fall back to." }
    if (Test-Path $Lock) { Write-Host "requirements.lock didn't install (often a different computer type). Using requirements.txt..." }
    & $VenvPy -m pip install --disable-pip-version-check -r $Txt
    if ($LASTEXITCODE -ne 0) { throw "requirements.txt install failed." }
    Write-Host "Installed from requirements.txt."
  }
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
Write-Host "Setup done. In each new window:"
Write-Host "  `$env:DATA_ROOT = '$(Join-Path $Root "data")'"
Write-Host "  `$py = '$(Join-Path $Root ".venv\Scripts\python.exe")'"
