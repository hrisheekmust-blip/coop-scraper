"""SQLite access: WAL mode, short IMMEDIATE transactions, forward-only migrations with a backup first."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS = Path(__file__).with_name("migrations")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def dumps(v) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def loads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


class DB:
    """One connection per thread. The service thread and each worker open their own."""

    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._depth = 0

    def close(self):
        self.conn.close()

    @contextlib.contextmanager
    def tx(self):
        """BEGIN IMMEDIATE so two writers never both read-then-write the same row."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        for i in range(50):
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or i == 49:
                    raise
                time.sleep(0.05 * (i + 1))
        self._depth = 1
        try:
            yield self
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth = 0
            self.conn.execute("COMMIT")

    def x(self, sql, params=()):
        return self.conn.execute(sql, params)

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ migrations
    def version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self, backup_dir: str | None = None) -> int:
        files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
        current = self.version()
        pending = [f for f in files if int(f.name[:3]) > current]
        if pending and current > 0 and backup_dir:
            self.backup(Path(backup_dir) / f"before-migration-{current:03d}-{int(time.time())}.sqlite3")
        for f in pending:
            n = int(f.name[:3])
            with self.tx():
                for stmt in _split_sql(f.read_text(encoding="utf-8")):
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version={n}")
        return self.version()

    def backup(self, dest: str | os.PathLike):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        with target:
            self.conn.backup(target)
        target.close()
        return str(dest)

    # ------------------------------------------------------------------ settings + events
    def setting(self, key, default=None):
        r = self.one("SELECT value_json FROM settings WHERE key=?", (key,))
        return loads(r["value_json"], default) if r else default

    def set_setting(self, key, value):
        self.x("INSERT INTO settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (key, dumps(value)))

    def event(self, entity_kind: str, entity_id: str, kind: str, data=None):
        from .evidence import redact
        self.x("INSERT INTO events(at,entity_kind,entity_id,kind,data_json) VALUES(?,?,?,?,?)",
               (now_iso(), entity_kind, entity_id, kind, dumps(redact(data or {}))))


def _split_sql(text: str):
    """Split a migration into statements (no semicolons inside our string literals except JSON defaults)."""
    out, cur, quote = [], [], False
    for line in text.splitlines():
        s = line.strip()
        if not quote and s.startswith("--"):
            continue
        cur.append(line)
        quote ^= line.count("'") % 2 == 1
        if not quote and s.endswith(";"):
            stmt = "\n".join(cur).strip().rstrip(";")
            if stmt:
                out.append(stmt)
            cur = []
    tail = "\n".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def open_db(path, backup_dir=None) -> DB:
    db = DB(path)
    db.migrate(backup_dir)
    return db
