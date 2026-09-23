"""Candidate accounts: one per (user, auth realm), created once and reused.

This module owns account state and the rules around credentials. The browser steps (typing into a login form,
pressing Create account) live in the adapters and go through `browser.Executor`, which asks this module which
credential to use and whether the current host may receive it.
"""
from __future__ import annotations

import time

from . import models as M
from .credentials import DEFAULT_POLICY, SessionStore, Vault
from .db import DB, dumps, loads, new_id, now_iso
from .identity import Realm, host_allowed

MAX_FAILED_LOGINS = 2          # stop well before typical 3-5 attempt lockouts


class AccountError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class Accounts:
    def __init__(self, db: DB, vault: Vault, sessions: SessionStore, user_id: str = "me"):
        self.db, self.vault, self.sessions, self.user = db, vault, sessions, user_id

    # ------------------------------------------------------------------ policy
    def policy(self) -> dict:
        return {**DEFAULT_POLICY, **(self.db.setting("credential_policy", {}) or {})}

    def set_policy(self, changes: dict):
        clean = {k: v for k, v in changes.items() if k in DEFAULT_POLICY}
        for k, v in clean.items():
            if k.endswith("_secret") and not (isinstance(v, str) and v.startswith("vault://")):
                raise ValueError("secrets are stored in the vault; pass a vault:// reference")
        with self.db.tx():
            self.db.set_setting("credential_policy", {**(self.db.setting("credential_policy", {}) or {}), **clean})

    def application_email(self) -> str:
        from .facts import Facts
        v = Facts(self.db).value(self.policy()["application_email_fact"])
        return v or ""

    # ------------------------------------------------------------------ realms + accounts
    def ensure_realm(self, realm: Realm, verified: bool = True):
        with self.db.tx():
            r = self.db.one("SELECT * FROM realms WHERE id=?", (realm.id,))
            if not r:
                self.db.x("INSERT INTO realms(id,portal,tenant,allowed_hosts,auth_method,verified,created_at) VALUES(?,?,?,?,?,?,?)",
                          (realm.id, realm.portal, realm.tenant, dumps(sorted(set(realm.allowed_hosts))), realm.auth_method, int(verified), now_iso()))
            else:
                hosts = sorted(set(loads(r["allowed_hosts"], [])) | set(realm.allowed_hosts)) if verified else loads(r["allowed_hosts"], [])
                self.db.x("UPDATE realms SET allowed_hosts=? WHERE id=?", (dumps(hosts), realm.id))

    def realm_hosts(self, realm_id: str) -> list[str]:
        r = self.db.one("SELECT allowed_hosts FROM realms WHERE id=?", (realm_id,))
        return loads(r["allowed_hosts"], []) if r else []

    def approve_host(self, realm_id: str, host: str):
        """Your explicit approval that a login page on `host` belongs to this realm (account.resolve)."""
        with self.db.tx():
            hosts = sorted(set(self.realm_hosts(realm_id)) | {host.lower()})
            self.db.x("UPDATE realms SET allowed_hosts=? WHERE id=?", (dumps(hosts), realm_id))
            self.db.event("account", realm_id, "host_approved", {"host": host})

    def get(self, realm_id: str):
        return self.db.one("SELECT * FROM accounts WHERE user_id=? AND realm_id=?", (self.user, realm_id))

    def ensure(self, realm: Realm):
        self.ensure_realm(realm)
        with self.db.tx():
            a = self.get(realm.id)
            if not a:
                aid = new_id("acct")
                self.db.x("""INSERT INTO accounts(id,user_id,realm_id,status,auth_method,created_at,updated_at)
                             VALUES(?,?,?,?,?,?,?)""", (aid, self.user, realm.id, M.ACC_UNKNOWN, realm.auth_method, now_iso(), now_iso()))
                self.db.event("account", aid, "created", {"realm": realm.id})
            return self.get(realm.id)

    def set_status(self, account_id: str, status: str, reason: str = "", **fields):
        cols = {"status": status, "status_reason": reason, "updated_at": now_iso(), **fields}
        with self.db.tx():
            self.db.x(f"UPDATE accounts SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), account_id))
            self.db.event("account", account_id, "status", {"to": status, "reason": reason})

    def row(self, account_id):
        return self.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))

    # ------------------------------------------------------------------ credentials
    def username_for(self, account_id: str, email_login: bool) -> tuple[str, str]:
        """(kind, value-or-ref). Email logins use the application email (not a secret); other usernames are
        vault refs. Per-account overrides you approved win."""
        a = self.row(account_id)
        over = loads(a["override_json"], {})
        if over.get("username_ref"):
            return "ref", over["username_ref"]
        ref = self.policy().get("preferred_username_secret")
        if email_login or not ref or not self.vault.has(ref):
            return "plain", self.application_email()
        return "ref", ref

    def password_ref(self, account_id: str) -> str:
        a = self.row(account_id)
        over = loads(a["override_json"], {})
        ref = over.get("password_ref") or self.policy()["password_secret"]
        if not self.vault.has(ref):
            raise AccountError("needs_credentials", "no password saved in the vault; add it in the extension settings")
        return ref

    def may_type_credentials(self, account_id: str, url: str) -> bool:
        a = self.row(account_id)
        return bool(a) and host_allowed(self.realm_hosts(a["realm_id"]), url)

    def login_failed(self, account_id: str) -> int:
        with self.db.tx():
            self.db.x("UPDATE accounts SET failed_logins=failed_logins+1, updated_at=? WHERE id=?", (now_iso(), account_id))
            n = self.row(account_id)["failed_logins"]
            if n >= MAX_FAILED_LOGINS:
                self.db.x("UPDATE accounts SET status=?, status_reason=? WHERE id=?",
                          (M.ACC_NEEDS_CREDENTIALS, "the portal rejected the saved password; stopped before a lockout", account_id))
            self.db.event("account", account_id, "login_failed", {"count": n})
        return n

    def login_ok(self, account_id: str):
        self.set_status(account_id, M.ACC_ACTIVE, "", failed_logins=0, last_login_at=now_iso())

    def can_try_login(self, account_id: str) -> bool:
        a = self.row(account_id)
        return a["failed_logins"] < MAX_FAILED_LOGINS and a["status"] not in (M.ACC_NEEDS_CREDENTIALS, M.ACC_LOCKED)

    # ------------------------------------------------------------------ registration as a resumable transaction
    def begin_registration(self, account_id: str):
        """Persisted before pressing Create account. A second call while one is outstanding is refused."""
        with self.db.tx():
            a = self.row(account_id)
            if a["registration_intent_at"] and a["status"] in (M.ACC_REGISTERING, M.ACC_AWAITING_EMAIL, M.ACC_UNCERTAIN):
                raise AccountError("registration_outstanding", "an earlier registration may have gone through; checking first")
            if not self.policy().get("allow_required_account_creation"):
                raise AccountError("needs_credentials", "account creation is turned off in your settings")
            self.db.x("UPDATE accounts SET status=?, registration_intent_at=?, updated_at=? WHERE id=?",
                      (M.ACC_REGISTERING, now_iso(), now_iso(), account_id))
            self.db.event("account", account_id, "registration_intent")

    def registration_result(self, account_id: str, outcome: str, reason: str = ""):
        """outcome: active | awaiting_email | existing_account | password_policy | username_taken | uncertain"""
        mapping = {"active": M.ACC_ACTIVE, "awaiting_email": M.ACC_AWAITING_EMAIL, "existing_account": M.ACC_EXISTING,
                   "password_policy": M.ACC_NEEDS_CREDENTIALS, "username_taken": M.ACC_NEEDS_CREDENTIALS,
                   "uncertain": M.ACC_UNCERTAIN, "human": M.ACC_NEEDS_HUMAN}
        st = mapping[outcome]
        extra = {"awaiting_since": now_iso()} if st == M.ACC_AWAITING_EMAIL else {}
        if outcome in ("password_policy", "username_taken"):
            extra["registration_intent_at"] = None      # nothing was created; a retry after your fix is safe
        self.set_status(account_id, st, reason or outcome, **extra)

    def resolve(self, account_id: str, action: str, **kw):
        """Your answer to an account exception, remembered for that account only.

        actions: set_password_ref / set_username_ref (a per-account vault override), approve_host,
        retry (clear the failure counter after you fixed something), existing_account_password_set.
        """
        a = self.row(account_id)
        over = loads(a["override_json"], {})
        if action == "set_password_ref":
            over["password_ref"] = kw["ref"]
        elif action == "set_username_ref":
            over["username_ref"] = kw["ref"]
        elif action == "approve_host":
            self.approve_host(a["realm_id"], kw["host"])
        elif action not in ("retry", "existing_account_password_set", "registration_not_created"):
            raise AccountError("bad_action", action)
        fields = {"override_json": dumps(over), "failed_logins": 0}
        if action == "registration_not_created":
            fields["registration_intent_at"] = None
        new_status = M.ACC_UNKNOWN if a["status"] != M.ACC_ACTIVE else M.ACC_ACTIVE
        self.set_status(account_id, new_status, f"resolved: {action}", **fields)
        with self.db.tx():
            self.db.event("account", account_id, "resolved", {"action": action})

    # ------------------------------------------------------------------ sessions
    def save_session(self, account_id: str, state: dict):
        ref = self.sessions.save(account_id, state)
        with self.db.tx():
            self.db.x("""INSERT INTO sessions(account_id,state_ref,saved_at,validated_at) VALUES(?,?,?,?)
                         ON CONFLICT(account_id) DO UPDATE SET state_ref=excluded.state_ref, saved_at=excluded.saved_at, validated_at=excluded.validated_at""",
                      (account_id, ref, now_iso(), now_iso()))

    def load_session(self, account_id: str) -> dict | None:
        r = self.db.one("SELECT state_ref FROM sessions WHERE account_id=?", (account_id,))
        return self.sessions.load(r["state_ref"]) if r else None

    def drop_session(self, account_id: str):
        r = self.db.one("SELECT state_ref FROM sessions WHERE account_id=?", (account_id,))
        if r:
            self.sessions.delete(r["state_ref"])
            with self.db.tx():
                self.db.x("DELETE FROM sessions WHERE account_id=?", (account_id,))

    # ------------------------------------------------------------------ verification hand-off from the mailbox
    def store_verification(self, account_id: str, secret_value: str, kind: str):
        from .credentials import one_time_ref
        ref = one_time_ref(kind)
        self.vault.set(ref, secret_value)
        with self.db.tx():
            self.db.x("UPDATE accounts SET verification_ref=?, updated_at=? WHERE id=?", (ref, now_iso(), account_id))
            self.db.event("account", account_id, "verification_received", {"kind": kind})
        # Anything parked on this account can run again.
        self.release_parked(account_id)

    def take_verification(self, account_id: str) -> str | None:
        """Returns the vault ref of a pending link/code and clears it: each one is used once."""
        a = self.row(account_id)
        ref = a["verification_ref"]
        if not ref:
            return None
        with self.db.tx():
            self.db.x("UPDATE accounts SET verification_ref='' WHERE id=?", (account_id,))
        return ref

    def release_parked(self, account_id: str):
        a = self.row(account_id)
        with self.db.tx():
            rows = self.db.all("""SELECT ap.id FROM applications ap JOIN jobs j ON j.id=ap.job_id
                                  WHERE j.realm_id=? AND ap.state IN (?,?)""", (a["realm_id"], M.AWAITING_EMAIL, M.NEEDS_HUMAN))
            for r in rows:
                self.db.x("UPDATE applications SET state=?, state_reason=?, updated_at=? WHERE id=?",
                          (M.QUEUED, "verification arrived; continuing", now_iso(), r["id"]))
                self.db.event("application", r["id"], "state", {"to": M.QUEUED, "reason": "verification arrived"})

    def waiting(self) -> list[dict]:
        """Accounts waiting on an email, with what the mailbox should look for (no secrets)."""
        out = []
        for a in self.db.all("SELECT a.*, r.portal, r.tenant, r.allowed_hosts FROM accounts a JOIN realms r ON r.id=a.realm_id WHERE a.status=?",
                             (M.ACC_AWAITING_EMAIL,)):
            out.append({"account_id": a["id"], "realm_id": a["realm_id"], "portal": a["portal"], "tenant": a["tenant"],
                        "since": a["awaiting_since"] or a["registration_intent_at"], "hosts": loads(a["allowed_hosts"], []),
                        "purpose": "login_code" if a["auth_method"] == "email_code" else "activation"})
        return out
