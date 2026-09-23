"""Stage B: durable core. Double clicks, aliases, crashes and restarts must never duplicate a submission."""
import threading
import time

import pytest

from runner import jobqueue as Q, models as M
from runner.db import open_db
from runner.identity import identify, normalize_url, realm_for, host_allowed

GH = "https://boards.greenhouse.io/lightmatter/jobs/5374627008?gh_jid=5374627008"
GH2 = "https://job-boards.greenhouse.io/lightmatter/jobs/5374627008"
LI = "https://www.linkedin.com/jobs/view/4471100416/?trk=abc"
WD = "https://skyworks.wd1.myworkdayjobs.com/en-US/Skyworks/job/Irvine-CA/Process-Engineering-Co-Op_R12345/apply/applyManually"
WD2 = "https://skyworks.wd1.myworkdayjobs.com/Skyworks/job/Irvine-CA/Process-Engineering-Co-Op_R12345"


@pytest.fixture
def db(tmp_path):
    d = open_db(tmp_path / "w.sqlite3", tmp_path / "bk")
    yield d
    d.close()


def test_identity_rules():
    assert identify(GH).key == identify(GH2).key == "greenhouse:lightmatter:5374627008"
    assert identify(WD).key == identify(WD2).key == "workday:skyworks:R12345"
    sf = identify("https://career5.successfactors.eu/career?company=skyworksP&career_job_req_id=78495&utm_source=x")
    assert sf.key == "successfactors:skyworksp:78495" and not sf.provisional
    assert identify("https://careers.skyworksinc.com/job/Semiconductor-Co-Op/78495-en_US/").provisional
    assert identify("https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/12345/apply").key == "oracle:acme:12345"
    assert identify("https://careers-acme.icims.com/jobs/4321/test-engineer/job?in_iframe=1").key == "icims:careers-acme:4321"
    assert identify(LI).provisional and identify(LI).requisition == "4471100416"
    # Query parameters are not dropped generically: they can be the requisition.
    assert normalize_url("https://x.com/job?id=5&utm_source=li") == "https://x.com/job?id=5"
    assert identify("https://x.com/careers?id=5").key != identify("https://x.com/careers?id=6").key


def test_realms_and_credential_hosts():
    r = realm_for(WD)
    assert r.id == "workday:skyworks" and r.auth_method == "password"
    assert host_allowed(r.allowed_hosts, "https://skyworks.wd1.myworkdayjobs.com/login")
    assert not host_allowed(r.allowed_hosts, "https://evil.example/skyworks.wd1.myworkdayjobs.com")
    assert not host_allowed(r.allowed_hosts, "http://skyworks.wd1.myworkdayjobs.com/login")
    # Two tenants on the same ATS never share a realm.
    assert realm_for("https://nxp.wd3.myworkdayjobs.com/careers/job/x_R1").id != realm_for("https://skyworks.wd3.myworkdayjobs.com/x/job/y_R1").id
    assert realm_for(GH).auth_method == "guest"
    assert realm_for("http://skyworks.wd1.myworkdayjobs.com/x") is None


def test_migration_backup_and_version(tmp_path):
    d = open_db(tmp_path / "a.sqlite3", tmp_path / "bk")
    assert d.version() >= 1
    d.x("INSERT INTO settings VALUES('k','1')")
    d.close()
    d2 = open_db(tmp_path / "a.sqlite3", tmp_path / "bk")   # re-open: no re-run, data kept
    assert d2.setting("k") == 1


def test_enqueue_replay_and_alias_dedupe(db):
    a = Q.enqueue(db, "req-1", GH, board_ref="b1", company="Lightmatter", title="Photonics Intern")
    replay = Q.enqueue(db, "req-1", GH)
    assert replay["application_id"] == a["application_id"] and replay["replayed"]
    other = Q.enqueue(db, "req-2", GH2, board_ref="b2")
    assert other["application_id"] == a["application_id"] and other["note"] == "Already in progress"
    assert db.one("SELECT COUNT(*) n FROM applications")["n"] == 1
    assert db.one("SELECT COUNT(*) n FROM job_aliases WHERE job_id=?", (a["job_id"],))["n"] >= 3


