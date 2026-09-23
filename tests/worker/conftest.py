"""Shared harness: a worker wired to synthetic portals, a synthetic profile, and a fake Outlook bridge."""
import threading
import time
from pathlib import Path

import pytest

from runner import jobqueue as Q
from runner.accounts import Accounts
from runner.answer_engine import AnswerEngine, Bank
from runner.credentials import MemoryVault, SessionStore
from runner.db import open_db
from runner.facts import Facts
from runner.identity import identify
from runner.mailbox import Mailbox
from runner.planner import RunConfig
from runner.questions import Question
from runner.worker import Worker, WorkerConfig

from .portals import Fixture

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
U = {"kind": "user_answer", "note": "synthetic test profile"}
EMAIL = "test.applicant@example.edu"
PASSWORD = "Test-Passw0rd!2026"


@pytest.fixture(scope="session")
def pw_browser():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    yield b
    b.close()
    p.stop()


def seed_profile(db):
    f = Facts(db)
    for pred, v in {
        "contact.first_name": "Test", "contact.last_name": "Applicant", "contact.full_name": "Test Applicant",
        "contact.application_email": EMAIL, "contact.phone": "617-555-0100", "contact.linkedin": "https://linkedin.com/in/test-applicant",
        "address.city": "Boston", "address.state": "MA", "address.zip": "02115", "address.country": "United States",
        "education.school": "Test University", "education.degree": "Bachelor of Science", "education.major": "Electrical Engineering",
        "education.graduation": "2027-12", "availability.intervals": [{"from": "2027-01", "to": "2027-06"}],
        "work_auth.us_authorized": True, "work_auth.sponsorship_now_or_future": False, "person.over_18": True,
        "prefs.eeo_policy": "decline", "prefs.relocate_general": True, "education.gpa": "3.9",
        "prefs.hear_about": "Company careers page", "history.employers": ["Covalta"], "history.employers_complete": True,
        "address.line1": "1 Test Street", "contact.phone_type": "Mobile", "education.start": "2024-09",
    }.items():
        f.add(pred, v, U)
    f.add("education.enrolled", True, U, valid_from="2024-09-01", valid_until="2027-12-31")


class Harness:
    def __init__(self, tmp: Path, browser):
        self.tmp = tmp
        self.fx = Fixture()
        self.db = open_db(tmp / "worker.sqlite3")
        self.vault = MemoryVault({"vault://apply/default_password": PASSWORD})
        seed_profile(self.db)
        mats = tmp / "coop-apps"
        (mats / "resumes").mkdir(parents=True)
        (mats / "me").mkdir()
        (mats / "apps").mkdir()
        (mats / "resumes" / "Test_Applicant_Resume.pdf").write_bytes(PDF)
        (mats / "me" / "bank.json").write_text('{"default_variant":"digital","resume_paths":{"digital":"resumes/Test_Applicant_Resume.pdf"}}')
        (mats / "apps" / "index.json").write_text("{}")
        self.cfg = WorkerConfig(db_path=str(tmp / "worker.sqlite3"), data_dir=str(tmp / "data"), materials_dir=str(mats),
                                bank_path=str(tmp / "bank.json"), headless=True,
                                run=RunConfig(verify_wait_s=20, human_wait_s=3, confirm_wait_s=6, poll_s=0.3))
        self.worker = Worker(self.cfg, self.vault, owner="test-worker", context_hook=self.fx.install, launch=lambda: browser)
        self.accounts = Accounts(self.db, self.vault, SessionStore(self.vault, tmp / "data" / "sessions"))
        self.engine = AnswerEngine(self.db, Bank(self.cfg.bank_path))
        self._bridge = None

    def enqueue(self, url, company="", title="", rid=None):
        return Q.enqueue(self.db, rid or f"req-{time.time_ns()}", url, board_ref="b" + str(abs(hash(url)) % 10 ** 12), company=company, title=title)

    def narrative(self, url, label, text, employer=""):
        jid = Q.job_id_for(identify(url))
        q = Question(label=label, employer=employer, job_id=jid)
        ev = [Facts(self.db).get("education.major")["id"]]
        self.engine.remember(self.engine.key_for(q), label, text, {"kind": "job", "job": jid}, "generated", evidence=ev)

    def run(self):
        # the worker never shares its connection with the harness
        return self.worker.run_once()

    def view(self, app_id):
        return Q.app_view(self.db, app_id)

    def start_outlook_bridge(self):
        """Feed the portal's outbox through Mailbox.ingest, like the extension does when Outlook is open."""
        stop = threading.Event()

        def loop():
            db = open_db(self.cfg.db_path)
            mb = Mailbox(db, Accounts(db, self.vault, SessionStore(self.vault, self.tmp / "data" / "sessions")))
            seen = set()
            while not stop.is_set():
                for m in list(self.fx.outbox):
                    if m["message_id"] not in seen:
                        seen.add(m["message_id"])
                        mb.ingest(m)
                time.sleep(0.2)
            db.close()
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._bridge = stop
        return stop

    def close(self):
        if self._bridge:
            self._bridge.set()
        self.db.close()


@pytest.fixture
def h(tmp_path, pw_browser):
    x = Harness(tmp_path, pw_browser)
    yield x
    x.close()
