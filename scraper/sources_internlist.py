"""internlist.org (Simplify) as a source.

internlist.org is a front end over Simplify's job index (they crawl ~20k company career pages). Its list pages are
backed by an open, unauthenticated endpoint:

  https://api.simplify.jobs/v2/job-list/:id/<list-uuid>/job-posting/active?size=100&page=N

The list uuid is embedded in each internlist.org/list/<name> page. We pull the two lists that matter for a
Jan-Jun 2027 search and let filters.classify() do the rest (requirements/responsibilities are passed as the
description so term detection sees "Spring 2027" etc.).
"""
import json
import re
import time
import urllib.request

LISTS = {
    # name: (internlist page, list uuid as of Sept 2026 — refreshed from the page at runtime, this is a fallback)
    "coop": ("https://internlist.org/list/Co-op-Internships", "4c4fdaa2-b146-4974-b6f5-0ab5632b309f"),
    "winter-spring": ("https://internlist.org/list/Winter-Spring-Internships", "ce7f1a06-1c6b-4a1d-8c49-83f6fac55f0e"),
}
API = "https://api.simplify.jobs/v2/job-list/:id/{uuid}/job-posting/active?size=100&page={page}"
UA = {"User-Agent": "Mozilla/5.0 (coop-scraper; +https://github.com/hrisheekmust-blip/coop-scraper)", "Accept": "application/json"}
UUID_RE = re.compile(r"internlist\.org/list/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def list_uuid(page_url, fallback):
    try:
        m = UUID_RE.search(_get(page_url))
        return m[1] if m else fallback
    except Exception:
        return fallback


def fetch_list(name, uuid, log, max_pages=200):
    out, page, pages = [], 1, 1
    while page <= pages and page <= max_pages:
        try:
            j = json.loads(_get(API.format(uuid=uuid, page=page)))
        except Exception as e:  # noqa
            log(f"  internlist:{name} page {page} failed {type(e).__name__}: {str(e)[:60]}")
            break
        pages = j.get("pages", 1)
        for x in j.get("items", []):
            job = x.get("job") or {}
            company = ((job.get("company") or {}).get("name") or "").strip()
            title = (x.get("title") or "").strip()
            url = x.get("url") or (f"https://simplify.jobs/p/{x['id']}" if x.get("id") else "")
            if not (company and title and url):
                continue
            locs = [l.get("value", "") for l in (x.get("locations") or []) if l.get("value")]
            desc = " ".join((x.get("requirements") or []) + (x.get("responsibilities") or []) + (x.get("subtitles") or []))
            funcs = ", ".join(f.get("title", "") for f in (x.get("functions") or []))
            out.append(dict(company=company, title=title, location="; ".join(locs[:3]), url=url,
                            posted=(x.get("start_date") or "")[:10], deadline=(x.get("end_date") or "")[:10],
                            description=f"{desc} {funcs}".strip(), source=f"internlist:{name}", functions=funcs))
        page += 1
        time.sleep(0.2)
    return out


def fetch_all(log):
    jobs = []
    for name, (page_url, fallback) in LISTS.items():
        t = time.time()
        uuid = list_uuid(page_url, fallback)
        got = fetch_list(name, uuid, log)
        log(f"  internlist:{name:14s} {len(got):6d} jobs  {time.time()-t:.1f}s")
        jobs += got
    return jobs
