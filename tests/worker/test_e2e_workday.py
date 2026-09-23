"""Stage F: a Workday-like tenant end to end (account creation, activation email, six steps, candidate home)."""
from runner import jobqueue as Q, models as M

from .conftest import EMAIL
from .portals.workday import REQ, WorkdayTenant


def test_workday_signup_activation_and_all_steps(h):
    wd = WorkdayTenant(h.fx)
    h.start_outlook_bridge()
    app = h.enqueue(wd.url(), "Acme Semiconductor", "Test Engineering Co-op")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    assert wd.registrations == 1 and wd.accounts[EMAIL]["verified"]
    (_, req, d), = h.fx.submissions
    assert req == REQ
    assert d["hear"] == "Company Website" and d["prevWorked"] == "No" and d["country"] == "United States of America"
    assert d["state"] == "Massachusetts" and d["phoneType"] == "Mobile" and d["phoneCode"] == "United States of America (+1)"
    assert d["school"] == "Test University" and d["degree"] == "Bachelor's Degree" and d["fieldOfStudy"] == "Electrical Engineering"
    assert (d["fromMonth"], d["fromYear"], d["toMonth"], d["toYear"]) == ("09", "2024", "12", "2027")
    assert d["gpa"] == "" and d["resume"] == "Test_Applicant_Resume.pdf"
    assert d["q1"] == "Yes" and d["q2"] == "No" and d["q3"] == "Yes"
    assert d["gender"] == "I do not wish to answer" and d["veteran"] == "I do not wish to self-identify" and d["termsCheckbox"] is True
    assert d["disability"] == "I do not want to answer" and d["sigName"] == "Test Applicant"
    v = h.view(app)
    assert v["receipt"]["kind"] == "page" and "Application Submitted" in v["receipt"]["text"]


def test_workday_missing_confirmation_is_settled_from_candidate_home(h):
    wd = WorkdayTenant(h.fx, verify_email=False, hang_confirm=True)
    app = h.enqueue(wd.url(), "Acme Semiconductor", "Test Engineering Co-op")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    assert h.view(app)["receipt"]["kind"] == "history"
    assert len(h.fx.submissions) == 1


def test_workday_uncertain_check_outcome_is_read_only(h):
    wd = WorkdayTenant(h.fx, verify_email=False)
    app = h.enqueue(wd.url(), "Acme Semiconductor", "Test Engineering Co-op")["application_id"]
    # an earlier attempt crashed after deciding to submit, and nothing reached the portal
    h.db.x("UPDATE applications SET state=? WHERE id=?", (M.UNCERTAIN, app))
    wd.accounts[EMAIL] = {"password": h.vault.get("vault://apply/default_password"), "verified": True, "token": ""}
    Q.request_check(h.db, app)
    assert h.run() == M.UNCERTAIN
    assert not h.fx.submissions                               # a check never fills or submits
    ev = [r["kind"] for r in h.db.all("SELECT kind FROM events WHERE entity_id=?", (app,))]
    assert "check_started" in ev
