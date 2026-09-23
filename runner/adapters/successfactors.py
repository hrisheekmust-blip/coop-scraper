"""SAP SuccessFactors career sites (and Recruiting Marketing front pages that hand off to them).

Account per company id, email as username. After sign-in some tenants show a data privacy consent statement that
must be accepted (covered only when 'privacy_policy'/'data_processing' are in your approved consents). The
application is one long page whose final button is labelled "Apply"."""
from __future__ import annotations

import re

from ..browser import Observation, observe
from .base import PortalAdapter

DPCS = re.compile(r"data privacy (consent )?statement|data protection|privacy consent", re.I)


class SuccessFactors(PortalAdapter):
    name = "successfactors"
    hosts = (r"(^|\.)successfactors\.(com|eu)$", r"(^|\.)sapsf\.(com|eu)$")
    supports_history = True
    email_login = True

    def matches(self, url):
        from urllib.parse import urlsplit
        h = (urlsplit(url).hostname or "").lower()
        if re.search(r"successfactors\.(com|eu)$|sapsf\.(com|eu)$", h):
            return True
        # Recruiting Marketing front page: /job/{slug}/{id}-{locale}/
        return bool(re.search(r"/job/[^/]+/\d+-[a-z]{2}_[A-Z]{2}/?$", urlsplit(url).path or ""))

    def page_kind(self, obs: Observation, run):
        text = obs.text()
        if DPCS.search(text) and any(re.fullmatch(r"(i )?accept|agree|i agree", b.text.strip(), re.I) for b in obs.buttons) \
                and not [c for c in obs.controls if c.required and c.control not in ("checkbox",)]:
            return "consent_page"
        if re.search(r"thank you for applying|application has been submitted|successfully applied", text, re.I) and not [c for c in obs.controls if c.required]:
            return "confirmation"
        return None

    def final_button(self, obs, run):
        subs = [b for b in obs.buttons if b.kind == "submit" and not b.disabled]
        if subs:
            return subs[0]
        ap = [b for b in obs.buttons if re.fullmatch(r"apply", b.text.strip(), re.I) and b.in_form and not b.disabled]
        if len(ap) == 1 and run.form_filled:
            ap[0].kind = "submit"
            return ap[0]
        return None

    def evidence(self, obs, before, run):
        text = obs.text()
        m = re.search(r"thank you for applying|your application has been submitted|successfully applied|application (was )?received", text, re.I)
        if m and not re.search(m.group(0), before.text(), re.I) and not [c for c in obs.controls if c.required]:
            return {"url": obs.url, "text": text[max(0, m.start() - 60):m.end() + 140].strip(), "kind": "page"}
        return None

    def history(self, run):
        from urllib.parse import parse_qsl, urlsplit
        u = urlsplit(run.job["resolved_url"])
        q = dict(parse_qsl(u.query))
        if not q.get("company"):
            return None
        run.page.goto(f"https://{u.hostname}/career?company={q['company']}&career_ns=job_applications", wait_until="domcontentloaded", timeout=45000)
        run.ex.settle(1200)
        obs = observe(run.page)
        if obs.password_fields:
            return None
        low = obs.text().lower()
        if not re.search(r"job applications|my applications|applied", low):
            return None
        req = (run.job.get("requisition") or "").lower()
        if not req or run.job.get("provisional"):
            return None
        return "submitted" if re.search(r"\b" + re.escape(req) + r"\b", low) else "not_found"
