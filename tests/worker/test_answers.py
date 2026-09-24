"""Stage D: the answer engine distinguishes supported derivations from unsupported claims and honors corrections.
The profile values here are synthetic test data, not the user's real answers."""
from datetime import date

import pytest

from runner import jobqueue as Q, models as M
from runner.answer_engine import AnswerEngine, Bank
from runner.db import open_db
from runner.facts import Facts
from runner.policy import Validated
from runner.prep import PrepError, export_packet, import_response
from runner.questions import Question

U = {"kind": "user_answer", "note": "test"}
JOB = {"id": "job_x", "company": "Acme Semiconductor", "title": "Test Engineering Co-op", "location": "Austin, TX"}
TODAY = date(2026, 10, 1)


@pytest.fixture
def eng(tmp_path):
    db = open_db(tmp_path / "w.sqlite3")
    f = Facts(db)
    f.add("contact.first_name", "Test", U)
    f.add("contact.application_email", "test@example.edu", U)
    f.add("address.state", "MA", U)
    f.add("address.country", "United States", U)
    f.add("education.degree", "Bachelor of Science", U)
    f.add("education.graduation", "2027-12", U)
    f.add("education.enrolled", True, U, valid_from="2024-09-01", valid_until="2027-12-31")
    f.add("availability.intervals", [{"from": "2027-01", "to": "2027-06"}], U)
    f.add("work_auth.us_authorized", True, U)
    f.add("skills.list", [{"name": "SystemVerilog", "contexts": ["project"], "evidence": "SpecLoop"}], U)
    f.add("prefs.eeo_policy", "decline", U)
    f.add("history.employers", ["Covalta"], U)
    e = AnswerEngine(db, Bank(tmp_path / "bank.json"), consents=["privacy_policy", "terms_of_use", "accuracy_certification"])
    yield db, f, e
    db.close()


def ask(e, label, control="text", options=None, job=JOB, **kw):
    q = Question(label=label, control=control, options=options or [], job_id=job["id"], employer=job["company"], job_title=job["title"], **kw)
    return e.answer(q, e.ctx(job, materials={"resume": "art_1"}, today=TODAY))


def ok(r):
    assert isinstance(r, Validated), getattr(r, "reason", r)
    return r.values


def needs(r):
    assert not isinstance(r, Validated), f"expected a need, got {getattr(r, 'values', r)}"
    return r


def test_wording_variants_map_to_one_fact(eng):
    db, f, e = eng
    for label in ("First Name", "first name*", "Legal First Name:", "Given name"):
        assert ok(ask(e, label)) == ["Test"]


def test_dropdown_options_that_differ_from_the_saved_wording(eng):
    db, f, e = eng
    assert ok(ask(e, "State", "select", ["Maine", "Massachusetts", "Michigan"])) == ["Massachusetts"]
    assert ok(ask(e, "Country", "select", ["Canada", "United States of America", "Mexico"])) == ["United States of America"]
    assert ok(ask(e, "Degree", "select", ["High School", "Bachelor's Degree", "Master's Degree"])) == ["Bachelor's Degree"]
    needs(ask(e, "Degree", "select", ["Associate", "Doctorate"]))


def test_currently_a_student_from_confirmed_enrollment(eng):
    db, f, e = eng
    assert ok(ask(e, "Are you currently a student?", "radio", ["Yes", "No"])) == ["Yes"]
    # After the enrollment's validity ends, the same question is unknown, not "No".
    q = Question(label="Are you currently a student?", control="radio", options=["Yes", "No"], job_id="job_x", employer="Acme")
    needs(e.answer(q, e.ctx(JOB, today=date(2028, 3, 1))))


def test_graduation_keeps_its_precision(eng):
    db, f, e = eng
    assert ok(ask(e, "Expected graduation date", placeholder="MM/YYYY")) == ["12/2027"]
    assert ok(ask(e, "Graduation Month", "select", ["November", "December"])) == ["December"]
    assert ok(ask(e, "Graduation Year")) == ["2027"]
    needs(ask(e, "Expected graduation date", "date"))                       # December 2027 is not Dec 1 or Dec 31
    needs(ask(e, "Expected graduation date", placeholder="MM/DD/YYYY"))


def test_availability_interval(eng):
    db, f, e = eng
    assert ok(ask(e, "Are you available to work January - June 2027?", "radio", ["Yes", "No"])) == ["Yes"]
    needs(ask(e, "Are you available to work January - August 2027?", "radio", ["Yes", "No"]))
    needs(ask(e, "Can you work 40 hours per week?", "radio", ["Yes", "No"]))    # dates don't imply hours


