"""Stages C+E in the browser: an unfamiliar portal that needs an account, email verification and a multi-step form."""
import time

from runner import jobqueue as Q, models as M

from .conftest import EMAIL, PASSWORD
from .portals.generic import GenericPortal


def events_text(h):
    return " ".join(r["data_json"] for r in h.db.all("SELECT data_json FROM events"))


def test_first_signup_verification_email_login_and_multistep_submit(h):
    portal = GenericPortal(h.fx)
    h.start_outlook_bridge()
    app = h.enqueue(portal.url(42), "AcmeCo", "Hardware Validation Intern")["application_id"]
    # An unfamiliar site: the worker stops before typing your password there until you approve the host once.
    assert h.run() == M.NEEDS_HUMAN and "credential_destination" in h.view(app)["reason"]
    assert portal.registrations == 0 and not portal.logins
    acct = h.db.one("SELECT id FROM accounts")["id"]
    h.accounts.resolve(acct, "approve_host")
    Q.resume(h.db, app)
    assert h.run() == M.APPLIED, h.view(app)
    acct = portal.accounts[EMAIL]
    assert portal.registrations == 1 and acct["verified"] and acct["password"] == PASSWORD
    assert acct["privacy"] is True and acct["marketing"] is False and acct["country"] == "United States"
    (_, job, d), = h.fx.submissions
    assert d["sponsor"] == "No" and d["avail"] == "Yes" and d["grad"] == "12/2027" and d["state"] == "Massachusetts"
    assert d["resume"] == "Test_Applicant_Resume.pdf" and d["notes"] == ""      # a "password" text box never gets the password
    assert "AC-" in h.view(app)["receipt"]["text"]
    # secrets never reach events or the database
    assert PASSWORD not in events_text(h)
    assert "token=" not in events_text(h)
    dump = "\n".join(h.db.conn.iterdump())
    assert PASSWORD not in dump


def test_second_job_at_the_same_employer_reuses_the_account_and_session(h):
    portal = GenericPortal(h.fx)
    h.approve("careers.acmeco.com")
    h.start_outlook_bridge()
    h.enqueue(portal.url(42), "AcmeCo")
    assert h.run() == M.APPLIED
    logins = len(portal.logins)
    app2 = h.enqueue(portal.url(43), "AcmeCo")["application_id"]
    assert h.run() == M.APPLIED, h.view(app2)
    assert portal.registrations == 1
    assert len(portal.logins) == logins                   # restored encrypted session, no new sign-in


def test_existing_account_with_a_different_password_stops_after_one_try(h):
    portal = GenericPortal(h.fx)
    h.approve("careers.acmeco.com")
    portal.accounts[EMAIL] = {"password": "someone-elses-password!", "verified": True, "token": "", "first": "T", "last": "A"}
    app = h.enqueue(portal.url(42), "AcmeCo")["application_id"]
    assert h.run() == M.NEEDS_HUMAN
    assert "existing_account" in h.view(app)["reason"]
    assert portal.registrations == 0 and portal.logins.count(EMAIL) == 1
    acct = h.db.one("SELECT status FROM accounts")
    assert acct["status"] == M.ACC_NEEDS_CREDENTIALS


def test_password_policy_rejection_is_an_account_exception_not_a_changed_password(h):
    portal = GenericPortal(h.fx, min_password=40)
    h.approve("careers.acmeco.com")
    app = h.enqueue(portal.url(42), "AcmeCo")["application_id"]
    assert h.run() == M.NEEDS_HUMAN
    assert "password_policy" in h.view(app)["reason"]
    assert portal.registrations == 0
    assert h.vault.get("vault://apply/default_password") == PASSWORD


def test_no_mailbox_leaves_it_awaiting_email_and_it_resumes_when_the_email_arrives(h):
    portal = GenericPortal(h.fx)
    h.approve("careers.acmeco.com")
    h.cfg.run.verify_wait_s = 1
    app = h.enqueue(portal.url(42), "AcmeCo")["application_id"]
    assert h.run() == M.AWAITING_EMAIL
    assert h.view(app)["display"] == "Needs verification" and not h.fx.submissions
    # Outlook opens later: the waiting account is verified and the application is re-queued automatically.
    h.start_outlook_bridge()
    for _ in range(50):
        if h.view(app)["state"] == M.QUEUED:
            break
        time.sleep(0.1)
    assert h.view(app)["state"] == M.QUEUED
    h.cfg.run.verify_wait_s = 20
    assert h.run() == M.APPLIED
    assert portal.registrations == 1                      # no second signup on resume


def test_outstanding_registration_after_a_crash_is_not_repeated(h):
    portal = GenericPortal(h.fx)
    app = h.enqueue(portal.url(42), "AcmeCo")["application_id"]
    # simulate: an earlier attempt pressed Create account and then the worker died
    from runner.identity import Realm
    h.approve("careers.acmeco.com")
    h.cfg.run.verify_wait_s = 1
    a = h.accounts.ensure(Realm("site:careers.acmeco.com", "site", "careers.acmeco.com", ()))
    h.accounts.begin_registration(a["id"])
    assert h.run() == M.NEEDS_HUMAN
    assert "registration_uncertain" in h.view(app)["reason"] and portal.registrations == 0


def test_login_on_an_unverified_host_never_receives_the_password(h):
    portal = GenericPortal(h.fx, idp_redirect=True)
    app = h.enqueue(portal.url(42), "AcmeCo")["application_id"]
    assert h.run() == M.NEEDS_HUMAN
    assert "credential_destination" in h.view(app)["reason"]
    assert not any(s[0] == "STOLEN" for s in h.fx.submissions)
    # you approve the host once; it's remembered for that realm only


def test_page_text_instructions_change_nothing(h):
    portal = GenericPortal(h.fx, hostile=True)
    h.approve("careers.acmeco.com")
    h.start_outlook_bridge()
    h.enqueue(portal.url(42), "AcmeCo")
    assert h.run() == M.APPLIED
    assert len(h.fx.submissions) == 1 and h.fx.submissions[0][2]["notes"] == ""
