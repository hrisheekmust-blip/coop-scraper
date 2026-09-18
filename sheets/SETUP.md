# Google Sheet setup (one time, ~3 minutes)

The sheet pulls `data/sheet.csv`, `data/watchlist.csv` and `data/events.csv` from your GitHub repo every hour,
so the repo has to exist first.

## 1. Push the repo (once)

In the `coop-scraper` folder:

```
git init
git add .
git commit -m "co-op scraper"
git branch -M main
git remote add origin https://github.com/hrisheekmust-blip/coop-scraper.git
git push -u origin main
```

Create the empty repo at github.com/new first (name it `coop-scraper`, **Public**, no README). Public matters: the sheet
reads the raw file URL without a token. If you want it private, add a GitHub token to the script's `UrlFetchApp` call.

Then on GitHub: Actions tab → enable workflows → "daily co-op scrape" → Run workflow. It runs twice a day after that.

## 2. Make the sheet

1. New Google Sheet, name it whatever.
2. Extensions → Apps Script. Delete the sample code, paste all of `sheets/Code.gs`, save.
3. In the function dropdown pick `setup`, press Run. Google asks you to authorize (it needs "connect to external
   service" for GitHub and access to this sheet). Approve.
4. Tabs appear: **APPLY NOW**, **All postings**, **Watchlist**, **Events**, **My status**. Refresh runs hourly from then on;
   the "Co-op board" menu in the sheet has "Refresh now".

## What the colours mean

- Red row, bold: **APPLY NOW** — spring/Jan 2027 role posted in the last 7 days, or deadline within 14 days.
- Orange row, bold: **APPLY** — spring/Jan 2027 role, older.
- Blue row: **SOON** — titled co-op with no term stated, posted in the last 7 days. Ask the recruiter if spring works.
- White: **WATCH** — intern posting with no term, kept because startups rarely say.
- Green **NEW** chip: first seen by the scraper in the last 3 days.
- Grey row: you set the status to applied / interview / offer / skip.
- Red "NOT QUALIFIED" in the NU eligible column: NUWorks shows you as ineligible (usually applicant-type mismatch).

Set **My status** and **Notes** directly in the row. They're stored by link in the My status tab and survive refreshes.

## Watchlist

Companies from `config/companies.json` with no spring/co-op posting yet. The "expect" column fills in as the scraper
learns when each company opened (it records the first-seen date of every posting from now on, so next cycle it can say
"Draper opened around Aug 21 last year").
