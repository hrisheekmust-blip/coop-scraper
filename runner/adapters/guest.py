"""Greenhouse, Ashby and Lever: no account, one form, a confirmation route or message."""
from __future__ import annotations

import re

from ..browser import CONFIRM_RX, Observation
from ..identity import identify
from .base import PortalAdapter


def _same_req(url, run):
    i = identify(url)
    return not i.provisional and (i.portal, i.tenant, i.requisition) == (run.job.get("portal"), run.job.get("tenant"), run.job.get("requisition"))


class Greenhouse(PortalAdapter):
    name = "greenhouse"
    hosts = (r"(^|\.)greenhouse\.io$",)

    def page_kind(self, obs, run):
        if re.search(r"/confirmation/?$", obs.url.split("?")[0]):
            return "confirmation"
        return None

    def evidence(self, obs: Observation, before: Observation, run):
        path = obs.url.split("?")[0].split("#")[0]
        if re.search(r"/confirmation/?$", path) and _same_req(obs.url, run):
            m = CONFIRM_RX.search(obs.text())
            return {"url": obs.url, "text": (obs.text()[max(0, m.start() - 60):m.end() + 100] if m else "confirmation page for this job").strip(), "kind": "page"}
        return super().evidence(obs, before, run)


class Ashby(PortalAdapter):
    name = "ashby"
    hosts = (r"^jobs\.ashbyhq\.com$",)

    def page_kind(self, obs, run):
        if not re.search(r"/application/?$", obs.url.split("?")[0]) and any(re.search(r"/application/?$", b.href or "") for b in obs.buttons):
            return "job_page"
        return None

    def apply_entry(self, obs, run):
        # The posting page links to /application; prefer that tab.
        for b in obs.buttons:
            if re.search(r"/application/?$", b.href or "") or re.fullmatch(r"application", b.text.strip(), re.I):
                b.kind = "apply_entry"
                return b
        return super().apply_entry(obs, run)

    def evidence(self, obs, before, run):
        text = obs.text()
        m = re.search(r"(thank you for applying|application (was )?successfully submitted|your application has been submitted)", text, re.I)
        if m and not re.search(m.group(0), before.text(), re.I) and not [c for c in obs.controls if c.required]:
            return {"url": obs.url, "text": text[max(0, m.start() - 60):m.end() + 100].strip(), "kind": "page"}
        return None


class Lever(PortalAdapter):
    name = "lever"
    hosts = (r"^jobs(\.eu)?\.lever\.co$",)

    def apply_entry(self, obs, run):
        for b in obs.buttons:
            if re.search(r"/apply/?$", b.href or ""):
                b.kind = "apply_entry"
                return b
        return super().apply_entry(obs, run)

    def page_kind(self, obs, run):
        if re.search(r"/thanks/?$", obs.url.split("?")[0]):
            return "confirmation"
        return None

    def evidence(self, obs, before, run):
        if re.search(r"/thanks/?$", obs.url.split("?")[0].split("#")[0]) and _same_req(obs.url, run):
            m = CONFIRM_RX.search(obs.text())
            return {"url": obs.url, "text": (obs.text()[max(0, m.start() - 60):m.end() + 100] if m else "Lever thanks page for this job").strip(), "kind": "page"}
        return None
