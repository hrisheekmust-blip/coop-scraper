"""The versioned fact store: what is true about you, where it came from, and for how long.

Facts are only ever things you stated or confirmed (profile, saved answers, a resume line you confirmed).
Generated text and derived answers are NOT facts; they live in answer_memory and point back here.
Changing a fact supersedes the old one and invalidates every answer derived from it.
"""
from __future__ import annotations

from datetime import date

from .db import DB, dumps, loads, new_id, now_iso

SENSITIVE = {"citizenship.us_citizen", "eeo.gender", "eeo.race", "eeo.veteran", "eeo.disability", "eeo.orientation",
             "work_auth.us_authorized", "work_auth.sponsorship_now", "work_auth.sponsorship_future", "work_auth.sponsorship_now_or_future"}

# Legacy board profile (coop-apps me/profile.json) -> predicates. Only keys that hold stated facts.
LEGACY = {
    "first": "contact.first_name", "last": "contact.last_name", "preferred": "contact.preferred_name", "name": "contact.full_name",
    "email": "contact.application_email", "phone": "contact.phone", "linkedin": "contact.linkedin", "github": "contact.github",
    "portfolio": "contact.portfolio", "city": "address.city", "state": "address.state", "zip": "address.zip",
    "country": "address.country", "location": "address.location_text", "school": "education.school", "major": "education.major",
    "degree": "education.degree", "year": "education.year_standing", "gpa": "education.gpa", "act": "test_scores.act",
    "test_scores": "test_scores.summary", "availability": "availability.summary", "salary": "prefs.salary",
    "hear": "prefs.hear_about", "preferred_locations": "prefs.locations", "terms_wanted": "availability.terms",
    "authorized": "work_auth.us_authorized", "sponsorship": "work_auth.sponsorship_now_or_future", "citizen": "citizenship.us_citizen",
    "relocate": "prefs.relocate_general", "over18": "person.over_18", "clearance": "clearance.active", "eeo": "prefs.eeo_policy",
}
BOOL_KEYS = {"authorized", "sponsorship", "relocate", "over18", "clearance", "prior_employee"}


def _bool(v):
    s = str(v).strip().lower()
    if s in ("yes", "true", "y", "1"):
        return True
    if s in ("no", "false", "n", "0"):
        return False
    return None


