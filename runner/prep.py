"""The preparation pass: the bridge between the local runtime (no model) and a Claude session.

  python -m runner prep export  -> prep/packet-<date>.json   pending questions, grouped, plus what facts they need
  (a Claude session reads the packet with your profile/materials, asks you what's genuinely missing, and writes
   prep/response-<date>.json)
  python -m runner prep import prep/response-<date>.json     validated, then affected applications resume

The import validates everything before writing anything: facts need a source that is you; generated text must
cite existing facts; equivalence patterns must be anchored; keys must exist. Generated text is stored as
'generated' answers, never as facts, so model output can't become its own evidence.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import jobqueue as Q, models as M
from .answer_engine import AnswerEngine, BUILTIN_KEYS
from .db import DB, dumps, loads, now_iso
from .facts import Facts
from .questions import Question

FACT_SOURCES = {"user_answer", "profile", "resume_confirmed", "prep_confirmed"}


def export_packet(db: DB) -> dict:
    groups = {}
    for r in db.all("""SELECT p.*, j.company, j.title, j.resolved_url FROM pending_questions p LEFT JOIN jobs j ON j.id=p.job_id
                       WHERE p.resolved_at IS NULL ORDER BY p.first_seen"""):
        q = loads(r["question_json"], {})
        g = groups.setdefault(r["semantic_key"], {"key": r["semantic_key"], "label": q.get("label"), "wordings": [], "control": q.get("control"),
                                                  "options": q.get("options") or [], "required": False, "reason": q.get("reason"),
                                                  "jobs": [], "missing_facts": []})
        if q.get("label") not in g["wordings"]:
            g["wordings"].append(q.get("label"))
        g["required"] |= bool(q.get("required"))
        g["jobs"].append({"job_id": r["job_id"], "company": r["company"], "title": r["title"], "url": r["resolved_url"]})
    facts = Facts(db).current()
    return {"generated_at": now_iso(), "kind": "coop-prep-packet", "version": 1,
            "instructions": ("Answer only from the user's confirmed information. Ask the user for anything missing; never guess "
                             "qualifications, dates, citizenship, sponsorship or self-identification. Return a coop-prep-response."),
            "questions": list(groups.values()),
            "facts": [{k: f[k] for k in ("id", "predicate", "value", "scope", "valid_from", "valid_until")} for f in facts],
            "response_schema": {
                "facts": [{"predicate": "str", "value": "any", "scope": {"kind": "user|employer|job"}, "valid_from": "YYYY-MM-DD?",
                           "valid_until": "YYYY-MM-DD?", "source": {"kind": "user_answer|profile|resume_confirmed", "note": "str"}}],
                "answers": [{"key": "semantic key from the packet", "label": "str", "value": "str|list", "scope": {"kind": "user|employer|job"}}],
                "equivalents": [{"key": "builtin or answered key", "labels": ["exact wording"], "patterns": ["^anchored regex$"]}],
                "narratives": [{"key": "str", "label": "str", "text": "str", "scope": {"kind": "job|employer", "job|employer": "id|name"},
                                "evidence": ["fact ids the text relies on"]}],
            }}


class PrepError(ValueError):
    pass


def validate_response(db: DB, resp: dict) -> list[str]:
    errs = []
    facts = Facts(db)
    known_keys = set(BUILTIN_KEYS) | {r["semantic_key"] for r in db.all("SELECT DISTINCT semantic_key FROM pending_questions")} \
        | {r["semantic_key"] for r in db.all("SELECT DISTINCT semantic_key FROM answer_memory")}
    new_keys = {a.get("key") for a in resp.get("answers", [])} | {n.get("key") for n in resp.get("narratives", [])}
    for i, f in enumerate(resp.get("facts", [])):
        if not isinstance(f.get("predicate"), str) or not re.fullmatch(r"[a-z_]+(\.[a-z0-9_]+)+", f["predicate"]):
            errs.append(f"facts[{i}]: bad predicate")
        if (f.get("source") or {}).get("kind") not in FACT_SOURCES:
            errs.append(f"facts[{i}]: a fact needs a source that is you (user_answer/profile/resume_confirmed)")
        for d in ("valid_from", "valid_until"):
            if f.get(d) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f[d]):
                errs.append(f"facts[{i}]: {d} must be YYYY-MM-DD")
    for i, a in enumerate(resp.get("answers", [])):
        if not a.get("key") or a.get("value") in (None, "", []):
            errs.append(f"answers[{i}]: key and value required")
        if (a.get("scope") or {}).get("kind", "user") not in ("user", "employer", "job"):
            errs.append(f"answers[{i}]: bad scope")
    for i, e in enumerate(resp.get("equivalents", [])):
        if e.get("key") not in known_keys | new_keys:
            errs.append(f"equivalents[{i}]: unknown key {e.get('key')}")
        for p in e.get("patterns", []):
            if not (p.startswith("^") and p.endswith("$")):
                errs.append(f"equivalents[{i}]: pattern must be anchored: {p}")
            try:
                re.compile(p)
            except re.error as x:
                errs.append(f"equivalents[{i}]: {x}")
    new_fact_preds = {f.get("predicate") for f in resp.get("facts", [])}
    for i, n in enumerate(resp.get("narratives", [])):
        if not n.get("text") or not n.get("key"):
            errs.append(f"narratives[{i}]: key and text required")
        sc = n.get("scope") or {}
        if sc.get("kind") not in ("job", "employer", "user"):
            errs.append(f"narratives[{i}]: scope kind required")
        ev = n.get("evidence") or []
        if not ev:
            errs.append(f"narratives[{i}]: generated text must cite the facts it relies on")
        for e in ev:
            if not (facts.exists(e) or (isinstance(e, str) and e.startswith("pred:") and e[5:] in new_fact_preds)):
                errs.append(f"narratives[{i}]: evidence {e} doesn't exist")
    return errs


def import_response(db: DB, resp: dict, engine: AnswerEngine, bank_save=True) -> dict:
    errs = validate_response(db, resp)
    if errs:
        raise PrepError("; ".join(errs[:20]))
    facts = Facts(db)
    pred_ids = {}
    for f in resp.get("facts", []):
        pred_ids[f["predicate"]] = facts.add(f["predicate"], f["value"], f["source"], f.get("scope"), f.get("valid_from"), f.get("valid_until"))
    for a in resp.get("answers", []):
        engine.remember(a["key"], a.get("label", a["key"]), a["value"], a.get("scope") or {"kind": "user"}, "explicit", options=a.get("options"))
    for e in resp.get("equivalents", []):
        engine.bank.add(e["key"], e.get("labels", []), e.get("patterns", []))
    for n in resp.get("narratives", []):
        ev = [pred_ids.get(x[5:], x) if isinstance(x, str) and x.startswith("pred:") else x for x in n.get("evidence", [])]
        engine.remember(n["key"], n.get("label", n["key"]), n["text"], n["scope"], "generated", evidence=ev)
    if bank_save:
        engine.bank.save()
    resumed = resume_answerable(db, engine)
    return {"facts": len(resp.get("facts", [])), "answers": len(resp.get("answers", [])), "equivalents": len(resp.get("equivalents", [])),
            "narratives": len(resp.get("narratives", [])), "resumed": resumed}


def resume_answerable(db: DB, engine: AnswerEngine) -> list[str]:
    """Applications parked on missing information go back to the queue once every recorded question answers.
    They resume from their checkpoint as a new attempt of the same application; nothing is resubmitted."""
    resumed = []
    for a in db.all("SELECT a.*, j.company, j.title, j.location FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.state=?", (M.NEEDS_INFO,)):
        needs = loads(a["needs_json"], [])
        job = {"id": a["job_id"], "company": a["company"], "title": a["title"], "location": a["location"]}
        ctx = engine.ctx(job, materials={"resume": "pending", "cover_letter": "pending"}, cover_letter=True)
        still = []
        for n in needs:
            q = Question.from_dict(n.get("question") or {})
            if not q.label:
                still.append(n)
                continue
            res = engine.answer(q, ctx)
            if getattr(res, "decision", "answer") == "need":
                still.append(n)
        if not still:
            Q.transition(db, a["id"], M.QUEUED, "missing information was added")
            with db.tx():
                db.x("UPDATE pending_questions SET resolved_at=? WHERE application_id=?", (now_iso(), a["id"]))
            resumed.append(a["id"])
        elif len(still) != len(needs):
            with db.tx():
                db.x("UPDATE applications SET needs_json=? WHERE id=?", (dumps(still), a["id"]))
    return resumed
