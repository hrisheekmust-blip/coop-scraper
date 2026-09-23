"""Application materials: which resume (and optional cover letter) goes with which job.

Source of truth is your private materials folder (the local clone of coop-apps): apps/index.json maps each board
posting id to its prepared files; me/bank.json names the resume variants and the default. Each file is registered
as an artifact (content hash + version) so a submitted application records exactly which file it sent.
Only files registered for an application can be uploaded to it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .db import DB, new_id, now_iso


class MaterialStore:
    def __init__(self, db: DB, root: str | Path | None):
        self.db = db
        self.root = Path(root) if root else None

    def _json(self, rel):
        if not self.root:
            return None
        p = self.root / rel
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def register(self, path: Path, kind: str) -> str:
        data = path.read_bytes()
        h = hashlib.sha256(data).hexdigest()
        with self.db.tx():
            r = self.db.one("SELECT id FROM artifacts WHERE sha256=? AND kind=?", (h, kind))
            if r:
                return r["id"]
            v = self.db.one("SELECT COALESCE(MAX(version),0)+1 v FROM artifacts WHERE name=?", (path.name,))["v"]
            aid = new_id("art")
            self.db.x("INSERT INTO artifacts(id,sha256,kind,name,path,version,created_at) VALUES(?,?,?,?,?,?,?)",
                      (aid, h, kind, path.name, str(path), v, now_iso()))
            return aid

    def for_application(self, board_ref: str, want_cover: bool = False) -> tuple[dict, dict, list[str]]:
        """(artifact id -> path, kind -> artifact id, problems)."""
        problems = []
        index = self._json("apps/index.json") or {}
        bank = self._json("me/bank.json") or {}
        entry = index.get(board_ref) or {}
        files = {}
        for m in entry.get("materials", []):
            name, path = m.get("name", ""), m.get("path", "")
            if not path or "(text)" in name:
                continue
            kind = "cover_letter" if "cover" in name.lower() else "resume" if "resume" in name.lower() else "other"
            files.setdefault(kind, path)
        if "resume" not in files:
            dv = bank.get("default_variant")
            rp = (bank.get("resume_paths") or {}).get(dv) if dv else None
            if rp:
                files["resume"] = rp
        out, kinds = {}, {}
        for kind in ("resume",) + (("cover_letter",) if want_cover else ()):
            rel = files.get(kind)
            if not rel:
                problems.append(f"no {kind.replace('_', ' ')} prepared for this posting")
                continue
            p = (self.root / rel) if self.root else Path(rel)
            if not p.exists() or p.suffix.lower() not in (".pdf", ".doc", ".docx"):
                problems.append(f"{kind.replace('_', ' ')} file missing: {rel}")
                continue
            aid = self.register(p, kind)
            out[aid] = str(p)
            kinds[kind] = aid
        return out, kinds, problems
