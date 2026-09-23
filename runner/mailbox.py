"""Mail events from the Outlook bridge: account verification, login codes, and application outcomes.

Email content is untrusted data. It can resolve a pending verification only when every correlation check
passes (waiting account, recipient, sender/provider, time window, purpose, and a link host inside the realm's
approved hosts). It can never add a host, change credentials, or trigger an action by itself. Each message is
processed once; tokens go straight into the vault and never into logs or events.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import jobqueue as Q, models as M
from .accounts import Accounts
from .db import DB, dumps, now_iso

RESET = re.compile(r"reset (your )?password|password reset|forgot (your )?password", re.I)
ACTIVATION = re.compile(r"verify (your )?(candidate )?(e-?mail|account|email address)|activate (your )?(candidate )?account|confirm (your )?(candidate )?(e-?mail|account|email address)|e-?mail verification|complete your (registration|account)|account activation", re.I)
CODE_WORDS = re.compile(r"(verification|security|one[- ]time|login|sign[- ]in|access|confirmation) (code|pin)|passcode|\botp\b|use this code|your code is", re.I)
RECOMMEND = re.compile(r"jobs? (you may|you might|recommended|matching|alert)|new jobs|similar jobs|job alert|recommended for you|jobs for you", re.I)
REJECT = re.compile(r"unfortunately|not (be )?moving forward|regret|other candidates|decided not to|will not be (moving|proceeding)|no longer under consideration|position has been filled", re.I)
STRONG_INTERVIEW = re.compile(r"invit\w* you to|interview (invitation|request)|schedul\w* (an? |your )?(interview|call|time|conversation|phone screen)|availability for (a|an) (call|interview)|coding challenge|hackerrank|codesignal|online assessment|phone screen", re.I)
CONFIRM = re.compile(r"thank you for (applying|your application|your interest)|application (has been |was )?(received|submitted)|we('ve| have) received your application|received your application|confirm(ation|ing) (of )?your application|your application (to|for)", re.I)

PORTAL_SENDERS = {
    "workday": re.compile(r"myworkday(jobs)?\.com|workday\.com", re.I),
    "successfactors": re.compile(r"successfactors\.(com|eu)|sapsf\.|sap\.com", re.I),
    "oracle": re.compile(r"oracle(cloud)?\.com|taleo\.net", re.I),
    "icims": re.compile(r"icims\.com", re.I),
}
WINDOW = timedelta(hours=72)
STOP = set("intern internship co-op coop engineer engineering spring summer fall winter 2026 2027 student the and for with".split())


CODE_NEAR = re.compile(r"(?:code|passcode|pin|otp)\b[^0-9]{0,40}?\b(\d{4,8})\b|\b(\d{4,8})\b[^0-9]{0,25}(?:is your|as your|is the) (?:verification |one[- ]time |security |login )?(?:code|passcode|pin)"
                       r"|(?:code|passcode)\b[^\n]{0,60}?[:：]\s*(\d{4,8})\b", re.I)


def codes_in(text: str) -> list[str]:
    """Only digits next to code wording count ("Your code is 482913"), not years or phone numbers."""
    return list(dict.fromkeys(next(x for x in m if x) for m in CODE_NEAR.findall(text or "")))


def mail_safe(text: str) -> str:
    """What may be stored about an email: codes and links removed."""
    from .evidence import scrub
    t = re.sub(r"https?://\S+", "[link]", text or "")
    return scrub(re.sub(r"\b\d{4,8}\b", "[number]", t))


def purpose_of(subject: str, body: str) -> str:
    t = f"{subject}\n{body}"
    if RESET.search(t):
        return "reset"
    if ACTIVATION.search(t):
        return "activation"
    if CODE_WORDS.search(t) and codes_in(t):
        return "login_code"
    if RECOMMEND.search(subject) or (RECOMMEND.search(t) and not CONFIRM.search(t)):
        return "recommendation"
    if REJECT.search(t):
        return "rejection"
    if STRONG_INTERVIEW.search(t):
        return "interview"
    if CONFIRM.search(t):
        return "confirmation"
    if re.search(r"\binterview", t, re.I):
        return "interview"
    return "other"


def _dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _unwrap(u: str) -> str:
    """Outlook rewrites links through safelinks; the real destination is in its url parameter."""
    from urllib.parse import parse_qs
    try:
        p = urlsplit(u)
        if (p.hostname or "").lower().endswith("safelinks.protection.outlook.com"):
            return parse_qs(p.query).get("url", [""])[0]
    except ValueError:
        return ""
    return u


def _host(u):
    try:
        p = urlsplit(u)
        return (p.hostname or "").lower() if p.scheme == "https" else ""
    except ValueError:
        return ""


class Mailbox:
    def __init__(self, db: DB, accounts: Accounts):
        self.db, self.accounts = db, accounts

    def ingest(self, msg: dict) -> dict:
        key = str(msg.get("message_id") or "").strip()
        if not key or len(key) > 400:
            return {"decision": "rejected", "reason": "missing stable message id"}
        prior = self.db.one("SELECT decision, purpose FROM mail_events WHERE message_key=?", (key,))
        # Consumed/ignored messages are final. A verification email that arrived before its account was marked as
        # waiting (the portal is often faster than the page) is looked at again; it still can only be used once.
        if prior and not (prior["decision"] == "unmatched" and prior["purpose"] in ("activation", "login_code")):
            return {"decision": prior["decision"], "purpose": prior["purpose"], "replayed": True}
        subject = str(msg.get("subject") or "")[:500]
        body = str(msg.get("body_text") or "")[:20000]
        purpose = purpose_of(subject, body)
        if purpose in ("activation", "login_code", "reset"):
            res = self._verification(msg, purpose, subject, body)
        elif purpose in ("confirmation", "rejection", "interview"):
            res = self._outcome(msg, purpose, subject, body)
        else:
            res = {"decision": "ignored"}
        with self.db.tx():
            self.db.x("DELETE FROM mail_events WHERE message_key=? AND decision='unmatched'", (key,))
            self.db.x("""INSERT OR IGNORE INTO mail_events(message_key,received_at,sender,subject,purpose,decision,account_id,application_id,candidates_json,processed_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (key, msg.get("received_at"), mail_safe(str(msg.get("from") or ""))[:300], mail_safe(subject)[:300], purpose, res["decision"],
                       res.get("account_id"), res.get("application_id"), dumps(res.get("candidates", [])), now_iso()))
            self.db.event("mail", key, "ingested", {"purpose": purpose, "decision": res["decision"]})
        return {"purpose": purpose, **{k: v for k, v in res.items() if k != "secret"}}

    # ---------------------------------------------------------------- verification
    def _verification(self, msg, purpose, subject, body):
        if purpose == "reset":
            # We never request resets automatically (policy); an unexpected reset email is only noted.
            return {"decision": "ignored", "reason": "password reset emails are never acted on automatically"}
        received = _dt(msg.get("received_at"))
        if not received:
            return {"decision": "unmatched", "reason": "no reliable received time"}
        to = str(msg.get("to") or "").lower()
        app_email = self.accounts.application_email().lower()
        if to and app_email and app_email not in to:
            return {"decision": "ignored", "reason": "sent to a different address"}
        sender = str(msg.get("from") or "")
        links = [_unwrap(str(u)) for u in (msg.get("links") or []) if isinstance(u, str)][:50]
        codes = codes_in(f"{subject}\n{body}")
        matches = []
        for w in self.accounts.waiting():
            since = _dt(w["since"])
            if not received or not since or received < since - timedelta(minutes=2) or received > since + WINDOW:
                continue
            if w["purpose"] != purpose:
                continue
            words = [w["tenant"].split(":")[0]] if w["tenant"] else []
            sender_ok = bool(PORTAL_SENDERS.get(w["portal"], re.compile(r"$^")).search(sender)) or any(
                x and len(x) >= 3 and x.lower() in (sender + " " + subject).lower() for x in words)
            if not sender_ok:
                continue
            if purpose == "activation":
                good = [u for u in links if _host(u) in {h.lower() for h in w["hosts"]}]
                if not good:
                    continue
                matches.append((w, good[0]))
            else:
                if len(set(codes)) != 1:
                    continue
                matches.append((w, codes[0]))
        if len(matches) != 1:
            return {"decision": "unassigned" if matches else "unmatched", "candidates": [m[0]["account_id"] for m in matches]}
        w, secret = matches[0]
        self.accounts.store_verification(w["account_id"], secret, purpose)
        return {"decision": "consumed", "account_id": w["account_id"]}

    # ---------------------------------------------------------------- application outcomes
    def _outcome(self, msg, purpose, subject, body):
        """An employer email changes at most one application, and only when it names that role (title or
        requisition) and arrived after that application was submitted. Anything weaker is kept unassigned."""
        text = f"{msg.get('from') or ''} {subject} {body}"
        lower = text.lower()
        received = _dt(msg.get("received_at"))
        rows = self.db.all("""SELECT a.*, j.company, j.title, j.requisition, j.resolved_url,
                                (SELECT MIN(submit_intent_at) FROM attempts t WHERE t.application_id=a.id AND t.submit_intent_at IS NOT NULL) submitted_at
                              FROM applications a JOIN jobs j ON j.id=a.job_id
                              WHERE a.state IN (?,?,?,?)""", (M.APPLIED, M.UNCERTAIN, M.VERIFYING, M.SUBMITTING))
        cands = [r for r in rows if r["company"] and _company_rx(r["company"]).search(text)]
        if not cands:
            return {"decision": "unmatched"}
        pick = _disambiguate(cands, lower, require=True)
        if not pick:
            return {"decision": "unassigned", "candidates": [c["id"] for c in cands]}
        after = pick["submitted_at"] or pick["created_at"]
        if not received or (after and received < _dt(after) - timedelta(minutes=5)):
            return {"decision": "unassigned", "candidates": [pick["id"]], "reason": "older than the application, or no received time"}
        received_s = received.isoformat().replace("+00:00", "Z")
        kind = {"confirmation": "confirmation", "rejection": "rejected", "interview": "interview"}[purpose]
        if pick["state"] in (M.UNCERTAIN, M.VERIFYING, M.SUBMITTING):
            Q.confirm(self.db, pick["id"], "email", {"text": f"{purpose} email: {mail_safe(subject)[:200]}", "at": received_s}, "outlook bridge")
        with self.db.tx():
            cur = self.db.one("SELECT employer_status_at FROM applications WHERE id=?", (pick["id"],))
            older = cur["employer_status_at"] and str(cur["employer_status_at"]) > received_s
            # Chronology: an older email never overwrites a newer outcome. A confirmation never downgrades.
            if not older and not (kind == "confirmation" and cur["employer_status_at"]):
                self.db.x("UPDATE applications SET employer_status=?, employer_status_at=?, updated_at=? WHERE id=?",
                          (kind, received_s, now_iso(), pick["id"]))
            self.db.event("application", pick["id"], "employer_email", {"kind": kind, "subject": mail_safe(subject)[:200]})
        return {"decision": "consumed", "application_id": pick["id"]}