def test_double_click_from_two_threads_makes_one_application(tmp_path):
    path = tmp_path / "t.sqlite3"
    open_db(path).close()
    results = []

    def click(i):
        d = open_db(path)
        results.append(Q.enqueue(d, f"click-{i}", GH)["application_id"])
        d.close()

    ts = [threading.Thread(target=click, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(results)) == 1


def test_provisional_linkedin_merges_into_real_job(db):
    li = Q.enqueue(db, "r1", LI, company="Lightmatter")
    real = Q.merge_job(db, li["job_id"], GH)
    assert Q.app_view(db, li["application_id"])["job_id"] == real
    again = Q.enqueue(db, "r2", GH2)
    assert again["application_id"] == li["application_id"]
    assert Q.enqueue(db, "r3", LI)["application_id"] == li["application_id"]


def test_merge_keeps_submitted_application_and_cancels_duplicate(db):
    real = Q.enqueue(db, "r1", GH)
    Q.confirm(db, real["application_id"], "page", {"text": "Thank you"}, "test")
    li = Q.enqueue(db, "r2", LI)
    assert li["application_id"] != real["application_id"]
    Q.merge_job(db, li["job_id"], GH)
    assert Q.app_view(db, real["application_id"])["state"] == M.APPLIED
    assert Q.app_view(db, li["application_id"])["state"] == M.CANCELLED


def test_claim_serializes_realms_and_caps_concurrency(db):
    a = Q.enqueue(db, "1", WD)
    b = Q.enqueue(db, "2", "https://skyworks.wd1.myworkdayjobs.com/Skyworks/job/X/Other_R999")
    c = Q.enqueue(db, "3", GH)
    first = Q.claim(db, "w1")
    second = Q.claim(db, "w1")
    assert first and second
    ids = {first["application"]["id"], second["application"]["id"]}
    assert ids == {a["application_id"], c["application_id"]}      # the second Skyworks job waits for the realm
    assert Q.claim(db, "w1", max_live=3) is None


def run_to_ready(db, app_id, att, owner="w1"):
    for s in (M.FILLING, M.VALIDATING, M.READY):
        Q.transition(db, app_id, s, attempt_id=att, owner=owner)


def test_crash_before_submit_requeues_and_after_submit_becomes_uncertain(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    c = Q.claim(db, "w1", lease_s=1)
    run_to_ready(db, a, c["attempt_id"])
    Q.recover(db, now=time.time() + 5)
    assert Q.app_view(db, a)["state"] == M.QUEUED
    c2 = Q.claim(db, "w2", lease_s=1)
    run_to_ready(db, a, c2["attempt_id"], "w2")
    Q.submit_intent(db, a, c2["attempt_id"], "w2", {"form": "fp"})
    Q.recover(db, now=time.time() + 5)
    assert Q.app_view(db, a)["state"] == M.UNCERTAIN
    # Not claimable, and Apply again doesn't requeue it.
    assert Q.claim(db, "w3") is None
    assert "uncertain" in Q.enqueue(db, "again", GH)["note"].lower()


def test_submit_intent_refused_when_an_earlier_attempt_may_have_submitted(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    c = Q.claim(db, "w1", lease_s=1)
    run_to_ready(db, a, c["attempt_id"])
    Q.submit_intent(db, a, c["attempt_id"], "w1", {})
    Q.recover(db, now=time.time() + 5)
    # Force a malicious path: put it back to READY in a new attempt without resolving.
    db.x("UPDATE applications SET state=? WHERE id=?", (M.QUEUED, a))
    c2 = Q.claim(db, "w2")
    run_to_ready(db, a, c2["attempt_id"], "w2")
    with pytest.raises(Q.Refused):
        Q.submit_intent(db, a, c2["attempt_id"], "w2", {})


def test_resolve_uncertain_not_submitted_allows_exactly_one_new_attempt(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    c = Q.claim(db, "w1", lease_s=1)
    run_to_ready(db, a, c["attempt_id"])
    Q.submit_intent(db, a, c["attempt_id"], "w1", {})
    Q.recover(db, now=time.time() + 5)
    Q.resolve_uncertain(db, a, "not_submitted")
    c2 = Q.claim(db, "w2")
    run_to_ready(db, a, c2["attempt_id"], "w2")
    Q.submit_intent(db, a, c2["attempt_id"], "w2", {})           # allowed now
    with pytest.raises(Q.Refused):
        Q.submit_intent(db, a, c2["attempt_id"], "w2", {})


def test_stale_lease_owner_cannot_act(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    c = Q.claim(db, "w1", lease_s=1)
    Q.recover(db, now=time.time() + 5)
    assert not Q.heartbeat(db, c["attempt_id"], "w1")
    with pytest.raises(Q.Refused):
        Q.transition(db, a, M.FILLING, attempt_id=c["attempt_id"], owner="w1")


def test_cancel_semantics(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    assert Q.cancel(db, a)["state"] == M.CANCELLED
    b = Q.enqueue(db, "2", WD)["application_id"]
    c = Q.claim(db, "w1")
    run_to_ready(db, b, c["attempt_id"])
    Q.cancel(db, b)
    with pytest.raises(Q.Refused):
        Q.submit_intent(db, b, c["attempt_id"], "w1", {})
    msg = Q.cancel(db, b)["message"]
    assert "before submit" in msg


def test_confirmation_is_idempotent_and_applied_is_final(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    Q.confirm(db, a, "page", {"text": "x"}, "t")
    Q.confirm(db, a, "email", {"text": "y"}, "t")
    assert db.one("SELECT COUNT(*) n FROM confirmations")["n"] == 1
    assert Q.enqueue(db, "2", GH)["note"] == "Already applied"
    with pytest.raises(Q.Refused):
        Q.transition(db, a, M.QUEUED)


def test_retry_backoff_then_failed(db):
    a = Q.enqueue(db, "1", GH)["application_id"]
    for i in range(4):
        Q.claim(db, "w", now=time.time() + 10 ** 6)
        assert Q.schedule_retry(db, a, "network")
        db.x("UPDATE attempts SET ended_at='x' WHERE ended_at IS NULL")
    Q.claim(db, "w", now=time.time() + 10 ** 6)
    assert not Q.schedule_retry(db, a, "network")
    assert Q.app_view(db, a)["state"] == M.FAILED


def test_events_are_redacted(db):
    from runner.evidence import register_secret
    register_secret("Hunter2-Secret!")
    db.event("system", "x", "note", {"password": "Hunter2-Secret!", "msg": "typed Hunter2-Secret! into box",
                                     "link": "https://wd.example/activate?token=abc123"})
    data = db.one("SELECT data_json FROM events ORDER BY seq DESC LIMIT 1")["data_json"]
    assert "Hunter2" not in data and "abc123" not in data
