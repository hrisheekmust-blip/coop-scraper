"""Alert on anything new worth acting on, straight from the scraper run (no sheet, no passwords).

  1. Opens a GitHub Issue in this repo listing the new rows -> GitHub emails the repo owner (issues: write, GITHUB_TOKEN).
  2. Pushes to ntfy.sh/<NTFY_TOPIC> if NTFY_TOPIC is set (repo variable) -> phone notification via the free ntfy app.

State lives in data/alerts_state.json so each posting/opening is announced once. First run only seeds the state.
"""
import csv
import json
import os
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REPO = os.environ.get("GITHUB_REPOSITORY", "hrisheekmust-blip/coop-scraper")
SITE = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}/"
FITS = ("CHIP", "HARDWARE", "MAYBE")


def rows(name):
    p = os.path.join(DATA, name)
    return list(csv.DictReader(open(p, newline=""))) if os.path.exists(p) else []


def post(url, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method or ("POST" if body else "GET"))
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main():
    spath = os.path.join(DATA, "alerts_state.json")
    seeded = os.path.exists(spath)
    state = json.load(open(spath)) if seeded else {"sent": []}
    sent = set(state["sent"])

    sheet = rows("sheet.csv")
    # every chip/hardware/maybe row alerts, whatever the urgency: an untermed "Analog Design Intern" at TI is still worth a ping
    new_rows = [r for r in sheet if r["fit"] in FITS and r["link"] not in sent]
    opens = [o for o in rows("openings.csv") if f"open:{o['company']}:{o['opened']}" not in sent]
    broken = [w for w in rows("watchlist.csv") if w["status"].startswith("FETCHER") and f"health:{w['company']}" not in sent]

    for r in new_rows:
        sent.add(r["link"])
    for o in opens:
        sent.add(f"open:{o['company']}:{o['opened']}")
    for w in broken:
        sent.add(f"health:{w['company']}")
    state["sent"] = sorted(sent)[-6000:]
    json.dump(state, open(spath, "w"), indent=0)

    if not seeded:
        print(f"alerts: seeded state with {len(sent)} keys, no alert on first run"); return
    if not (new_rows or opens or broken):
        print("alerts: nothing new"); return

    uo = {"APPLY NOW": 0, "APPLY": 1, "SOON": 2, "WATCH": 3}
    new_rows.sort(key=lambda r: ({"CHIP": 0, "HARDWARE": 1, "MAYBE": 2}[r["fit"]], uo.get(r["urgency"], 9), r["company"]))
    chips = sum(1 for r in new_rows if r["fit"] == "CHIP")
    title = f"{len(new_rows)} new co-op posting(s)" + (f", {chips} CHIP" if chips else "") + (f", {len(opens)} company opening(s)" if opens else "") + f" — {date.today()}"
    md = [f"[Open the board]({SITE})\n"]
    if opens:
        md.append("## Companies that just opened spring reqs\n")
        md += [f"- **{o['company']}** — {o['n']} posting(s), e.g. [{o['sample']}]({o['link']})" for o in opens]
        md.append("")
    if new_rows:
        md.append("## New postings\n\n| Fit | Urgency | Company | Role | Location | Deadline |\n|---|---|---|---|---|---|")
        md += [f"| **{r['fit']}** | {r['urgency']} | {r['company']} | [{r['role']}]({r['link']}) | {r['location']} | {r['deadline']} |" for r in new_rows]
        md.append("")
    if broken:
        md.append("## Fetcher health\n")
        md += [f"- {w['company']}: {w['status']}" for w in broken]
    body = "\n".join(md)

    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        try:
            st, _ = post(f"https://api.github.com/repos/{REPO}/issues", {"title": title, "body": body, "labels": ["alert"]},
                         {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                          "User-Agent": "coop-scraper"})
            print("alerts: github issue", st)
        except Exception as e:  # noqa
            print("alerts: github issue failed", type(e).__name__, str(e)[:100])
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        lines = [f"{r['fit']} · {r['company']} · {r['role']}" for r in new_rows[:12]]
        if len(new_rows) > 12:
            lines.append(f"…and {len(new_rows) - 12} more")
        lines += [f"OPENED: {o['company']} ({o['n']})" for o in opens]
        try:
            st, _ = post(f"https://ntfy.sh/{topic}", "\n".join(lines).encode(),
                         {"Title": title, "Click": SITE, "Priority": "high" if chips or opens else "default", "Tags": "chip" if chips else "clipboard"})
            print("alerts: ntfy", st)
        except Exception as e:  # noqa
            print("alerts: ntfy failed", type(e).__name__, str(e)[:100])


if __name__ == "__main__":
    main()
