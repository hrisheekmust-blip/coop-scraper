"""Stage G: the service over its authenticated pipe, the native-messaging host, channel rules, restart."""
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from runner import models as M
from runner.client import call
from runner.config import Settings
from runner.credentials import MemoryVault
from runner.service import Service

from .conftest import PASSWORD, PDF, seed_profile
from .portals import Fixture
from .portals.guest import lever

ROOT = Path(__file__).resolve().parents[2]


def make_root(tmp: Path):
    mats = tmp / "coop-apps"
    (mats / "resumes").mkdir(parents=True)
    (mats / "me").mkdir()
    (mats / "apps").mkdir()
    (mats / "resumes" / "Test_Applicant_Resume.pdf").write_bytes(PDF)
    (mats / "me" / "bank.json").write_text('{"default_variant":"d","resume_paths":{"d":"resumes/Test_Applicant_Resume.pdf"}}')
    (mats / "apps" / "index.json").write_text("{}")
    root = tmp / "home"
    Settings(materials_dir=str(mats), headless=True, max_live=1).save(root)
    return root


@pytest.fixture
def svc(tmp_path):
    root = make_root(tmp_path)
    fx = Fixture()
    s = Service(root, vault=MemoryVault({"vault://apply/default_password": PASSWORD}), worker_kwargs={"context_hook": fx.install})
    seed_profile(s.db)
    s.start()
    yield s, root, fx
    s.shutdown()


def wait_for(fn, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return None


def test_click_is_acknowledged_durably_under_a_second_and_the_worker_finishes(svc):
    s, root, fx = svc
    url = lever(fx)
    t0 = time.time()
    r = call({"type": "application.enqueue", "request_id": "11111111-aaaa", "source_url": url, "job_ref": "b1", "company": "Acme Devices"},
             channel="board", root=root)
    assert r["ok"] and r["application"]["display"] == "Queued" and time.time() - t0 < 1.0
    app = r["application"]["application_id"]
    done = wait_for(lambda: next((v for v in call({"type": "application.status"}, "board", root)["applications"]
                                  if v["application_id"] == app and v["state"] in (M.APPLIED, M.FAILED, M.NEEDS_INFO)), None))
    assert done and done["state"] == M.APPLIED and done["receipt"]["kind"] == "page"
    # replay of the same click and a second click with a new id both return the same application
    again = call({"type": "application.enqueue", "request_id": "11111111-aaaa", "source_url": url}, "board", root)
    other = call({"type": "application.enqueue", "request_id": "22222222-bbbb", "source_url": url}, "board", root)
    assert again["application"]["application_id"] == other["application"]["application_id"] == app
    assert len(fx.submissions) == 1


def test_channel_rules(svc):
    s, root, fx = svc
    assert not call({"type": "credentials.set", "what": "password", "value": "x"}, "board", root)["ok"]
    assert not call({"type": "account.resolve", "account_id": "a1", "action": "approve_host", "host": "evil.com"}, "board", root)["ok"]
    assert not call({"type": "application.enqueue", "request_id": "33333333-cccc", "source_url": "https://evil.example/x"}, "outlook", root)["ok"]
    assert not call({"type": "application.enqueue", "request_id": "44444444-dddd", "source_url": "https://www.linkedin.com/jobs/view/123"}, "board", root)["ok"]
    assert not call({"type": "application.enqueue", "request_id": "55555555-eeee", "source_url": "http://jobs.lever.co/x/y"}, "board", root)["ok"]
    assert not call({"type": "nope"}, "board", root)["ok"]
    st = call({"type": "credentials.status"}, "settings", root)
    assert st["ok"] and st["password"] is True and "value" not in json.dumps(st)


def test_wrong_pipe_key_is_rejected(svc, tmp_path):
    s, root, fx = svc
    (root / "pipe.key").write_bytes(os.urandom(32))   # a different key
    with pytest.raises(Exception):
        call({"type": "worker.status"}, "cli", root)


def frame(obj):
    b = json.dumps(obj).encode()
    return struct.pack("<I", len(b)) + b


def test_native_messaging_host_relays_and_labels_channels(svc):
    s, root, fx = svc
    env = {**os.environ, "COOP_RUNNER_HOME": str(root), "PYTHONPATH": str(ROOT)}
    p = subprocess.Popen([sys.executable, str(ROOT / "native_host" / "bridge.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    out, _ = p.communicate(frame({"rid": 1, "channel": "board", "msg": {"type": "worker.status"}})
                           + frame({"rid": 2, "channel": "evil", "msg": {"type": "worker.status"}})
                           + frame({"rid": 3, "channel": "board", "msg": {"type": "credentials.set", "what": "password", "value": "x"}}), timeout=30)
    msgs = []
    while out:
        n = struct.unpack("<I", out[:4])[0]
        msgs.append(json.loads(out[4:4 + n]))
        out = out[4 + n:]
    assert msgs[0]["rid"] == 1 and msgs[0]["ok"] and msgs[0]["version"]
    assert msgs[1]["rid"] == 2 and not msgs[1]["ok"]
    assert msgs[2]["rid"] == 3 and not msgs[2]["ok"]


def test_restart_keeps_state_and_never_resubmits(tmp_path):
    root = make_root(tmp_path)
    fx = Fixture()
    url = lever(fx)
    vault = MemoryVault({"vault://apply/default_password": PASSWORD})
    s = Service(root, vault=vault, worker_kwargs={"context_hook": fx.install})
    seed_profile(s.db)
    s.start()
    app = call({"type": "application.enqueue", "request_id": "66666666-ffff", "source_url": url}, "board", root)["application"]["application_id"]
    assert wait_for(lambda: call({"type": "application.status"}, "board", root)["applications"][-1]["state"] == M.APPLIED)
    s.shutdown()
    s2 = Service(root, vault=vault, worker_kwargs={"context_hook": fx.install})
    s2.start()
    try:
        v = call({"type": "application.enqueue", "request_id": "77777777-0000", "source_url": url}, "board", root)
        assert v["application"]["application_id"] == app and v["application"]["note"] == "Already applied"
        time.sleep(3)
        assert len(fx.submissions) == 1
    finally:
        s2.shutdown()
