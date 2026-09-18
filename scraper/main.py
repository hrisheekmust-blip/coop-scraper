"""Co-op scraper pipeline.

  python -m scraper.main            # fetch everything (needs network to ATS hosts, e.g. GitHub Actions)
  python -m scraper.main --raw data/raw_browser.json   # skip ATS fetch, use jobs dumped from the browser
  python -m scraper.main --no-github                   # skip the community markdown lists

Outputs (all in data/):
  jobs.json     every kept posting with classification + first_seen/last_seen
  NEW.md        postings first seen on this run (this is what you read each morning)
  tracker.csv   flat table for the tracker (status column preserved across runs)
  seen.json     memory of every posting ever seen (by url)
  rejected.jsonl  what got filtered out and why (debug the filters here)
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper import fetchers, filters, sources_github, sources_internlist, jobspy_search  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CFG = os.path.join(ROOT, "config", "companies.json")
TODAY = date.today().isoformat()


def log(*a):
    print(*a, flush=True)


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def key(job):
    return job["url"].split("?")[0].rstrip("/")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def fetch_ats(companies):
    jobs = []
    for c in companies:
        fn = fetchers.FETCHERS.get(c["ats"])
        if not fn:
            log(f"  {c['name']}: no fetcher for {c['ats']}")
            continue
        try:
            t = time.time()
            got = fn(c)
            for j in got:
                j["tier"] = c.get("tier", "")
            jobs += got
            log(f"  {c['name']:28s} {c['ats']:14s} {len(got):5d} jobs  {time.time()-t:.1f}s")
        except Exception as e:  # noqa
            log(f"  {c['name']:28s} {c['ats']:14s} FAILED {type(e).__name__}: {str(e)[:80]}")
    return jobs


def enrich_workday(companies, kept):
    """Workday list results have no description; pull it for intern-looking postings so term detection works."""
    by_name = {c["name"]: c for c in companies if c["ats"] == "workday"}
    for j in kept:
        c = by_name.get(j["company"])
        if c and not j.get("description"):
            try:
                j["description"] = fetchers.workday_detail(c, j)
            except Exception:
                pass


def run(args):
    os.makedirs(DATA, exist_ok=True)
    companies = [c for c in load_json(CFG, []) if c.get("tier") != "skip"]
    seen = load_json(os.path.join(DATA, "seen.json"), {})
    prev_tracker = {}
    tpath = os.path.join(DATA, "tracker.csv")
    if os.path.exists(tpath):
        with open(tpath, newline="") as f:
            for r in csv.DictReader(f):
                prev_tracker[r["url"]] = r

    raw = []
    if args.raw:
        raw += load_json(args.raw, [])
        log(f"loaded {len(raw)} raw jobs from {args.raw}")
    else:
        log("fetching ATS boards…")
        raw += fetch_ats(companies)
    if not args.no_github:
        log("fetching GitHub lists…")
        raw += sources_github.fetch_all(log)
    if not args.raw and not args.no_internlist:
        log("fetching internlist.org (Simplify) lists…")
        try:
            raw += sources_internlist.fetch_all(log)
        except Exception as e:  # noqa
            log("  internlist failed:", type(e).__name__, str(e)[:80])
    if not args.raw and not args.no_jobspy:
        log("LinkedIn/Indeed sweep…")
        raw += jobspy_search.fetch_all(log)

    # first pass: cheap filters. Workday needs descriptions for term detection, so enrich the survivors then re-classify.
    survivors = []
    for j in raw:
        if filters.is_intern(j) and filters.hardware_score(j) and not filters.EXCLUDE_TITLE.search(j["title"]):
            survivors.append(j)
    if not args.raw and not args.no_detail:
        enrich_workday(companies, [j for j in survivors if j.get("source") == "workday"])

    kept, rejected, dedup = [], [], {}
    for j in raw:
        ok, why, meta = filters.classify(j)
        if not ok:
            rejected.append(dict(company=j["company"], title=j["title"], location=j.get("location", ""), why=why, url=j["url"]))
            continue
        k = key(j)
        k2 = (_norm(j["company"])[:12], _norm(j["title"]), _norm(j.get("location"))[:12])
        if k2 in dedup:
            k = k2
        if k in dedup:  # same url from two sources: keep the richer one, note both sources
            dedup[k]["sources"] = sorted(set(dedup[k]["sources"] + [j["source"]]))
            continue
        rec = dict(company=j["company"], title=j["title"], location=j.get("location", ""), url=j["url"], posted=j.get("posted", ""),
                   deadline=j.get("deadline", ""),
                   term=meta["term"], rank=meta["rank"], hw=",".join(meta["hw"]), tier=j.get("tier", ""),
                   sources=[j["source"]], first_seen=seen.get(k, {}).get("first_seen", TODAY), last_seen=TODAY)
        dedup[k] = rec
        dedup[k2] = rec
        kept.append(rec)

    new = [r for r in kept if r["first_seen"] == TODAY and key(r) not in seen]
    for r in kept:
        seen[key(r)] = {"first_seen": r["first_seen"], "title": r["title"], "company": r["company"]}

    order = {"A": 0, "B": 1, "C": 2}
    kept.sort(key=lambda r: (order[r["rank"]], r["company"], r["title"]))

    # ---- write outputs
    json.dump(kept, open(os.path.join(DATA, "jobs.json"), "w"), indent=1)
    json.dump(seen, open(os.path.join(DATA, "seen.json"), "w"), indent=0)
    with open(os.path.join(DATA, "rejected.jsonl"), "w") as f:
        for r in rejected:
            f.write(json.dumps(r) + "\n")

    cols = ["status", "rank", "term", "company", "title", "location", "tier", "hw", "posted", "first_seen", "last_seen", "nu_connection", "notes", "url", "sources"]
    with open(tpath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in kept:
            old = prev_tracker.get(r["url"], {})
            w.writerow({**{c: r.get(c, "") for c in cols}, "sources": "+".join(r["sources"]),
                        "status": old.get("status", "new"), "nu_connection": old.get("nu_connection", ""), "notes": old.get("notes", "")})

    def md_table(rows):
        lines = ["| Rank | Term | Company | Role | Location | Posted | Link |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['rank']} | {r['term']} | {r['company']} | {r['title']} | {r['location'][:60]} | {r['posted']} | [apply]({r['url']}) |")
        return "\n".join(lines)

    with open(os.path.join(DATA, "NEW.md"), "w") as f:
        f.write(f"# New postings — {TODAY}\n\n{len(new)} new of {len(kept)} tracked. Rank A = spring/Jan 2027 explicit, B = co-op with term unstated, C = intern with no term stated.\n\n")
        f.write(md_table(new) if new else "_nothing new today_")
        f.write("\n\n## All tracked\n\n" + md_table(kept) + "\n")

    try:
        from scraper import priority
        priority.build()
    except Exception as e:  # noqa
        log("priority/sheet build failed:", e)
    log(f"\nraw={len(raw)} kept={len(kept)} new={len(new)} rejected={len(rejected)}")
    from collections import Counter
    log("rank:", dict(Counter(r["rank"] for r in kept)), " reject reasons:", dict(Counter(r["why"] for r in rejected).most_common(8)))
    return kept, new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", help="json file of raw jobs dumped from the browser fetcher")
    ap.add_argument("--no-github", action="store_true")
    ap.add_argument("--no-detail", action="store_true", help="skip Workday description fetch")
    ap.add_argument("--no-jobspy", action="store_true", help="skip the LinkedIn/Indeed sweep")
    ap.add_argument("--no-internlist", action="store_true", help="skip the internlist.org / Simplify lists")
    run(ap.parse_args())
