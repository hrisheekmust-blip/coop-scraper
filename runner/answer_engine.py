"""Answer resolution. Runs locally with no model; everything it knows comes from the fact store, your explicit
answers, the equivalence bank and grounded text prepared ahead of time in a Claude preparation pass.

Order (spec section 8):
  1. your explicit answer for this exact job/question
  2. your explicit answer to an equivalent question with a compatible scope/options
  3. a direct fact mapping           } answer_rules
  4. a deterministic derivation      }
  5. equivalence mappings from the preparation pass (they route new wordings to 1-4)
  6. grounded text generated in the preparation pass for this job/employer
  7. needs_information, recorded as a pending question for the next preparation pass
Every result then goes through policy.validate.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .answer_rules import Ctx, builtin, RULES
from .db import DB, dumps, loads, new_id, now_iso
from .facts import Facts
from .policy import Proposal, Validated, need, validate
from .questions import Question, norm

BUILTIN_KEYS = {k for k, _, _ in RULES} | {"material.resume", "material.cover", "material.cover_text", "consent"}
# Rules whose question carries a parameter (an employer, a skill, a place, a date range). A saved answer to one of
# these is only reusable for the same wording, never across employers/skills/places: they're keyed by the exact
# question (and employer when the wording is contextual) instead of the rule.
PARAM_KEYS = {"history.prior_employee", "skills.experience", "prefs.relocate_to", "availability.covers_interval", "consent"}


class Bank:
    """Equivalence mappings: new wordings -> an existing semantic key. Plain JSON you can read and edit."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self.data = {"version": 0, "equivalents": []}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self._compile()

    def _compile(self):
        self.exact = {}
        self.patterns = []
        for e in self.data.get("equivalents", []):
            for lab in e.get("labels", []):
                self.exact[norm(lab)] = e["key"]
            for p in e.get("patterns", []):
                self.patterns.append((re.compile(p), e["key"]))

    def key_for(self, q: Question) -> str | None:
        n = q.norm
        if n in self.exact:
            return self.exact[n]
        for rx, key in self.patterns:
            if rx.fullmatch(n):
                return key
        return None

    def add(self, key: str, labels=(), patterns=()):
        for p in patterns:
            if not (p.startswith("^") and p.endswith("$")):
                raise ValueError(f"equivalence pattern must be anchored: {p}")
            re.compile(p)
        eqs = self.data.setdefault("equivalents", [])
        cur = next((e for e in eqs if e["key"] == key), None)
        if not cur:
            cur = {"key": key, "labels": [], "patterns": []}
            eqs.append(cur)
        cur["labels"] = sorted(set(cur["labels"]) | set(labels))
        cur["patterns"] = sorted(set(cur["patterns"]) | set(patterns))
        self._compile()

    def save(self):
        if self.path:
            self.data["version"] = int(self.data.get("version", 0)) + 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)


