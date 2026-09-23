"""One application attempt: the deterministic page loop.

observe -> classify the page (adapter first, generic second) -> act -> re-observe. There is no runtime model:
the "general planner" is this loop plus the adapters' hints and the answer engine. Anything it can't do
becomes a precise blocker, never a guess.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import jobqueue as Q, models as M
from .accounts import AccountError, Accounts
from .adapters import pick_adapter
from .browser import (BAD_LOGIN_RX, EXISTS_RX, PASSWORD_POLICY_RX, USERNAME_TAKEN_RX, VERIFY_EMAIL_RX, ActionRefused, Button,
                      Control, Executor, Observation, observe)
from .db import now_iso
from .evidence import scrub
from .identity import Realm, host_allowed, identify, realm_for
from .policy import Validated
from .questions import norm

ALREADY_RX = re.compile(r"you (have )?already applied|already submitted an application|application (already )?exists for this (job|position)", re.I)


class Park(Exception):
    """Stop this attempt and leave the application waiting on something outside the worker."""

    def __init__(self, state, code, detail="", needs=None):
        super().__init__(f"{code}: {detail}")
        self.state, self.code, self.detail, self.needs = state, code, detail, needs or []


class Done(Exception):
    def __init__(self, state, detail=""):
        super().__init__(detail)
        self.state, self.detail = state, detail


class Retry(Exception):
    pass


@dataclass
class RunConfig:
    max_steps: int = 40
    verify_wait_s: float = 600         # wait in place for a verification email / code
    human_wait_s: float = 600          # wait in place for a captcha you solve in the worker window
    confirm_wait_s: float = 90         # wait for the confirmation after the final click
    poll_s: float = 2.0


class ApplicationRun:
    def __init__(self, *, db, page, job: dict, app: dict, attempt_id: str, owner: str, engine, accounts: Accounts, vault,
                 materials: dict, material_kinds: dict, cover_letter=False, cfg: RunConfig | None = None, heartbeat=None, log=None):
        self.db, self.page, self.job, self.app = db, page, job, app
        self.attempt_id, self.owner = attempt_id, owner
        self.engine, self.accounts, self.vault = engine, accounts, vault
        self.materials, self.material_kinds, self.cover_letter = materials, material_kinds, cover_letter
        self.cfg = cfg or RunConfig()
        self.heartbeat = heartbeat or (lambda: True)
        self._log = log or (lambda *a, **k: None)
        self.account_id = None
        self.realm: Realm | None = None
        self.form_filled = False
        self.submitted = False
        self.answers_used = []
        self.seen_sigs = {}
        self._last_park = None
        self.ex = Executor(page, accounts, vault, None, budget=self._budget, log=self.log, materials=materials)

    # ------------------------------------------------------------------ plumbing
    def log(self, _ev, **data):
        self._log(_ev, **data)
        try:
            with self.db.tx():
                self.db.event("application", self.app["id"], "action:" + _ev, data)
        except Exception:
            pass

    def _budget(self):
        if not self.heartbeat():
            raise Done(None, "lease lost")
        return Q.use_action(self.db, self.attempt_id, self.owner)

    def state(self, st, reason="", needs=None):
        cur = Q.app_row(self.db, self.app["id"])["state"]
        if cur == st:
            return
        Q.transition(self.db, self.app["id"], st, reason, needs, attempt_id=self.attempt_id, owner=self.owner)

    def check_cancel(self):
        if Q.cancel_requested(self.db, self.app["id"]) and not self.submitted:
            raise Done(M.CANCELLED, "cancelled by you before submit")
        if not self.heartbeat():
            raise Done(None, "lease lost")

    def use_page(self, page):
        self.page = page
        self.ex.page = page

    # ------------------------------------------------------------------ main loop
    def execute(self) -> tuple[str, str]:
        """Returns (final application state, detail). Never raises for expected outcomes."""
        try:
            self._run()
            return M.FAILED, "loop ended without an outcome"
        except Done as d:
            return d.state, d.detail
        except Park as p:
            self._last_park = p
            return p.state, f"{p.code}: {p.detail}"

    def _run(self):
        self.state(M.RESOLVING)
        self.page.goto(self.job["resolved_url"], wait_until="domcontentloaded", timeout=45000)
        self.ex.settle(800)
        stale = 0
        for step in range(self.cfg.max_steps):
            self.check_cancel()
            try:
                if self._step():
                    return
                stale = 0
            except (Park, Done):
                raise
            except Exception as e:
                # The page changed under us (a reload after sign-in, a re-render): observe again, a few times at most.
                if ("waiting for locator" in str(e) or "not attached" in str(e) or "Execution context was destroyed" in str(e)) \
                        and not self.submitted and stale < 3:
                    stale += 1
                    self.log("page_changed_during_action")
                    self.ex.settle(800)
                    continue
                raise
        raise Park(M.FAILED, "no_progress", "too many pages without reaching the end of the application")

    def _step(self) -> bool:
        """One observe/act cycle. True when the application reached its final outcome."""
        obs = observe(self.page)
        self._maybe_merge(obs.url)
        adapter = pick_adapter(obs.url)
        kind = adapter.page_kind(obs, self) or adapter.generic_kind(obs, self)
        self.log("page", kind=kind, url=scrub(obs.url.split("?")[0]), adapter=adapter.name)
        sig = obs.signature() + ":" + kind
        self.seen_sigs[sig] = self.seen_sigs.get(sig, 0) + 1
        if self.seen_sigs[sig] > 3:
            raise Park(M.NEEDS_INFO if obs.errors else M.FAILED, "no_progress",
                       "the page stopped advancing" + (": " + "; ".join(obs.errors[:3]) if obs.errors else ""),
                       [{"blocker": "no_progress", "errors": obs.errors[:5]}])
        if kind == "closed":
            Q.mark_posting_closed(self.db, self.job["id"])
            raise Done(M.CLOSED, "the posting is closed")
        if kind == "confirmation":
            if ALREADY_RX.search(obs.text()):
                self._history_confirmed(obs, "the portal says you already applied")
            raise Park(M.FAILED, "unsupported", "a confirmation page appeared before anything was submitted in this attempt")
        if ALREADY_RX.search(obs.text()):
            self._history_confirmed(obs, "the portal says you already applied")
        if kind == "human":
            self.wait_human(obs)
            return False
        if kind == "mfa":
            raise Park(M.NEEDS_HUMAN, "mfa", "the portal asks for a second factor; sign in once in the worker window")
        if kind == "job_page":
            self.state(M.PREPARING) if Q.app_row(self.db, self.app["id"])["state"] == M.RESOLVING else None
            self.enter_application(obs, adapter)
            return False
        if kind in ("login", "register", "verify_email", "email_code", "email_login", "consent_page"):
            self.state(M.AUTHENTICATING) if Q.app_row(self.db, self.app["id"])["state"] in (M.RESOLVING, M.PREPARING, M.FILLING) else None
            getattr(self, "do_" + kind)(obs, adapter)
            return False
        if kind == "form":
            if Q.app_row(self.db, self.app["id"])["state"] in (M.RESOLVING, M.AUTHENTICATING):
                self.state(M.PREPARING)
            self.state(M.FILLING)
            self.verify_identity(obs, adapter)
            return bool(self.fill_and_advance(obs, adapter))
        if kind == "review":
            self.state(M.FILLING)
            return bool(self.fill_and_advance(obs, adapter))
        # unknown: give the page a moment (SPAs), then try an apply entry, then stop with a diagnostic
        self.ex.settle(1500)
        obs2 = observe(self.page)
        if obs2.signature() == obs.signature():
            b = adapter.apply_entry(obs2, self)
            if b:
                self.enter_application(obs2, adapter)
                return False
            raise Park(M.FAILED, "unsupported", f"unrecognized page ({obs.title[:80]})")
        return False

    # ------------------------------------------------------------------ identity
    def _maybe_merge(self, url):
        if self.job.get("provisional"):
            ident = identify(url)
            if not ident.provisional:
                new = Q.merge_job(self.db, self.job["id"], url)
                if new != self.job["id"]:
                    row = self.db.one("SELECT * FROM jobs WHERE id=?", (new,))
                    self.job = {**self.job, **dict(row), "provisional": 0}
                    self.log("job_resolved", identity=ident.key)
                    # The application may now belong to a job that already has a stronger application.
                    a = Q.app_row(self.db, self.app["id"])
                    if a["job_id"] != new or a["state"] in (M.CANCELLED,):
                        raise Done(a["state"] if a["state"] in M.TERMINAL else M.CANCELLED, "duplicate listing of an existing application")
        elif not self.job.get("provisional"):
            ident = identify(url)
            want = (self.job["portal"], self.job["tenant"], self.job["requisition"])
            if not ident.provisional and ident.portal == self.job["portal"] and (ident.tenant, ident.requisition) != want[1:]:
                raise Park(M.FAILED, "wrong_identity", f"the page is for a different requisition ({ident.key})")

    def verify_identity(self, obs, adapter):
        shown = adapter.signed_in_identity(obs, self)
        mine = (self.accounts.application_email() or "").lower()
        if shown and mine and shown != mine and self.account_id:
            raise Park(M.FAILED, "wrong_identity", "the portal is signed in as a different applicant")

    def _history_confirmed(self, obs, text):
        Q.confirm(self.db, self.app["id"], "history", {"url": obs.url, "text": text}, "portal said already applied", self.attempt_id)
        raise Done(M.APPLIED, "already applied on the portal")

    # ------------------------------------------------------------------ entering the application
    def enter_application(self, obs, adapter):
        b = adapter.apply_entry(obs, self)
        if not b:
            raise Park(M.FAILED, "unsupported", "no Apply button on the posting")
        before = set(self.page.context.pages)
        self.ex.click(b)
        self.ex.settle(1200)
        new = [p for p in self.page.context.pages if p not in before]
        if new:
            self.use_page(new[-1])
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            self.ex.settle(800)

    # ------------------------------------------------------------------ accounts
    def _realm(self, url) -> Realm:
        r = realm_for(url)
        if r and r.auth_method != "guest":
            return r
        host = (urlsplit(url).hostname or "").lower()
        job_host = (urlsplit(self.job["resolved_url"]).hostname or "").lower()
        from .protocol import AGGREGATORS
        if AGGREGATORS.search(host):
            raise Park(M.FAILED, "unsupported", f"{host} is a job board; the worker only applies on the employer's own portal")
        if urlsplit(url).scheme == "https" and host and host == job_host:
            # Unknown portal, but it's the employer's own posting host from the board: a site-level realm.
            return Realm(f"site:{host}", "site", host, (host,), "password")
        acc = self.db.one("SELECT r.id FROM realms r WHERE r.allowed_hosts LIKE ?", (f'%"{host}"%',))
        if acc:
            row = self.db.one("SELECT * FROM realms WHERE id=?", (acc["id"],))
            return Realm(row["id"], row["portal"], row["tenant"], tuple(), row["auth_method"])
        raise Park(M.NEEDS_HUMAN, "credential_destination", f"the login page is on {host}, which isn't verified for this employer; approve it in the board to continue")

    def ensure_account(self, url):
        realm = self._realm(url)
        if self.realm and self.realm.id == realm.id and self.account_id:
            return self.accounts.row(self.account_id)
        a = self.accounts.ensure(realm)
        self.realm, self.account_id = realm, a["id"]
        self.ex.account_id = a["id"]
        with self.db.tx():
            self.db.x("UPDATE jobs SET realm_id=COALESCE(realm_id, ?) WHERE id=?", (realm.id, self.job["id"]))
        return self.accounts.row(a["id"])

    def _controls(self, obs, pred):
        return [c for c in obs.controls if pred(c)]

    def _email_field(self, obs):
        cs = self._controls(obs, lambda c: c.control == "email" or re.search(r"e-?mail|user ?name|login|user id", c.label, re.I) and c.control in ("text", "email"))
        return cs[0] if cs else None

    def do_login(self, obs, adapter):
        a = self.ensure_account(obs.url)
        st = a["status"]
        if st in (M.ACC_UNKNOWN, M.ACC_CHECKING) and self.accounts.policy()["allow_required_account_creation"]:
            link = adapter.create_account_link(obs, self)
            if link and not a["registration_intent_at"]:
                self.ex.click(link, allow=("create_account", "other", "signin", "next", "apply_entry"))
                self.ex.settle(800)
                return
        if st == M.ACC_AWAITING_EMAIL:
            return self.wait_verification(obs, adapter, "activation")
        if st in (M.ACC_REGISTERING, M.ACC_UNCERTAIN):
            # An earlier Create account may have gone through: look for its verification email before anything else.
            return self.wait_verification(obs, adapter, "activation", on_timeout=Park(
                M.NEEDS_HUMAN, "registration_uncertain", "an earlier account registration here may have gone through; "
                "check your email, then resolve it in the board (created / not created)"))
        if st in (M.ACC_NEEDS_CREDENTIALS, M.ACC_LOCKED) or not self.accounts.can_try_login(a["id"]):
            raise Park(M.NEEDS_HUMAN, "credentials_rejected", a["status_reason"] or "the saved password was rejected")
        user = self._email_field(obs)
        pw = self._controls(obs, lambda c: c.control == "password")
        if not user or not pw:
            raise Park(M.FAILED, "unsupported", "couldn't find the sign-in fields")
        self._cred(user, "email" if adapter.email_login or user.control == "email" or re.search(r"e-?mail", user.label, re.I) else "username")
        self._cred(pw[0], "password")
        btn = adapter.signin_submit(obs, self)
        if not btn:
            raise Park(M.FAILED, "unsupported", "no sign-in button")
        self.ex.click(btn, allow=("signin", "next", "other"))
        after = self.ex.wait_change(obs)
        if BAD_LOGIN_RX.search(" ".join(after.errors) + " " + after.body[:3000]):
            n = self.accounts.login_failed(a["id"])
            # Never created or used by us here: the portal already had an account for this email with another password.
            if st == M.ACC_EXISTING or (a["registration_intent_at"] is None and st != M.ACC_ACTIVE):
                self.accounts.set_status(a["id"], M.ACC_NEEDS_CREDENTIALS, "an account exists for your email with a different password")
                raise Park(M.NEEDS_HUMAN, "existing_account", "an account already exists for your email here with a different password; add its password in the extension settings")
            if not self.accounts.can_try_login(a["id"]):
                raise Park(M.NEEDS_HUMAN, "credentials_rejected", "the portal rejected the saved password")
            return
        if after.password_fields and after.signature() == obs.signature():
            if VERIFY_EMAIL_RX.search(after.text()):
                self.accounts.set_status(a["id"], M.ACC_AWAITING_EMAIL, "portal asks to verify the email first", awaiting_since=now_iso())
                return
            return  # let the loop's progress guard decide
        self.accounts.login_ok(a["id"])
        self.save_session()

    def do_email_login(self, obs, adapter):
        """Passwordless portals: email first, then an emailed code."""
        a = self.ensure_account(obs.url)
        em = self._email_field(obs)
        if not em:
            raise Park(M.FAILED, "unsupported", "no email field on the sign-in page")
        self._cred(em, "email")
        for c in obs.controls:
            if c is not em and c.control != "password":
                self.fill_one(c, obs)
        needs = self.collect_needs(observe(self.page))
        if needs:
            self.park_needs(needs)
        btn = adapter.signin_submit(obs, self) or adapter.next_button(obs, self)
        if not btn:
            raise Park(M.FAILED, "unsupported", "no button to request the code")
        self.accounts.set_status(a["id"], M.ACC_AWAITING_EMAIL, "waiting for the emailed login code", awaiting_since=now_iso())
        self.ex.click(btn, allow=("signin", "next", "other"))
        self.ex.settle(1200)

    def restart_at_posting(self):
        self.page.goto(self.job["resolved_url"], wait_until="domcontentloaded", timeout=45000)
        self.ex.settle(800)

    def do_register(self, obs, adapter):
        a = self.ensure_account(obs.url)
        if a["status"] == M.ACC_EXISTING:
            return self.restart_at_posting()           # sign in instead; never create a second identity
        if a["status"] in (M.ACC_REGISTERING, M.ACC_UNCERTAIN, M.ACC_AWAITING_EMAIL):
            return self.wait_verification(obs, adapter, "activation", on_timeout=Park(
                M.NEEDS_HUMAN, "registration_uncertain", "an earlier account registration here may have gone through; check your email"))
        if a["status"] == M.ACC_ACTIVE:
            # We think the account exists; the portal shows a registration form: look for a sign-in link instead.
            for b in obs.buttons:
                if re.search(r"sign in|log ?in|already have an account", b.text, re.I):
                    self.ex.click(b, allow=("signin", "other", "next", "apply_entry"))
                    self.ex.settle(800)
                    return
        try:
            self.accounts.password_ref(a["id"])
        except AccountError as e:
            raise Park(M.NEEDS_HUMAN, e.code, e.detail)
        # Required fields first, from confirmed facts only.
        pws = self._controls(obs, lambda c: c.control == "password")
        for p in pws:
            self._cred(p, "password")
        em = self._email_field(obs)
        for c in obs.controls:
            if c.control == "password":
                continue
            if c.control in ("text", "email") and re.search(r"e-?mail", c.label, re.I):
                self._cred(c, "email")       # the email (and "confirm email") is the application email
                continue
            if c.control == "text" and re.search(r"user ?name|user id", c.label, re.I):
                self._cred(c, "username")
                continue
            self.fill_one(c, obs, register=True)
        needs = self.collect_needs(observe(self.page), register=True)
        if needs:
            self.park_needs(needs)
        btn = adapter.create_account_submit(observe(self.page), self)
        if not btn:
            raise Park(M.FAILED, "unsupported", "no Create Account button")
        try:
            self.accounts.begin_registration(a["id"])
        except AccountError as e:
            if e.code == "registration_outstanding":
                raise Park(M.NEEDS_HUMAN, "registration_uncertain", "an earlier account registration here may have gone through; checking the mailbox first")
            raise Park(M.NEEDS_HUMAN, e.code, e.detail)
        btn.kind = "create_account"
        before = observe(self.page)
        self.ex.click(btn, allow=("create_account",))
        after = self.ex.wait_change(before, timeout=20)
        text = " ".join(after.errors) + " " + after.text()
        if EXISTS_RX.search(text):
            self.accounts.registration_result(a["id"], "existing_account", "the portal says an account exists for this email")
            with self.db.tx():
                self.db.x("UPDATE accounts SET registration_intent_at=NULL WHERE id=?", (a["id"],))
            for b in after.buttons:
                if re.search(r"sign in|log ?in", b.text, re.I):
                    self.ex.click(b, allow=("signin", "other", "next", "apply_entry"))
                    self.ex.settle(800)
                    return
            return self.restart_at_posting()
        if PASSWORD_POLICY_RX.search(text) and after.password_fields >= 2:
            self.accounts.registration_result(a["id"], "password_policy", scrub(" ".join(after.errors)[:300]))
            raise Park(M.NEEDS_HUMAN, "password_policy", "this portal's password rules reject your configured password; add a per-account password in the extension settings")
        if USERNAME_TAKEN_RX.search(text):
            self.accounts.registration_result(a["id"], "username_taken")
            raise Park(M.NEEDS_HUMAN, "username_taken", "the configured username is taken here; add a per-account username")
        if VERIFY_EMAIL_RX.search(after.text()):
            self.accounts.registration_result(a["id"], "awaiting_email")
            self.wait_verification(after, adapter, "activation")
            return
        if after.password_fields >= 2 and after.signature() == obs.signature():
            self.accounts.registration_result(a["id"], "uncertain", "the registration page didn't change")
            raise Park(M.NEEDS_HUMAN, "registration_uncertain", "the account form didn't respond; check the worker window")
        self.accounts.registration_result(a["id"], "active")
        self.save_session()

    def do_consent_page(self, obs, adapter):
        """A privacy/data-processing statement to accept. Only accepted when your saved policy covers it."""
        from .questions import Question
        q = Question(label="I accept the data privacy statement", control="checkbox", options=["I accept"], context=obs.text()[:300],
                     job_id=self.job["id"], employer=self.job.get("company", ""))
        from .answer_rules import consent
        p = consent(self.ctx(), q, "consent:data_privacy_statement")
        if p.decision != "answer":
            raise Park(M.NEEDS_HUMAN, "consent_unconfigured", "the portal asks you to accept a privacy statement your saved policy doesn't cover")
        for c in obs.controls:
            if c.control == "checkbox":
                self.fill_one(c, obs)
        for b in obs.buttons:
            if re.fullmatch(r"(i )?accept|agree|i agree", b.text.strip(), re.I) and not b.disabled:
                self.ex.click(b, allow=("other", "next", "signin"))
                self.ex.settle(1000)
                return
        raise Park(M.FAILED, "unsupported", "couldn't find the Accept button")

    def do_verify_email(self, obs, adapter):
        self.ensure_account(obs.url)
        a = self.accounts.row(self.account_id)
        if a["status"] != M.ACC_AWAITING_EMAIL:
            self.accounts.set_status(a["id"], M.ACC_AWAITING_EMAIL, "portal asks to verify the email", awaiting_since=a["awaiting_since"] or now_iso())
        self.wait_verification(obs, adapter, "activation")

    def do_email_code(self, obs, adapter):
        self.ensure_account(obs.url)
        a = self.accounts.row(self.account_id)
        if a["status"] != M.ACC_AWAITING_EMAIL:
            self.accounts.set_status(a["id"], M.ACC_AWAITING_EMAIL, "waiting for the emailed code", awaiting_since=now_iso())
        self.wait_verification(obs, adapter, "login_code")

    def wait_verification(self, obs, adapter, purpose, on_timeout: "Park | None" = None):
        """Wait in place for the Outlook bridge to deliver the link/code. Park if it doesn't come."""
        a = self.accounts.row(self.account_id)
        if purpose == "activation" and a["auth_method"] == "email_code":
            purpose = "login_code"
        with self.db.tx():
            self.db.x("UPDATE accounts SET auth_method=CASE WHEN ?='login_code' THEN 'email_code' ELSE auth_method END WHERE id=?", (purpose, a["id"]))
        deadline = time.time() + self.cfg.verify_wait_s
        self.note("Needs verification: waiting for the verification email")
        ref = self.accounts.take_verification(a["id"])
        while not ref and time.time() < deadline:
            self.check_cancel()
            time.sleep(self.cfg.poll_s)
            ref = self.accounts.take_verification(a["id"])
        if not ref and on_timeout:
            raise on_timeout
        if not ref:
            raise Park(M.AWAITING_EMAIL, "email_verification" if purpose == "activation" else "login_code",
                       "waiting for the verification email (keep Outlook open in Chrome so the extension can pass it on)")
        secret = self.vault.reveal(ref)
        self.vault.delete(ref)
        if purpose == "activation" or re.match(r"https://", secret or ""):
            if not host_allowed(self.accounts.realm_hosts(a["realm_id"]), secret):
                raise Park(M.NEEDS_HUMAN, "credential_destination", "the verification link points outside this employer's approved hosts")
            self.log("verification_link_opened")
            self.page.goto(secret, wait_until="domcontentloaded", timeout=45000)
            self.ex.settle(1500)
        else:
            cur = observe(self.page)
            box = [c for c in cur.controls if c.control in ("text", "tel", "number") and re.search(r"code|pin|passcode", c.label + " " + c.placeholder, re.I)]
            if not box:
                box = [c for c in cur.controls if c.control in ("text", "tel", "number")]
            if len(box) != 1:
                raise Park(M.NEEDS_HUMAN, "login_code", "couldn't find where to enter the code")
            self.ex.loc(box[0].key).fill(secret)
            btn = next((b for b in cur.buttons if re.fullmatch(r"(verify|confirm|continue|submit|next|sign in|log ?in)( code)?", b.text.strip(), re.I)
                        and not b.disabled), None) or adapter.signin_submit(cur, self) or adapter.next_button(cur, self)
            if btn:
                self.ex.click(btn, allow=("signin", "next", "other", "verify"))
            self.ex.settle(1500)
        secret = None
        self.accounts.set_status(a["id"], M.ACC_ACTIVE, "verified", registration_intent_at=None)
        self.save_session()

    def note(self, reason):
        """Update what the board shows without changing state (e.g. while waiting for you in place)."""
        with self.db.tx():
            self.db.x("UPDATE applications SET state_reason=?, updated_at=? WHERE id=?", (reason, now_iso(), self.app["id"]))

    def wait_human(self, obs):
        """A captcha or identity check: you solve it in the worker window; the worker never tries to."""
        self.note("Needs verification: solve the check in the worker window")
        self.log("waiting_for_you", reason="human verification")
        deadline = time.time() + self.cfg.human_wait_s
        while time.time() < deadline:
            self.check_cancel()
            time.sleep(self.cfg.poll_s)
            if not observe(self.page).captcha:
                self.note("")
                return
        raise Park(M.NEEDS_HUMAN, "human_verification", "the portal is showing a captcha or identity check; open the worker window and solve it, then click Resume")

    def _cred(self, c: Control, what: str):
        try:
            ok = self.ex.fill_credential(c, what)
        except ActionRefused as e:
            raise Park(M.NEEDS_HUMAN, "credential_destination" if "destination" in str(e) else "credentials_rejected", str(e))
        except AccountError as e:
            raise Park(M.NEEDS_HUMAN, e.code, e.detail)
        if not ok:
            raise Park(M.FAILED, "changed_form", f"the {what} field didn't take the value")

    def save_session(self):
        if not self.account_id or not self.realm:
            return
        name = f"realm:{self.realm.id}"
        if Q.acquire_lock(self.db, name, self.owner, ttl=60):
            try:
                self.accounts.save_session(self.account_id, self.page.context.storage_state())
            finally:
                Q.release_lock(self.db, name, self.owner)

    # ------------------------------------------------------------------ forms
    def ctx(self):
        return self.engine.ctx(self.job, materials=self.material_kinds, cover_letter=self.cover_letter)

    def fill_one(self, c: Control, obs: Observation, register=False) -> str:
        """Returns 'filled' | 'kept' | 'skipped' | 'need:<reason>'."""
        if c.control in ("password", "search") or c.raw.get("disabled") or c.raw.get("readonly"):
            return "skipped"
        q = c.question(self.job)
        if c.control == "combobox" and not c.options:
            try:
                opts = self.ex.combo_options(c)
            except Exception:
                opts = []
            if not opts and c.raw.get("tag") == "input":
                # async search box (school, city): search for the answer we would give, then read the results
                first = self.engine.propose(q, self.ctx())
                val = first.value if first.decision == "answer" else None
                if isinstance(val, list):
                    val = val[0] if val else None
                if isinstance(val, str) and val.strip():
                    try:
                        opts = self.ex.combo_options(c, val.split(",")[0][:40])
                    except Exception:
                        opts = []
            q.options = opts
            c.options = [{"label": o, "ref": ""} for o in opts]
        res = self.engine.answer(q, self.ctx())
        if not isinstance(res, Validated):
            return "need:" + res.reason
        p = res.proposal
        if p.kind == "skip":
            if c.control == "checkbox" and c.value:
                self.ex.choose(c, [])
            return "skipped"
        if p.kind == "file":
            if c.value:
                return "kept"
            ok = self.ex.upload(c, p.value)
            self._used(q, "[file]", p)
            return "filled" if ok else "need:the upload didn't complete"
        cur = c.value if isinstance(c.value, list) else ([c.value] if c.value not in (None, "") else [])
        if [norm(x) for x in cur] == [norm(x) for x in res.values]:
            self._used(q, res.values, p)
            return "kept"
        if c.control in ("select", "radio", "checkbox", "combobox") or c.options:
            ok = self.ex.choose(c, res.values)
        else:
            ok = self.ex.fill_text(c, res.text)
        self._used(q, res.values, p)
        return "filled" if ok else "need:the control wouldn't take the answer"

    def _used(self, q, values, p):
        self.answers_used.append({"label": q.label, "value": values if p.basis != "material" else "[file]", "basis": p.basis,
                                  "evidence": p.evidence, "key": p.key})

    def collect_needs(self, obs: Observation, register=False) -> list:
        needs = []
        for c in obs.controls:
            if c.control in ("password", "search") or not c.required or not c.empty():
                continue
            q = c.question(self.job)
            res = self.engine.answer(q, self.ctx())
            reason = getattr(res, "reason", "") or "required and still empty"
            key = getattr(res, "key", "") or self.engine.key_for(q)
            needs.append({"question": q.to_dict(), "key": key, "reason": reason})
        return needs

    def park_needs(self, needs):
        for n in needs:
            from .questions import Question
            self.engine.record_pending(Question.from_dict(n["question"]), self.job["id"], self.app["id"], n["key"], n["reason"])
        labels = "; ".join(n["question"]["label"][:60] for n in needs[:6])
        raise Park(M.NEEDS_INFO, "missing_fact", labels, needs)

    def fill_and_advance(self, obs: Observation, adapter) -> bool:
        """Fill this page. Returns True when the application reached a final outcome (via submission)."""
        results = {}
        for rnd in range(3):                       # conditional questions appear after earlier answers
            changed = False
            for c in obs.controls:
                sig = (c.label, c.control, c.context)
                if sig in results and not results[sig].startswith("need"):
                    continue
                r = self.fill_one(c, obs)
                results[sig] = r
                changed |= r == "filled"
            adapter.fix_page(obs, self)
            new = observe(self.page)
            if not changed or new.fingerprint() == obs.fingerprint():
                obs = new
                break
            obs = new
        needs = self.collect_needs(obs)
        failed = [k for k, v in results.items() if v.startswith("need:the control") or v.startswith("need:the upload")]
        if needs:
            self.park_needs(needs)
        if failed:
            raise Park(M.FAILED, "changed_form", "some answers didn't stick: " + "; ".join(k[0][:50] for k in failed[:5]))
        self.form_filled = True
        nxt = adapter.next_button(obs, self)
        fin = adapter.final_button(obs, self)
        if fin and not nxt:
            from .submission import submit
            submit(self, obs, fin, adapter)
            return True
        if not nxt:
            raise Park(M.FAILED, "unsupported", "no Next or Submit button on this page")
        before = obs.signature()
        self.ex.click(nxt)
        after = self.ex.wait_change(obs)
        if after.signature() == before and after.errors:
            needs = self.collect_needs(after)
            if needs:
                self.park_needs(needs)
            raise Park(M.NEEDS_INFO, "no_progress", "the form flagged: " + "; ".join(after.errors[:4]),
                       [{"blocker": "form_errors", "errors": after.errors[:6]}])
        return False
