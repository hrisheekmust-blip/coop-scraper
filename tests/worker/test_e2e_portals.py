"""Stage F: SuccessFactors (RMK hand-off, consent statement, final "Apply"), Oracle (passwordless email code),
iCIMS (everything inside an iframe)."""
from runner import jobqueue as Q, models as M

from .conftest import EMAIL
from .portals.accounts_portals import ICIMSPortal, OraclePortal, SFPortal


def test_successfactors_rmk_handoff_signup_consent_and_apply_button(h):
    sf = SFPortal(h.fx)
    app = h.enqueue(sf.url(), "Acme Semiconductor", "Process Engineering Co-Op")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    assert sf.registrations == 1 and sf.accounts[EMAIL]["dps"] is True
    (_, req, d), = h.fx.submissions
    assert d["state"] == "MA" and d["enrolled"] == "Yes" and d["spons"] == "No" and d["resume"] == "Test_Applicant_Resume.pdf"
    # the provisional RMK listing was merged into the real requisition
    v = h.view(app)
    assert v["portal"] == "successfactors" and v["requisition"] == "12345"


def test_successfactors_second_listing_of_same_requisition_is_one_application(h):
    sf = SFPortal(h.fx)
    first = h.enqueue(sf.url(), "Acme Semiconductor")["application_id"]
    assert h.run() == M.APPLIED
    again = h.enqueue(sf.sf_url(), "Acme Semiconductor")
    assert again["application_id"] == first and again["note"] == "Already applied"


def test_oracle_passwordless_code_from_the_mailbox(h):
    op = OraclePortal(h.fx)
    h.start_outlook_bridge()
    app = h.enqueue(op.url(), "Acme", "Test Engineer Intern")["application_id"]
    assert h.run() == M.APPLIED, h.view(app)
    (_, _, d), = h.fx.submissions
    assert d["first"] == "Test" and d["auth"] == "Yes"
    assert h.db.one("SELECT auth_method FROM accounts")["auth_method"] == "email_code"


def test_icims_inside_an_iframe(h):
    ic = ICIMSPortal(h.fx)
    app = h.enqueue(ic.url(), "Acme", "Test Engineer")["application_id"]
    st = h.run()
    assert st == M.APPLIED, (h.view(app)["reason"], [(r["kind"], r["data_json"][:150]) for r in h.db.all("SELECT kind, data_json FROM events WHERE entity_id=? ORDER BY seq", (app,))][-12:])
    steps = {s[2]["step"]: s[2] for s in h.fx.submissions}
    assert steps["1"]["state"] == "Massachusetts" and steps["2"]["spons"] == "No" and steps["2"]["avail"] == "Yes"
