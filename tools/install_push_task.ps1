# Run once in PowerShell: creates a Windows scheduled task that pushes data/nuworks.json every day at 8:45pm
# (after the daily NUWorks crawl task in the Claude app). Remove with: schtasks /Delete /TN "coop-scraper push" /F
$script = "$HOME\Documents\coop-scraper\tools\push_nuworks.ps1"
schtasks /Create /F /SC DAILY /ST 20:45 /TN "coop-scraper push" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$script`""
"installed: 'coop-scraper push' runs daily at 8:45pm. Test it now with: schtasks /Run /TN `"coop-scraper push`""
