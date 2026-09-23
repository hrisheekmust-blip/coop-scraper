"""Portal adapters: optimizations over the one general engine.

An adapter can recognize its pages, find its buttons, open its dropdowns and read its confirmation more
reliably than the generic heuristics. It can't retry, store credentials, decide answers, or press the final
submit on its own: those belong to planner.py, accounts.py, answer_engine.py and submission.py.
When an adapter doesn't recognize a page, it returns None and the generic behavior runs.
"""
from __future__ import annotations

import re

from ..browser import (APPLY_ENTRY_RX, CLOSED_RX, CODE_RX, CONFIRM_RX, MFA_RX, VERIFY_EMAIL_RX, Button, Observation)


class PortalAdapter:
    name = "generic"
    hosts: tuple = ()
    supports_history = False
    email_login = True           # username is the email address

    def matches(self, url: str) -> bool:
        from urllib.parse import urlsplit
        h = (urlsplit(url).hostname or "").lower()
        return any(re.search(p, h) for p in self.hosts)

    # ---- page kinds -----------------------------------------------------------------------------
    def page_kind(self, obs: Observation, run) -> str | None:
        """Return a page kind, or None to let the generic classifier decide."""
        return None

    def generic_kind(self, obs: Observation, run) -> str:
        text = obs.text()
        fillable = [c for c in obs.controls if c.control not in ("password",) and not c.raw.get("disabled")]
        form_like = [c for c in fillable if c.control not in ("search",)]
        if CLOSED_RX.search(text) and len(form_like) <= 1:
            return "closed"
        if CONFIRM_RX.search(text) and len(form_like) <= 2:
            return "confirmation"
        if obs.captcha:
            return "human"
        if MFA_RX.search(text) and obs.password_fields == 0:
            return "mfa"
        if CODE_RX.search(text) and any(c.control in ("text", "tel", "number") for c in fillable) and obs.password_fields == 0:
            return "email_code"
        if VERIFY_EMAIL_RX.search(text) and obs.password_fields == 0 and len(form_like) <= 1:
            return "verify_email"
        if obs.password_fields >= 2:
            return "register"
        if obs.password_fields == 1:
            return "login"
        if form_like and any(c.required or c.control in ("file", "select", "radio", "combobox", "textarea") for c in form_like) or len(form_like) >= 3:
            return "form"
        if any(b.kind == "submit" and not b.disabled for b in obs.buttons):
            return "review"
        if any(b.kind == "apply_entry" for b in obs.buttons):
            return "job_page"
        return "unknown"

    # ---- navigation -------------------------------------------------------------------------------
    def apply_entry(self, obs: Observation, run) -> Button | None:
        cands = [b for b in obs.buttons if b.kind == "apply_entry" and not b.disabled]
        # prefer "apply manually" over autofill options, then plain apply
        cands.sort(key=lambda b: (0 if re.search(r"manual", b.text, re.I) else 1, len(b.text)))
        return cands[0] if cands else None

    def next_button(self, obs: Observation, run) -> Button | None:
        for kind in ("next", "review"):
            for b in obs.buttons:
                if b.kind == kind and not b.disabled:
                    return b
        return None

    def final_button(self, obs: Observation, run) -> Button | None:
        subs = [b for b in obs.buttons if b.kind == "submit" and not b.disabled]
        if len(subs) == 1:
            return subs[0]
        if not subs:
            # Some portals label the final button "Apply" inside the form.
            ap = [b for b in obs.buttons if b.kind == "apply_entry" and b.in_form and re.fullmatch(r"apply( now)?", b.text.strip(), re.I)]
            if len(ap) == 1 and run.form_filled:
                ap[0].kind = "submit"
                return ap[0]
        return None

    def create_account_link(self, obs: Observation, run) -> Button | None:
        for b in obs.buttons:
            if re.search(r"create (an )?account|sign up|register|new user|don'?t have an account", b.text, re.I) and not b.disabled:
                return b
        return None

    def create_account_submit(self, obs: Observation, run) -> Button | None:
        for b in obs.buttons:
            if b.kind == "create_account" and not b.disabled:
                return b
        for b in obs.buttons:
            if re.fullmatch(r"(create account|register|sign up|submit|continue)", b.text.strip(), re.I) and b.in_form:
                return b
        return None

    def signin_submit(self, obs: Observation, run) -> Button | None:
        for b in obs.buttons:
            if b.kind == "signin" and not b.disabled:
                return b
        for b in obs.buttons:
            if b.kind in ("next", "other") and re.fullmatch(r"(continue|next|submit)", b.text.strip(), re.I):
                return b
        return None

    # ---- evidence -------------------------------------------------------------------------------
    def evidence(self, obs: Observation, before: Observation, run) -> dict | None:
        """Application-specific evidence after the final click. Generic rule: confirmation wording that was NOT
        on the pre-submit page, and the form is gone or the URL moved to a confirmation route."""
        text = obs.text()
        m = CONFIRM_RX.search(text)
        if not m or CONFIRM_RX.search(before.text()):
            return None
        form_gone = len([c for c in obs.controls if c.required]) == 0
        moved = obs.url.split("#")[0] != before.url.split("#")[0]
        if not (form_gone or moved):
            return None
        s = max(0, m.start() - 80)
        return {"url": obs.url, "text": text[s:m.end() + 120].strip(), "kind": "page"}

    def history(self, run) -> str | None:
        """Read-only check of the portal's application history: 'submitted', 'not_found', or None (unknown)."""
        return None

    def signed_in_identity(self, obs: Observation, run) -> str | None:
        """Email/name the portal shows for the signed-in candidate, if visible."""
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", " ".join(obs.headings) + " " + obs.body[:1500])
        return m.group(0).lower() if m else None

    def fix_page(self, obs: Observation, run) -> None:
        """Portal quirks after filling (e.g. a device-type dropdown). Must only choose observed options."""
        return None


class GenericAdapter(PortalAdapter):
    name = "generic"

    def matches(self, url):
        return True
