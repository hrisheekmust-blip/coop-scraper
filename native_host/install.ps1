# Installs the co-op application worker for the current Windows user.
#   powershell -ExecutionPolicy Bypass -File native_host\install.ps1 -Materials "$HOME\Documents\coop-apps"
# What it does (all per-user, no admin rights):
#   1. a Python virtual environment in %LOCALAPPDATA%\CoopApplyRunner\venv with pinned packages + Playwright's Chromium
#   2. first-time setup: data folder, pipe key, profile import from your materials folder
#   3. the Chrome/Edge native-messaging host registration (only the pinned extension id may start it)
#   4. a scheduled task that starts the service at logon (no terminal window) and starts it now
param(
  [Parameter(Mandatory = $true)][string]$Materials,
  [string]$Python = "py"
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA "CoopApplyRunner"
$Venv = Join-Path $Data "venv"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

Write-Host "1/4 Python environment in $Venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
  & $Python -3 -m venv $Venv
  if ($LASTEXITCODE -ne 0) { & $Python -m venv $Venv }
}
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }
$Py = Join-Path $Venv "Scripts\python.exe"
$Pyw = Join-Path $Venv "Scripts\pythonw.exe"
& $Py -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'"; Check "Python version check"
& $Py -m pip install --upgrade pip | Out-Null; Check "pip upgrade"
& $Py -m pip install -r (Join-Path $Repo "runner\requirements.txt"); Check "installing packages"
& $Py -m playwright install chromium; Check "installing Chromium"

Write-Host "2/4 First-time setup"
Push-Location $Repo
& $Py -m runner setup --materials $Materials; Check "setup"
Pop-Location

Write-Host "3/4 Native messaging host"
$Bat = Join-Path $Data "bridge.bat"
"@echo off`r`ncd /d `"$Repo`"`r`n`"$Py`" -u `"$Repo\native_host\bridge.py`"`r`n" | Set-Content -Encoding ASCII $Bat
$Manifest = Join-Path $Data "com.coop.apply_runner.json"
(Get-Content (Join-Path $PSScriptRoot "manifest.template.json") -Raw).Replace("__BRIDGE_BAT__", $Bat.Replace("\", "\\")) | Set-Content -Encoding UTF8 $Manifest
foreach ($browser in @("Google\Chrome", "Microsoft\Edge")) {
  $key = "HKCU:\Software\$browser\NativeMessagingHosts\com.coop.apply_runner"
  New-Item -Force -Path $key | Out-Null
  Set-ItemProperty -Path $key -Name "(default)" -Value $Manifest
}

Write-Host "4/4 Start at logon"
$Action = New-ScheduledTaskAction -Execute $Pyw -Argument "-m runner service" -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "CoopApplyRunner" -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName "CoopApplyRunner"
Start-Sleep -Seconds 4
Push-Location $Repo
& $Py -m runner status
Pop-Location

Write-Host ""
Write-Host "Next:"
Write-Host "  - Chrome: chrome://extensions > Developer mode > Load unpacked > $Repo\extension"
Write-Host "    (its id must be jbpfdafeplfiphgmcohcpffnjfcdfaik)"
Write-Host "  - Open the extension's settings and save your portal password"
Write-Host "  - Board > settings > Execution mode: Worker"
