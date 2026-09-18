"""Turn data/jobs.json (+ optional data/nuworks.json) into per-document JSON files for the tracker's database.

  python -m scraper.seed_db            # writes data/db/jobs/<id>.json and data/db/writes_N.json (batches of 50)

Doc id = first 16 hex of sha1(url). Fields mirror tracker.csv plus the NUWorks extras.
"""
import glob
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "db")


def doc_id(url):
    return hashlib.sha1(url.split("?")[0].rstrip("/").encode()).hexdigest()[:16]


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def main():
    jobs = json.load(open(os.path.join(DATA, "jobs.json")))
    nu = json.load(open(os.path.join(DATA, "nuworks.json"))) if os.path.exists(os.path.join(DATA, "nuworks.json")) else []
    # match NUWorks rows to scraped rows by company+title similarity; otherwise they become their own rows
    by_key = {(norm(j["company"])[:12], norm(j["title"])): j for j in jobs}
    docs = {}
    for j in jobs:
        d = dict(j)
        d.pop("sources", None)
        d["sources"] = j.get("sources", [])
        d.setdefault("status", "new")
        d.setdefault("nu_connection", "")
        d.setdefault("notes", "")
        d["nuworks"] = False
        docs[doc_id(j["url"])] = d
    for n in nu:
        k = (norm(n["company"])[:12], norm(n["title"]))
        hit = by_key.get(k)
        if hit:
            d = docs[doc_id(hit["url"])]
        else:
            d = dict(company=n["company"], title=n["title"], location=n.get("location", ""), url=n["url"], posted="",
                     term=n.get("term", "unspecified"), rank=n.get("rank", "C"), hw=n.get("hw", ""), tier="",
                     sources=["nuworks"], first_seen=n.get("first_seen", ""), last_seen=n.get("last_seen", ""),
                     status="new", nu_connection="", notes="")
            docs[doc_id(n["url"])] = d
        d["nuworks"] = True
        d["nu_url"] = n["url"]
        d["nu_qualified"] = n.get("qualified")
        d["nu_applicant_type"] = n.get("applicantType", "")
        d["deadline"] = n.get("deadline", "")
        d["nu_length"] = n.get("length", "")
        if "nuworks" not in d["sources"]:
            d["sources"] = d["sources"] + ["nuworks"]

    for f in glob.glob(os.path.join(OUT, "jobs", "*.json")):
        os.remove(f)
    os.makedirs(os.path.join(OUT, "jobs"), exist_ok=True)
    ids = sorted(docs)
    for i in ids:
        json.dump(docs[i], open(os.path.join(OUT, "jobs", f"{i}.json"), "w"), indent=0)
    batches = [ids[k:k + 50] for k in range(0, len(ids), 50)]
    for n, b in enumerate(batches):
        writes = [dict(op="set", collection="jobs", doc_id=i, file_path=os.path.join(OUT, "jobs", f"{i}.json")) for i in b]
        json.dump(writes, open(os.path.join(OUT, f"writes_{n}.json"), "w"), indent=0)
    print(f"{len(ids)} docs, {len(batches)} batches -> {OUT}")


if __name__ == "__main__":
    main()