def _company_rx(company: str):
    n = re.sub(r"[^\w\s&.-]", "", company)
    n = re.sub(r"\b(inc|corp|corporation|llc|ltd|technologies|technology|systems|labs|the)\b\.?", "", n, flags=re.I).strip()
    first = n.split()[0] if n.split() else company
    return re.compile(r"\b" + re.escape(n if len(n) >= 4 else first) + r"\b", re.I)


def _role_words(role: str):
    role = re.sub(r"\([^)]*\)", " ", role.lower())
    return [w for w in re.split(r"[^a-z0-9+#]+", role) if len(w) >= 3 and w not in STOP]


def _disambiguate(cands, lower, require=False):
    """The one application an email is about: its requisition id, or most of its role title's distinctive words.
    With require=True a single company match isn't enough on its own."""
    def score(r):
        req = (r["requisition"] or "").lower()
        if req and len(req) >= 4 and re.search(r"\b" + re.escape(req) + r"\b", lower):
            return 10
        words = _role_words(r["title"] or "")
        if not words:
            return 0
        return sum(1 for w in words if re.search(r"\b" + re.escape(w) + r"\b", lower)) / len(words)
    ranked = sorted(((score(r), r) for r in cands), key=lambda x: -x[0])
    top = ranked[0][0]
    if top < 0.6:
        return None
    if len(ranked) > 1 and ranked[1][0] >= top:
        return None
    return ranked[0][1]
