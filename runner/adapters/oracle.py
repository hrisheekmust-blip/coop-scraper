"""Oracle Recruiting Cloud (Candidate Experience). Passwordless: you enter your email, accept the site's terms,
and receive a one-time code by email. The code arrives through the Outlook bridge and is typed once."""
from __future__ import annotations

import re

from ..browser import CODE_RX, Observation
from .base import PortalAdapter


class Oracle(PortalAdapter):
    name = "oracle"
    hosts = (r"\.fa\.[a-z0-9-]+\.oraclecloud\.com$",)
    email_login = True

    def page_kind(self, obs: Observation, run):
        text = obs.text()
        fill = [c for c in obs.controls if c.control not in ("checkbox",)]
        if CODE_RX.search(text) and any(c.control in ("text", "tel", "number") for c in obs.controls) and len(fill) <= 6:
            return "email_code"
        emails = [c for c in obs.controls if c.control == "email" or re.search(r"e-?mail", c.label, re.I)]
        if emails and len(fill) == 1 and not obs.password_fields and re.search(r"email address|enter your email", text, re.I):
            return "email_login"
        if re.search(r"thank you for your (job )?application|application (was )?submitted", text, re.I) and not [c for c in obs.controls if c.required]:
            return "confirmation"
        return None

    def evidence(self, obs, before, run):
        text = obs.text()
        m = re.search(r"thank you for your (job )?application|your application (was|has been) submitted|application submitted", text, re.I)
        if m and not re.search(m.group(0), before.text(), re.I) and not [c for c in obs.controls if c.required]:
            return {"url": obs.url, "text": text[max(0, m.start() - 60):m.end() + 140].strip(), "kind": "page"}
        return None
