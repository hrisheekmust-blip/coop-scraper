"""Command line for the application worker.

  python -m runner setup --materials "C:\\Users\\you\\Documents\\coop-apps"   first-time setup (profile import, key, config)
  python -m runner service                  run the service (the installer starts it at login)
  python -m runner status                   what the worker is doing
  python -m runner set-password             store the shared portal password in Windows Credential Manager
  python -m runner prep export [--forms data/forms.json --sheet data/sheet.csv]
  python -m runner prep import prep/response.json
  python -m runner import-profile <coop-apps dir>
  python -m runner backup [file] | restore <file>
  python -m runner doctor [file]            diagnostics with secrets removed
  python -m runner resolve <application_id> submitted|not_submitted
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from .config import Settings, home, paths, pipe_key


def _db():
    from .db import open_db
    p = paths()
    return open_db(p["db"], p["backups"])


def cmd_setup(a):
    root = home()
    root.mkdir(parents=True, exist_ok=True)
    s = Settings.load()
    if a.materials:
        s.materials_dir = str(Path(a.materials).resolve())
    if a.board_url:
        s.board_url = a.board_url
    s.save()
    pipe_key(create=True)
    db = _db()
    print(f"data folder: {root}")
    if s.materials_dir:
        n = import_profile(db, Path(s.materials_dir))
        print(f"imported {n['facts']} profile facts and {n['answers']} saved answers ({n['skipped']} job-specific answers left for the prep pass)")
    print("next: python -m runner set-password, then install the extension and run native_host/install.ps1")


def import_profile(db, root: Path) -> dict:
    from .answer_engine import AnswerEngine, Bank
    from .facts import Facts
    from .questions import Question
    out = {"facts": 0, "answers": 0, "skipped": 0}
    prof = root / "me" / "profile.json"
    if prof.exists():
        out["facts"] = len(Facts(db).import_legacy_profile(json.loads(prof.read_text(encoding="utf-8"))))
    learned = root / "me" / "learned-answers.json"
    if learned.exists():
        eng = AnswerEngine(db, Bank(paths()["bank"]))
        for r in json.loads(learned.read_text(encoding="utf-8")):
            if r.get("deleted") or not r.get("values"):
                continue
            if r.get("jobId"):
                out["skipped"] += 1          # tied to one posting on the old board; the prep pass will ask with context
                continue
            q = Question(label=r["label"], control="select" if r.get("options") else "text", options=r.get("options") or [])
            eng.remember(eng.key_for(q), r["label"], r["values"] if len(r["values"]) > 1 else r["values"][0], {"kind": "user"}, "explicit",
                         options=r.get("options") or [])
            out["answers"] += 1
    return out


def cmd_import_profile(a):
    print(json.dumps(import_profile(_db(), Path(a.dir)), indent=2))


def cmd_service(a):
    from .service import run_forever
    run_forever()


def cmd_status(a):
    from .client import call
    try:
        st = call({"type": "worker.status"})
    except (FileNotFoundError, ConnectionError, OSError) as e:
        print(f"worker service not reachable: {e}")
        return 1
    print(json.dumps(st, indent=2))
    apps = call({"type": "application.status"}).get("applications", [])
    for v in apps[-25:]:
        print(f"{v['display']:22} {v['company'][:24]:24} {v['title'][:40]:40} {v['reason'][:60]}")


def cmd_set_password(a):
    from .accounts import Accounts
    from .credentials import SessionStore, default_vault
    v = default_vault()
    pw = getpass.getpass("Shared portal password (stored in Windows Credential Manager): ")
    if not pw or pw != getpass.getpass("Again: "):
        print("didn't match; nothing stored")
        return 1
    acc = Accounts(_db(), v, SessionStore(v, paths()["sessions"]))
    v.set(acc.policy()["password_secret"], pw)
    print("stored")


def cmd_prep(a):
    from .answer_engine import AnswerEngine, Bank
    from .prep import export_packet, import_response
    db = _db()
    p = paths()
    p["prep"].mkdir(parents=True, exist_ok=True)
    if a.action == "export":
        if a.forms:
            n = preflight_from_forms(db, Path(a.forms), Path(a.sheet) if a.sheet else None)
            print(f"checked {n['jobs']} postings' saved question lists; {n['pending']} questions need answers")
        pk = export_packet(db)
        out = p["prep"] / f"packet-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(pk, indent=2), encoding="utf-8")
        print(f"{len(pk['questions'])} question groups -> {out}")
    else:
        from .accounts import Accounts
        from .credentials import SessionStore, default_vault
        v = default_vault()
        acc = Accounts(db, v, SessionStore(v, p["sessions"]))
        eng = AnswerEngine(db, Bank(p["bank"]), acc.policy().get("approved_consents", []))
        res = import_response(db, json.loads(Path(a.file).read_text(encoding="utf-8")), eng)
        print(json.dumps(res, indent=2))


def preflight_from_forms(db, forms_path: Path, sheet_path: Path | None) -> dict:
    """Preparation before the click: evaluate the public question lists (data/forms.json) for board postings and
    record every required question the engine can't answer yet, so a prep pass can cover them in advance."""
    from . import jobqueue as Q
    from .accounts import Accounts
    from .answer_engine import AnswerEngine, Bank
    from .credentials import SessionStore, default_vault
    from .questions import Question
    forms = json.loads(forms_path.read_text(encoding="utf-8"))
    rows = {}
    if sheet_path and sheet_path.exists():
        for r in csv.DictReader(open(sheet_path, encoding="utf-8")):
            rows[hashlib.sha1(r["link"].encode()).hexdigest()[:16]] = r
    v = default_vault()
    acc = Accounts(db, v, SessionStore(v, paths()["sessions"]))
    eng = AnswerEngine(db, Bank(paths()["bank"]), acc.policy().get("approved_consents", []))
    n = {"jobs": 0, "pending": 0}
    for bid, rec in forms.items():
        if not rec.get("fields") or rec.get("closed") or not rec.get("apply_url"):
            continue
        r = rows.get(bid, {})
        if rows and r.get("fit") not in ("CHIP", "HARDWARE", "MAYBE"):
            continue
        jid = Q.resolve_job(db, rec["apply_url"], bid, rec.get("company", ""), rec.get("role", ""), r.get("location", ""), "forms.json")
        job = {"id": jid, "company": rec.get("company", ""), "title": rec.get("role", "")}
        ctx = eng.ctx(job, materials={"resume": "prepared"}, cover_letter=False)
        n["jobs"] += 1
        for f in rec["fields"]:
            ctl = _ctl(f.get("type", ""))
            if not f.get("required") or ctl == "hidden":
                continue
            q = Question(label=f.get("label", ""), control=ctl, options=f.get("options") or [], required=True, job_id=jid,
                         employer=job["company"], job_title=job["title"])
            res = eng.answer(q, ctx)
            if getattr(res, "decision", "answer") == "need":
                eng.record_pending(q, jid, None, res.key or eng.key_for(q), res.reason)
                n["pending"] += 1
    return n