class AnswerEngine:
    def __init__(self, db: DB, bank: Bank | None = None, consents=None):
        self.db = db
        self.facts = Facts(db)
        self.bank = bank or Bank(None)
        self.consents = list(consents or [])

    # ------------------------------------------------------------------ keys and memory
    def key_for(self, q: Question) -> str:
        k = self.bank.key_for(q)
        if k and k not in PARAM_KEYS:
            return k
        if k:
            return q.fallback_key()
        # builtin rule keys are computed by running the matcher without facts
        for key, rx, _ in RULES:
            if rx.match(q.norm):
                return q.fallback_key() if key in PARAM_KEYS else key
        return q.fallback_key()

    def _memory(self, keys, q: Question, bases=("explicit",)):
        rows = []
        for k in keys:
            rows += self.db.all(f"SELECT * FROM answer_memory WHERE semantic_key=? AND invalidated_at IS NULL AND basis IN ({','.join('?'*len(bases))})",
                                (k, *bases))
        best = None
        for r in rows:
            sc = loads(r["scope_json"], {"kind": "user"})
            kind = sc.get("kind", "user")
            if kind == "job" and sc.get("job") != q.job_id:
                continue
            if kind == "employer" and norm(sc.get("employer", "")) != norm(q.employer):
                continue
            rank = ({"job": 3, "employer": 2, "user": 1}[kind], r["updated_at"])
            if not best or rank > best[0]:
                best = (rank, r)
        return best[1] if best else None

    def memory_ok(self, ans_id: str) -> bool:
        r = self.db.one("SELECT invalidated_at FROM answer_memory WHERE id=?", (ans_id,))
        return bool(r) and r["invalidated_at"] is None

    def remember(self, key: str, q_label: str, value, scope: dict, basis: str = "explicit", evidence=None, options=None, derivation="") -> str:
        """Save an answer. An explicit answer (you) supersedes derived/generated answers for the same key+scope."""
        aid = new_id("ans")
        with self.db.tx():
            if basis == "explicit":
                for r in self.db.all("SELECT id, scope_json FROM answer_memory WHERE semantic_key=? AND invalidated_at IS NULL", (key,)):
                    if loads(r["scope_json"]) == scope:
                        self.db.x("UPDATE answer_memory SET invalidated_at=? WHERE id=?", (now_iso(), r["id"]))
            self.db.x("""INSERT INTO answer_memory(id,semantic_key,wording,options_json,value_json,scope_json,basis,evidence_json,derivation,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (aid, key, q_label[:1000], dumps(list(options or [])), dumps(value), dumps(scope), basis, dumps(list(evidence or [])),
                       derivation, now_iso(), now_iso()))
            self.db.event("system", aid, "answer_saved", {"key": key, "basis": basis, "scope": scope})
        return aid

    # ------------------------------------------------------------------ resolution
    def ctx(self, job: dict, materials: dict | None = None, cover_letter=False, today: date | None = None) -> Ctx:
        return Ctx(facts=self.facts, today=today or date.today(), employer=job.get("company", ""), job_id=job.get("id", ""),
                   job_title=job.get("title", ""), job_location=job.get("location", ""), consents=self.consents,
                   cover_letter=cover_letter, materials=materials or {})

    def propose(self, q: Question, ctx: Ctx) -> Proposal:
        key = self.key_for(q)
        fallback = q.fallback_key()
        keys = [key] + ([fallback] if fallback != key else [])
        # 1-2: your explicit answers (exact job first, then equivalent with compatible scope)
        mem = self._memory(keys, q, ("explicit",))
        if mem:
            v = loads(mem["value_json"])
            return Proposal(value=v, basis="explicit", evidence=[mem["id"]], key=key, scope=loads(mem["scope_json"]),
                            synonyms=_bool_syn(v))
        # 3-4: facts and derivations
        bkey, prop = builtin(q, ctx, key if key in BUILTIN_KEYS else None)
        if prop is not None and prop.decision == "answer":
            prop.key = prop.key or key
            return prop
        # 6: grounded text prepared for this job/employer
        gen = self._memory(keys + ([bkey] if bkey and bkey not in keys else []), q, ("generated",))
        if gen:
            ev = loads(gen["evidence_json"], [])
            return Proposal(value=loads(gen["value_json"]), basis="generated", evidence=[gen["id"], *ev], key=key,
                            scope=loads(gen["scope_json"]), kind="long")
        if prop is not None:
            prop.key = prop.key or key
            return prop
        return need("no saved answer for this question yet", [], key)

    def answer(self, q: Question, ctx: Ctx) -> Validated | Proposal:
        p = self.propose(q, ctx)
        return validate(p, q, self.facts, self.memory_ok, ctx.today)

    # ------------------------------------------------------------------ pending questions
    def record_pending(self, q: Question, job_id: str, app_id: str, key: str, reason: str) -> str:
        with self.db.tx():
            r = self.db.one("SELECT id FROM pending_questions WHERE semantic_key=? AND job_id=?", (key, job_id))
            data = {**q.to_dict(), "reason": reason, "key": key}
            if r:
                self.db.x("UPDATE pending_questions SET question_json=?, last_seen=?, resolved_at=NULL, application_id=? WHERE id=?",
                          (dumps(data), now_iso(), app_id, r["id"]))
                return r["id"]
            pid = new_id("pq")
            self.db.x("""INSERT INTO pending_questions(id,semantic_key,question_json,job_id,application_id,first_seen,last_seen)
                         VALUES(?,?,?,?,?,?,?)""", (pid, key, dumps(data), job_id, app_id, now_iso(), now_iso()))
            return pid


def _bool_syn(v):
    if isinstance(v, str) and norm(v) in ("yes", "no"):
        return [r"^yes\b"] if norm(v) == "yes" else [r"^no\b"]
    return []
