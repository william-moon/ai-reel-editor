# Installer for Windows 10/11 (PowerShell). Safe to re-run.
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-WithRclone] [-WithEnvato] [-NoOptional]
param([switch]$WithRclone, [switch]$WithEnvato, [switch]$NoOptional)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
function Say($m) { Write-Host "`n==> $m" }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

Say "ffmpeg"
if (-not (Have ffmpeg)) {
  if (Have winget) { winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements; Refresh-Path }
  else { throw "Install ffmpeg (https://www.gyan.dev/ffmpeg/builds/) and add it to PATH, then re-run." }
}
ffmpeg -version | Select-Object -First 1

Say "Python 3.12"
$py = $null
foreach ($v in @("3.12", "3.11", "3.10")) {
  if (Have py) { & py -$v -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } }
}
if (-not $py) {
  if (Have winget) { winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements; Refresh-Path; $py = @("py", "-3.12") }
  else { throw "Install Python 3.12 from https://www.python.org/downloads/ then re-run." }
}

Say "virtual environment (.venv) + packages"
if (-not (Test-Path .venv)) { & $py[0] $py[1] -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q -r requirements.txt
if (-not $NoOptional) {
  & $vpy -m pip install -q -r requirements-optional.txt
  if ($LASTEXITCODE -ne 0) { Write-Host "(optional packages skipped: editor works without face tracking)" }
}

Say "models (face tracking + voice denoise)"
& $vpy -m reel_editor fetch-assets

if ($WithRclone -and -not (Have rclone)) {
  Say "rclone (Google Drive)"
  winget install -e --id Rclone.Rclone --accept-source-agreements --accept-package-agreements; Refresh-Path
}
if ($WithEnvato) {
  Say "Envato adapter (Node.js)"
  if (-not (Have node)) { winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements; Refresh-Path }
  Push-Location tools; npm install --silent; Pop-Location
}

if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "created .env" }
New-Item -ItemType Directory -Force workspace | Out-Null

Say "check"
& $vpy -m reel_editor doctor
Write-Host "`nInstalled. Next: follow SKILL.md (B-roll keys, storage, test edit)."
