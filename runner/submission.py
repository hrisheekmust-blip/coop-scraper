"""The only code that presses a final submit button.

Order: verify -> freeze snapshot -> persist submit_intent -> click -> persist click -> look for application-specific
evidence -> applied, or uncertain. A crash anywhere after submit_intent leaves the application uncertain, and
nothing re-submits it until the outcome is reconciled (portal history, a matching email, or you).
"""
from __future__ import annotations

import re
import time

from . import jobqueue as Q, models as M
from .browser import Button, Observation, observe
from .facts import Facts
from .identity import identify


def _prechecks(run, obs: Observation, adapter) -> list[str]:
    problems = []
    for c in obs.controls:
        if c.required and c.control not in ("password", "search") and c.empty():
            problems.append(f"required field still empty: {c.label[:60]}")
    if obs.errors:
        problems.append("the page shows errors: " + "; ".join(obs.errors[:3]))
    resume_boxes = [c for c in obs.controls if c.control == "file" and re.search(r"resume|\bcv\b", c.label + " " + c.raw.get("around", ""), re.I)]
    if resume_boxes and all(c.empty() for c in resume_boxes) and not any(a["key"] == "material.resume" for a in run.answers_used):
        problems.append("the resume isn't attached")
    ident = identify(obs.url)
    if not ident.provisional and ident.portal == run.job.get("portal") and (ident.tenant, ident.requisition) != (run.job.get("tenant"), run.job.get("requisition")):
        problems.append("the page belongs to a different requisition")
    if Q.cancel_requested(run.db, run.app["id"]):
        problems.append("cancelled")
    return problems


def snapshot(run, obs: Observation) -> dict:
    facts = Facts(run.db).current()
    return {"form_fingerprint": obs.fingerprint(), "url": obs.url, "answers": run.answers_used,
            "materials": {k: v for k, v in run.material_kinds.items()},
            "profile_version": max([f.get("id", "") for f in facts] or [""]), "fact_count": len(facts)}


def submit(run, obs: Observation, button: Button, adapter):
    from .planner import Done, Park
    problems = _prechecks(run, obs, adapter)
    if problems:
        raise Park(M.NEEDS_INFO if any("empty" in p or "errors" in p for p in problems) else M.FAILED, "changed_form",
                   "; ".join(problems[:4]), [{"blocker": "precheck", "problems": problems}])
    run.state(M.VALIDATING)
    run.state(M.READY)
    snap = snapshot(run, obs)
    try:
        Q.submit_intent(run.db, run.app["id"], run.attempt_id, run.owner, snap)
    except Q.Refused as e:
        raise Park(M.NEEDS_HUMAN if "reconcile" in str(e) else M.FAILED, "wrong_identity" if "confirmed" in str(e) else "no_progress", str(e))
    run.submitted = True
    button.kind = "submit"
    run.ex.click(button, allow=("submit",))
    Q.submit_clicked(run.db, run.attempt_id, run.owner)
    run.state(M.VERIFYING)
    run.log("submitted_click")
    deadline = time.time() + run.cfg.confirm_wait_s
    first_errors_at, seen_errors = None, 0
    while time.time() < deadline:
        time.sleep(run.cfg.poll_s)
        run.heartbeat()
        try:
            now = observe(run.page)
        except Exception:
            continue
        ev = adapter.evidence(now, obs, run)
        if ev:
            ev = {**ev, "requisition": run.job.get("requisition", ""), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            Q.confirm(run.db, run.app["id"], ev.get("kind", "page"), ev, f"{adapter.name} confirmation after submit", run.attempt_id)
            run.save_session()
            raise Done(M.APPLIED, ev.get("text", "")[:200])
        # Same form, same URL, field errors: the portal rejected the submission before accepting it.
        same_form = now.url.split("#")[0] == obs.url.split("#")[0] and now.fingerprint() == obs.fingerprint()
        if not (same_form and now.errors):
            first_errors_at, seen_errors = None, 0
        if same_form and now.errors:
            first_errors_at = first_errors_at or time.time()
            seen_errors += 1
            if seen_errors >= 3 and time.time() - first_errors_at >= 2:
                Q.set_attempt_outcome(run.db, run.attempt_id, "validation_rejected")
                run.log("validation_rejected", errors=now.errors[:5])
                raise Park(M.NEEDS_INFO, "no_progress", "the portal rejected the form: " + "; ".join(now.errors[:4]),
                           [{"blocker": "form_errors", "errors": now.errors[:6]}])
    # No evidence in time. Try the portal's own history, read-only.
    verdict = None
    if adapter.supports_history:
        try:
            verdict = adapter.history(run)
        except Exception:
            verdict = None
    if verdict == "submitted":
        Q.confirm(run.db, run.app["id"], "history", {"text": "listed in the portal's application history", "url": run.page.url},
                  f"{adapter.name} candidate home", run.attempt_id)
        raise Done(M.APPLIED, "found in the portal's application history")
    Q.transition(run.db, run.app["id"], M.UNCERTAIN, "Submit was pressed but no confirmation appeared; waiting for the portal or an email",
                 attempt_id=run.attempt_id, owner=run.owner)
    raise Done(M.UNCERTAIN, "no confirmation yet")


def reconcile(run, adapter) -> str:
    """Read-only outcome check for an uncertain application ('Check outcome'). Never fills or submits."""
    if not adapter.supports_history:
        return "unknown"
    verdict = adapter.history(run)
    if verdict == "submitted":
        Q.confirm(run.db, run.app["id"], "history", {"text": "listed in the portal's application history", "url": run.page.url},
                  f"{adapter.name} candidate home", run.attempt_id)
    return verdict or "unknown"
