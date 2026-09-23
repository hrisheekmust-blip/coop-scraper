# coop-scraper

Daily scan for Spring 2027 (Jan–Jun) hardware / semiconductor co-ops and internships, tuned for one person's
search. Built from the useful parts of existing projects rather than from scratch:

- ATS layer: the same public JSON endpoints [ats-jobs](https://github.com/shunsukefuruyama/ats-jobs) uses
  (Greenhouse, Lever, Ashby, Workday `wday/cxs`, Oracle HCM REST, SuccessFactors `services/recruiting/v1/jobs`, Jibe).
- Company map seeded from [quickjobs_public](https://github.com/deibhaid/quickjobs_public) (`quickjobs.companies.json`)
  and [hardware-internships-2027](https://github.com/Khushsj30/hardware-internships-2027), plus the semiconductor
  companies those lists were missing (NXP, TI, onsemi, Skyworks, Microchip, Allegro, MPS, Silicon Labs, Wolfspeed, Samsung…).
- Community lists ingested as extra sources: vanshb03 off-season + summer lists, sndsh404, hardware-internships-2027.
- ATS fetchers ported from the provider notes in [career-ops](https://github.com/career-ops-hq/career-ops)
  (Eightfold, Phenom, SuccessFactors CSB, SmartRecruiters, iCIMS) plus direct career-site APIs for Amazon, Google,
  Tesla and Apple — `scraper/fetchers_more.py`. `scraper/discover.py` probes a company's ATS/tenant automatically.
- [internlist.org](https://internlist.org) (Simplify's index of ~20k company career pages): the Co-op and Winter/Spring
  lists via their open `api.simplify.jobs` endpoint. This is what catches companies whose ATS we can't reach directly
  (AMD, Draper, Anduril, …). See `scraper/sources_internlist.py`.

What it adds that none of those have: **term detection** (spring / Jan 2027 / winter-spring / 6-month / co-op with no
term stated), a hardware-title filter that isn't fooled by "software engineer, validation", US-only, undergrad-only,
and a **diff** so you only read what is new.

## Layout

```
config/companies.json      who to scan, which ATS, tenant/site ids. Add companies here.
scraper/fetchers.py        one fetcher per ATS (stdlib only)
scraper/filters.py         hardware keywords, exclusions, term detection, ranking
scraper/sources_github.py  parses the community markdown lists
scraper/sources_internlist.py  pulls internlist.org / Simplify's Co-op + Winter-Spring lists (open JSON API)
scraper/main.py            pipeline: fetch -> classify -> diff against seen.json -> write outputs
scraper/fetchers_more.py   Eightfold / Phenom / SuccessFactors-CSB / SmartRecruiters / iCIMS + Amazon, Google, Tesla, Apple
scraper/discover.py        works out which ATS + tenant ids a company uses (config/candidates.json -> data/discover_config.json)
scraper/priority.py        urgency bands + sheet.csv / watchlist.csv / openings.csv / events.csv (what the Google Sheet reads)
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

## "Just opened" + alerts

`data/openings.csv` lists companies whose earliest spring/co-op posting appeared in the last 7 days — the signal for
"X just dropped their reqs". The sheet shows it as the **Openings** tab and emails you (the Google account that owns
the sheet) whenever a CHIP/HARDWARE role enters APPLY NOW/APPLY or a company opens. `data/last_cycle_2026.json`
holds last cycle's first-seen dates (from the community lists' git history + our own records from now on) and feeds
the Watchlist "expect" column.

## Adding companies in bulk

Put names + guessed slugs + careers URL in `config/candidates.json`, run the workflow with "discover" ticked, then
paste `data/discover_config.json` entries into `config/companies.json`.

## The site

`index.html` at the repo root is the board, served by GitHub Pages (Settings → Pages → main, `/`): it reads `data/*.csv`
so it is current within a minute of every scrape. Tabs: Apply now, Maybe, All, Openings, Watchlist, Events, Leads,
Rejected (audit trail). Statuses are saved in the browser (localStorage). The Google Sheet (`sheets/`) still works but
is no longer the primary UI.

## Batch application setup and troubleshooting

Batch Apply supports `jobs.ashbyhq.com`, `boards.greenhouse.io`, `job-boards.greenhouse.io`, and `jobs.lever.co`.
Other portals have an **Open application** link for manual completion.

1. In board Settings, configure a token with Contents read/write access to the private materials repository.
2. Install or update the Tampermonkey apply script to **version 3.3** from the board's Settings link.
3. Select postings with prepared materials and click **Apply to all**. Allow the popup and keep the board tab open.
4. Review every answer and attachment, complete missing fields or captcha, and submit using the application site's own button. The script never presses final Submit automatically. A confirmed submission returns to the relay
   and advances to the next posting. **Stop** prevents the next handoff; it does not close a form already open.

Version 3.3 replaces broad keyword guesses with narrow profile-based mappings and exact option validation.
Hidden portal metadata is omitted from the question preview. Unknown facts, ambiguous choices, unsupported
options and dates without a confirmed day are left for review. The script stops before every final submission;
batches proceed after you submit and a confirmation is detected. Upgrade the installed script to get both
answer engine v7 and the new form controls (the engine dependency URL is versioned for Tampermonkey caching).
The broader portal helpers remain in the script, but supported board/relay hosts are still limited to those above.

If preparation fails, the board and relay show the reason. Stop the batch, correct the token/materials problem,
then retry. If GitHub cannot save a result, its receipt remains in this browser and the next board refresh retries it.
Clearing browser storage before sync succeeds removes that receipt. A token is still required for private materials
and cross-browser status persistence.

Run the offline regression suite with `node --test tests/*.test.cjs`. These tests use simulated portals and GitHub
responses and never send applications. Push/PR CI runs this suite. The existing live-form dry-run workflow is
manual-only, uses its synthetic applicant, and the userscript honors its dry-run flag before pressing Submit.

## Alerts

`scraper/alerts.py` runs after every scrape: new CHIP / HARDWARE / MAYBE rows, company openings and broken fetchers
open a GitHub Issue (GitHub emails you) and push to `ntfy.sh/<NTFY_TOPIC>` when the repo variable `NTFY_TOPIC` is set.

## NUWorks

`scraper/nuworks_crawl.js` runs in a logged-in NUWorks tab (a daily scheduled task in the Claude desktop app does it,
or paste it in the DevTools console) and produces `data/browser/coop_nuworks.json`; `tools/push_nuworks.ps1` pushes it
(install the daily Windows task once with `tools/install_push_task.ps1`); the next scrape converts it.

## Google Sheet (optional)

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
- Google / Apple / Qualcomm / Micron / Keysight use bespoke career sites; they show up via the community lists and internlist.org.
- NUWorks is behind SSO — no scraper can reach it; use the browser step.
