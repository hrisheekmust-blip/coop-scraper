"""More ATS fetchers, ported from the provider notes in career-ops (github.com/career-ops-hq/career-ops, MIT)
and a few bespoke career-site APIs (Amazon, Google, Tesla, Apple). Same contract as fetchers.py:
each returns [{company, title, location, url, posted, description, source}].
"""
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone

from scraper.fetchers import _get, _strip_html, SEARCH_TERMS

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
              "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}


def _epoch(s):
    try:
        return datetime.fromtimestamp(int(s), tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


# ---------------------------------------------------------------- Eightfold (Micron, Qualcomm?, PepsiCo…)
def eightfold(c):
    """GET https://<tenant>.eightfold.ai/api/apply/v2/jobs?domain=&start=&num=  — server caps num at 10."""
    host = c["host"]
    out, seen = [], set()
    for term in SEARCH_TERMS:
        start = 0
        while start < 300:
            q = {"start": start, "num": 10, "query": term}
            if c.get("domain"):
                q["domain"] = c["domain"]
            j = json.loads(_get(f"https://{host}/api/apply/v2/jobs?{urllib.parse.urlencode(q)}", headers=BROWSER_UA))
            pos = j.get("positions") or []
            for p in pos:
                pid = str(p.get("id", ""))
                if pid in seen:
                    continue
                seen.add(pid)
                url = p.get("canonicalPositionUrl") or f"https://{host}/careers?pid={pid}"
                loc = p.get("location") or "; ".join(p.get("locations") or [])
                out.append(dict(company=c["name"], title=(p.get("name") or p.get("posting_name") or "").strip(), location=loc,
                                url=url, posted=_epoch(p.get("t_create") or p.get("t_update")), description="", source="eightfold"))
            start += 10
            if not pos or start >= int(j.get("count", 0)):
                break
    return out


# ---------------------------------------------------------------- Phenom People ("CareerConnect" branded sites)
def phenom(c):
    """POST {origin}/widgets with ddoKey=refineSearch. `origin` is the branded careers host (https://careers.x.com)."""
    origin = c["base"].rstrip("/")
    prefix = c.get("url_prefix", "global/en")
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for page in range(0, 5):
            body = {"lang": c.get("lang", "en_global"), "deviceType": "desktop", "country": c.get("country", "global"),
                    "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
                    "from": page * 100, "jobs": True, "counts": True, "all_fields": ["category", "country", "city"],
                    "size": 100, "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page10",
                    "siteType": "external", "keywords": term, "global": c.get("country", "global") == "global",
                    "selected_fields": c.get("selected_fields", {}), "locationData": {}}
            j = json.loads(_get(f"{origin}/widgets", data=body, headers=BROWSER_UA))
            rs = j.get("refineSearch") or {}
            jobs = ((rs.get("data") or {}).get("jobs")) or []
            for p in jobs:
                jid = str(p.get("jobId", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = _strip_html(p.get("title", "")).strip()
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]
                loc = p.get("location") or ", ".join(x for x in [p.get("city"), p.get("state"), p.get("country")] if x)
                out.append(dict(company=c["name"], title=title, location=loc, url=f"{origin}/{prefix}/job/{jid}/{slug}",
                                posted=(p.get("postedDate") or "")[:10], description=_strip_html(p.get("descriptionTeaser", ""))[:2000],
                                source="phenom"))
            if (page + 1) * 100 >= int(rs.get("totalHits") or 0):
                break
    return out


# ---------------------------------------------------------------- SuccessFactors, Career Site Builder variant (AMD, Infineon, TSMC…)
def successfactors_csb(c):
    """POST {origin}/services/recruiting/v1/jobs {keywords, locale, pageNumber} — 10/page, 0-based."""
    origin = c["base"].rstrip("/")
    locales = c.get("locales") or ["en_US"]
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for locale in locales:
            for page in range(0, 20):
                body = {"keywords": term, "locale": locale, "location": "", "pageNumber": page, "sortBy": "recent"}
                j = json.loads(_get(f"{origin}/services/recruiting/v1/jobs", data=body, headers=BROWSER_UA))
                res = j.get("jobSearchResult") or []
                for r in res:
                    p = r.get("response") or r
                    jid = str(p.get("id", ""))
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    title = p.get("unifiedStandardTitle") or p.get("title") or ""
                    slug = p.get("unifiedUrlTitle") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                    loc = "; ".join(p.get("jobLocationShort") or []) if isinstance(p.get("jobLocationShort"), list) else (p.get("jobLocationShort") or "")
                    posted = p.get("unifiedStandardStart") or ""
                    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", posted)
                    if m:
                        y = int(m[3]); y += 2000 if y < 100 else 0
                        posted = f"{y:04d}-{int(m[1]):02d}-{int(m[2]):02d}"
                    out.append(dict(company=c["name"], title=title, location=loc, url=f"{origin}/job/{slug}/{jid}-{locale}",
                                    posted=posted, description="", source="successfactors"))
                if len(res) < 10 or (page + 1) * 10 >= int(j.get("totalJobs") or 0):
                    break
    return out


# ---------------------------------------------------------------- SmartRecruiters
def smartrecruiters(c):
    out = []
    for offset in range(0, 1000, 100):
        j = json.loads(_get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings?limit=100&offset={offset}&status=PUBLIC"))
        items = j.get("content") or []
        for p in items:
            loc = p.get("location") or {}
            l = loc.get("fullLocation") or ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            out.append(dict(company=c["name"], title=p.get("name", ""), location=l,
                            url=f"https://jobs.smartrecruiters.com/{c['slug']}/{p.get('id')}", posted=(p.get("releasedDate") or "")[:10],
                            description="", source="smartrecruiters"))
        if len(items) < 100:
            break
    return out


# ---------------------------------------------------------------- iCIMS (server-rendered cards)
ICIMS_CARD = re.compile(r'href="([^"]*/jobs/\d+/[^"]*?)"[^>]*>.*?<h3[^>]*>(.*?)</h3>.*?(?:<dd[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</dd>|<span[^>]*>(?:Location|Job Locations)[^<]*</span>\s*<span[^>]*>(.*?)</span>)?', re.S | re.I)


def icims(c):
    origin = c["base"].rstrip("/")
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for page in range(1, 6):
            html = _get(f"{origin}/jobs/search?ss=1&pr={page}&in_iframe=1&searchKeyword={urllib.parse.quote(term)}", headers={"Accept": "text/html"})
            cards = html.split("iCIMS_JobCardItem")[1:]
            if not cards:
                break
            for card in cards:
                m = ICIMS_CARD.search(card)
                if not m:
                    continue
                url = m[1].split("?")[0]
                url = url if url.startswith("http") else origin + url
                if url in seen:
                    continue
                seen.add(url)
                out.append(dict(company=c["name"], title=_strip_html(m[2]).strip(), location=_strip_html(m[3] or m[4] or "").strip(),
                                url=url, posted="", description="", source="icims"))
    return out


# ---------------------------------------------------------------- Amazon (amazon.jobs public search JSON)
def amazon(c):
    out, seen = [], set()
    for term in ["hardware intern", "asic intern", "silicon intern", "electrical engineer intern", "co-op"]:
        for offset in (0, 100):
            q = urllib.parse.urlencode({"base_query": term, "country": "USA", "result_limit": 100, "offset": offset, "sort": "recent"})
            j = json.loads(_get(f"https://www.amazon.jobs/en/search.json?{q}", headers=BROWSER_UA))
            jobs = j.get("jobs") or []
            for p in jobs:
                path = p.get("job_path") or ""
                if not path or path in seen:
                    continue
                seen.add(path)
                posted = p.get("posted_date") or ""
                try:
                    posted = datetime.strptime(re.sub(r"\s+", " ", posted), "%B %d, %Y").date().isoformat()
                except Exception:
                    pass
                out.append(dict(company=c["name"], title=p.get("title", ""), location=p.get("location") or p.get("normalized_location", ""),
                                url="https://www.amazon.jobs" + path, posted=posted, description=_strip_html(p.get("description_short", ""))[:1500],
                                source="amazon"))
            if len(jobs) < 100:
                break
    return out


# ---------------------------------------------------------------- Google (careers.google.com public search API)
def google(c):
    out, seen = [], set()
    for term in ["hardware intern", "silicon intern", "electrical engineer intern", "asic intern"]:
        for page in (1, 2, 3):
            q = urllib.parse.urlencode({"q": term, "location": "United States", "employment_type": "INTERN", "page": page})
            j = json.loads(_get(f"https://careers.google.com/api/v3/search/?{q}", headers=BROWSER_UA))
            jobs = j.get("jobs") or []
            for p in jobs:
                jid = str(p.get("id", ""))
                if jid in seen:
                    continue
                seen.add(jid)
                locs = "; ".join((l.get("display") or "") for l in (p.get("locations") or [])[:3])
                out.append(dict(company=c["name"], title=p.get("title", ""), location=locs,
                                url=p.get("apply_url") or f"https://www.google.com/about/careers/applications/jobs/results/{jid.split('/')[-1]}",
                                posted=(p.get("publish_date") or "")[:10],
                                description=_strip_html((p.get("qualifications") or "") + " " + (p.get("description") or ""))[:3000], source="google"))
            if len(jobs) < 20:
                break
    return out


# ---------------------------------------------------------------- Tesla (single JSON blob for the whole careers site)
def tesla(c):
    j = json.loads(_get("https://www.tesla.com/cua-api/apps/careers/state", headers=BROWSER_UA))
    locs = j.get("geo") or {}
    lookup = {}
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict) and "name" in v:
                    lookup[k] = v["name"]
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(locs)
    out = []
    for p in j.get("listings") or []:
        title = p.get("t") or ""
        if not re.search(r"intern|co-?op", title, re.I):
            continue
        loc = p.get("l") or ""
        out.append(dict(company=c["name"], title=title, location=lookup.get(loc, loc), url=f"https://www.tesla.com/careers/search/job/{p.get('id')}",
                        posted="", description="", source="tesla"))
    return out


# ---------------------------------------------------------------- Apple (jobs.apple.com search API, needs a CSRF token)
def apple(c):
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    req = urllib.request.Request("https://jobs.apple.com/api/csrfToken", headers=BROWSER_UA)
    with opener.open(req, timeout=40) as r:
        token = r.headers.get("X-Apple-CSRF-Token", "")
    out, seen = [], set()
    for term in ["hardware intern", "silicon intern", "analog intern", "electrical engineer intern"]:
        for page in (1, 2):
            body = json.dumps({"query": term, "filters": {"postingpostLocation": ["postLocation-USA"]}, "page": page, "locale": "en-us", "sort": "newest"}).encode()
            req = urllib.request.Request("https://jobs.apple.com/api/v1/search", data=body,
                                         headers={**BROWSER_UA, "Content-Type": "application/json", "X-Apple-CSRF-Token": token})
            with opener.open(req, timeout=40) as r:
                j = json.loads(r.read().decode())
            res = (j.get("res") or j).get("searchResults") or []
            for p in res:
                pid = p.get("positionId") or p.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                locs = "; ".join((l.get("name") or "") for l in (p.get("locations") or [])[:3])
                slug = p.get("transformedPostingTitle") or re.sub(r"[^a-z0-9]+", "-", (p.get("postingTitle") or "").lower())
                out.append(dict(company=c["name"], title=p.get("postingTitle", ""), location=locs,
                                url=f"https://jobs.apple.com/en-us/details/{pid}/{slug}", posted=(p.get("postDateInGMT") or "")[:10],
                                description=_strip_html(p.get("jobSummary", ""))[:2000], source="apple"))
            if len(res) < 20:
                break
    return out


FETCHERS_MORE = {"eightfold": eightfold, "phenom": phenom, "successfactors_csb": successfactors_csb, "smartrecruiters": smartrecruiters,
                 "icims": icims, "amazon": amazon, "google": google, "tesla": tesla, "apple": apple}
