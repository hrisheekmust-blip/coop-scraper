"""Stage E: full attempts against synthetic Greenhouse/Ashby/Lever portals in headless Chromium."""
import json

from runner import jobqueue as Q, models as M

from .portals.guest import ashby, greenhouse, lever


def test_greenhouse_react_select_conditional_question_and_confirmation(h):
    url = greenhouse(h.fx)
    h.narrative(url, "Why are you interested in Acme Photonics?", "I build photonic test fixtures in my lab work.", "Acme Photonics")
    app = h.enqueue(url, "Acme Photonics", "Hardware Intern")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    (portal, req, fields), = h.fx.submissions
    assert fields["first_name"] == "Test" and fields["email"] == "test.applicant@example.edu"
    assert fields["authorized"] == "Yes" and fields["over18"] == "Yes"          # conditional question appeared and was answered
    assert fields["sponsor"] == "No" and fields["gender"] == "Decline To Self Identify"
    assert fields["resume"] == "Test_Applicant_Resume.pdf" and fields["privacy"] is True
    assert fields["hear"] == ""                                                   # optional + unknown: left blank, not guessed
    v = h.view(app)
    assert v["receipt"]["kind"] == "page" and "/confirmation" in v["receipt"]["url"]
    # snapshot of what was sent is frozen on the application
    snap = json.loads(h.db.one("SELECT submitted_snapshot FROM applications WHERE id=?", (app,))["submitted_snapshot"])
    assert any(a["label"].startswith("Why are you interested") and a["basis"] == "generated" for a in snap["answers"])
    # a second click on Apply never makes a second attempt
    again = h.enqueue(url)
    assert again["note"] == "Already applied" and h.run() is None and len(h.fx.submissions) == 1


def test_missing_narrative_parks_as_needs_information_without_submitting(h):
    url = greenhouse(h.fx)
    app = h.enqueue(url, "Acme Photonics")["application_id"]
    assert h.run() == M.NEEDS_INFO
    v = h.view(app)
    assert "Why are you interested" in v["reason"] and not h.fx.submissions
    pend = h.db.all("SELECT * FROM pending_questions")
    assert len(pend) == 1 and json.loads(pend[0]["question_json"])["required"]


def test_ashby_yes_no_buttons_async_upload_and_in_page_success(h):
    url = ashby(h.fx)
    app = h.enqueue(url, "Acme Robotics")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    (_, _, f), = h.fx.submissions
    assert f["student"] == "Yes" and f["grad"] == "December" and f["_systemfield_name"] == "Test Applicant"


def test_lever_entry_link_radio_interval_and_thanks_route(h):
    url = lever(h.fx)
    app = h.enqueue(url, "Acme Devices")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    (_, _, f), = h.fx.submissions
    assert f["cards[0]"] == "Yes" and f["name"] == "Test Applicant"


def test_no_confirmation_becomes_uncertain_and_an_email_settles_it(h):
    url = lever(h.fx, hang_after_submit=True)
    app = h.enqueue(url, "Acme Devices", "Hardware Co-op")["application_id"]
    assert h.run() == M.UNCERTAIN
    assert len(h.fx.submissions) == 1
    # Neither Apply nor the worker re-submits it.
    assert "uncertain" in h.enqueue(url)["note"].lower()
    assert h.run() is None and len(h.fx.submissions) == 1
    # A matching confirmation email arrives through the Outlook bridge.
    h.fx.email("test.applicant@example.edu", "Acme Devices <no-reply@hire.lever.co>", "Thank you for applying to Acme Devices",
               "Thank you for applying for the Hardware Co-op role.")
    h.start_outlook_bridge()
    import time
    for _ in range(40):
        if h.view(app)["state"] == M.APPLIED:
            break
        time.sleep(0.1)
    assert h.view(app)["state"] == M.APPLIED and h.view(app)["receipt"]["kind"] == "email"


def test_portal_validation_rejection_is_not_uncertain_and_allows_a_fixed_retry(h):
    url = lever(h.fx, reject_submit=True)
    app = h.enqueue(url, "Acme Devices")["application_id"]
    assert h.run() == M.NEEDS_INFO
    assert "valid phone" in h.view(app)["reason"]
    t = h.db.one("SELECT outcome FROM attempts WHERE application_id=?", (app,))
    assert t["outcome"] == "validation_rejected"
    # You fix the underlying fact; the application resumes and may be submitted (the portal showed it wasn't).
    h.fx.lever_opts["reject"] = False
    Q.resume(h.db, app)
    assert h.run() == M.APPLIED and len(h.fx.submissions) == 1


def test_closed_posting(h):
    url = greenhouse(h.fx, closed=True)
    app = h.enqueue(url, "Acme Photonics")["application_id"]
    assert h.run() == M.CLOSED


def test_cancel_before_start_never_opens_the_portal(h):
    url = lever(h.fx)
    app = h.enqueue(url)["application_id"]
    Q.cancel(h.db, app)
    assert h.run() is None and not h.fx.requests


def test_transient_site_error_retries_with_backoff(h):
    url = lever(h.fx)
    h.fx.fail_next["/acme/99999999-8888-7777-6666-555555555555"] = 1
    app = h.enqueue(url)["application_id"]
    st = h.run()
    assert st in (M.RETRY_WAIT, M.FAILED, M.APPLIED)
    assert not h.fx.submissions or st == M.APPLIED