def test_project_experience_is_not_professional_years(eng):
    db, f, e = eng
    assert ok(ask(e, "Do you have experience with SystemVerilog?", "radio", ["Yes", "No"])) == ["Yes"]
    needs(ask(e, "Do you have 3+ years of professional SystemVerilog experience?", "radio", ["Yes", "No"]))
    needs(ask(e, "Do you have experience with Cadence Virtuoso?", "radio", ["Yes", "No"]))


def test_authorization_does_not_imply_citizenship_or_sponsorship(eng):
    db, f, e = eng
    assert ok(ask(e, "Are you legally authorized to work in the United States?", "radio", ["Yes", "No"])) == ["Yes"]
    needs(ask(e, "Are you a U.S. citizen?", "radio", ["Yes", "No"]))
    needs(ask(e, "Will you now or in the future require sponsorship?", "radio", ["Yes", "No"]))
    needs(ask(e, "Are you legally authorized to work in Canada?", "radio", ["Yes", "No"]))     # country-specific


def test_sponsorship_logic_direction(eng):
    db, f, e = eng
    f.add("work_auth.sponsorship_now_or_future", False, U)
    assert ok(ask(e, "Will you now or in the future require sponsorship?", "radio", ["Yes", "No"])) == ["No"]
    assert ok(ask(e, "Will you require sponsorship in the future?", "radio", ["Yes", "No"])) == ["No"]
    f.add("work_auth.sponsorship_now_or_future", True, U)
    needs(ask(e, "Will you require sponsorship in the future?", "radio", ["Yes", "No"]))      # "yes to either" says nothing about "future"


def test_negated_question_does_not_reuse_the_positive_answer(eng):
    db, f, e = eng
    needs(ask(e, "Are you NOT legally authorized to work in the United States?", "radio", ["Yes", "No"]))


def test_prior_employer_needs_employer_specific_history(eng):
    db, f, e = eng
    needs(ask(e, "Have you previously worked for Acme Semiconductor?", "radio", ["Yes", "No"]))
    f.add("history.employers_complete", True, U)
    assert ok(ask(e, "Have you previously worked for Acme Semiconductor?", "radio", ["Yes", "No"])) == ["No"]
    covalta = {**JOB, "company": "Covalta"}
    assert ok(ask(e, "Have you previously worked for this company?", "radio", ["Yes", "No"], job=covalta)) == ["Yes"]


def test_relocation_is_place_specific(eng):
    db, f, e = eng
    f.add("prefs.locations", ["Boston, MA"], U)
    needs(ask(e, "Are you willing to relocate to Austin, TX?", "radio", ["Yes", "No"]))
    assert ok(ask(e, "Are you willing to relocate to Boston?", "radio", ["Yes", "No"])) == ["Yes"]
    f.add("prefs.relocate_general", True, U)
    assert ok(ask(e, "Are you willing to relocate to Austin, TX?", "radio", ["Yes", "No"])) == ["Yes"]


def test_self_identification_only_from_saved_policy(eng):
    db, f, e = eng
    assert ok(ask(e, "Gender", "select", ["Male", "Female", "Decline to self-identify"])) == ["Decline to self-identify"]
    needs(ask(e, "Gender", "select", ["Male", "Female"]))       # no decline option: never pick one


def test_consents(eng):
    db, f, e = eng
    assert ok(ask(e, "I have read and agree to the Privacy Policy", "checkbox", ["I agree"])) == ["I agree"]
    r = ask(e, "Send me job alerts and marketing emails", "checkbox", ["Yes"])
    assert isinstance(r, Validated) and r.values == [] and r.proposal.kind == "skip"
    needs(ask(e, "I consent to a background check", "checkbox", ["I agree"]))


def test_resume_and_unknown_uploads(eng):
    db, f, e = eng
    r = ask(e, "Resume/CV", "file")
    assert isinstance(r, Validated) and r.proposal.value == "art_1"
    needs(ask(e, "Transcript", "file"))


def test_human_correction_wins_and_fact_change_invalidates_generated(eng):
    db, f, e = eng
    q = Question(label="First Name", job_id="job_x", employer="Acme")
    e.remember(e.key_for(q), "First Name", "Tess", {"kind": "user"})
    assert ok(ask(e, "First Name")) == ["Tess"]
    fid = f.get("education.graduation")["id"]
    e.remember("q:why", "Why Acme?", "Because of X.", {"kind": "job", "job": "job_x"}, "generated", evidence=[fid])
    f.add("education.graduation", "2028-05", U)
    assert db.one("SELECT invalidated_at FROM answer_memory WHERE semantic_key='q:why'")["invalidated_at"]


