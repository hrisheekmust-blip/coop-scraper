"""Stage C: accounts, credentials, sessions, and verification mail correlation."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from runner import jobqueue as Q, models as M
from runner.accounts import Accounts, AccountError, MAX_FAILED_LOGINS
from runner.credentials import MemoryVault, SessionStore
from runner.db import open_db
from runner.facts import Facts
from runner.identity import realm_for
from runner.mailbox import Mailbox, purpose_of

WD = "https://skyworks.wd1.myworkdayjobs.com/Skyworks/job/Irvine/Co-Op_R12345"
EMAIL = "mustyala.h@northeastern.edu"


@pytest.fixture
def env(tmp_path):
    db = open_db(tmp_path / "w.sqlite3")
    vault = MemoryVault({"vault://apply/default_password": "S3cret-Pass!word", "vault://apply/default_username": "hrisheek.m"})
    acc = Accounts(db, vault, SessionStore(vault, tmp_path / "sessions"))
    Facts(db).add("contact.application_email", EMAIL, {"kind": "test"})
    yield db, vault, acc
    db.close()


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_one_account_per_realm_and_credential_host_check(env):
    db, vault, acc = env
    a = acc.ensure(realm_for(WD))
    b = acc.ensure(realm_for("https://skyworks.wd1.myworkdayjobs.com/Other/job/X/Y_R9"))
    assert a["id"] == b["id"]
    assert acc.may_type_credentials(a["id"], "https://skyworks.wd1.myworkdayjobs.com/Skyworks/login")
    assert not acc.may_type_credentials(a["id"], "https://skyworks-careers.example.com/login")
    other = acc.ensure(realm_for("https://nxp.wd3.myworkdayjobs.com/careers/job/X/Y_R1"))
    assert other["id"] != a["id"]


def test_email_login_uses_application_email_and_username_is_a_ref(env):
    db, vault, acc = env
    a = acc.ensure(realm_for(WD))
    assert acc.username_for(a["id"], email_login=True) == ("plain", EMAIL)
    assert acc.username_for(a["id"], email_login=False) == ("ref", "vault://apply/default_username")
    assert acc.password_ref(a["id"]) == "vault://apply/default_password"


def test_missing_password_is_an_explicit_exception(tmp_path):
    db = open_db(tmp_path / "w.sqlite3")
    acc = Accounts(db, MemoryVault(), SessionStore(MemoryVault(), tmp_path / "s"))
    a = acc.ensure(realm_for(WD))
    with pytest.raises(AccountError) as e:
        acc.password_ref(a["id"])
    assert e.value.code == "needs_credentials"


def test_login_failures_stop_before_lockout(env):
    db, vault, acc = env
    a = acc.ensure(realm_for(WD))
    for _ in range(MAX_FAILED_LOGINS):
        assert acc.can_try_login(a["id"])
        acc.login_failed(a["id"])
    assert not acc.can_try_login(a["id"])
    assert acc.row(a["id"])["status"] == M.ACC_NEEDS_CREDENTIALS
    acc.resolve(a["id"], "set_password_ref", ref="vault://apply/skyworks_password")
    assert acc.can_try_login(a["id"])


def test_registration_intent_blocks_a_second_signup_until_checked(env):
    db, vault, acc = env
    a = acc.ensure(realm_for(WD))
    acc.begin_registration(a["id"])
    with pytest.raises(AccountError) as e:
        acc.begin_registration(a["id"])
    assert e.value.code == "registration_outstanding"
    acc.registration_result(a["id"], "uncertain")
    with pytest.raises(AccountError):
        acc.begin_registration(a["id"])
    acc.resolve(a["id"], "registration_not_created")
    acc.begin_registration(a["id"])


def test_password_policy_rejection_doesnt_change_credentials(env):
    db, vault, acc = env
    a = acc.ensure(realm_for(WD))
    acc.begin_registration(a["id"])
    acc.registration_result(a["id"], "password_policy", "needs a special character from a set your password lacks")
    row = acc.row(a["id"])
    assert row["status"] == M.ACC_NEEDS_CREDENTIALS and row["registration_intent_at"] is None
    assert vault.get("vault://apply/default_password") == "S3cret-Pass!word"


def test_sessions_are_encrypted_at_rest(env, tmp_path):
    db, vault, acc = env
    a = acc.ensure(realm_for(WD))
    acc.save_session(a["id"], {"cookies": [{"name": "wd-session", "value": "COOKIEVALUE123"}]})
    files = list((tmp_path / "sessions").glob("*.bin"))
    assert files and b"COOKIEVALUE123" not in files[0].read_bytes()
    assert acc.load_session(a["id"])["cookies"][0]["value"] == "COOKIEVALUE123"


def waiting_account(acc):
    a = acc.ensure(realm_for(WD))
    acc.begin_registration(a["id"])
    acc.registration_result(a["id"], "awaiting_email")
    return a


def msg(**kw):
    base = {"message_id": "m1", "received_at": iso(datetime.now(timezone.utc)), "from": "Skyworks <skyworks@myworkday.com>",
            "to": EMAIL, "subject": "Verify your candidate account", "body_text": "Please verify your email address.",
            "links": ["https://skyworks.wd1.myworkdayjobs.com/Skyworks/activate/abc123token"]}
    base.update(kw)
    return base


def test_verification_link_resolves_the_waiting_account_once(env):
    db, vault, acc = env
    a = waiting_account(acc)
    app = Q.enqueue(db, "1", WD)["application_id"]
    Q.claim(db, "w")
    Q.transition(db, app, M.AUTHENTICATING)
    Q.transition(db, app, M.AWAITING_EMAIL)
    db.x("UPDATE attempts SET ended_at='x'")
    mb = Mailbox(db, acc)
    r = mb.ingest(msg())
    assert r["decision"] == "consumed" and r["account_id"] == a["id"]
    ref = acc.take_verification(a["id"])
    assert vault.get(ref).endswith("abc123token")
    assert acc.take_verification(a["id"]) is None                 # single use
    assert Q.app_view(db, app)["state"] == M.QUEUED               # parked application resumes
    assert mb.ingest(msg())["replayed"]                            # same message never processed twice
    events = " ".join(r["data_json"] for r in db.all("SELECT data_json FROM events"))
    assert "abc123token" not in events


@pytest.mark.parametrize("change", [
    {"links": ["https://evil.example/activate/abc"]},                                 # link to an unapproved host
    {"to": "someone.else@gmail.com"},                                                  # different recipient
    {"from": "Random <news@marketing.example>"},                                       # unexpected sender
    {"received_at": iso(datetime.now(timezone.utc) - timedelta(days=5))},              # sent before registration
    {"subject": "Reset your password", "body_text": "Click to reset your password"},  # resets are never acted on
    {"subject": "Jobs you may like", "body_text": "New jobs matching your profile"},   # recommendation
])
def test_hostile_or_unrelated_mail_never_activates_an_account(env, change):
    db, vault, acc = env
    a = waiting_account(acc)
    r = Mailbox(db, acc).ingest(msg(**change))
    assert r["decision"] != "consumed"
    assert acc.take_verification(a["id"]) is None


def test_purposes():
    assert purpose_of("Verify your candidate account", "") == "activation"
    assert purpose_of("Your one-time code", "Your verification code is 482913") == "login_code"
    assert purpose_of("Thank you for applying to Acme", "Next steps: our team will review") == "confirmation"
    assert purpose_of("New jobs for you", "Thank you for your interest") == "recommendation"
    assert purpose_of("Your application", "Unfortunately we will not be moving forward") == "rejection"


def test_outcome_email_matches_one_role_and_settles_uncertain(env):
    db, vault, acc = env
    a1 = Q.enqueue(db, "1", "https://boards.greenhouse.io/acme/jobs/111111", company="Acme Corp", title="Analog Design Co-op")["application_id"]
    a2 = Q.enqueue(db, "2", "https://boards.greenhouse.io/acme/jobs/222222", company="Acme Corp", title="Test Engineering Co-op")["application_id"]
    db.x("UPDATE applications SET state=? WHERE id=?", (M.UNCERTAIN, a1))
    db.x("UPDATE applications SET state=? WHERE id=?", (M.APPLIED, a2))
    mb = Mailbox(db, acc)
    # Ambiguous: names neither role.
    r = mb.ingest(msg(message_id="x1", from_="", subject="Acme Corp update", body_text="Unfortunately we will not be moving forward", links=[]))
    assert r["decision"] == "unassigned"
    assert Q.app_view(db, a1)["state"] == M.UNCERTAIN
    r = mb.ingest(msg(message_id="x2", subject="Acme Corp: Thank you for applying to Analog Design Co-op", body_text="", links=[]))
    assert r["decision"] == "consumed" and r["application_id"] == a1
    assert Q.app_view(db, a1)["state"] == M.APPLIED
    assert Q.app_view(db, a1)["receipt"]["kind"] == "email"
    # An older email can't overwrite a newer outcome.
    now = datetime.now(timezone.utc)
    mb.ingest(msg(message_id="x3", received_at=iso(now), subject="Acme Corp Test Engineering Co-op interview invitation",
                  body_text="We'd like to invite you to a phone screen", links=[]))
    mb.ingest(msg(message_id="x4", received_at=iso(now - timedelta(days=1)), subject="Acme Corp Test Engineering Co-op",
                  body_text="Unfortunately we will not be moving forward", links=[]))
    assert db.one("SELECT employer_status FROM applications WHERE id=?", (a2,))["employer_status"] == "interview"
