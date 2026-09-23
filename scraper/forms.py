"""Fetch the real application form (question list) for every target posting, where the ATS exposes it publicly.

Writes data/forms.json: { job_id: {portal, apply_url, fetched, fields:[{label,type,required,options}], error} }
job_id = sha1(link)[:16], same as the site. Only CHIP/HARDWARE/MAYBE rows. Postings with no question list are
retried after a day; saved question lists are refreshed once they are older than FORMS_REFRESH_DAYS (default 3),
so new or changed questions show up before the Apply click instead of during it. A 404/410 from a supported
portal marks the posting closed.

Supported: Greenhouse (boards-api ?questions=true), Ashby (job-board GraphQL), Lever (apply page HTML),
simplify.jobs click links (resolved to the real ATS first). Everything else is recorded with portal only;
the site shows the standard field list for those.
"""
import calendar, csv, hashlib, json, os, re, sys, time, urllib.error, urllib.request, urllib.parse
from html import unescape

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = os.path.join(DATA, "forms.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "*/*"}
TARGET = {"CHIP", "HARDWARE", "MAYBE"}
MAX_PER_RUN = int(os.environ.get("FORMS_MAX", "150"))
REFRESH = float(os.environ.get("FORMS_REFRESH_DAYS", "3")) * 86400


def jid(link):
    return hashlib.sha1(link.encode()).hexdigest()[:16]


def _open(url, data=None, headers=None, timeout=30):
    h = dict(UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def _get(url, **kw):
    with _open(url, **kw) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def linkedin_external(url):
    """LinkedIn guest job page: if the posting uses an external ATS, return that URL; else the LinkedIn URL (Easy Apply)."""
    try:
        html, _ = _get(url, timeout=25)
    except Exception:
        return url
    m = re.search(r'<code id="applyUrl"[^>]*>\s*<!--\s*"?([^"<]+?)"?\s*-->', html) or re.search(r'"applyUrl"\s*:\s*"([^"]+)"', html) or \
        re.search(r'companyApplyUrl\\?"?\s*:\s*\\?"([^"\\]+)', html)
    if m:
        ext = unescape(m.group(1)).replace("\\u002F", "/").strip()
        if ext.startswith("http") and "linkedin.com" not in ext:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(ext).query)
            if "url" in q:
                ext = q["url"][0]
            try:
                _, final = _get(ext, timeout=25)
                return final or ext
            except Exception:
                return ext
    return url


def resolve(url):
    """Follow simplify / shortener redirects to the real ATS URL."""
    if "linkedin.com/jobs" in url:
        return linkedin_external(url)
    if "simplify.jobs" in url or "/click/" in url:
        try:
            _, final = _get(url, timeout=25)
            if final and "simplify.jobs" not in final:
                return final
        except Exception as e:  # noqa
            return url
    return url


def portal_of(url):
    u = url.lower()
    for k, rx in [("greenhouse", r"greenhouse\.io|gh_jid="), ("ashby", r"ashbyhq\.com"), ("lever", r"lever\.co"),
                  ("workday", r"myworkday"), ("smartrecruiters", r"smartrecruiters"), ("icims", r"icims"),
                  ("oracle", r"oraclecloud"), ("successfactors", r"successfactors|jobs\.[a-z]+\.com/.*sf"),
                  ("linkedin", r"linkedin\.com"), ("nuworks", r"symplicity|joinhandshake"), ("amazon", r"amazon\.jobs"),
                  ("apple", r"jobs\.apple"), ("tesla", r"tesla\.com"), ("eightfold", r"eightfold"), ("phenom", r"phenom")]:
        if re.search(rx, u):
            return k
    return "other"


def board_from_config(url):
    """Greenhouse board token from config/companies.json, matched by the posting's host name."""
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(DATA), "config", "companies.json")))
    except Exception:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    key = re.sub(r"^(www|careers|jobs)\.", "", host).split(".")[0]
    for c in cfg if isinstance(cfg, list) else cfg.get("companies", []):
        if c.get("ats") == "greenhouse" and c.get("token") and (c["token"].lower() in key or key in c["token"].lower() or key in c.get("name", "").lower().replace(" ", "")):
            return c["token"]
    return None


# ---------------------------------------------------------------- Greenhouse
def greenhouse(url):
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?[^#]*token=|)([^/?#]+)/jobs/(\d+)", url) or \
        re.search(r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", url)
    board = job = None
    if m:
        board, job = m.group(1), m.group(2)
    else:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        job = (q.get("gh_jid") or [None])[0]
        board = (q.get("for") or q.get("token") or [None])[0]
    if job and not board:
        try:
            page, _ = _get(url, timeout=25)
            mm = re.search(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", page) or \
                 re.search(r"greenhouse\.io/embed/job_(?:app|board)/?\?[^\"']*?for=([A-Za-z0-9_-]+)", page) or \
                 re.search(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", page) or \
                 re.search(r"gh_src=|greenhouse\.io/([A-Za-z0-9_-]+)/jobs/" + job, page)
            board = mm.group(1) if mm and mm.lastindex else None
        except Exception:
            board = None
    if job and not board:
        board = board_from_config(url)
    if not (board and job):
        raise ValueError("no board/job in url")
    txt, _ = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}?questions=true")
    j = json.loads(txt)
    fields = []
    for q in (j.get("questions") or []) + (j.get("location_questions") or []):
        f0 = (q.get("fields") or [{}])[0]
        fields.append(dict(label=q.get("label", ""), type=f0.get("type", "input_text"), required=bool(q.get("required")),
                           options=[v.get("label") for v in (f0.get("values") or []) if v.get("label")]))
    for blk in j.get("compliance") or []:
        for q in blk.get("questions") or []:
            f0 = (q.get("fields") or [{}])[0]
            fields.append(dict(label=q.get("label", ""), type=f0.get("type", "multi_value_single_select"), required=bool(q.get("required")),
                               options=[v.get("label") for v in (f0.get("values") or []) if v.get("label")], eeo=True))
    for q in (j.get("demographic_questions") or {}).get("questions") or []:
        fields.append(dict(label=q.get("label", ""), type="select", required=bool(q.get("required")),
                           options=[a.get("label") for a in (q.get("answer_options") or []) if a.get("label")], eeo=True))
    return dict(apply_url=f"https://boards.greenhouse.io/{board}/jobs/{job}", fields=fields)


# ---------------------------------------------------------------- Ashby
ASHBY_Q = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title applicationDeadline
    applicationForm { sections { title fieldEntries { isRequired field } } }
  }
}"""


def ashby(url):
    m = re.search(r"ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url)
    if not m:
        raise ValueError("no org/id in url")
    org, pid = m.group(1), m.group(2)
    txt, _ = _get("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
                  data={"operationName": "ApiJobPosting", "variables": {"organizationHostedJobsPageName": org, "jobPostingId": pid}, "query": ASHBY_Q})
    j = json.loads(txt)
    if j.get("errors"):
        raise ValueError(str(j["errors"])[:200])
    jp = (j.get("data") or {}).get("jobPosting") or {}
    fields = []
    for sec in ((jp.get("applicationForm") or {}).get("sections") or []):
        for fe in sec.get("fieldEntries") or []:
            f = fe.get("field") or {}
            if isinstance(f, str):
                try:
                    f = json.loads(f)
                except Exception:
                    f = {"title": f}
            opts = f.get("selectableValues") or f.get("options") or []
            opts = [(v.get("label") or v.get("value")) if isinstance(v, dict) else str(v) for v in opts]
            fields.append(dict(label=f.get("title") or f.get("label") or f.get("humanReadablePath") or "", type=f.get("fieldType") or f.get("type") or "",
                               required=bool(fe.get("isRequired") or (f.get("isNullable") is False)), options=[o for o in opts if o],
                               section=sec.get("title") or ""))
    return dict(apply_url=f"https://jobs.ashbyhq.com/{org}/{pid}/application", fields=fields)


# ---------------------------------------------------------------- Lever
def lever(url):
    m = re.search(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})", url)
    if not m:
        raise ValueError("no company/id in url")
    html, _ = _get(f"https://jobs.lever.co/{m.group(1)}/{m.group(2)}/apply")
    clean = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(x))).strip()
    pos = [mm.start() for mm in re.finditer(r'class="application-label[^"]*"', html)]
    fields = []
    for i, p in enumerate(pos):
        chunk = html[p:pos[i + 1] if i + 1 < len(pos) else p + 6000]
        lab = re.match(r'[^>]*>(.*?)</(?:label|div)>', chunk, re.S)
        if not lab:
            continue
        t = clean(lab.group(1))
        req = "✱" in t
        t = t.replace("✱", "").strip()
        rest = chunk[lab.end():]
        opts = [clean(o) for o in re.findall(r"<option[^>]*>(.*?)</option>", rest, re.S)]
        opts = [o for o in opts if o and not o.lower().startswith(("select", "choose", "--"))]
        if not opts:
            opts = [clean(o) for o in re.findall(r'class="application-answer-alternative"[^>]*>(.*?)</label>', rest, re.S)]
        typ = "file" if "type=\"file\"" in rest[:600] else "textarea" if "<textarea" in rest[:600] else ("select" if opts else "input_text")
        if t and not any(f["label"] == t for f in fields):
            fields.append(dict(label=t, type=typ, required=req, options=[o for o in opts if o]))
    if not any(re.search(r"name|email", f["label"], re.I) for f in fields):
        std = [("Full name", "input_text", True), ("Email", "input_text", True), ("Phone", "input_text", True), ("Current company", "input_text", False),
               ("Current location", "input_text", False), ("Resume/CV", "file", True), ("LinkedIn URL", "input_text", False), ("GitHub URL", "input_text", False),
               ("Portfolio URL", "input_text", False), ("Other website", "input_text", False), ("Additional information", "textarea", False)]
        fields = [dict(label=a, type=b, required=c, options=[]) for a, b, c in std] + fields
    return dict(apply_url=f"https://jobs.lever.co/{m.group(1)}/{m.group(2)}/apply", fields=fields)


FETCHERS = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever}


def main():
    forms = json.load(open(OUT)) if os.path.exists(OUT) else {}
    rows = [r for r in csv.DictReader(open(os.path.join(DATA, "sheet.csv"), encoding="utf-8")) if r.get("fit") in TARGET and r.get("link")]
    order = {"APPLY NOW": 0, "APPLY": 1, "SOON": 2}
    rows.sort(key=lambda r: order.get(r.get("urgency"), 3))
    now = time.time()
    done = 0

    def fetched_at(rec):
        try:
            return calendar.timegm(time.strptime(rec.get("fetched") or "", "%Y-%m-%dT%H:%MZ"))
        except ValueError:
            return 0

    def due(rec):
        if rec.get("closed") and rec.get("attempted", 0) > now - 86400:
            return False
        if rec.get("fields"):
            return fetched_at(rec) < now - REFRESH and rec.get("attempted", 0) < now - 21600
        return not (rec.get("attempted", 0) > now - 86400 and not (rec.get("error") and rec.get("portal") in FETCHERS))

    for r in rows:
        k = jid(r["link"])
        rec = forms.get(k) or {}
        if rec.get("portal") == "linkedin" and not rec.get("li_checked"):
            rec["apply_url"] = None; rec["li_checked"] = True; rec["attempted"] = 0
            forms[k] = rec
    # New postings first, then the stalest saved forms.
    todo = [r for r in rows if due(forms.get(jid(r["link"])) or {})]
    todo.sort(key=lambda r: (bool((forms.get(jid(r["link"])) or {}).get("fields")), fetched_at(forms.get(jid(r["link"])) or {})))
    for r in todo:
        k = jid(r["link"])
        rec = forms.get(k) or {}
        if done >= MAX_PER_RUN:
            break
        done += 1
        rec.update(company=r["company"], role=r["role"], link=r["link"], attempted=int(now))
        try:
            real = rec.get("apply_url") or resolve(r["link"])
            rec["apply_url"] = real
            rec["portal"] = portal_of(real)
            f = FETCHERS.get(rec["portal"])
            if f:
                res = f(real)
                rec["fields"] = res["fields"]; rec["apply_url"] = res.get("apply_url") or real; rec["error"] = ""; rec["closed"] = False
                rec["fetched"] = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
                print(f"forms: {r['company']:30.30} {rec['portal']:11} {len(res['fields']):3} fields")
            else:
                rec["fields"] = None; rec["error"] = "form not public for this portal"
                print(f"forms: {r['company']:30.30} {rec['portal']:11} (no public form)")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410) and rec.get("portal") in FETCHERS:
                rec["closed"] = True; rec["fields"] = None; rec["error"] = f"posting closed (HTTP {e.code})"
            else:
                rec["error"] = f"HTTPError: {e.code}"
                rec.setdefault("fields", None)
            print(f"forms: {r['company']:30.30} {rec.get('portal','?'):11} {rec['error']}")
        except Exception as e:  # noqa
            rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            rec.setdefault("fields", None)
            print(f"forms: {r['company']:30.30} {rec.get('portal','?'):11} FAILED {rec['error']}")
        forms[k] = rec
    for rec in forms.values():  # normalize: greenhouse forms always open on boards.greenhouse.io so the apply script can run there
        if rec.get("portal") == "greenhouse" and rec.get("fields") and "greenhouse.io" not in (rec.get("apply_url") or ""):
            mm = re.search(r"gh_jid=(\d+)", rec.get("apply_url") or "") or re.search(r"/jobs/(\d+)", rec.get("apply_url") or "")
            b = board_from_config(rec.get("apply_url") or "")
            if mm and b:
                rec["apply_url"] = f"https://boards.greenhouse.io/{b}/jobs/{mm.group(1)}"
    json.dump(forms, open(OUT, "w"), indent=0)
    have = sum(1 for v in forms.values() if v.get("fields"))
    print(f"forms.json: {len(forms)} postings, {have} with a real question list, {done} fetched this run")


if __name__ == "__main__":
    main()
