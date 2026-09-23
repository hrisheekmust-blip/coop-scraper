"""Redaction for everything that gets persisted or logged, and evidence records.

Secrets never reach events, logs, diagnostics or traces: values registered here (passwords, tokens,
verification links, codes) are scrubbed from any string, and keys that name secrets are dropped.
"""
from __future__ import annotations

import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()

SECRET_KEY = re.compile(r"pass(word)?|secret|token|cookie|otp|mfa|credential_value|verification_(link|code)|reset_link|storage_state|authorization|api[_-]?key", re.I)
# Things that look like one-time links or codes even if nobody registered them.
LINKISH = re.compile(r"https?://\S*(?:token|code|verify|activate|activation|reset|otp|signature|sig|key)=[^\s\"'&]+[^\s\"']*", re.I)


def register_secret(value: str | None):
    if value and len(value) >= 4:
        with _lock:
            _secrets.add(value)


def forget_secret(value: str | None):
    with _lock:
        _secrets.discard(value or "")


def scrub(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    with _lock:
        known = sorted(_secrets, key=len, reverse=True)
    for s in known:
        if s in text:
            text = text.replace(s, "[redacted]")
    return LINKISH.sub("[redacted-link]", text)


def redact(value, depth=0):
    if depth > 12:
        return "[truncated]"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and SECRET_KEY.search(k) and not k.endswith("_ref"):
                out[k] = "[redacted]"
            else:
                out[k] = redact(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        return scrub(value)
    return value
