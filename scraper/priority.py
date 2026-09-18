"""Turn jobs.json (+ nuworks.json, nuevents.json) into the sheet-facing CSVs.

  data/sheet.csv      every tracked posting, urgency-ranked, newest first inside each band
  data/watchlist.csv  target companies with no spring posting yet + what we know about when they open
  data/events.csv     employer events worth showing up to

Urgency bands (what the sheet colors):
  APPLY NOW  spring/Jan-2027 posting that is fresh (<= 7 days) or has a deadline inside 14 days
  APPLY      spring/Jan-2027 posting, older
  SOON       co-op with term not stated, fresh (employers often confirm spring on request)
  WATCH      everything else we kept (intern, no term stated)
"""
import csv
import json
import os
import re
from datetime import date, datetime, timedelta

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from scraper.fit import fit_of, FIT_ORDER  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TODAY = date.today()
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_posted(s, fallback):
    """Best-effort date from the many 'posted' formats the sources use. Returns a date or None."""
    s = (s or "").strip()
    if not s:
        return fallback
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        y = int(m[3]); y += 2000 if y < 100 else 0
        return date(y, int(m[1]), int(m[2]))
    low = s.lower()
    if "today" in low or "just posted" in low:
        return TODAY
    if "yesterday" in low:
        return TODAY - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days? ago", low)
    if m:
        return TODAY - timedelta(days=int(m[1]))
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})", low)
    if m and m[1] in MONTHS:
        d = date(TODAY.year, MONTHS[m[1]], int(m[2]))
        if d > TODAY + timedelta(days=7):  # a month name ahead of today means last year
            d = date(TODAY.year - 1, MONTHS[m[1]], int(m[2]))
        return d
    return fallback