def _ctl(t: str) -> str:
    t = (t or "").lower()
    if "hidden" in t:
        return "hidden"
    if "file" in t or t in ("input_file",):
        return "file"
    if "textarea" in t or t in ("longtext",):
        return "textarea"
    if "multi" in t:
        return "checkbox"
    if "select" in t or t in ("valueselect", "dropdown"):
        return "select"
    if t in ("boolean", "yes_no"):
        return "boolean"
    if "date" in t:
        return "date"
    return "text"


def cmd_backup(a):
    db = _db()
    dest = a.file or str(paths()["backups"] / f"manual-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3")
    print(db.backup(dest))


def cmd_restore(a):
    p = paths()
    src = Path(a.file)
    if not src.exists():
        print("no such backup")
        return 1
    import sqlite3
    cur = p["db"]
    if cur.exists():
        _db().backup(p["backups"] / f"before-restore-{int(time.time())}.sqlite3")
    # Restore through SQLite's backup API into the live file: its WAL is handled correctly, unlike a file copy.
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(cur))
    with d:
        s.backup(d)
    s.close()
    d.close()
    print(f"restored {src} (stop the service first; the previous database was kept in backups)")


def cmd_doctor(a):
    from .adapters import names
    from .evidence import redact
    db = _db()
    out = {"version": "1.0.0", "python": sys.version, "home": str(home()), "settings": Settings.load().__dict__, "adapters": names(),
           "db_version": db.version(),
           "applications": {r["state"]: r["n"] for r in db.all("SELECT state, COUNT(*) n FROM applications GROUP BY state")},
           "accounts": [dict(r) for r in db.all("SELECT realm_id, status, status_reason, failed_logins FROM accounts")],
           "pending_questions": db.one("SELECT COUNT(*) n FROM pending_questions WHERE resolved_at IS NULL")["n"],
           "recent_events": [dict(r) for r in db.all("SELECT at, entity_kind, entity_id, kind, data_json FROM events ORDER BY seq DESC LIMIT 300")]}
    try:
        import playwright
        from importlib.metadata import version
        out["playwright"] = version("playwright")
    except Exception:
        out["playwright"] = "missing"
    text = json.dumps(redact(out), indent=2, default=str)
    dest = Path(a.file) if a.file else paths()["root"] / f"diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.json"
    dest.write_text(text, encoding="utf-8")
    print(f"diagnostics (secrets removed) -> {dest}")


