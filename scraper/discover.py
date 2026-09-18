"""Figure out which ATS a company uses and the exact tenant/site ids, by probing public endpoints.

  python -m scraper.discover            # reads config/candidates.json, writes data/discover.json + prints config snippets

Each candidate: {"name": "Lam Research", "slugs": ["lamresearch", "lam"], "careers": "https://careers.lamresearch.com"}
Probes, in order: Workday (wd1..wd5 root redirect gives the site name), Greenhouse, Lever, Ashby, Eightfold,
SmartRecruiters, iCIMS, Phenom (/widgets on the careers host), SuccessFactors CSB (/services/recruiting/v1/jobs).
Nothing is guessed silently: only a probe that returns real postings counts.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scraper import fetchers  # noqa: E402
from scraper.fetchers_more import BROWSER_UA  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def head_location(url):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(urllib.request.Request(url, headers=BROWSER_UA), timeout=20)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.headers.get("Location", "")
        return None
    except Exception:
        return None
    return ""


def try_fetch(ats, cfg):
    try:
        got = fetchers.FETCHERS[ats](cfg)
        return len(got), ""
    except Exception as e:  # noqa
        return -1, f"{type(e).__name__}: {str(e)[:60]}"


def probe(cand):
    name, slugs = cand["name"], cand.get("slugs") or [re.sub(r"[^a-z0-9]", "", name.lower())]
    careers = (cand.get("careers") or "").rstrip("/")
    found = []
    # Workday: root of https://<tenant>.wdN.myworkdayjobs.com redirects to the default site
    for slug in slugs:
        for host in ("wd1", "wd2", "wd3", "wd5", "wd12", "wd103"):
            loc = head_location(f"https://{slug}.{host}.myworkdayjobs.com/")
            if loc:
                site = urllib.parse.urlparse(loc).path.strip("/").split("/")[0]
                if site and site != "wday":
                    n, err = try_fetch("workday", dict(name=name, tenant=slug, host=host, site=site))
                    found.append(dict(ats="workday", tenant=slug, host=host, site=site, n=n, err=err))
                    if n >= 0:
                        return found
    for slug in slugs:
        for ats, cfg in (("greenhouse", dict(name=name, token=slug)), ("lever", dict(name=name, token=slug)), ("ashby", dict(name=name, token=slug)),
                         ("smartrecruiters", dict(name=name, slug=slug)),
                         ("eightfold", dict(name=name, host=f"{slug}.eightfold.ai")),
                         ("icims", dict(name=name, base=f"https://careers-{slug}.icims.com"))):
            n, err = try_fetch(ats, cfg)
            if n > 0:
                found.append(dict(ats=ats, **{k: v for k, v in cfg.items() if k != "name"}, n=n))
                return found
    if careers:
        for ats, cfg in (("phenom", dict(name=name, base=careers)), ("successfactors_csb", dict(name=name, base=careers)),
                         ("eightfold", dict(name=name, host=urllib.parse.urlparse(careers).hostname))):
            n, err = try_fetch(ats, cfg)
            if n > 0:
                found.append(dict(ats=ats, **{k: v for k, v in cfg.items() if k != "name"}, n=n))
                return found
            found.append(dict(ats=ats, tried=careers, n=n, err=err))
    return found


def main():
    cands = json.load(open(os.path.join(ROOT, "config", "candidates.json")))
    results = {}
    for cand in cands:
        r = probe(cand)
        ok = [x for x in r if x.get("n", -1) > 0]
        results[cand["name"]] = dict(ok=ok[:1], tried=r)
        print(f"{cand['name']:28s} {'FOUND ' + json.dumps(ok[0]) if ok else 'not found: ' + '; '.join(x.get('err','') or x.get('ats','') for x in r)[:120]}", flush=True)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump(results, open(os.path.join(ROOT, "data", "discover.json"), "w"), indent=1)
    snippets = []
    for name, r in results.items():
        if r["ok"]:
            x = dict(r["ok"][0]); x.pop("n", None)
            cand = next((c for c in cands if c["name"] == name), {})
            snippets.append({"name": name, **x, "tier": cand.get("tier", "big")})
    json.dump(snippets, open(os.path.join(ROOT, "data", "discover_config.json"), "w"), indent=1)
    print(f"\n{len(snippets)} of {len(cands)} resolved → data/discover_config.json (paste into config/companies.json)")


if __name__ == "__main__":
    main()
