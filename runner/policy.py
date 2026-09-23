"""Answer proposals and the independent validator.

Every answer the worker types goes through `validate`. It re-checks the evidence (exists, current, valid today,
right scope), the logical direction encoded by the rule, the control's format, and that the value maps to exactly
one of the options actually on the page. A rule can propose; only this module lets an answer through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .questions import Question, norm


@dataclass
class Proposal:
    decision: str = "answer"            # answer | need
    value: object = None                # str | list[str]
    basis: str = ""                     # explicit | fact | derived | bank | generated | material | policy
    evidence: list = field(default_factory=list)
    derivation: str = ""
    scope: dict = field(default_factory=lambda: {"kind": "user"})
    reason: str = ""
    missing: list = field(default_factory=list)
    key: str = ""
    kind: str = "value"                 # value | long | file | skip | consent
    synonyms: list = field(default_factory=list)   # extra option patterns (regex strings), tried in order, must be unique
    sensitive: bool = False


def need(reason: str, missing=None, key: str = "") -> Proposal:
    return Proposal(decision="need", reason=reason, missing=list(missing or []), key=key)


@dataclass
class Validated:
    key: str
    values: list                        # what to put in the control: option labels, or [text]
    proposal: Proposal

    @property
    def text(self):
        return self.values[0] if self.values else ""


YES = [r"^yes\b", r"^y$", r"^true$"]
NO = [r"^no\b", r"^n$", r"^false$"]


def match_options(options: list[str], wanted: list[str], synonyms: list[str] | None = None) -> list[str] | None:
    """Map each wanted value to exactly one option. Exact (normalized) text first, then the rule's synonym
    patterns in order. Anything ambiguous or missing returns None: never 'closest'."""
    if not options:
        return None
    normed = [norm(o) for o in options]
    out = []
    for w in wanted:
        nw = norm(w)
        hits = [o for o, n in zip(options, normed) if n == nw]
        if len(hits) != 1:
            hits = []
            for pat in synonyms or []:
                rx = re.compile(pat, re.I)
                cand = [o for o, n in zip(options, normed) if rx.search(n)]
                if len(cand) == 1:
                    hits = cand
                    break
                if len(cand) > 1:
                    return None
        if len(hits) != 1:
            return None
        out.append(hits[0])
    return out


def validate(p: Proposal, q: Question, facts, memory_lookup=None, today=None) -> Validated | Proposal:
    if p.decision != "answer":
        return p
    if p.kind in ("file", "skip"):
        return Validated(p.key, [], p)
    # Evidence: must exist, be current, valid today, and (for sensitive facts) come from you, not a derivation.
    for ev in p.evidence:
        if ev.startswith("fact_"):
            f = facts.exists(ev)
            if not f or not f["current"]:
                return need("the fact behind this answer changed", key=p.key)
            day = str(today) if today else None
            if day and ((f["valid_from"] and day < f["valid_from"][:10]) or (f["valid_until"] and day > f["valid_until"][:10])):
                return need("the fact behind this answer isn't valid today", key=p.key)
            sc = f["scope"] or {}
            if sc.get("kind") == "employer" and norm(sc.get("employer", "")) != norm(q.employer):
                return need("that answer was for a different employer", key=p.key)
            if sc.get("kind") == "job" and sc.get("job") != q.job_id:
                return need("that answer was for a different job", key=p.key)
        elif ev.startswith("ans_"):
            if memory_lookup and not memory_lookup(ev):
                return need("a saved answer behind this was withdrawn", key=p.key)
        elif not ev.startswith(("material:", "policy:", "bank:")):
            return need("unrecognized evidence", key=p.key)
    if p.basis not in ("explicit", "policy", "material") and not p.evidence:
        return need("no evidence for this answer", key=p.key)
    sc = p.scope or {"kind": "user"}
    if sc.get("kind") == "job" and sc.get("job") != q.job_id:
        return need("that answer was for a different job", key=p.key)
    if sc.get("kind") == "employer" and norm(sc.get("employer", "")) != norm(q.employer):
        return need("that answer was for a different employer", key=p.key)

    values = p.value if isinstance(p.value, list) else [p.value]
    values = [str(v) for v in values if v is not None and str(v).strip() != ""]
    if not values:
        return need("empty answer", key=p.key)
    opts = q.option_list()
    if opts:
        if len(values) > 1 and not q.multiple:
            return need("a single choice is required", key=p.key)
        syn = list(p.synonyms)
        if len(values) == 1 and norm(values[0]) in ("yes", "no") and not syn:
            syn = YES if norm(values[0]) == "yes" else NO
        mapped = match_options(opts, values, syn)
        if not mapped:
            return need("no single option matches the saved answer", key=p.key)
        return Validated(p.key, mapped, p)
    if q.control in ("select", "radio", "combobox") and not opts:
        return need("the choices weren't readable", key=p.key)
    text = values[0] if len(values) == 1 else ", ".join(values)
    if q.control == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return need("an exact number is needed", key=p.key)
    if q.control == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return need("an exact calendar date is needed", key=p.key)
    if q.control == "email" and "@" not in text:
        return need("not an email address", key=p.key)
    if q.max_len and len(text) > q.max_len:
        return need(f"the saved text is longer than the {q.max_len}-character limit", key=p.key)
    return Validated(p.key, [text], p)
