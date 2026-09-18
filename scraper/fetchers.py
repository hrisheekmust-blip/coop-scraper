"""Fetch postings from public ATS endpoints. stdlib only so GitHub Actions needs no install.

Each fetcher returns a list of normalized jobs:
  {company, title, location, url, posted, description, source}
"""
import json
import re
import urllib.request
import urllib.parse
from html import unescape

UA = {"User-Agent": "Mozilla/5.0 (coop-scraper; personal job search)", "Accept": "application/json"}
SEARCH_TERMS = ["intern", "co-op", "coop", "student"]   # used for ATSs that need a keyword search


def _get(url, data=None, headers=None, timeout=40):
    h = dict(UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _strip_html(s):
    return re.sub(r"<[^>]+>", " ", unescape(s or ""))


# ---------------------------------------------------------------- Greenhouse
def greenhouse(c):
    j = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs?content=true"))
    out = []
    for job in j.get("jobs", []):
        out.append(dict(company=c["name"], title=job["title"], location=(job.get("location") or {}).get("name", ""),
                        url=job["absolute_url"], posted=(job.get("updated_at") or "")[:10],
                        description=_strip_html(job.get("content", ""))[:6000], source="greenhouse"))
    return out


# ---------------------------------------------------------------- Lever
def lever(c):
    j = json.loads(_get(f"https://api.lever.co/v0/postings/{c['token']}?mode=json"))
    out = []
    for job in j:
        cats = job.get("categories") or {}
        out.append(dict(company=c["name"], title=job["text"], location=cats.get("location", ""),
                        url=job["hostedUrl"], posted=str(job.get("createdAt", ""))[:10],
                        description=_strip_html(job.get("descriptionPlain") or job.get("description", ""))[:6000],
                        source="lever"))
    return out


# ---------------------------------------------------------------- Ashby
def ashby(c):
    j = json.loads(_get(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}?includeCompensation=false"))
    out = []
    for job in j.get("jobs", []):
        out.append(dict(company=c["name"], title=job["title"], location=job.get("location", ""),
                        url=job.get("jobUrl") or job.get("applyUrl", ""), posted=(job.get("publishedAt") or "")[:10],
                        description=(job.get("descriptionPlain") or "")[:6000], source="ashby"))
    return out


# ---------------------------------------------------------------- Workday
def workday(c):
    """Workday's career-site JSON API (same one the site's own JS calls). Search per keyword, dedupe."""
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    seen, out = set(), []
    for term in SEARCH_TERMS:
        offset = 0
        while True:
            j = json.loads(_get(api, data={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}))
            posts = j.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                out.append(dict(company=c["name"], title=p.get("title", ""), location=p.get("locationsText", ""),
                                url=f"{base}/{c['site']}{path}", posted=p.get("postedOn", ""),
                                description="", source="workday"))
            offset += 20
            if offset >= j.get("total", 0) or not posts or offset > 400:
                break
    return out


def workday_detail(c, job):
    """Optional: pull the description for one Workday posting (used to detect spring/co-op in body text)."""
    path = job["url"].split(f"/{c['site']}", 1)[1]
    j = json.loads(_get(f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com/wday/cxs/{c['tenant']}/{c['site']}{path}"))
    return _strip_html(j.get("jobPostingInfo", {}).get("jobDescription", ""))[:6000]


# ---------------------------------------------------------------- Oracle HCM (TI, onsemi)
def oracle_hcm(c):
    out, seen = [], set()
    for term in SEARCH_TERMS:
        offset = 0
        while True:
            finder = f"findReqs;siteNumber={c['site']},keyword={urllib.parse.quote(term)},limit=100,offset={offset},sortBy=POSTING_DATES_DESC"
            url = (f"https://{c['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations&finder={finder}")
            j = json.loads(_get(url))
            items = (j.get("items") or [{}])[0]
            reqs = items.get("requisitionList", [])
            for r in reqs:
                rid = r.get("Id")
                if rid in seen:
                    continue
                seen.add(rid)
                out.append(dict(company=c["name"], title=r.get("Title", ""), location=r.get("PrimaryLocation", ""),
                                url=f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{rid}",
                                posted=(r.get("PostedDate") or "")[:10], description=_strip_html(r.get("ShortDescriptionStr", ""))[:2000],
                                source="oracle_hcm"))
            offset += 100
            if len(reqs) < 100 or offset > 500:
                break
    return out


# ---------------------------------------------------------------- SuccessFactors (Skyworks, Teradyne, Infineon)
SF_ROW = re.compile(r'<a[^>]+class="jobTitle-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                    r'<span class="jobLocation">\s*(.*?)\s*</span>', re.S)


def successfactors(c):
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for start in (0, 25, 50):
            html = _get(f"{c['base']}/search/?q={urllib.parse.quote(term)}&sortColumn=referencedate&sortDirection=desc&startrow={start}",
                        headers={"Accept": "text/html"})
            rows = SF_ROW.findall(html)
            for href, title, loc in rows:
                url = href if href.startswith("http") else c["base"] + href
                if url in seen:
                    continue
                seen.add(url)
                out.append(dict(company=c["name"], title=_strip_html(title).strip(), location=_strip_html(loc).strip(),
                                url=url, posted="", description="", source="successfactors"))
            if len(rows) < 25:
                break
    return out


# ---------------------------------------------------------------- Jibe (AMD careers-home)
def jibe(c):
    out, seen = [], set()
    for term in SEARCH_TERMS:
        for page in range(1, 6):
            j = json.loads(_get(f"{c['base']}/api/jobs?keywords={urllib.parse.quote(term)}&page={page}&sortBy=posted_date&descending=true"))
            jobs = j.get("jobs", [])
            for w in jobs:
                d = w.get("data", w)
                slug = d.get("slug") or d.get("req_id")
                if slug in seen:
                    continue
                seen.add(slug)
                out.append(dict(company=c["name"], title=d.get("title", ""), location=d.get("full_location") or d.get("location", ""),
                                url=d.get("apply_url") or f"{c['base']}/careers-home/jobs/{d.get('req_id')}",
                                posted=(d.get("posted_date") or "")[:10], description=_strip_html(d.get("description", ""))[:6000],
                                source="jibe"))
            if len(jobs) < 10:
                break
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday,
            "oracle_hcm": oracle_hcm, "successfactors": successfactors, "jibe": jibe}

from scraper.fetchers_more import FETCHERS_MORE  # noqa: E402
FETCHERS.update(FETCHERS_MORE)
