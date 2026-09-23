# Removes the scheduled task and the native-host registration. Your data folder (database, backups) is kept.
$ErrorActionPreference = "Continue"
Unregister-ScheduledTask -TaskName "CoopApplyRunner" -Confirm:$false
foreach ($browser in @("Google\Chrome", "Microsoft\Edge")) {
  Remove-Item -Path "HKCU:\Software\$browser\NativeMessagingHosts\com.coop.apply_runner" -Force -ErrorAction SilentlyContinue
}
Write-Host "Removed. Data is still in $env:LOCALAPPDATA\CoopApplyRunner (delete it yourself if you want it gone)."
