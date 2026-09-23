"""Canonical jobs, applications, attempts, leases, locks and the submit checkpoint.

Everything here runs in short IMMEDIATE transactions, so a double click, two board tabs, a crash, or a restart
can't produce two live attempts or a second final submit for the same requisition.
"""
from __future__ import annotations

import hashlib
import time

from . import models as M
from .db import DB, dumps, loads, new_id, now_iso
from .identity import JobIdentity, identify, normalize_url, realm_for

DEFAULT_USER = "me"


class Refused(Exception):
    """A state change the rules don't allow. The message is safe to show."""


def job_id_for(ident: JobIdentity) -> str:
    return "job_" + hashlib.sha1(ident.key.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------- jobs
def resolve_job(db: DB, url: str, board_ref: str = "", company="", title="", location="", source="") -> str:
    """Find or create the canonical job for a URL; record the URL (and board id) as aliases."""
    url = url.strip()
    ident = identify(url)
    alias_url = "url:" + normalize_url(url)
    with db.tx():
        row = db.one("SELECT job_id FROM job_aliases WHERE alias=?", (alias_url,))
        if not row and board_ref:
            row = db.one("SELECT job_id FROM job_aliases WHERE alias=?", ("board:" + board_ref,))
        jid = _follow(db, row["job_id"]) if row else None
        if jid is None:
            jid = job_id_for(ident)
            existing = db.one("SELECT id, merged_into FROM jobs WHERE id=?", (jid,))
            if existing:
                jid = _follow(db, jid)
            else:
                realm = realm_for(url)
                db.x("""INSERT INTO jobs(id,portal,tenant,requisition,provisional,resolved_url,company,title,location,realm_id,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (jid, ident.portal, ident.tenant, ident.requisition, int(ident.provisional), url, company, title, location,
                      realm.id if realm else None, now_iso(), now_iso()))
                db.event("job", jid, "created", {"identity": ident.key, "url": url})
        _alias(db, alias_url, jid, source)
        if board_ref:
            _alias(db, "board:" + board_ref, jid, source)
        if company or title:
            db.x("UPDATE jobs SET company=CASE WHEN company='' THEN ? ELSE company END, title=CASE WHEN title='' THEN ? ELSE title END, "
                 "location=CASE WHEN location='' THEN ? ELSE location END WHERE id=?", (company, title, location, jid))
    return jid


def _alias(db, alias, jid, source):
    cur = db.one("SELECT job_id FROM job_aliases WHERE alias=?", (alias,))
    if not cur:
        db.x("INSERT INTO job_aliases(alias,job_id,source,created_at) VALUES(?,?,?,?)", (alias, jid, source, now_iso()))
    elif _follow(db, cur["job_id"]) != jid:
        # The same alias pointing at two jobs means one of them is provisional; the caller merges explicitly.
        db.event("job", jid, "alias_conflict", {"alias": alias, "other": cur["job_id"]})


def _follow(db, jid):
    seen = set()
    while jid and jid not in seen:
        seen.add(jid)
        r = db.one("SELECT merged_into FROM jobs WHERE id=?", (jid,))
        if not r or not r["merged_into"]:
            return jid
        jid = r["merged_into"]
    return jid


def canonical(db: DB, jid: str) -> str:
    return _follow(db, jid)


STRENGTH = {M.APPLIED: 9, M.UNCERTAIN: 8, M.VERIFYING: 7, M.SUBMITTING: 7, M.READY: 5, M.VALIDATING: 5, M.FILLING: 5,
            M.PREPARING: 5, M.AUTHENTICATING: 5, M.RESOLVING: 5, M.AWAITING_EMAIL: 4, M.NEEDS_HUMAN: 4, M.NEEDS_INFO: 4,
            M.QUEUED: 3, M.RETRY_WAIT: 3, M.FAILED: 2, M.CLOSED: 1, M.CANCELLED: 0}


def merge_job(db: DB, provisional_id: str, url: str) -> str:
    """A page revealed the real identity of a provisional job. Move aliases/applications onto the real job.

    If both carry an application for the same user, the stronger one (submitted beats uncertain beats live
    beats queued) is kept; the other is cancelled as a duplicate. Nothing is deleted.
    """
    ident = identify(url)
    if ident.provisional:
        return canonical(db, provisional_id)
    with db.tx():
        src = canonical(db, provisional_id)
        dst = job_id_for(ident)
        if src == dst:
            return dst
        if not db.one("SELECT id FROM jobs WHERE id=?", (dst,)):
            s = db.one("SELECT * FROM jobs WHERE id=?", (src,))
            realm = realm_for(url)
            db.x("""INSERT INTO jobs(id,portal,tenant,requisition,provisional,resolved_url,company,title,location,realm_id,created_at,updated_at)
                    VALUES(?,?,?,?,0,?,?,?,?,?,?,?)""",
                 (dst, ident.portal, ident.tenant, ident.requisition, url, s["company"], s["title"], s["location"],
                  realm.id if realm else s["realm_id"], now_iso(), now_iso()))
        db.x("UPDATE job_aliases SET job_id=? WHERE job_id=?", (dst, src))
        _alias(db, "url:" + normalize_url(url), dst, "resolved")
        for a in db.all("SELECT * FROM applications WHERE job_id=?", (src,)):
            other = db.one("SELECT * FROM applications WHERE job_id=? AND user_id=?", (dst, a["user_id"]))
            if not other:
                db.x("UPDATE applications SET job_id=?, updated_at=? WHERE id=?", (dst, now_iso(), a["id"]))
                continue
            keep, drop = (a, other) if STRENGTH.get(a["state"], 0) > STRENGTH.get(other["state"], 0) else (other, a)
            if drop["state"] in M.ACTIVE and drop["state"] not in (M.RESOLVING,):
                # Never silently cancel something mid-flight: keep both visible, flag for reconciliation.
                db.event("application", drop["id"], "duplicate_in_flight", {"duplicate_of": keep["id"]})
                continue
            # The unique (user, job) pair forces the dropped one onto its old job row, cancelled.
            db.x("UPDATE applications SET state=?, state_reason=?, updated_at=? WHERE id=?",
                 (M.CANCELLED if drop["state"] not in (M.APPLIED, M.UNCERTAIN) else drop["state"],
                  f"duplicate listing of {keep['id']}", now_iso(), drop["id"]))
            if keep["job_id"] != dst:
                # Swap them: the kept application moves to the real job, the duplicate stays on the merged one.
                db.x("UPDATE applications SET user_id=? WHERE id=?", (drop["user_id"] + "#swap", drop["id"]))
                db.x("UPDATE applications SET job_id=? WHERE id=?", (src, drop["id"]))
                db.x("UPDATE applications SET job_id=?, updated_at=? WHERE id=?", (dst, now_iso(), keep["id"]))
                db.x("UPDATE applications SET user_id=? WHERE id=?", (drop["user_id"], drop["id"]))
            db.event("application", drop["id"], "merged_duplicate", {"kept": keep["id"]})
        db.x("UPDATE jobs SET merged_into=?, updated_at=? WHERE id=?", (dst, now_iso(), src))
        db.event("job", src, "merged", {"into": dst, "identity": ident.key})
    return dst


def mark_posting_closed(db: DB, jid: str):
    with db.tx():
        db.x("UPDATE jobs SET posting_state='closed', updated_at=? WHERE id=?", (now_iso(), jid))
        db.event("job", jid, "closed")


# ---------------------------------------------------------------------------------------------- applications
def app_row(db, app_id):
    return db.one("SELECT * FROM applications WHERE id=?", (app_id,))


def app_view(db: DB, app_id: str) -> dict:
    a = app_row(db, app_id)
    if not a:
        return {}
    j = db.one("SELECT * FROM jobs WHERE id=?", (a["job_id"],))
    conf = db.one("SELECT kind, evidence_json, observed_at FROM confirmations WHERE application_id=? ORDER BY observed_at DESC LIMIT 1", (app_id,))
    return {"application_id": a["id"], "job_id": a["job_id"], "board_ref": a["board_ref"], "state": a["state"],
            "display": M.DISPLAY.get(a["state"], a["state"]), "reason": a["state_reason"], "needs": loads(a["needs_json"], []),
            "company": j["company"] if j else "", "title": j["title"] if j else "", "portal": j["portal"] if j else "",
            "requisition": j["requisition"] if j else "", "updated_at": a["updated_at"],
            "receipt": ({"kind": conf["kind"], "at": conf["observed_at"], **loads(conf["evidence_json"], {})} if conf else None)}


def enqueue(db: DB, request_id: str, source_url: str, board_ref: str = "", user_id: str = DEFAULT_USER,
            company="", title="", location="", source="board", material_policy="saved_default") -> dict:
    """Durably accept one Apply click. Replays of the same request id return the same answer; a different
    request id for the same requisition returns the existing application instead of creating another."""
    prior = db.one("SELECT response_json FROM requests WHERE request_id=?", (request_id,))
    if prior:
        resp = loads(prior["response_json"], {})
        return {**resp, **app_view(db, resp.get("application_id", "")), "replayed": True}
    jid = resolve_job(db, source_url, board_ref, company, title, location, source)
    with db.tx():
        prior = db.one("SELECT response_json FROM requests WHERE request_id=?", (request_id,))
        if prior:
            resp = loads(prior["response_json"], {})
            return {**resp, **app_view(db, resp.get("application_id", "")), "replayed": True}
        a = db.one("SELECT * FROM applications WHERE user_id=? AND job_id=?", (user_id, jid))
        note = ""
        if not a:
            app_id = new_id("app")
            db.x("""INSERT INTO applications(id,user_id,job_id,state,board_ref,material_policy,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (app_id, user_id, jid, M.QUEUED, board_ref, material_policy, now_iso(), now_iso()))
            db.event("application", app_id, "queued", {"request_id": request_id, "source_url": source_url})
        else:
            app_id = a["id"]
            st = a["state"]
            if st in (M.CANCELLED, M.FAILED, M.CLOSED) or st in M.PARKED:
                _set_state(db, app_id, M.QUEUED, "requeued by Apply", [])
            elif st == M.UNCERTAIN:
                note = "Submission is uncertain: check the portal or your email, then resolve it before applying again"
            elif st == M.APPLIED:
                note = "Already applied"
            else:
                note = "Already in progress"
            if board_ref and not a["board_ref"]:
                db.x("UPDATE applications SET board_ref=? WHERE id=?", (board_ref, app_id))
        resp = {"application_id": app_id, "job_id": jid, "note": note}
        db.x("INSERT INTO requests(request_id,kind,response_json,created_at) VALUES(?,?,?,?)",
             (request_id, "application.enqueue", dumps(resp), now_iso()))
    return {**resp, **app_view(db, app_id), "replayed": False}


def _set_state(db, app_id, state, reason="", needs=None, force=False):
    a = app_row(db, app_id)
    if not a:
        raise Refused("unknown application")
    if not force and state != a["state"] and state not in M.ALLOWED.get(a["state"], set()):
        raise Refused(f"can't move from {a['state']} to {state}")
    db.x("UPDATE applications SET state=?, state_reason=?, needs_json=?, updated_at=? WHERE id=?",
         (state, reason, dumps(needs if needs is not None else loads(a["needs_json"], [])), now_iso(), app_id))
    db.event("application", app_id, "state", {"from": a["state"], "to": state, "reason": reason})


def transition(db: DB, app_id: str, state: str, reason: str = "", needs=None, attempt_id: str | None = None, owner: str | None = None):
    with db.tx():
        if attempt_id:
            _own(db, attempt_id, owner)
        _set_state(db, app_id, state, reason, needs)


def cancel(db: DB, app_id: str) -> dict:
    with db.tx():
        a = app_row(db, app_id)
        if not a:
            raise Refused("unknown application")
        st = a["state"]
        if st in M.RUNNABLE or st in M.PARKED or st == M.FAILED:
            _set_state(db, app_id, M.CANCELLED, "cancelled by you")
            msg = "cancelled"
        elif st in M.POST_SUBMIT:
            db.x("UPDATE applications SET cancel_requested=1 WHERE id=?", (app_id,))
            msg = "Submit was already pressed; the worker will stop and reconcile the outcome. It can't withdraw a sent application."
        elif st in M.ACTIVE:
            db.x("UPDATE applications SET cancel_requested=1 WHERE id=?", (app_id,))
            msg = "cancelling before submit"
        else:
            msg = f"nothing to cancel ({st})"
        db.event("application", app_id, "cancel_requested", {"state": st})
    return {"message": msg, **app_view(db, app_id)}


def resume(db: DB, app_id: str) -> dict:
    with db.tx():
        a = app_row(db, app_id)
        if a and (a["state"] in M.PARKED or a["state"] in (M.FAILED, M.CANCELLED, M.CLOSED)):
            _set_state(db, app_id, M.QUEUED, "resumed")
            db.x("UPDATE applications SET cancel_requested=0 WHERE id=?", (app_id,))
    return app_view(db, app_id)


def resolve_uncertain(db: DB, app_id: str, resolution: str, note: str = "") -> dict:
    """You checked the portal/email. 'submitted' records it as applied (your assertion, labelled as such);
    'not_submitted' allows one new attempt."""
    with db.tx():
        a = app_row(db, app_id)
        if not a or a["state"] != M.UNCERTAIN:
            raise Refused("only an uncertain submission can be resolved")
        if resolution == "submitted":
            _confirm(db, a, "user", {"text": note or "you confirmed it was submitted"}, "user assertion")
        elif resolution == "not_submitted":
            db.x("UPDATE applications SET allow_resubmit=1 WHERE id=?", (app_id,))
            db.x("UPDATE attempts SET outcome='resolved_not_submitted' WHERE application_id=? AND submit_intent_at IS NOT NULL "
                 "AND (outcome IS NULL OR outcome NOT IN ('applied'))", (app_id,))
            _set_state(db, app_id, M.QUEUED, "you confirmed it was not submitted")
        else:
            raise Refused("resolution must be submitted or not_submitted")
    return app_view(db, app_id)


# ---------------------------------------------------------------------------------------------- attempts
def claim(db: DB, owner: str, lease_s: float = 90, max_live: int = 2, now: float | None = None):
    """Take the next runnable application. At most `max_live` live attempts overall and one per realm."""
    now = now or time.time()
    with db.tx():
        live = db.one("SELECT COUNT(*) n FROM attempts WHERE ended_at IS NULL")["n"]
        if live >= max_live:
            return None
        rows = db.all("""SELECT a.*, j.realm_id FROM applications a JOIN jobs j ON j.id=a.job_id
                         WHERE a.cancel_requested=0 AND (a.state=? OR (a.state=? AND COALESCE(a.retry_at,0)<=?))
                         ORDER BY a.updated_at""", (M.QUEUED, M.RETRY_WAIT, now))
        busy = {r["realm_id"] for r in db.all("""SELECT j.realm_id FROM attempts t JOIN applications a ON a.id=t.application_id
                                                 JOIN jobs j ON j.id=a.job_id WHERE t.ended_at IS NULL AND j.realm_id IS NOT NULL""")}
        busy |= {r["name"][6:] for r in db.all("SELECT name FROM locks WHERE name LIKE 'realm:%' AND expires>?", (now,))}
        for a in rows:
            if a["realm_id"] and a["realm_id"] in busy:
                continue
            n = db.one("SELECT COALESCE(MAX(number),0)+1 n FROM attempts WHERE application_id=?", (a["id"],))["n"]
            att = new_id("att")
            db.x("""INSERT INTO attempts(id,application_id,number,lease_owner,lease_expires,started_at)
                    VALUES(?,?,?,?,?,?)""", (att, a["id"], n, owner, now + lease_s, now_iso()))
            _set_state(db, a["id"], M.RESOLVING, "")
            db.event("application", a["id"], "attempt_started", {"attempt": att, "number": n})
            return {"application": dict(a), "attempt_id": att}
    return None


def _own(db, attempt_id, owner):
    t = db.one("SELECT * FROM attempts WHERE id=?", (attempt_id,))
    if not t or t["ended_at"] or (owner and t["lease_owner"] != owner):
        raise Refused("lease lost")
    return t


def heartbeat(db: DB, attempt_id: str, owner: str, lease_s: float = 90) -> bool:
    with db.tx():
        c = db.x("UPDATE attempts SET lease_expires=? WHERE id=? AND lease_owner=? AND ended_at IS NULL",
                 (time.time() + lease_s, attempt_id, owner)).rowcount
    return c == 1


def use_action(db: DB, attempt_id: str, owner: str) -> bool:
    """Count one page action. False when the budget is spent or the lease is gone."""
    with db.tx():
        t = _own(db, attempt_id, owner)
        if t["actions_used"] >= t["action_budget"]:
            return False
        db.x("UPDATE attempts SET actions_used=actions_used+1 WHERE id=?", (attempt_id,))
    return True


def cancel_requested(db: DB, app_id: str) -> bool:
    a = app_row(db, app_id)
    return bool(a and a["cancel_requested"])


def submit_intent(db: DB, app_id: str, attempt_id: str, owner: str, snapshot: dict) -> None:
    """The last durable write before the final click. Refuses if anything suggests a prior submit."""
    with db.tx():
        t = _own(db, attempt_id, owner)
        a = app_row(db, app_id)
        if a["cancel_requested"]:
            raise Refused("cancelled before submit")
        if a["state"] != M.READY:
            raise Refused(f"not ready to submit ({a['state']})")
        prior = db.one("""SELECT id FROM attempts WHERE application_id=? AND id<>? AND submit_intent_at IS NOT NULL
                          AND (outcome IS NULL OR outcome NOT IN ('resolved_not_submitted','validation_rejected'))""", (app_id, attempt_id))
        if prior:
            raise Refused("an earlier attempt may have submitted; reconcile first")
        if db.one("SELECT 1 FROM confirmations WHERE application_id=?", (app_id,)):
            raise Refused("already confirmed")
        if t["submit_intent_at"]:
            raise Refused("this attempt already pressed submit")
        db.x("UPDATE attempts SET submit_intent_at=?, snapshot_json=? WHERE id=?", (now_iso(), dumps(snapshot), attempt_id))
        db.x("UPDATE applications SET allow_resubmit=0 WHERE id=?", (app_id,))
        _set_state(db, app_id, M.SUBMITTING, "")


def submit_clicked(db: DB, attempt_id: str, owner: str):
    with db.tx():
        _own(db, attempt_id, owner)
        db.x("UPDATE attempts SET submit_clicked_at=? WHERE id=?", (now_iso(), attempt_id))


def _confirm(db, a, kind, evidence, provenance, attempt_id=None):
    db.x("INSERT INTO confirmations(id,job_id,application_id,kind,evidence_json,provenance,observed_at) VALUES(?,?,?,?,?,?,?)",
         (new_id("conf"), a["job_id"], a["id"], kind, dumps(evidence), provenance, now_iso()))
    snap = None
    if attempt_id:
        t = db.one("SELECT snapshot_json FROM attempts WHERE id=?", (attempt_id,))
        snap = t["snapshot_json"] if t else None
    db.x("UPDATE applications SET submitted_snapshot=COALESCE(submitted_snapshot, ?) WHERE id=?", (snap, a["id"]))
    _set_state(db, a["id"], M.APPLIED, f"confirmed ({kind})", [], force=True)
    db.x("UPDATE attempts SET outcome='applied' WHERE application_id=? AND submit_intent_at IS NOT NULL", (a["id"],))


def confirm(db: DB, app_id: str, kind: str, evidence: dict, provenance: str, attempt_id: str | None = None):
    """Record application-specific evidence of submission. Idempotent."""
    with db.tx():
        a = app_row(db, app_id)
        if a["state"] == M.APPLIED:
            return
        _confirm(db, a, kind, evidence, provenance, attempt_id)


def set_attempt_outcome(db: DB, attempt_id: str, outcome: str):
    with db.tx():
        db.x("UPDATE attempts SET outcome=? WHERE id=?", (outcome, attempt_id))


def request_check(db: DB, app_id: str) -> dict:
    """'Check outcome': a read-only look at the portal's history / confirmation for an uncertain submission."""
    with db.tx():
        a = app_row(db, app_id)
        if not a or a["state"] != M.UNCERTAIN:
            raise Refused("only an uncertain submission can be checked")
        db.x("UPDATE applications SET check_requested=1, updated_at=? WHERE id=?", (now_iso(), app_id))
        db.event("application", app_id, "check_requested")
    return app_view(db, app_id)


def claim_check(db: DB, owner: str, lease_s: float = 90, max_live: int = 2, now: float | None = None):
    """Claim an outcome check. It never submits: the attempt is created with kind='reconcile'."""
    now = now or time.time()
    with db.tx():
        if db.one("SELECT COUNT(*) n FROM attempts WHERE ended_at IS NULL")["n"] >= max_live:
            return None
        a = db.one("SELECT a.*, j.realm_id FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.check_requested=1 AND a.state=? "
                   "AND NOT EXISTS (SELECT 1 FROM attempts t WHERE t.application_id=a.id AND t.ended_at IS NULL) ORDER BY a.updated_at LIMIT 1", (M.UNCERTAIN,))
        if not a:
            return None
        n = db.one("SELECT COALESCE(MAX(number),0)+1 n FROM attempts WHERE application_id=?", (a["id"],))["n"]
        att = new_id("att")
        db.x("INSERT INTO attempts(id,application_id,number,kind,lease_owner,lease_expires,started_at) VALUES(?,?,?,?,?,?,?)",
             (att, a["id"], n, "reconcile", owner, now + lease_s, now_iso()))
        db.x("UPDATE applications SET check_requested=0 WHERE id=?", (a["id"],))
        db.event("application", a["id"], "check_started", {"attempt": att})
        return {"application": dict(a), "attempt_id": att}


def end_attempt(db: DB, attempt_id: str, outcome: str):
    with db.tx():
        db.x("UPDATE attempts SET ended_at=?, outcome=COALESCE(outcome, ?), lease_owner=NULL WHERE id=? AND ended_at IS NULL",
             (now_iso(), outcome, attempt_id))
        t = db.one("SELECT application_id FROM attempts WHERE id=?", (attempt_id,))
        if t:
            db.event("application", t["application_id"], "attempt_ended", {"attempt": attempt_id, "outcome": outcome})


def schedule_retry(db: DB, app_id: str, reason: str, delay_s: float | None = None, max_retries: int = 4):
    """Bounded exponential backoff for failures before submit."""
    with db.tx():
        a = app_row(db, app_id)
        if a["state"] in M.POST_SUBMIT:
            raise Refused("never retry after submit")
        n = a["retries"] + 1
        if n > max_retries:
            _set_state(db, app_id, M.FAILED, reason, force=True)
            return False
        delay = delay_s if delay_s is not None else min(3600, 60 * 2 ** (n - 1))
        db.x("UPDATE applications SET retries=?, retry_at=? WHERE id=?", (n, time.time() + delay, app_id))
        _set_state(db, app_id, M.RETRY_WAIT, reason, force=True)
    return True


def recover(db: DB, now: float | None = None) -> list[dict]:
    """After a crash or restart: end attempts whose lease expired. One that recorded submit intent becomes
    uncertain (a reconcile check runs before anything else); others go back to the queue."""
    now = now or time.time()
    out = []
    with db.tx():
        for t in db.all("SELECT * FROM attempts WHERE ended_at IS NULL AND COALESCE(lease_expires,0)<?", (now,)):
            a = app_row(db, t["application_id"])
            if t["submit_intent_at"] and t["outcome"] != "validation_rejected":
                db.x("UPDATE attempts SET ended_at=?, outcome='interrupted_after_submit_intent', lease_owner=NULL WHERE id=?", (now_iso(), t["id"]))
                if a["state"] != M.APPLIED:
                    _set_state(db, a["id"], M.UNCERTAIN, "The worker stopped after deciding to submit; checking the outcome before any retry", force=True)
                out.append({"application_id": a["id"], "result": M.UNCERTAIN})
            else:
                db.x("UPDATE attempts SET ended_at=?, outcome='interrupted', lease_owner=NULL WHERE id=?", (now_iso(), t["id"]))
                if a["state"] in M.ACTIVE:
                    nxt = M.CANCELLED if a["cancel_requested"] else M.QUEUED
                    _set_state(db, a["id"], nxt, "resumed after the worker restarted", force=True)
                out.append({"application_id": a["id"], "result": a["state"]})
            db.event("application", a["id"], "recovered", {"attempt": t["id"]})
        db.x("DELETE FROM locks WHERE expires<?", (now,))
    return out


# ---------------------------------------------------------------------------------------------- locks
def acquire_lock(db: DB, name: str, owner: str, ttl: float = 300, now: float | None = None) -> bool:
    now = now or time.time()
    with db.tx():
        r = db.one("SELECT owner, expires FROM locks WHERE name=?", (name,))
        if r and r["expires"] > now and r["owner"] != owner:
            return False
        db.x("INSERT INTO locks(name,owner,expires) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, expires=excluded.expires",
             (name, owner, now + ttl))
    return True


def release_lock(db: DB, name: str, owner: str):
    with db.tx():
        db.x("DELETE FROM locks WHERE name=? AND owner=?", (name, owner))


def list_views(db: DB, since: str = "") -> list[dict]:
    rows = db.all("SELECT id FROM applications WHERE updated_at>? ORDER BY updated_at", (since or "",))
    return [app_view(db, r["id"]) for r in rows]