def cmd_resolve(a):
    from . import jobqueue as Q
    print(json.dumps(Q.resolve_uncertain(_db(), a.application_id, a.resolution), indent=2))


def cmd_migrate_board(a):
    """One-time import of the old board's statuses (state.json) so the worker never re-applies to them.
    Old "applied" records are kept as your historical assertions, not as verified receipts; anything that was
    mid-flight on the old board becomes uncertain until you check it. A mapping file makes it reversible."""
    from . import jobqueue as Q, models as M
    from .db import dumps, now_iso
    db = _db()
    db.backup(paths()["backups"] / f"before-board-migration-{int(time.time())}.sqlite3")
    state = json.loads(Path(a.state).read_text(encoding="utf-8"))
    links = {}
    for r in csv.DictReader(open(a.sheet, encoding="utf-8")):
        links[hashlib.sha1(r["link"].encode()).hexdigest()[:16]] = r
    forms = json.loads(Path(a.forms).read_text(encoding="utf-8")) if a.forms and Path(a.forms).exists() else {}
    mapping, counts = [], {"applied": 0, "uncertain": 0, "skipped": 0, "unknown_row": 0}
    for bid, st in state.items():
        if not isinstance(st, dict) or bid.startswith("_"):
            continue
        status = st.get("status", "")
        if status in ("applied", "interview", "rejected", "offer"):
            target, reason = M.APPLIED, f"recorded as {status} on the old board (your record, not verified by the worker)"
        elif status in ("applying", "queued", "uncertain"):
            target, reason = M.UNCERTAIN, "was in progress on the old board; check the portal or your email before applying again"
        else:
            counts["skipped"] += 1
            continue
        row = links.get(bid)
        if not row:
            counts["unknown_row"] += 1
            continue
        url = (forms.get(bid) or {}).get("apply_url") or row["link"]
        jid = Q.resolve_job(db, url, bid, row.get("company", ""), row.get("role", ""), row.get("location", ""), "board-migration")
        with db.tx():
            cur = db.one("SELECT id, state FROM applications WHERE user_id='me' AND job_id=?", (jid,))
            if cur:
                continue
            from .db import new_id
            aid = new_id("app")
            db.x("INSERT INTO applications(id,user_id,job_id,state,state_reason,board_ref,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                 (aid, "me", jid, target, reason, bid, now_iso(), now_iso()))
            if status in ("interview", "rejected", "offer"):
                db.x("UPDATE applications SET employer_status=?, employer_status_at=? WHERE id=?", (status, st.get("statusAt") or now_iso(), aid))
            db.event("application", aid, "migrated", {"from_status": status, "board_ref": bid})
        mapping.append({"board_ref": bid, "application_id": aid, "job_id": jid, "old_status": status, "new_state": target})
        counts["applied" if target == M.APPLIED else "uncertain"] += 1
    out = paths()["backups"] / f"board-migration-{int(time.time())}.json"
    out.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(json.dumps(counts), f"\nmapping (to undo: delete these application ids) -> {out}")


def cmd_report(a):
    """Release metrics (spec section 15). Per-portal numbers come only from attempts this worker ran."""
    from . import models as M
    db = _db()
    q = lambda sql, *p: db.all(sql, p)
    rep = {"canonical_jobs": q("SELECT COUNT(*) n FROM jobs WHERE merged_into IS NULL")[0]["n"],
           "aliases": q("SELECT COUNT(*) n FROM job_aliases")[0]["n"],
           "applications_attempted": q("SELECT COUNT(DISTINCT application_id) n FROM attempts WHERE kind='apply'")[0]["n"],
           "verified_submissions": {r["kind"]: r["n"] for r in q("SELECT kind, COUNT(*) n FROM confirmations GROUP BY kind")},
           "uncertain_now": q("SELECT COUNT(*) n FROM applications WHERE state=?", M.UNCERTAIN)[0]["n"],
           "duplicate_clicks_absorbed": q("SELECT COUNT(*) n FROM requests WHERE response_json LIKE '%Already%'")[0]["n"],
           "manual_interventions": q("SELECT COUNT(*) n FROM events WHERE kind='state' AND data_json LIKE ?", f'%"to":"{M.NEEDS_HUMAN}"%')[0]["n"],
           "missing_fact_categories": {r["semantic_key"]: r["n"] for r in q("SELECT semantic_key, COUNT(*) n FROM pending_questions GROUP BY semantic_key ORDER BY n DESC LIMIT 30")},
           "accounts": {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM accounts GROUP BY status")}}
    per = {}
    for r in q("""SELECT j.portal, a.id, a.state,
                  (SELECT MIN(started_at) FROM attempts t WHERE t.application_id=a.id AND t.kind='apply') first_start,
                  (SELECT MIN(observed_at) FROM confirmations c WHERE c.application_id=a.id) confirmed,
                  (SELECT COUNT(*) FROM attempts t WHERE t.application_id=a.id AND t.kind='apply') attempts
                  FROM applications a JOIN jobs j ON j.id=a.job_id WHERE EXISTS (SELECT 1 FROM attempts t WHERE t.application_id=a.id)"""):
        d = per.setdefault(r["portal"], {"attempted": 0, "applied": 0, "first_try": 0, "durations_s": []})
        d["attempted"] += 1
        if r["state"] == M.APPLIED and r["confirmed"]:
            d["applied"] += 1
            d["first_try"] += int(r["attempts"] == 1)
            from datetime import datetime
            f = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
            d["durations_s"].append((f(r["confirmed"]) - f(r["first_start"])).total_seconds())
    for d in per.values():
        ds = sorted(d.pop("durations_s"))
        d["uninterrupted_rate"] = round(d["first_try"] / d["attempted"], 3) if d["attempted"] else None
        d["median_s"] = ds[len(ds) // 2] if ds else None
        d["p95_s"] = ds[min(len(ds) - 1, int(len(ds) * 0.95))] if ds else None
    rep["per_portal"] = per
    print(json.dumps(rep, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--materials")
    s.add_argument("--board-url")
    s.set_defaults(fn=cmd_setup)
    sub.add_parser("service").set_defaults(fn=cmd_service)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("set-password").set_defaults(fn=cmd_set_password)
    s = sub.add_parser("import-profile")
    s.add_argument("dir")
    s.set_defaults(fn=cmd_import_profile)
    s = sub.add_parser("prep")
    s.add_argument("action", choices=["export", "import"])
    s.add_argument("file", nargs="?")
    s.add_argument("--forms")
    s.add_argument("--sheet")
    s.set_defaults(fn=cmd_prep)
    s = sub.add_parser("backup")
    s.add_argument("file", nargs="?")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore")
    s.add_argument("file")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("doctor")
    s.add_argument("file", nargs="?")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("resolve")
    s.add_argument("application_id")
    s.add_argument("resolution", choices=["submitted", "not_submitted"])
    s.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("migrate-board")
    s.add_argument("--state", required=True, help="state.json from the private coop-apps repo")
    s.add_argument("--sheet", required=True, help="data/sheet.csv from the board")
    s.add_argument("--forms", help="data/forms.json (resolved application links)")
    s.set_defaults(fn=cmd_migrate_board)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
