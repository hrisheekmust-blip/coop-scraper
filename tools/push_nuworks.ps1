# Daily: push the latest NUWorks crawl (data/nuworks.json) so the scraper and site pick it up.
# Installed once with tools/install_push_task.ps1. Safe to run any time.
Set-Location "$HOME\Documents\coop-scraper"
git pull --rebase -q origin main
if (git status --porcelain -- data/browser/coop_nuworks.json data/nuworks.json) {
  git add data/browser/coop_nuworks.json data/nuworks.json
  git commit -q -m "nuworks crawl $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
  git push -q origin main
  "pushed nuworks.json"
} else { "nuworks.json unchanged" }
