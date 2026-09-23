"""The local service: owns the queue, answers the extension bridge, runs the workers.

Transport: a per-user named pipe (Windows) or unix socket, authenticated with a random key from the user's
profile (multiprocessing.connection HMAC challenge). There is no HTTP endpoint and nothing listens on a network port.
An Apply request is durable once this returns: the application row is committed before the reply.
"""
from __future__ import annotations

import logging
import threading
import time
from multiprocessing.connection import Listener
from pathlib import Path

from . import jobqueue as Q, models as M
from .accounts import Accounts
from .adapters import DISABLED
from .answer_engine import AnswerEngine, Bank
from .config import Settings, home, paths, pipe_address, pipe_key
from .credentials import SessionStore, Vault, default_vault
from .db import DB, loads, now_iso, open_db
from .evidence import scrub
from .facts import Facts
from .mailbox import Mailbox
from .planner import RunConfig
from .protocol import BadRequest, validate
from .worker import Worker, WorkerConfig

log = logging.getLogger("coop.service")
VERSION = "1.0.0"
SETTABLE = {"approved_consents", "allow_required_account_creation", "optional_marketing_consent", "username_alternatives"}


class Service:
    def __init__(self, root: Path | None = None, vault: Vault | None = None, start_workers: bool = True, worker_kwargs=None):
        self.root = root or home()
        self.p = paths(self.root)
        self.p["root"].mkdir(parents=True, exist_ok=True)
        self.settings = Settings.load(self.root)
        self.vault = vault or default_vault()
        self.db = open_db(self.p["db"], self.p["backups"])
        DISABLED.clear()
        DISABLED.update(self.settings.disabled_adapters)
        self.stop = threading.Event()
        self.workers: list[threading.Thread] = []
        self.start_workers = start_workers
        self.worker_kwargs = worker_kwargs or {}
        self.started = now_iso()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def worker_config(self) -> WorkerConfig:
        return WorkerConfig(db_path=str(self.p["db"]), data_dir=str(self.p["root"]), materials_dir=self.settings.materials_dir or None,
                            bank_path=str(self.p["bank"]), headless=self.settings.headless,
                            browser_channel=self.settings.browser_channel or None, max_live=self.settings.max_live, run=RunConfig())

    def start(self):
        Q.recover(self.db)
        if self.start_workers:
            for i in range(max(1, self.settings.max_live)):
                w = Worker(self.worker_config(), self.vault, owner=f"svc-{i}", **self.worker_kwargs)
                t = threading.Thread(target=w.loop, args=(self.stop,), name=f"worker-{i}", daemon=True)
                t.start()
                self.workers.append(t)
        addr, fam = pipe_address(self.root)
        if fam == "AF_UNIX" and Path(addr).exists():
            Path(addr).unlink()
        self.listener = Listener(addr, family=fam, authkey=pipe_key(self.root, create=True))
        self.accept_thread = threading.Thread(target=self._accept, name="bridge", daemon=True)
        self.accept_thread.start()
        log.info("service %s listening on %s", VERSION, addr)

    def shutdown(self):
        self.stop.set()
        try:
            self.listener.close()
        except Exception:
            pass
        for t in self.workers:
            t.join(timeout=30)

    def _accept(self):
        while not self.stop.is_set():
            try:
                conn = self.listener.accept()
            except Exception:
                if self.stop.is_set():
                    return
                time.sleep(0.2)
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        db = open_db(self.p["db"])
        try:
            while True:
                try:
                    req = conn.recv()
                except (EOFError, OSError):
                    return
                conn.send(self.handle(req, db))
        finally:
            db.close()
            conn.close()

    # ------------------------------------------------------------------ requests
    def handle(self, req: dict, db: DB | None = None) -> dict:
        db = db or self.db
        channel = (req or {}).get("channel", "")
        try:
            msg = validate(channel, (req or {}).get("msg"))
            return {"ok": True, **self.dispatch(msg, db)}
        except (BadRequest, Q.Refused) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("request failed")
            return {"ok": False, "error": scrub(f"{type(e).__name__}: {e}")[:300]}

    def dispatch(self, m: dict, db: DB) -> dict:
        t = m["type"]
        acc = Accounts(db, self.vault, SessionStore(self.vault, self.p["sessions"]))
        if t == "worker.status":
            alive = sum(t.is_alive() for t in self.workers)
            counts = {r["state"]: r["n"] for r in db.all("SELECT state, COUNT(*) n FROM applications GROUP BY state")}
            return {"version": VERSION, "started": self.started, "workers": alive, "mode": "worker", "counts": counts,
                    "has_password": self.vault.has(acc.policy()["password_secret"]), "materials": bool(self.settings.materials_dir)}
        if t == "application.enqueue":
            r = Q.enqueue(db, m["request_id"], m["source_url"], board_ref=m["job_ref"], company=m["company"], title=m["title"],
                          location=m["location"], material_policy=m["material_policy"])
            return {"application": r}
        if t == "application.status":
            return {"applications": Q.list_views(db, m.get("since", "")), "now": now_iso()}
        if t == "application.cancel":
            return {"application": Q.cancel(db, m["application_id"])}
        if t == "application.resume":
            return {"application": Q.resume(db, m["application_id"])}
        if t == "application.check":
            return {"application": Q.request_check(db, m["application_id"])}
        if t == "application.resolve_uncertain":
            return {"application": Q.resolve_uncertain(db, m["application_id"], m["resolution"], m.get("note", ""))}
        if t == "application.set_cover":
            with db.tx():
                db.set_setting(f"cover:{m['job_ref']}", m["on"])
            return {}
        if t == "mail.wanted":
            # What the Outlook bridge should open: waiting accounts (no secrets) + employers with open outcomes.
            employers = sorted({r["company"] for r in db.all("""SELECT DISTINCT j.company FROM applications a JOIN jobs j ON j.id=a.job_id
                                                                 WHERE a.state IN (?,?,?) AND j.company<>''""", (M.APPLIED, M.UNCERTAIN, M.VERIFYING))})
            return {"verifications": [{k: w[k] for k in ("portal", "tenant", "since", "purpose")} for w in acc.waiting()], "employers": employers}
        if t == "mail.ingest":
            return {"result": Mailbox(db, acc).ingest(m["message"])}
        if t == "account.list":
            rows = db.all("SELECT a.id, a.realm_id, a.status, a.status_reason, a.last_login_at, a.override_json, r.allowed_hosts FROM accounts a JOIN realms r ON r.id=a.realm_id")
            return {"accounts": [{"account_id": r["id"], "realm": r["realm_id"], "status": r["status"], "reason": r["status_reason"],
                                  "last_login": r["last_login_at"], "has_override": bool(loads(r["override_json"], {})),
                                  "hosts": loads(r["allowed_hosts"], [])} for r in rows]}
        if t == "account.resolve":
            kw = {}
            if m["action"] == "approve_host":
                kw["host"] = m["host"]
            if m["action"] in ("set_password", "set_username"):
                if not m.get("value"):
                    raise BadRequest("value required")
                ref = f"vault://apply/acct/{m['account_id']}/{m['action'][4:]}"
                self.vault.set(ref, m["value"])
                acc.resolve(m["account_id"], "set_password_ref" if m["action"] == "set_password" else "set_username_ref", ref=ref)
                self._release(db, m["account_id"])
                return {"account": m["account_id"]}
            acc.resolve(m["account_id"], m["action"], **kw)
            self._release(db, m["account_id"])
            return {"account": m["account_id"]}
        if t == "credentials.set":
            pol = acc.policy()
            ref = pol["password_secret"] if m["what"] == "password" else pol["preferred_username_secret"]
            self.vault.set(ref, m["value"])
            return {"stored": m["what"]}
        if t == "credentials.status":
            pol = acc.policy()
            return {"password": self.vault.has(pol["password_secret"]), "username": self.vault.has(pol["preferred_username_secret"]),
                    "application_email": acc.application_email()}
        if t == "profile.update":
            f = Facts(db)
            n = 0
            for x in m["facts"]:
                if not isinstance(x, dict) or not isinstance(x.get("predicate"), str):
                    raise BadRequest("each fact needs a predicate")
                f.add(x["predicate"], x.get("value"), {"kind": "user_answer", "note": "extension settings"}, x.get("scope"),
                      x.get("valid_from"), x.get("valid_until"))
                n += 1
            from .prep import resume_answerable
            resumed = resume_answerable(db, AnswerEngine(db, Bank(self.p["bank"]), acc.policy().get("approved_consents", [])))
            return {"facts": n, "resumed": resumed}
        if t == "pending.list":
            from .prep import export_packet
            return {"packet": export_packet(db)}
        if t == "settings.get":
            return {"policy": {k: v for k, v in acc.policy().items() if not k.endswith("_secret")}, "settings": self.settings.__dict__}
        if t == "settings.set":
            vals = {k: v for k, v in m["values"].items() if k in SETTABLE}
            acc.set_policy(vals)
            return {"policy": {k: v for k, v in acc.policy().items() if not k.endswith("_secret")}}
        raise BadRequest(f"unhandled {t}")

    def _release(self, db, account_id):
        """After you resolve an account exception, applications parked on it can run again."""
        a = db.one("SELECT realm_id FROM accounts WHERE id=?", (account_id,))
        with db.tx():
            for r in db.all("""SELECT ap.id FROM applications ap JOIN jobs j ON j.id=ap.job_id WHERE j.realm_id=? AND ap.state=?""",
                            (a["realm_id"], M.NEEDS_HUMAN)):
                Q._set_state(db, r["id"], M.QUEUED, "account issue resolved", force=True)


from .client import call  # noqa: E402,F401  (re-export)


def run_forever():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(paths()["root"] / "service.log", encoding="utf-8"), logging.StreamHandler()]
                        if paths()["root"].exists() else None)
    s = Service()
    s.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        s.shutdown()
