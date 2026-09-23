"""Canonical job identity and authentication realms.

A job is (portal, tenant, requisition). LinkedIn, Simplify, and direct links are aliases of it. Query parameters
are not stripped generically: some portals carry the requisition in them (SuccessFactors career_job_req_id,
Greenhouse gh_jid). When no portal rule applies, the job gets a provisional identity from its normalized URL
and is merged into the real one once a page reveals it.

A realm is who issues the login, not just a hostname: two employers on the same ATS host never share one
unless a rule says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote

TRACKING = re.compile(r"^(utm_.*|gh_src|source|src|ref|refs|lever-source|lever-origin|trk|trackingid|refid|tracking|mode|ccuid|jobpipeline|_ga|fbclid|gclid|mc_.*|campaign|codes|iis|iisn|urlhash)$", re.I)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")


@dataclass(frozen=True)
class JobIdentity:
    portal: str
    tenant: str
    requisition: str
    provisional: bool = False

    @property
    def key(self) -> str:
        return f"{self.portal}:{self.tenant}:{self.requisition}"


@dataclass(frozen=True)
class Realm:
    id: str
    portal: str
    tenant: str
    allowed_hosts: tuple = field(default_factory=tuple)
    auth_method: str = "password"


def normalize_url(url: str) -> str:
    """Stable form of a URL for alias matching: https, lowercase host, no fragment, no tracking params."""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (u.hostname or "").lower()
    if not host:
        return url.strip()
    path = re.sub(r"/{2,}", "/", u.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    q = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not TRACKING.match(k))
    return urlunsplit(("https", host + (f":{u.port}" if u.port and u.port not in (80, 443) else ""), path, urlencode(q), ""))


def _parts(u):
    return [unquote(p) for p in (u.path or "").split("/") if p]


def identify(url: str) -> JobIdentity:
    """Canonical identity for a job URL. Never merges on titles; unknown URLs are provisional."""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return JobIdentity("url", "", normalize_url(url), True)
    host = (u.hostname or "").lower()
    parts = _parts(u)
    q = {k.lower(): v for k, v in parse_qsl(u.query, keep_blank_values=True)}

    # Greenhouse: boards / job-boards / eu, and the embed form.
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        if "embed" in (u.path or "") and q.get("for") and re.fullmatch(r"\d+", q.get("token") or q.get("gh_jid") or ""):
            return JobIdentity("greenhouse", q["for"].lower(), q.get("token") or q["gh_jid"])
        if len(parts) >= 3 and parts[1] == "jobs" and re.fullmatch(r"\d+", parts[2]):
            return JobIdentity("greenhouse", parts[0].lower(), parts[2])
    # Company-hosted page that embeds Greenhouse: the id is in gh_jid, the board isn't known yet.
    if re.fullmatch(r"\d{5,}", q.get("gh_jid") or ""):
        return JobIdentity("greenhouse", "?" + host, q["gh_jid"], True)

    if host == "jobs.ashbyhq.com" and len(parts) >= 2 and UUID.match(parts[1]):
        return JobIdentity("ashby", parts[0].lower(), parts[1].lower())

    if re.fullmatch(r"jobs(\.eu)?\.lever\.co", host) and len(parts) >= 2 and UUID.match(parts[1]):
        return JobIdentity("lever", parts[0].lower(), parts[1].lower())

    # Workday: {tenant}.wd{N}.myworkdayjobs.com/{locale?}/{site}/job/{location}/{title}_{REQ}[/apply...]
    m = re.fullmatch(r"([a-z0-9-]+)\.wd\d+\.(myworkdayjobs|myworkdaysite)\.com", host)
    if m:
        tenant = m.group(1)
        segs = [p for p in parts if not LOCALE.match(p)]
        for i, p in enumerate(segs):
            if p in ("job", "details") and i + 1 < len(segs):
                tail = [s for s in segs[i + 1:] if s not in ("apply", "applyManually", "autofillWithResume", "useMyLastApplication")]
                slug = tail[-1] if tail else ""
                r = re.search(r"_([A-Za-z]*-?\d[\w-]*)$", slug)
                if r:
                    return JobIdentity("workday", tenant, r.group(1).upper())
        return JobIdentity("workday", tenant, normalize_url(url), True)

    # SuccessFactors career site.
    if re.search(r"(^|\.)successfactors\.(com|eu)$|(^|\.)sapsf\.(com|eu)$", host):
        company = (q.get("company") or "").lower()
        req = q.get("career_job_req_id") or q.get("jobreqid") or q.get("career_job_req_id".upper()) or ""
        if company and re.fullmatch(r"\d+", req):
            return JobIdentity("successfactors", company, req)
        return JobIdentity("successfactors", company or host, normalize_url(url), True)
    # SuccessFactors Recruiting Marketing (RMK) career sites: /job/{slug}/{id}-{locale}/
    if len(parts) >= 3 and parts[0] == "job" and re.fullmatch(r"\d+-[a-z]{2}_[A-Z]{2}", parts[-1]):
        return JobIdentity("successfactors_rmk", host, parts[-1].split("-")[0], True)

    # Oracle Recruiting Cloud (Candidate Experience).
    m = re.fullmatch(r"([a-z0-9-]+)\.fa\.([a-z0-9-]+)\.oraclecloud\.com", host)
    if m:
        pod = m.group(1)
        if "job" in parts:
            i = parts.index("job")
            if i + 1 < len(parts) and re.fullmatch(r"\d+", parts[i + 1]):
                return JobIdentity("oracle", pod, parts[i + 1])
        return JobIdentity("oracle", pod, normalize_url(url), True)

    # iCIMS: {sub}.icims.com/jobs/{id}/...
    if host.endswith(".icims.com"):
        sub = host[: -len(".icims.com")]
        if len(parts) >= 2 and parts[0] == "jobs" and re.fullmatch(r"\d+", parts[1]):
            return JobIdentity("icims", sub, parts[1])
        return JobIdentity("icims", sub, normalize_url(url), True)

    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and len(parts) >= 2:
        r = re.match(r"(\d{6,})", parts[1])
        if r:
            return JobIdentity("smartrecruiters", parts[0].lower(), r.group(1))

    if host.endswith("linkedin.com"):
        r = re.search(r"/jobs/view/(?:[^/]*?-)?(\d{6,})", u.path or "") or re.search(r"(\d{6,})", q.get("currentjobid", ""))
        if r:
            return JobIdentity("linkedin", "", r.group(1), True)

    return JobIdentity("url", "", normalize_url(url), True)


# Portals where an application needs no account.
GUEST_PORTALS = {"greenhouse", "ashby", "lever", "smartrecruiters"}


def realm_for(url: str) -> Realm | None:
    """The login realm a URL belongs to, or None when the host has no known realm rule.

    allowed_hosts is the complete list of hosts where the realm's credentials may be typed. Anything else,
    including a host a page links to or an email names, is refused.
    """
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    if u.scheme != "https":
        return None
    host = (u.hostname or "").lower()
    q = {k.lower(): v for k, v in parse_qsl(u.query, keep_blank_values=True)}
    parts = _parts(u)
    m = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.(myworkdayjobs|myworkdaysite)\.com", host)
    if m:
        # Workday candidate accounts belong to the tenant; every wdN host variant of it is the same realm.
        return Realm(f"workday:{m.group(1)}", "workday", m.group(1), (host,), "password")
    if re.search(r"(^|\.)successfactors\.(com|eu)$|(^|\.)sapsf\.(com|eu)$", host):
        company = (q.get("company") or "").lower()
        if company:
            return Realm(f"successfactors:{company}", "successfactors", company, (host,), "password")
        return None
    m = re.fullmatch(r"([a-z0-9-]+)\.fa\.([a-z0-9-]+)\.oraclecloud\.com", host)
    if m:
        site = ""
        if "sites" in parts:
            i = parts.index("sites")
            site = parts[i + 1].lower() if i + 1 < len(parts) else ""
        return Realm(f"oracle:{m.group(1)}:{site}", "oracle", f"{m.group(1)}:{site}", (host,), "email_code")
    if host.endswith(".icims.com"):
        sub = host[: -len(".icims.com")]
        return Realm(f"icims:{sub}", "icims", sub, (host,), "password")
    ident = identify(url)
    if ident.portal in GUEST_PORTALS:
        return Realm(f"{ident.portal}:{ident.tenant}", ident.portal, ident.tenant, (host,), "guest")
    return None


def host_allowed(realm_hosts, url: str) -> bool:
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in {h.lower() for h in realm_hosts}
