"""Typed requests between the extension bridge and the service, and which channel may send which.

Channels are assigned by the extension's background worker from the sender it can verify (the paired board page,
an Outlook tab, or the extension's own settings page), never from anything a page claims.
"""
from __future__ import annotations

import re
import uuid

BOARD, OUTLOOK, SETTINGS, CLI = "board", "outlook", "settings", "cli"

ALLOWED = {
    "worker.status": {BOARD, SETTINGS, CLI, OUTLOOK},
    "application.enqueue": {BOARD, CLI},
    "application.status": {BOARD, SETTINGS, CLI},
    "application.cancel": {BOARD, CLI},
    "application.resume": {BOARD, CLI},
    "application.check": {BOARD, CLI},
    "application.resolve_uncertain": {BOARD, CLI},
    "application.set_cover": {BOARD, CLI},
    "mail.wanted": {OUTLOOK, CLI},
    "mail.ingest": {OUTLOOK, CLI},
    "account.list": {SETTINGS, CLI},
    "account.resolve": {SETTINGS, CLI},
    "credentials.set": {SETTINGS, CLI},
    "credentials.status": {SETTINGS, CLI},
    "profile.update": {SETTINGS, CLI},
    "pending.list": {SETTINGS, CLI},
    "settings.get": {SETTINGS, CLI},
    "settings.set": {SETTINGS, CLI},
}

ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
# Job boards and school portals: the worker never creates accounts or applies there.
AGGREGATORS = re.compile(r"(^|\.)(linkedin\.com|indeed\.com|glassdoor\.com|symplicity\.com|joinhandshake\.com|simplify\.jobs|ziprecruiter\.com|monster\.com)$")


class BadRequest(ValueError):
    pass


def _str(d, k, required=True, max_len=2000, pattern=None):
    v = d.get(k)
    if v is None or v == "":
        if required:
            raise BadRequest(f"{k} is required")
        return ""
    if not isinstance(v, str) or len(v) > max_len:
        raise BadRequest(f"{k} must be a string up to {max_len} characters")
    if pattern and not pattern.match(v):
        raise BadRequest(f"{k} has an invalid format")
    return v


def validate(channel: str, msg: dict) -> dict:
    if not isinstance(msg, dict):
        raise BadRequest("request must be an object")
    t = msg.get("type")
    if t not in ALLOWED:
        raise BadRequest(f"unknown request type {t!r}")
    if channel not in ALLOWED[t]:
        raise BadRequest(f"{t} isn't allowed from the {channel} channel")
    out = {"type": t}
    if t == "application.enqueue":
        out["request_id"] = _str(msg, "request_id", pattern=re.compile(r"^[A-Za-z0-9-]{8,80}$"))
        url = _str(msg, "source_url", max_len=2000)
        if not url.startswith("https://"):
            raise BadRequest("source_url must be https")
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower()
        if AGGREGATORS.search(host):
            raise BadRequest("that link is a job board, not the employer's application; open the employer posting instead")
        out["source_url"] = url
        out["job_ref"] = _str(msg, "job_ref", required=False, max_len=64, pattern=ID)
        for k in ("company", "title", "location"):
            out[k] = _str(msg, k, required=False, max_len=300)
        out["material_policy"] = _str(msg, "material_policy", required=False, max_len=40) or "saved_default"
    elif t in ("application.cancel", "application.resume", "application.check"):
        out["application_id"] = _str(msg, "application_id", pattern=ID)
    elif t == "application.resolve_uncertain":
        out["application_id"] = _str(msg, "application_id", pattern=ID)
        out["resolution"] = _str(msg, "resolution")
        if out["resolution"] not in ("submitted", "not_submitted"):
            raise BadRequest("resolution must be submitted or not_submitted")
        out["note"] = _str(msg, "note", required=False, max_len=500)
    elif t == "application.set_cover":
        out["job_ref"] = _str(msg, "job_ref", pattern=ID)
        out["on"] = bool(msg.get("on"))
    elif t == "application.status":
        c = msg.get("cursor", 0)
        out["cursor"] = c if isinstance(c, int) and c >= 0 else 0
    elif t == "mail.ingest":
        m = msg.get("message")
        if not isinstance(m, dict):
            raise BadRequest("message must be an object")
        out["message"] = {
            "message_id": _str(m, "message_id", max_len=400),
            "received_at": _str(m, "received_at", required=False, max_len=40),
            "from": _str(m, "from", required=False, max_len=300),
            "to": _str(m, "to", required=False, max_len=300),
            "subject": _str(m, "subject", required=False, max_len=500),
            "body_text": _str(m, "body_text", required=False, max_len=20000),
            "links": [u for u in (m.get("links") or []) if isinstance(u, str) and len(u) < 2000][:50],
            "codes": [c for c in (m.get("codes") or []) if isinstance(c, str) and re.fullmatch(r"\d{4,8}", c)][:5],
        }
    elif t == "account.resolve":
        out["account_id"] = _str(msg, "account_id", pattern=ID)
        out["action"] = _str(msg, "action", max_len=40)
        out["ref"] = _str(msg, "ref", required=False, max_len=200)
        out["host"] = _str(msg, "host", required=False, max_len=253, pattern=re.compile(r"^[a-z0-9.-]+$"))
        out["value"] = msg.get("value") if isinstance(msg.get("value"), str) and len(msg.get("value")) <= 500 else None
    elif t == "credentials.set":
        out["what"] = _str(msg, "what", max_len=20)
        if out["what"] not in ("password", "username"):
            raise BadRequest("what must be password or username")
        v = msg.get("value")
        if not isinstance(v, str) or not (1 <= len(v) <= 500):
            raise BadRequest("value required")
        out["value"] = v
        out["account_id"] = _str(msg, "account_id", required=False, pattern=ID)
    elif t == "profile.update":
        facts = msg.get("facts")
        if not isinstance(facts, list) or len(facts) > 200:
            raise BadRequest("facts must be a list")
        out["facts"] = facts
    elif t == "settings.set":
        if not isinstance(msg.get("values"), dict):
            raise BadRequest("values must be an object")
        out["values"] = msg["values"]
    return out


def new_request_id() -> str:
    return str(uuid.uuid4())
