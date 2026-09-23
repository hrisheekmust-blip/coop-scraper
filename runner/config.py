"""Where the worker keeps its data, and its local settings file (no secrets in it)."""
from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def home() -> Path:
    if os.environ.get("COOP_RUNNER_HOME"):
        return Path(os.environ["COOP_RUNNER_HOME"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "CoopApplyRunner"
    return Path.home() / ".local" / "share" / "coop-apply-runner"


@dataclass
class Settings:
    materials_dir: str = ""                 # local clone of coop-apps (resumes, apps/index.json, me/*.json)
    board_url: str = "https://hrisheekmust-blip.github.io/coop-scraper/"
    headless: bool = False                  # visible window so you can solve a captcha when asked
    browser_channel: str = ""               # "" = Playwright's Chromium, or "chrome"/"msedge"
    max_live: int = 2                       # concurrent applications (always one per account realm)
    disabled_adapters: list = field(default_factory=list)

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        p = (root or home()) / "config.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self, root: Path | None = None):
        r = root or home()
        r.mkdir(parents=True, exist_ok=True)
        (r / "config.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def paths(root: Path | None = None) -> dict:
    r = root or home()
    return {"root": r, "db": r / "worker.sqlite3", "backups": r / "backups", "sessions": r / "sessions", "bank": r / "answer-bank.json",
            "key": r / "pipe.key", "logs": r / "logs", "prep": r / "prep", "socket": r / "runner.sock"}


def pipe_address(root: Path | None = None) -> tuple[str, str]:
    """(address, family). A per-user named pipe on Windows, a unix socket elsewhere."""
    if sys.platform == "win32":
        return r"\\.\pipe\coop-apply-runner-" + "".join(c for c in getpass.getuser() if c.isalnum()), "AF_PIPE"
    return str(paths(root)["socket"]), "AF_UNIX"


def pipe_key(root: Path | None = None, create: bool = False) -> bytes:
    p = paths(root)["key"]
    if not p.exists():
        if not create:
            raise FileNotFoundError("the worker service hasn't been set up yet (no pipe key)")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(os.urandom(32))
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p.read_bytes()