class Facts:
    def __init__(self, db: DB):
        self.db = db

    def add(self, predicate: str, value, source: dict, scope: dict | None = None, valid_from=None, valid_until=None,
            sensitivity: str | None = None, confirmed_at: str | None = None) -> str:
        scope = scope or {"kind": "user"}
        with self.db.tx():
            old = self._current_row(predicate, scope)
            if old and loads(old["value_json"]) == value and loads(old["scope_json"]) == scope \
                    and old["valid_from"] == valid_from and old["valid_until"] == valid_until:
                return old["id"]
            fid = new_id("fact")
            self.db.x("""INSERT INTO profile_facts(id,predicate,value_json,scope_json,source_json,confirmed_at,valid_from,valid_until,sensitivity,supersedes,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (fid, predicate, dumps(value), dumps(scope), dumps(source), confirmed_at or now_iso(), valid_from, valid_until,
                       sensitivity or ("sensitive" if predicate in SENSITIVE else "normal"), old["id"] if old else None, now_iso()))
            if old:
                self.db.x("UPDATE profile_facts SET superseded_by=? WHERE id=?", (fid, old["id"]))
                self._invalidate_dependents(old["id"])
            self.db.event("system", fid, "fact", {"predicate": predicate, "supersedes": old["id"] if old else None})
        return fid

    def retract(self, predicate: str, scope: dict | None = None):
        with self.db.tx():
            old = self._current_row(predicate, scope or {"kind": "user"})
            if old:
                self.db.x("UPDATE profile_facts SET superseded_by='retracted' WHERE id=?", (old["id"],))
                self._invalidate_dependents(old["id"])

    def _invalidate_dependents(self, fact_id: str):
        for r in self.db.all("SELECT id, evidence_json FROM answer_memory WHERE invalidated_at IS NULL AND basis<>'explicit'"):
            if fact_id in loads(r["evidence_json"], []):
                self.db.x("UPDATE answer_memory SET invalidated_at=? WHERE id=?", (now_iso(), r["id"]))

    def _current_row(self, predicate, scope):
        for r in self.db.all("SELECT * FROM profile_facts WHERE predicate=? AND superseded_by IS NULL ORDER BY created_at DESC", (predicate,)):
            if loads(r["scope_json"]) == scope:
                return r
        return None

    def get(self, predicate: str, scope: dict | None = None, on: date | None = None) -> dict | None:
        """Current fact for a predicate. Scoped facts (employer/job) win over user-wide ones when the scope matches."""
        on = on or date.today()
        rows = self.db.all("SELECT * FROM profile_facts WHERE predicate=? AND superseded_by IS NULL ORDER BY created_at DESC", (predicate,))
        best = None
        for r in rows:
            sc = loads(r["scope_json"], {"kind": "user"})
            if not _scope_ok(sc, scope):
                continue
            if r["valid_from"] and str(on) < r["valid_from"][:10]:
                continue
            if r["valid_until"] and str(on) > r["valid_until"][:10]:
                continue
            rank = {"job": 3, "employer": 2, "user": 1}.get(sc.get("kind"), 0)
            if not best or rank > best[0]:
                best = (rank, r)
        if not best:
            return None
        r = best[1]
        return {"id": r["id"], "predicate": r["predicate"], "value": loads(r["value_json"]), "scope": loads(r["scope_json"]),
                "source": loads(r["source_json"]), "valid_from": r["valid_from"], "valid_until": r["valid_until"],
                "sensitivity": r["sensitivity"], "confirmed_at": r["confirmed_at"]}

    def value(self, predicate: str, scope: dict | None = None, default=None):
        f = self.get(predicate, scope)
        return f["value"] if f else default

    def exists(self, fact_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM profile_facts WHERE id=?", (fact_id,))
        if not r:
            return None
        return {"id": r["id"], "current": r["superseded_by"] is None, "valid_from": r["valid_from"], "valid_until": r["valid_until"],
                "predicate": r["predicate"], "value": loads(r["value_json"]), "scope": loads(r["scope_json"]), "sensitivity": r["sensitivity"]}

    def current(self) -> list[dict]:
        return [{"id": r["id"], "predicate": r["predicate"], "value": loads(r["value_json"]), "scope": loads(r["scope_json"]),
                 "valid_from": r["valid_from"], "valid_until": r["valid_until"], "sensitivity": r["sensitivity"]}
                for r in self.db.all("SELECT * FROM profile_facts WHERE superseded_by IS NULL ORDER BY predicate")]

    # ------------------------------------------------------------------ import
    def import_legacy_profile(self, profile: dict, source_name: str = "coop-apps/me/profile.json") -> list[str]:
        """Import the board's existing profile. Unknown or ambiguous values are skipped, not guessed."""
        added = []
        src = {"kind": "profile", "record_id": source_name}
        for k, pred in LEGACY.items():
            if k not in profile or profile[k] in (None, "", []):
                continue
            v = profile[k]
            if k in BOOL_KEYS or pred.startswith("work_auth.") or pred == "citizenship.us_citizen":
                import re as _re
                b = _bool(v)
                if b is None and k == "citizen" and isinstance(v, str) and _re.fullmatch(r"(u\.?s\.?|united states) citizen", v.strip(), _re.I):
                    b = True
                if b is None:
                    continue
                v = b
            added.append(self.add(pred, v, src))
        # Graduation: keep only the precision given (YYYY-MM), never invent a day.
        gm, gy = profile.get("grad_month"), profile.get("grad_year")
        if gm and gy and str(gy).isdigit() and str(gm).isdigit():
            added.append(self.add("education.graduation", f"{int(gy):04d}-{int(gm):02d}", src))
        if profile.get("prior_employee") is not None and _bool(profile["prior_employee"]) is False:
            # A blanket "No" only holds as "no prior employers among the ones asked"; keep it as the employer list.
            pass
        return added


def _scope_ok(fact_scope: dict, want: dict | None) -> bool:
    kind = fact_scope.get("kind", "user")
    if kind == "user":
        return True
    if not want:
        return False
    if kind == "employer":
        return bool(want.get("employer")) and fact_scope.get("employer", "").lower() == want.get("employer", "").lower()
    if kind == "job":
        return bool(want.get("job")) and fact_scope.get("job") == want.get("job")
    return False
