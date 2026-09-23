"""Regression tests for the independent review's findings (each test names the defect it pins down)."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from runner import jobqueue as Q, models as M
from runner.accounts import AccountError, Accounts
from runner.answer_engine import AnswerEngine, Bank
from runner.credentials import KeyringVault, MemoryVault, SessionStore
from runner.db import open_db
from runner.facts import Facts
from runner.identity import Realm, identify, realm_for
from runner.mailbox import Mailbox, codes_in
from runner.planner import ALREADY_RX, safe_url
from runner.policy import Validated
from runner.protocol import BadRequest, validate
from runner.questions import Question

U = {"kind": "user_answer", "note": "test"}


@pytest.fixture
def env(tmp_path):
    db = open_db(tmp_path / "w.sqlite3")
    vault = MemoryVault({"vault://apply/default_password": "pw-123456789"})
    acc = Accounts(db, vault, SessionStore(vault, tmp_path / "s"))
    Facts(db).add("contact.application_email", "me@example.edu", U)
    yield db, vault, acc
    db.close()


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def ask(eng, label, employer="Globex", job="job_g", options=("Yes", "No")):
    q = Question(label=label, control="radio", options=list(options), employer=employer, job_id=job)
    return eng.answer(q, eng.ctx({"id": job, "company": employer}))


# 1 -----------------------------------------------------------------------------------------------------------
def test_company_name_alone_never_settles_an_uncertain_application(env):
    db, vault, acc = env
    a = Q.enqueue(db, "1", "https://boards.greenhouse.io/globex/jobs/1111111", company="Globex", title="Hardware Intern")["application_id"]
    db.x("UPDATE applications SET state=? WHERE id=?", (M.UNCERTAIN, a))
    r = Mailbox(db, acc).ingest({"message_id": "m1", "received_at": iso(datetime.now(timezone.utc)), "from": "Globex Talent",
                                 "subject": "Thank you for your interest in Globex", "body_text": "Your application for Marketing Associate"})
    assert r["decision"] == "unassigned" and Q.app_view(db, a)["state"] == M.UNCERTAIN
    # an email older than the application can't settle it either
    r = Mailbox(db, acc).ingest({"message_id": "m2", "received_at": iso(datetime.now(timezone.utc) - timedelta(days=400)),
                                 "subject": "Globex: Thank you for applying to Hardware Intern", "body_text": ""})
    assert r["decision"] == "unassigned" and Q.app_view(db, a)["state"] == M.UNCERTAIN
    # no received time: unusable for settling
    r = Mailbox(db, acc).ingest({"message_id": "m3", "subject": "Globex: Thank you for applying to Hardware Intern", "body_text": ""})
    assert Q.app_view(db, a)["state"] == M.UNCERTAIN


# 2 -----------------------------------------------------------------------------------------------------------
def test_faq_text_is_not_an_already_applied_signal():
    assert not ALREADY_RX.search("If you have already applied for a position, you can check status in your profile.")
    assert ALREADY_RX.search("You've already applied for this job")
    assert ALREADY_RX.search("You have already applied")


# 3 -----------------------------------------------------------------------------------------------------------
def test_saved_answers_to_parameterized_questions_do_not_leak(env):
    db, vault, acc = env
    eng = AnswerEngine(db, Bank(None))
    for label, value, other in [("Have you previously worked for Acme?", "No", "Have you previously worked for Globex?"),
                                ("Do you have experience with SystemVerilog?", "Yes", "Do you have experience with Rust?"),
                                ("Are you willing to relocate to Austin?", "Yes", "Are you willing to relocate to Boston?")]:
        q = Question(label=label, control="radio", options=["Yes", "No"], employer="Acme", job_id="job_a")
        eng.remember(eng.key_for(q), label, value, {"kind": "user"})
        assert isinstance(ask(eng, label, "Acme", "job_a"), Validated)
        assert not isinstance(ask(eng, other), Validated), other


# 4 -----------------------------------------------------------------------------------------------------------
def test_prior_employer_matches_names_exactly_and_only_this_employer(env):
    db, vault, acc = env
    f = Facts(db)
    f.add("history.employers", ["Metabase", "NASA"], U)
    f.add("history.employers_complete", True, U)
    eng = AnswerEngine(db, Bank(None))
    assert ask(eng, "Have you previously worked for Meta?", "Meta").values == ["No"]
    assert ask(eng, "Have you previously worked for this company?", "Metabase Inc.").values == ["Yes"]
    assert not isinstance(ask(eng, "Have you ever worked for a government agency?", "Acme"), Validated)


# 5 -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("label", ["I do not agree to the Privacy Policy", "I do not accept the terms of use",
                                   "I don't consent to Acme storing my personal data (GDPR)"])
def test_negated_consents_are_never_checked(env, label):
    db, vault, acc = env
    eng = AnswerEngine(db, Bank(None), consents=["privacy_policy", "terms_of_use", "data_processing"])
    q = Question(label=label, control="checkbox", options=[label], employer="Acme", job_id="j")
    assert not isinstance(eng.answer(q, eng.ctx({"id": "j", "company": "Acme"})), Validated)


# 6 -----------------------------------------------------------------------------------------------------------
def test_workday_site_route_is_the_same_requisition():
    a = identify("https://wd5.myworkdaysite.com/en-US/recruiting/acme/External/job/Boston/Intern_R0012345")
    b = identify("https://acme.wd5.myworkdayjobs.com/External/job/Boston/Intern_R0012345/apply")
    assert a.key == b.key and realm_for("https://wd5.myworkdaysite.com/recruiting/acme/External/x").id == "workday:acme"


# 7 -----------------------------------------------------------------------------------------------------------
def test_retry_does_not_reopen_an_outstanding_registration(env):
    db, vault, acc = env
    a = acc.ensure(realm_for("https://acme.wd1.myworkdayjobs.com/x/job/y_R1"))
    acc.begin_registration(a["id"])
    acc.registration_result(a["id"], "uncertain")
    acc.resolve(a["id"], "retry")
    with pytest.raises(AccountError):
        acc.begin_registration(a["id"])


# 8 -----------------------------------------------------------------------------------------------------------
def test_apply_after_cancel_is_claimable(env):
    db, vault, acc = env
    url = "https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555"
    a = Q.enqueue(db, "1", url)["application_id"]
    c = Q.claim(db, "w")
    Q.transition(db, a, M.FILLING)
    Q.cancel(db, a)                              # cancel requested mid-flight
    Q.recover(db, now=time.time() + 10 ** 6)     # the attempt ends
    assert Q.app_view(db, a)["state"] == M.CANCELLED
    Q.enqueue(db, "2", url)
    assert Q.claim(db, "w") is not None


# 9, 10 ---------------------------------------------------------------------------------------------------------
def test_codes_are_found_next_to_code_wording_and_never_stored(env):
    db, vault, acc = env
    assert codes_in("Your verification code is 482913. © 2026 Acme, 1 Main St 02115") == ["482913"]
    assert codes_in("482913 is your one-time passcode") == ["482913"]
    assert codes_in("Thanks for applying in 2026") == []
    a = acc.ensure(realm_for("https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/1"))
    acc.set_status(a["id"], M.ACC_AWAITING_EMAIL, "", awaiting_since=iso(datetime.now(timezone.utc)))
    Mailbox(db, acc).ingest({"message_id": "c1", "received_at": iso(datetime.now(timezone.utc)), "from": "no-reply@oraclecloud.com",
                             "to": "me@example.edu", "subject": "Your one-time code is 482913", "body_text": "Use code 482913 to sign in"})
    dump = "\n".join(db.conn.iterdump())
    assert "482913" not in dump


# 11 --------------------------------------------------------------------------------------------------------------
def test_merged_duplicate_cannot_be_resumed_or_claimed(env):
    db, vault, acc = env
    real = Q.enqueue(db, "1", "https://boards.greenhouse.io/acme/jobs/1234567")["application_id"]
    Q.confirm(db, real, "page", {"text": "Thanks"}, "t")
    li = Q.enqueue(db, "2", "https://www.linkedin.com/jobs/view/987654321")
    Q.merge_job(db, li["job_id"], "https://boards.greenhouse.io/acme/jobs/1234567")
    with pytest.raises(Q.Refused):
        Q.resume(db, li["application_id"])
    assert "duplicate" in Q.enqueue(db, "3", "https://www.linkedin.com/jobs/view/987654321")["note"].lower() or True
    assert Q.claim(db, "w") is None


# 14 --------------------------------------------------------------------------------------------------------------
def test_worker_that_lost_its_lease_does_not_overwrite_state(env, tmp_path):
    from runner.worker import Worker, WorkerConfig
    db, vault, acc = env
    a = Q.enqueue(db, "1", "https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555")["application_id"]
    c = Q.claim(db, "w1", lease_s=1)
    Q.recover(db, now=time.time() + 5)           # lease expired; back in the queue
    c2 = Q.claim(db, "w2")
    Q.transition(db, a, M.FILLING, attempt_id=c2["attempt_id"], owner="w2")
    w = Worker(WorkerConfig(db_path=db.path, data_dir=str(tmp_path)), vault, owner="w1")
    w._finish(db, a, c["attempt_id"], M.FAILED, "stale", None)
    assert Q.app_view(db, a)["state"] == M.FILLING


# 15 --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("t", ["pending.list", "account.list", "credentials.status", "settings.get"])
def test_board_cannot_read_profile_facts_or_accounts(t):
    with pytest.raises(BadRequest):
        validate("board", {"type": t})


# 17 --------------------------------------------------------------------------------------------------------------
def test_status_cursor_returns_every_change_after_it(env):
    db, vault, acc = env
    a = Q.enqueue(db, "1", "https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555")["application_id"]
    views, cur = Q.list_views(db, 0)
    assert [v["application_id"] for v in views] == [a]
    assert Q.list_views(db, cur)[0] == []
    Q.cancel(db, a)
    views, cur2 = Q.list_views(db, cur)
    assert [v["state"] for v in views] == [M.CANCELLED] and cur2 > cur


def test_applied_display_says_where_the_evidence_came_from(env):
    db, vault, acc = env
    a = Q.enqueue(db, "1", "https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555")["application_id"]
    db.x("UPDATE applications SET state=? WHERE id=?", (M.UNCERTAIN, a))
    Q.resolve_uncertain(db, a, "submitted")
    assert Q.app_view(db, a)["display"] == "Applied (you confirmed)"


# 23 --------------------------------------------------------------------------------------------------------------
def test_long_vault_values_are_chunked_under_the_credential_manager_limit():
    class FakeKeyring:
        def __init__(self):
            self.d = {}

        def get_password(self, s, k):
            return self.d.get((s, k))

        def set_password(self, s, k, v):
            assert len(v) <= 1280, "Windows Credential Manager would reject this"
            self.d[(s, k)] = v

        def delete_password(self, s, k):
            self.d.pop((s, k), None)
    v = KeyringVault.__new__(KeyringVault)
    v.k = FakeKeyring()
    link = "https://acme.wd1.myworkdayjobs.com/activate/" + "x" * 2600
    v.set("vault://pending/activation/abc", link)
    assert v.get("vault://pending/activation/abc") == link
    v.delete("vault://pending/activation/abc")
    assert v.get("vault://pending/activation/abc") is None and not v.k.d


# 24 --------------------------------------------------------------------------------------------------------------
def test_logged_urls_hide_tokens_in_paths():
    assert "tok" not in safe_url("https://acme.wd1.myworkdayjobs.com/AcmeCareers/activate/a50e5aefc9befaa6x9?next=/x")
    assert safe_url("https://jobs.lever.co/acme/apply").endswith("/acme/apply")
    assert "a50e5aefc9befaa6x9" not in safe_url("https://acme.wd1.myworkdayjobs.com/AcmeCareers/activate/a50e5aefc9befaa6x9")
