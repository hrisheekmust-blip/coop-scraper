"""The worker loop: claim an application, run one attempt in a fresh browser context, record the outcome.

Leases + heartbeats mean a crash or restart never loses work: recover() hands abandoned attempts back to the queue,
or marks them uncertain if they had decided to submit.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass

from . import jobqueue as Q, models as M
from .accounts import Accounts
from .answer_engine import AnswerEngine, Bank
from .credentials import SessionStore, Vault
from .db import DB, dumps, open_db
from .materials import MaterialStore
from .planner import ApplicationRun, Done, Park, RunConfig
from .evidence import scrub

log = logging.getLogger("coop.worker")

def keep_awake(on: bool):
    """While an application is running, ask Windows not to sleep (the display may still turn off). No-op elsewhere."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


TRANSIENT = ("Timeout", "net::", "Navigation failed", "Target closed", "ERR_", "browser has been closed")


@dataclass
class WorkerConfig:
    db_path: str
    data_dir: str
    materials_dir: str | None = None
    bank_path: str | None = None
    headless: bool = False
    browser_channel: str | None = None      # e.g. "chrome" to use installed Chrome instead of bundled Chromium
    max_live: int = 2
    lease_s: float = 120
    idle_s: float = 3
    run: RunConfig = None


class Worker:
    def __init__(self, cfg: WorkerConfig, vault: Vault, owner: str | None = None, context_hook=None, launch=None):
        self.cfg = cfg
        self.vault = vault
        self.owner = owner or f"w-{threading.get_ident()}-{int(time.time())}"
        self.context_hook = context_hook       # tests: install routes for fixture portals
        self.launch = launch                   # tests: custom browser launcher
        self._pw = None
        self._browser = None

    # ------------------------------------------------------------------ browser
    def browser(self):
        if self._browser and self._browser.is_connected():
            return self._browser
        if self.launch:
            self._browser = self.launch()
            return self._browser
        from playwright.sync_api import sync_playwright
        self._pw = self._pw or sync_playwright().start()
        kw = {"headless": self.cfg.headless}
        if self.cfg.browser_channel:
            kw["channel"] = self.cfg.browser_channel
        self._browser = self._pw.chromium.launch(**kw)
        return self._browser

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------ one attempt
    def run_once(self, db: DB | None = None) -> str | None:
        own = db is None
        db = db or open_db(self.cfg.db_path)
        try:
            Q.recover(db)
            c = Q.claim_check(db, self.owner, self.cfg.lease_s, self.cfg.max_live) or Q.claim(db, self.owner, self.cfg.lease_s, self.cfg.max_live)
            if not c:
                return None
            return self._attempt(db, c)
        finally:
            if own:
                db.close()

    def _attempt(self, db: DB, c: dict) -> str:
        app_id, att = c["application"]["id"], c["attempt_id"]
        app = dict(Q.app_row(db, app_id))
        job = dict(db.one("SELECT * FROM jobs WHERE id=?", (app["job_id"],)))
        kind = db.one("SELECT kind FROM attempts WHERE id=?", (att,))["kind"]
        vault = self.vault
        accounts = Accounts(db, vault, SessionStore(vault, f"{self.cfg.data_dir}/sessions"))
        engine = AnswerEngine(db, Bank(self.cfg.bank_path), consents=accounts.policy().get("approved_consents", []))
        mats = MaterialStore(db, self.cfg.materials_dir)
        cover = bool(db.setting(f"cover:{app['board_ref']}", False))
        materials, kinds, problems = mats.for_application(app["board_ref"], cover)
        last_beat = [time.time()]

        def heartbeat():
            if time.time() - last_beat[0] < 10:
                return True
            last_beat[0] = time.time()
            return Q.heartbeat(db, att, self.owner, self.cfg.lease_s)

        if kind == "apply" and problems and "resume" not in kinds:
            Q.transition(db, app_id, M.NEEDS_INFO, "; ".join(problems), [{"blocker": "materials", "problems": problems}], attempt_id=att, owner=self.owner)
            Q.end_attempt(db, att, "needs_materials")
            return M.NEEDS_INFO

        ctx = None
        state, detail = M.FAILED, ""
        run = None
        keep_awake(True)
        try:
            ctx = self.browser().new_context(accept_downloads=False, viewport={"width": 1366, "height": 900})
            if self.context_hook:
                self.context_hook(ctx)
            # Restore the realm's session (cookies) before the first navigation.
            realm_id = job.get("realm_id")
            if not realm_id:
                from urllib.parse import urlsplit
                host = (urlsplit(job["resolved_url"]).hostname or "").lower()
                r = db.one("SELECT id FROM realms WHERE allowed_hosts LIKE ?", (f'%"{host}"%',))
                realm_id = r["id"] if r else None
            if realm_id:
                a = accounts.get(realm_id)
                st = accounts.load_session(a["id"]) if a else None
                if st and st.get("cookies"):
                    ctx.add_cookies(st["cookies"])
            page = ctx.new_page()
            run = ApplicationRun(db=db, page=page, job=job, app=app, attempt_id=att, owner=self.owner, engine=engine, accounts=accounts,
                                 vault=vault, materials=materials, material_kinds=kinds, cover_letter=cover, cfg=self.cfg.run or RunConfig(),
                                 heartbeat=heartbeat)
            if kind == "reconcile":
                state, detail = self._reconcile(run)
            else:
                state, detail = run.execute()
        except Exception as e:  # unexpected: never retry blindly after a submit
            msg = scrub(f"{type(e).__name__}: {e}")[:400]
            log.warning("attempt %s crashed: %s\n%s", att, msg, scrub(traceback.format_exc())[-2000:])
            submitted = bool(run and run.submitted)
            if submitted:
                state, detail = M.UNCERTAIN, "the worker hit an error after pressing submit: " + msg
            elif any(t in msg for t in TRANSIENT):
                state, detail = M.RETRY_WAIT, msg
            else:
                state, detail = M.FAILED, msg
        finally:
            keep_awake(False)
            try:
                if ctx:
                    ctx.close()
            except Exception:
                pass
        self._finish(db, app_id, att, state, detail, run)
        return Q.app_view(db, app_id)["state"]

    def _reconcile(self, run):
        from .adapters import pick_adapter
        from .submission import reconcile
        adapter = pick_adapter(run.job["resolved_url"])
        run.page.goto(run.job["resolved_url"], wait_until="domcontentloaded", timeout=45000)
        verdict = reconcile(run, adapter)
        if verdict == "submitted":
            return M.APPLIED, "found in the portal's history"
        return M.UNCERTAIN, f"outcome check: {verdict}"

    def _finish(self, db, app_id, att, state, detail, run):
        cur = Q.app_row(db, app_id)["state"]
        try:
            if state is None:                      # lease lost: someone else owns it now
                Q.end_attempt(db, att, "lease_lost")
                return
            if cur in (M.APPLIED,) or state == cur:
                pass
            elif state == M.RETRY_WAIT:
                if cur in M.POST_SUBMIT:
                    Q.transition(db, app_id, M.UNCERTAIN, detail)
                else:
                    Q.schedule_retry(db, app_id, detail)
            elif state == M.UNCERTAIN:
                with db.tx():
                    Q._set_state(db, app_id, M.UNCERTAIN, detail, force=True)
            else:
                needs = None
                if run is not None and isinstance(getattr(run, "_last_park", None), Park):
                    needs = run._last_park.needs
                with db.tx():
                    Q._set_state(db, app_id, state, detail, needs, force=True)
        finally:
            Q.end_attempt(db, att, state or "ended")

    # ------------------------------------------------------------------ service loop
    def loop(self, stop: threading.Event):
        db = open_db(self.cfg.db_path)
        try:
            Q.recover(db)
            while not stop.is_set():
                try:
                    did = self.run_once(db)
                except Exception as e:
                    log.error("worker loop error: %s", scrub(str(e)))
                    did = None
                if not did:
                    stop.wait(self.cfg.idle_s)
        finally:
            db.close()
            self.close()