def parse_deadline(s):
    s = (s or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def urgency(job, posted, deadline):
    days_open = (TODAY - posted).days if posted else 999
    days_left = (deadline - TODAY).days if deadline else None
    fresh = days_open <= 7
    if job["rank"] == "A":
        if fresh or (days_left is not None and days_left <= 14):
            return "APPLY NOW"
        return "APPLY"
    if job["rank"] == "B":
        return "SOON" if fresh else "WATCH"
    return "WATCH"


def build():
    jobs = json.load(open(os.path.join(DATA, "jobs.json")))
    nu = json.load(open(os.path.join(DATA, "nuworks.json"))) if os.path.exists(os.path.join(DATA, "nuworks.json")) else []
    events = json.load(open(os.path.join(DATA, "nuevents.json"))) if os.path.exists(os.path.join(DATA, "nuevents.json")) else []
    last_cycle = json.load(open(os.path.join(DATA, "last_cycle_2026.json"))) if os.path.exists(os.path.join(DATA, "last_cycle_2026.json")) else []

    # NUWorks rows join the main list; match by company+title so a posting seen on both isn't doubled
    def norm(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    have = {(norm(j["company"])[:12], norm(j["title"])): j for j in jobs}
    for n in nu:
        k = (norm(n["company"])[:12], norm(n["title"]))
        if k in have:
            j = have[k]
            j["nuworks"] = True; j["nu_url"] = n["url"]; j["deadline"] = n.get("deadline") or j.get("deadline", ""); j["nu_qualified"] = n.get("qualified")
        else:
            jobs.append(dict(company=n["company"], title=n["title"], location=n.get("location", ""), url=n["url"], posted="",
                             term=n["term"], rank=n["rank"], hw=n.get("hw", ""), tier="", sources=["nuworks"],
                             first_seen=n.get("first_seen", TODAY.isoformat()), last_seen=n.get("last_seen", TODAY.isoformat()),
                             nuworks=True, nu_url=n["url"], deadline=n.get("deadline", ""), nu_qualified=n.get("qualified")))

    rows = []
    for j in jobs:
        fs = date.fromisoformat(j["first_seen"]) if j.get("first_seen") else TODAY
        posted = parse_posted(j.get("posted"), fs)
        deadline = parse_deadline(j.get("deadline"))
        u = urgency(j, posted, deadline)
        fit, why = fit_of(j if "title" in j else dict(company=j["company"], title=j["title"]))
        if j.get("hw") == "maybe" and fit != "CHIP":
            fit, why = "MAYBE", "spring/co-op at a tracked company, no hardware word in title — check it"
        days_open = (TODAY - posted).days if posted else ""
        rows.append(dict(
            urgency=u, fit=fit, fit_why=why, new="NEW" if (TODAY - fs).days <= 3 else "", rank=j["rank"], term=j["term"],
            company=j["company"], role=j["title"], location=(j.get("location") or "")[:60],
            posted=posted.isoformat() if posted else "", days_open=days_open,
            deadline=deadline.isoformat() if deadline else "", first_seen=j["first_seen"],
            nuworks="yes" if j.get("nuworks") else "", nu_eligible=("" if not j.get("nuworks") else ("yes" if j.get("nu_qualified") else "NOT QUALIFIED")),
            link=j["url"], nuworks_link=j.get("nu_url", ""), source="+".join(j.get("sources", [])), tier=j.get("tier", ""),
        ))
    order = {"APPLY NOW": 0, "APPLY": 1, "SOON": 2, "WATCH": 3}
    rows.sort(key=lambda r: (FIT_ORDER[r["fit"]], order[r["urgency"]],
                             r["posted"] and -int(r["posted"].replace("-", "")) or 0, r["company"]))
    # first run: every row looks NEW, which is noise. Only flag NEW once there is real history to compare against.
    if sum(1 for r in rows if r["new"]) > 0.4 * len(rows):
        for r in rows:
            r["new"] = ""

    cols = ["urgency", "fit", "new", "rank", "term", "company", "role", "location", "posted", "days_open", "deadline", "first_seen",
            "nuworks", "nu_eligible", "link", "nuworks_link", "source", "tier", "fit_why"]
    with open(os.path.join(DATA, "sheet.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

    # watchlist: configured target companies with no spring/co-op posting on the board yet
    companies = json.load(open(os.path.join(ROOT, "config", "companies.json")))
    posting = {norm(r["company"])[:10] for r in rows if r["rank"] in ("A", "B")}
    lc = {}
    for x in last_cycle:
        lc.setdefault(norm(x["company"])[:10], []).append(x["first_seen"])
    health = json.load(open(os.path.join(DATA, "health.json"))) if os.path.exists(os.path.join(DATA, "health.json")) else {}
    wl = []
    for c in companies:
        if c.get("tier") == "skip":
            continue
        k = norm(c["name"])[:10]
        if k in posting:
            continue
        seen_last = sorted(lc.get(k, []))
        h = health.get(c["name"], {})
        status = "no spring/co-op posting yet"
        if h.get("zero_runs", 0) >= 3 and h.get("best", 0) > 0:
            status = f"FETCHER BROKEN? 0 postings for {h['zero_runs']} runs (used to return {h['best']})"
        elif h.get("fail_runs", 0) >= 3:
            status = f"FETCHER FAILING for {h['fail_runs']} runs: {h.get('err', '')[:50]}"
        wl.append(dict(company=c["name"], tier=c.get("tier", ""), ats=c["ats"],
                       status=status,
                       last_cycle_first_seen=(seen_last[0] if seen_last else ""),
                       expect=("around " + seen_last[0][5:] + " (last year)" if seen_last else "unknown; scraper records first-seen dates from now on")))
    wl.sort(key=lambda r: (r["tier"] != "big", r["company"]))
    with open(os.path.join(DATA, "watchlist.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "tier", "ats", "status", "last_cycle_first_seen", "expect"]); w.writeheader(); w.writerows(wl)

    # openings: a company whose EARLIEST kept spring/co-op posting is recent = "they just opened their reqs"
    by_co = {}
    for r in rows:
        if r["rank"] in ("A", "B") and r["fit"] in ("CHIP", "HARDWARE"):
            k = norm(r["company"])[:12]
            g = by_co.setdefault(k, dict(company=r["company"], opened=r["first_seen"], n=0, fit=r["fit"], sample=r["role"], link=r["link"]))
            g["n"] += 1
            if r["first_seen"] < g["opened"]:
                g["opened"] = r["first_seen"]
            if r["fit"] == "CHIP" and g["fit"] != "CHIP":
                g["fit"], g["sample"], g["link"] = "CHIP", r["role"], r["link"]
    openings = [g for g in by_co.values() if (TODAY - date.fromisoformat(g["opened"])).days <= 7]
    if len(openings) > 0.4 * max(1, len(by_co)):   # first run: everything looks freshly opened, which is noise
        openings = []
    openings.sort(key=lambda g: (FIT_ORDER[g["fit"]], g["opened"]), reverse=False)
    with open(os.path.join(DATA, "openings.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "opened", "n", "fit", "sample", "link"]); w.writeheader(); w.writerows(openings)

    with open(os.path.join(DATA, "events.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "time", "title", "type", "source"]); w.writeheader()
        for e in sorted(events, key=lambda e: e["date"]):
            w.writerow({k: e.get(k, "") for k in ["date", "time", "title", "type", "source"]})

    from collections import Counter
    print("openings:", len(openings), "|", end=" ")
    print("sheet.csv:", len(rows), dict(Counter(r["fit"] for r in rows)), dict(Counter(r["urgency"] for r in rows)), "| watchlist:", len(wl), "| events:", len(events))


if __name__ == "__main__":
    build()
