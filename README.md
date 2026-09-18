# coop-scraper

Daily scan for Spring 2027 (Jan–Jun) hardware / semiconductor co-ops and internships, tuned for one person's
search. Built from the useful parts of existing projects rather than from scratch:

- ATS layer: the same public JSON endpoints [ats-jobs](https://github.com/shunsukefuruyama/ats-jobs) uses
  (Greenhouse, Lever, Ashby, Workday `wday/cxs`, Oracle HCM REST, SuccessFactors `services/recruiting/v1/jobs`, Jibe).
- Company map seeded from [quickjobs_public](https://github.com/deibhaid/quickjobs_public) (`quickjobs.companies.json`)
  and [hardware-internships-2027](https://github.com/Khushsj30/hardware-internships-2027), plus the semiconductor
  companies those lists were missing (NXP, TI, onsemi, Skyworks, Microchip, Allegro, MPS, Silicon Labs, Wolfspeed, Samsung…).
- Community lists ingested as extra sources: vanshb03 off-season + summer lists, sndsh404, hardware-internships-2027.

What it adds that none of those have: **term detection** (spring / Jan 2027 / winter-spring / 6-month / co-op with no
term stated), a hardware-title filter that isn't fooled by "software engineer, validation", US-only, undergrad-only,
and a **diff** so you only read what is new.

## Layout

```
config/companies.json      who to scan, which ATS, tenant/site ids. Add companies here.
scraper/fetchers.py        one fetcher per ATS (stdlib only)
scraper/filters.py         hardware keywords, exclusions, term detection, ranking
scraper/sources_github.py  parses the community markdown lists
scraper/main.py            pipeline: fetch -> classify -> diff against seen.json -> write outputs
scraper/priority.py        urgency bands + sheet.csv / watchlist.csv / events.csv (what the Google Sheet reads)
scraper/jobspy_search.py   optional LinkedIn/Indeed sweep (python-jobspy), runs in GitHub Actions
scraper/nuworks.py         converts a NUWorks browser crawl into data/nuworks.json
sheets/Code.gs             Google Apps Script for the auto-updating sheet
scraper/browser_fetch.js   same fetch logic for running inside a browser (when the machine can't reach ATS hosts)
scraper/merge_browser.py   merges the browser's coop_*.json downloads into data/raw_browser.json
data/NEW.md                what to read each morning (new today + full ranked list)
data/tracker.csv           flat table; status / nu_connection / notes columns survive re-runs
data/jobs.json, seen.json, rejected.jsonl
.github/workflows/daily.yml  runs it every day and commits data/
```

## Google Sheet

`sheets/SETUP.md` — a Sheet that refreshes itself hourly from this repo's `data/sheet.csv`, colour-banded by urgency
(APPLY NOW / APPLY / SOON / WATCH), NEW chips, deadlines, NUWorks eligibility, and your own status/notes that survive refreshes.

## Run

```
python -m scraper.main                 # full run (needs network to the ATS hosts; GitHub Actions has it)
python -m scraper.main --raw data/raw_browser.json   # classify jobs pulled via the browser instead
```

To get it running daily: push this folder to a GitHub repo, enable Actions, done. Results land in `data/` and
`data/NEW.md` shows the delta each day. Trigger manually from the Actions tab with "Run workflow".

## Ranks

- **A** — spring / January 2027 / winter-spring / 6-month stated in title or description
- **B** — titled "co-op" but no term stated (many of these are flexible; ask the recruiter)
- **C** — intern with no term stated (usually summer; kept because startups often don't say)

Summer-only and Fall-2026-only postings are dropped (see `data/rejected.jsonl` for what was filtered and why).

## Adding a company

Greenhouse / Lever / Ashby: `{"name": "X", "ats": "greenhouse", "token": "<board token>"}` — the token is in the
company's job-board URL. Workday: find `https://<tenant>.<wdN>.myworkdayjobs.com/<site>` in the careers URL
(`{"ats":"workday","tenant":"nxp","host":"wd3","site":"careers"}`). Oracle HCM: host + `siteNumber` from the
careers URL. SuccessFactors: `base` URL of the careers site.

## Known gaps

- Teradyne (`careers.teradyne.com` cert error) and Infineon (login-walled search) need manual checks.
- Google / Apple / Qualcomm / Micron / Keysight use bespoke career sites; they show up only via the community lists.
- NUWorks is behind SSO — no scraper can reach it; use the browser step.
