"""Prepare board-db documents from data/sheet.csv.
  <out>/board/sheet_N.json  compact chunks {cols:[...], rows:[[...],...], generated} (~120 rows each, < 256 KiB)
  <out>/board/index.json    {chunks:N, generated, counts}
Per-job state (status, materials, submitted, email) lives in jobs/<id> docs written by the board and by Claude; id = sha1(link)[:16]."""
import csv, hashlib, json, os, sys
COLS = ["urgency","fit","new","rank","term","company","role","location","posted","days_open","deadline","first_seen","nuworks","nu_eligible","link","nuworks_link","source","tier","fit_why"]
def jid(link): return hashlib.sha1(link.encode()).hexdigest()[:16]
def main(sheet, meta, out, per=120):
    os.makedirs(os.path.join(out, "board"), exist_ok=True)
    rows = list(csv.DictReader(open(sheet, newline="", encoding="utf-8")))
    gen = json.load(open(meta)).get("generated", "")
    n = 0
    for i in range(0, len(rows), per):
        chunk = [[jid(r["link"])] + [r.get(c, "") for c in COLS] for r in rows[i:i+per]]
        json.dump({"cols": ["id"] + COLS, "rows": chunk, "generated": gen}, open(os.path.join(out, "board", f"sheet_{n}.json"), "w")); n += 1
    from collections import Counter
    json.dump({"chunks": n, "generated": gen, "rows": len(rows), "fit": dict(Counter(r["fit"] for r in rows))}, open(os.path.join(out, "board", "index.json"), "w"))
    print(len(rows), "rows in", n, "chunks")
if __name__ == "__main__":
    main(*sys.argv[1:4])
