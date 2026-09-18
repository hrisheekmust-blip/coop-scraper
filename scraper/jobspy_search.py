"""Optional LinkedIn / Indeed / Glassdoor sweep via python-jobspy (speedyapply/JobSpy).

Best-effort: these sites rate-limit and change markup, so failures are logged and skipped, never fatal.
Installed only in the GitHub Actions run (see .github/workflows/daily.yml); locally `pip install python-jobspy`.
"""
QUERIES = [
    "electrical engineering co-op spring 2027", "hardware engineer co-op 2027", "analog design intern 2027",
    "ASIC intern spring 2027", "test engineer co-op 2027", "silicon engineering intern spring 2027",
    "RF engineer co-op", "semiconductor co-op", "design verification intern 2027", "physical design intern 2027",
]


def fetch_all(log=print, hours_old=72):
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log("  jobspy not installed, skipping LinkedIn/Indeed sweep")
        return []
    out, seen = [], set()
    for q in QUERIES:
        try:
            df = scrape_jobs(site_name=["linkedin", "indeed"], search_term=q, location="United States",
                             results_wanted=40, hours_old=hours_old, country_indeed="USA", linkedin_fetch_description=False)
        except Exception as e:  # noqa
            log(f"  jobspy '{q}': FAILED {type(e).__name__}: {str(e)[:60]}")
            continue
        n = 0
        for _, r in df.iterrows():
            url = str(r.get("job_url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            n += 1
            out.append(dict(company=str(r.get("company") or ""), title=str(r.get("title") or ""), location=str(r.get("location") or ""),
                            url=url, posted=str(r.get("date_posted") or "")[:10], description=str(r.get("description") or "")[:3000],
                            source=f"jobspy:{r.get('site')}"))
        log(f"  jobspy '{q}': {n} new")
    return out
