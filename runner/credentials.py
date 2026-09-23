"""Secrets: the vault, the credential policy, and encrypted browser sessions.

The database and everything the planner/answer code sees hold only references like
`vault://apply/default_password`. Values are resolved inside `fill_credential` (browser.py) at the moment they are
typed, registered for redaction, and never returned to callers that persist or log.

On Windows the vault is Windows Credential Manager (via `keyring`), which encrypts per user with DPAPI.
Authenticated browser state is treated as a credential: it is encrypted with a key that lives in the vault.
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
from pathlib import Path

from .evidence import register_secret

SERVICE = "coop-apply-runner"

DEFAULT_POLICY = {
    "credential_mode": "shared",
    "application_email_fact": "contact.application_email",
    "preferred_username_secret": "vault://apply/default_username",
    "password_secret": "vault://apply/default_password",
    "username_for_email_login": "application_email",
    "username_alternatives": [],
    "allow_required_account_creation": True,
    "allow_existing_account_password_reset": False,
    "email_verification": "outlook_bridge",
    "optional_marketing_consent": False,
    # Consents the user pre-approved for account creation / application. Anything else becomes an exception.
    "approved_consents": ["privacy_policy", "terms_of_use", "data_processing", "accuracy_certification"],
}


class Vault:
    def get(self, ref: str) -> str | None: ...
    def set(self, ref: str, value: str) -> None: ...
    def delete(self, ref: str) -> None: ...

    def reveal(self, ref: str) -> str | None:
        v = self.get(ref)
        register_secret(v)
        return v

    def has(self, ref: str) -> bool:
        return bool(self.get(ref))


def _check_ref(ref: str):
    if not isinstance(ref, str) or not ref.startswith("vault://") or len(ref) > 200:
        raise ValueError("bad vault reference")


class KeyringVault(Vault):
    def __init__(self):
        import keyring  # Windows Credential Manager backend on Windows
        self.k = keyring

    def get(self, ref):
        _check_ref(ref)
        return self.k.get_password(SERVICE, ref)

    def set(self, ref, value):
        _check_ref(ref)
        self.k.set_password(SERVICE, ref, value)
        register_secret(value)

    def delete(self, ref):
        _check_ref(ref)
        try:
            self.k.delete_password(SERVICE, ref)
        except Exception:
            pass


class MemoryVault(Vault):
    """Tests only."""

    def __init__(self, data=None):
        self.data = dict(data or {})
        for v in self.data.values():
            register_secret(v)

    def get(self, ref):
        _check_ref(ref)
        return self.data.get(ref)

    def set(self, ref, value):
        _check_ref(ref)
        self.data[ref] = value
        register_secret(value)

    def delete(self, ref):
        self.data.pop(ref, None)


def default_vault() -> Vault:
    if os.environ.get("COOP_RUNNER_VAULT") == "memory":
        return MemoryVault()
    return KeyringVault()


# ------------------------------------------------------------------------------------ encrypted sessions
class SessionStore:
    """Playwright storage_state (cookies + localStorage) encrypted at rest. The key is in the vault."""

    KEY_REF = "vault://runner/session_key"

    def __init__(self, vault: Vault, directory: str | os.PathLike):
        self.vault = vault
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _fernet(self):
        from cryptography.fernet import Fernet
        key = self.vault.get(self.KEY_REF)
        if not key:
            key = Fernet.generate_key().decode()
            self.vault.set(self.KEY_REF, key)
        return Fernet(key.encode())

    def save(self, account_id: str, state: dict) -> str:
        blob = self._fernet().encrypt(json.dumps(state).encode())
        path = self.dir / f"{_safe(account_id)}.bin"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        return f"session://{path.name}"

    def load(self, ref: str) -> dict | None:
        if not ref or not ref.startswith("session://"):
            return None
        path = self.dir / _safe(ref[len("session://"):])
        if not path.exists():
            return None
        try:
            return json.loads(self._fernet().decrypt(path.read_bytes()))
        except Exception:
            return None

    def delete(self, ref: str):
        if ref and ref.startswith("session://"):
            p = self.dir / _safe(ref[len("session://"):])
            if p.exists():
                p.unlink()


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]


def one_time_ref(kind: str) -> str:
    return f"vault://pending/{kind}/{_secrets.token_hex(8)}"
