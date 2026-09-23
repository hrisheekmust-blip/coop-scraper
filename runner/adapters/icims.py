"""iCIMS (*.icims.com). The application lives in an iframe (in_iframe=1); login is email + password per
tenant; the form spans several pages and ends with "Submit Application"."""
from __future__ import annotations

import re

from ..browser import Observation
from .base import PortalAdapter


class ICIMS(PortalAdapter):
    name = "icims"
    hosts = (r"\.icims\.com$",)
    email_login = True

    def page_kind(self, obs: Observation, run):
        text = obs.text()
        if re.search(r"thank you for (applying|your interest|submitting)|application (has been )?(submitted|received)", text, re.I) \
                and not [c for c in obs.controls if c.required]:
            return "confirmation"
        return None

    def apply_entry(self, obs, run):
        for b in obs.buttons:
            if re.search(r"apply for this job( online)?|apply now|^apply$", b.text, re.I) and not b.disabled:
                b.kind = "apply_entry"
                return b
        return super().apply_entry(obs, run)

    def evidence(self, obs, before, run):
        text = obs.text()
        m = re.search(r"thank you for (applying|submitting)|application (has been )?(submitted|received)", text, re.I)
        if m and not re.search(m.group(0), before.text(), re.I) and not [c for c in obs.controls if c.required]:
            return {"url": obs.url, "text": text[max(0, m.start() - 60):m.end() + 140].strip(), "kind": "page"}
        return None
