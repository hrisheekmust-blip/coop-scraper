"""Workday (myworkdayjobs.com). Candidate accounts per tenant, email as username, multi-step SPA with
data-automation-id hooks, a review step whose footer button reads "Submit", and a candidate home that lists
submitted applications (used for read-only outcome checks)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..browser import CONFIRM_RX, Button, Observation, observe
from .base import PortalAdapter

NEXT_ID = ("bottom-navigation-next-button", "pagefooternextbutton")


class Workday(PortalAdapter):
    name = "workday"
    hosts = (r"\.myworkdayjobs\.com$", r"\.myworkdaysite\.com$")
    supports_history = True
    email_login = True

    def _auto(self, obs, *ids):
        ids = {i.lower() for i in ids}
        return [b for b in obs.buttons if (b.automation or "").lower() in ids and not b.disabled]

    def page_kind(self, obs: Observation, run):
        text = obs.text()
        if re.search(r"application submitted|you('ve| have) (successfully )?applied", text, re.I) and not [c for c in obs.controls if c.required]:
            return "confirmation"
        if self._auto(obs, "applyManually", "autofillWithResume", "useMyLastApplication") or (
                self._auto(obs, "adventureButton", "applyButton") and not obs.controls):
            return "job_page"
        if self._auto(obs, "createAccountSubmitButton") or (obs.password_fields >= 2):
            return "register"
        if self._auto(obs, "signInSubmitButton") or re.search(r"^sign in$", " ".join(obs.headings), re.I | re.M):
            return "login" if obs.password_fields else None
        if self._auto(obs, *NEXT_ID) and not [c for c in obs.controls if c.control not in ("password",)]:
            return "review"
        return None

    def apply_entry(self, obs, run):
        for ids in (("applyManually",), ("adventureButton", "applyButton")):
            bs = self._auto(obs, *ids)
            if bs:
                bs[0].kind = "apply_entry"
                return bs[0]
        return super().apply_entry(obs, run)

    def create_account_link(self, obs, run):
        bs = self._auto(obs, "createAccountLink")
        if bs:
            bs[0].kind = "create_account"
            return bs[0]
        return super().create_account_link(obs, run)

    def create_account_submit(self, obs, run):
        bs = self._auto(obs, "createAccountSubmitButton")
        return bs[0] if bs else super().create_account_submit(obs, run)

    def signin_submit(self, obs, run):
        bs = self._auto(obs, "signInSubmitButton")
        if bs:
            bs[0].kind = "signin"
            return bs[0]
        return super().signin_submit(obs, run)

    def next_button(self, obs, run):
        for b in self._auto(obs, *NEXT_ID):
            if not re.fullmatch(r"submit", b.text.strip(), re.I):
                b.kind = "next"
                return b
        return None

    def final_button(self, obs, run):
        for b in self._auto(obs, *NEXT_ID):
            if re.fullmatch(r"submit", b.text.strip(), re.I):
                b.kind = "submit"
                return b
        return None

    def evidence(self, obs, before, run):
        text = obs.text()
        m = re.search(r"application submitted|thank you for applying|you('ve| have) (successfully )?applied", text, re.I)
        if m and not re.search(m.group(0), before.text(), re.I):
            return {"url": obs.url, "text": text[max(0, m.start() - 60):m.end() + 140].strip(), "kind": "page"}
        return None

    def history(self, run):
        """Read-only: open the candidate home and look for this requisition under My Applications."""
        u = urlsplit(run.job["resolved_url"])
        parts = [p for p in u.path.split("/") if p]
        site = next((p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)), "")
        run.page.goto(f"https://{u.hostname}/{site}/userHome", wait_until="domcontentloaded", timeout=45000)
        run.ex.settle(1500)
        obs = observe(run.page)
        if obs.password_fields:
            return None
        text = obs.text()
        if not re.search(r"my applications|candidate home", text, re.I):
            return None
        req = (run.job.get("requisition") or "").lower()
        low = text.lower()
        if not req or run.job.get("provisional"):
            return None            # a title alone can match last year's or another location's posting
        return "submitted" if re.search(r"\b" + re.escape(req) + r"\b", low) else "not_found"

    def fix_page(self, obs, run):
        return None