def test_generated_text_is_job_scoped(eng):
    db, f, e = eng
    fid = f.get("skills.list")["id"]
    q = Question(label="Why are you interested in this role?", control="textarea", job_id="job_x", employer=JOB["company"])
    key = e.key_for(q)
    e.remember(key, q.label, "I built SpecLoop in SystemVerilog...", {"kind": "job", "job": "job_x"}, "generated", evidence=[fid])
    assert ok(ask(e, "Why are you interested in this role?", "textarea"))[0].startswith("I built")
    other = {**JOB, "id": "job_y"}
    needs(ask(e, "Why are you interested in this role?", "textarea", job=other))
    needs(ask(e, "Why are you interested in this role?", "textarea", max_len=10))


def test_prep_round_trip_resumes_parked_application(eng):
    db, f, e = eng
    app = Q.enqueue(db, "1", "https://boards.greenhouse.io/acme/jobs/123456", company="Acme Semiconductor", title="Test Engineering Co-op")["application_id"]
    job_id = Q.app_view(db, app)["job_id"]
    q = Question(label="Are you willing to work on-site 5 days a week?", control="radio", options=["Yes", "No"], required=True,
                 job_id=job_id, employer="Acme Semiconductor")
    r = e.answer(q, e.ctx({"id": job_id, "company": "Acme Semiconductor"}))
    key = r.key
    e.record_pending(q, job_id, app, key, r.reason)
    db.x("UPDATE applications SET state=?, needs_json=? WHERE id=?", (M.NEEDS_INFO, __import__("json").dumps([{"question": q.to_dict(), "key": key}]), app))
    packet = export_packet(db)
    assert packet["questions"][0]["key"] == key
    with pytest.raises(PrepError):
        import_response(db, {"narratives": [{"key": key, "text": "x", "scope": {"kind": "job", "job": job_id}, "evidence": []}]}, e)
    with pytest.raises(PrepError):
        import_response(db, {"facts": [{"predicate": "person.onsite_ok", "value": True, "source": {"kind": "model_guess"}}]}, e)
    out = import_response(db, {"answers": [{"key": key, "label": q.label, "value": "Yes", "scope": {"kind": "employer", "employer": "Acme Semiconductor"}}]}, e)
    assert out["resumed"] == [app]
    assert Q.app_view(db, app)["state"] == M.QUEUED


def test_equivalence_bank_routes_new_wording(eng):
    db, f, e = eng
    needs(ask(e, "Primary e-mail for correspondence"))
    import_response(db, {"equivalents": [{"key": "contact.email", "labels": ["Primary e-mail for correspondence"]}]}, e)
    assert ok(ask(e, "Primary e-mail for correspondence")) == ["test@example.edu"]
    with pytest.raises(PrepError):
        import_response(db, {"equivalents": [{"key": "contact.email", "patterns": ["email"]}]}, e)


def test_gpa_dropdown_rounds_down_and_graduate_gpa_is_not_applicable(eng):
    db, f, e = eng
    f.add("education.gpa", "3.87", U)
    one_dec = ["Not applicable/Do not recall", "4.0 out of 4.0", "3.9 out of 4.0", "3.8 out of 4.0", "3.7 out of 4.0"]
    assert ok(ask(e, "GPA (Undergraduate)", "select", one_dec)) == ["3.8 out of 4.0"]   # never rounds up
    assert ok(ask(e, "What is your current GPA?")) == ["3.87"]                          # text keeps the exact value
    assert ok(ask(e, "GPA (Graduate)", "select", ["Other/Not Applicable", "4.0 out of 4.0"])) == ["Other/Not Applicable"]
    assert ok(ask(e, "Masters GPA: Please convert your GPA to a 4.0 scale. Select \"Not Applicable\" if you do not have a Masters GPA",
                  "select", ["Not Applicable", "3.9 out of 4.0"])) == ["Not Applicable"]
    needs(ask(e, "GPA (Graduate)"))                                                     # free text: don't invent "N/A"
    f.add("education.degree", "Master of Science", U)
    needs(ask(e, "GPA (Graduate)", "select", ["Other/Not Applicable", "4.0 out of 4.0"]))
