"""Outreach leads: small chip / photonics / RF / power / analog startups that just raised money.

Postings at these companies are rare and their best hires come from outreach (a founder's lab, a shared university,
a warm intro). Google News RSS is free and reachable from GitHub Actions. Output: data/leads.csv (date, company guess,
headline, link, source, query) — newest first, 90-day window, deduped by headline.
"""
import csv
import html
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
QUERIES = [
    '"semiconductor startup" raises', '"chip startup" raises', '"chip design" startup "Series A"', '"analog" chip startup funding',
    '"photonics startup" raises', '"silicon photonics" startup funding', '"RF" startup raises "Series A"',
    '"power semiconductor" startup raises', '"GaN" startup funding', '"ASIC" startup raises', '"AI chip" startup "Series A" OR "seed"',
    '"mixed-signal" startup', '"EDA" startup raises', '"chiplet" startup raises', '"MEMS" startup raises',
    'semiconductor startup "UCSB" OR "MIT" OR "Stanford" OR "Berkeley" professor founded raises',
]
NOISE = re.compile(r"\b(stock|shares|earnings|ETF|analyst|price target|dividend|lawsuit|acquires?|acquisition|layoffs?)\b", re.I)
CO = re.compile(r"^([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,3})\s+(?:raises|secures|lands|closes|nabs|bags|gets|announces)", re.I)


def fetch(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 coop-scraper"})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title") or "")
        link = it.findtext("link") or ""
        src = (it.find("source").text if it.find("source") is not None else "") or ""
        try:
            d = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc)
        except Exception:
            continue
        out.append(dict(date=d.date().isoformat(), title=title, link=link, source=src, query=q))
    return out


def main():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    seen, rows = set(), []
    for q in QUERIES:
        try:
            items = fetch(q)
        except Exception as e:  # noqa
            print("leads:", q[:40], "failed", type(e).__name__); continue
        for it in items:
            k = re.sub(r"\W+", " ", it["title"].lower())[:70]
            if it["date"] < cutoff or k in seen or NOISE.search(it["title"]):
                continue
            seen.add(k)
            t = re.sub(r"\s+-\s+[^-]+$", "", it["title"])  # strip " - Publisher"
            m = CO.match(t)
            rows.append(dict(date=it["date"], company=(m[1] if m else ""), headline=t, link=it["link"], source=it["source"], query=it["query"]))
    rows.sort(key=lambda r: r["date"], reverse=True)
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "leads.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "company", "headline", "link", "source", "query"]); w.writeheader(); w.writerows(rows)
    print(f"leads: {len(rows)} funding items in the last 90 days")


if __name__ == "__main__":
    main()
