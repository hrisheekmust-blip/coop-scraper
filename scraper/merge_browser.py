"""Merge the coop_*.json files that browser_fetch.js downloads into one raw file for main.py --raw.

  python -m scraper.merge_browser ~/Downloads data/raw_browser.json
"""
import glob
import json
import os
import sys


def main(src_dir, out):
    jobs, seen, files = [], set(), 0
    # newest copy wins when Chrome created "name (1).json" duplicates
    for path in sorted(glob.glob(os.path.join(src_dir, "coop_*.json")), key=os.path.getmtime):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:  # noqa
            print(f"skip {path}: {e}")
            continue
        files += 1
        for j in data:
            k = j.get("url", "").split("?")[0]
            if k in seen:
                continue
            seen.add(k)
            jobs.append(j)
    json.dump(jobs, open(out, "w"), indent=0)
    print(f"{files} files -> {len(jobs)} jobs -> {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "data/raw_browser.json")
