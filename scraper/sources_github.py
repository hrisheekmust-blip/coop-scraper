"""Ingest the community-maintained GitHub internship lists (markdown tables) as extra sources.

These catch companies that aren't in companies.json and postings people found by hand.
"""
import re
import urllib.request

LISTS = [
    # (name, raw url)
    ("vanshb03-offseason", "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/OFFSEASON_README.md"),
    ("vanshb03-summer", "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md"),
    ("sndsh404", "https://raw.githubusercontent.com/sndsh404/summer-2027-internships/main/README.md"),
    ("hardware-internships-2027", "https://raw.githubusercontent.com/Khushsj30/hardware-internships-2027/main/README.md"),
]

ROW = re.compile(r"^\|(.+)\|\s*$")
HREF = re.compile(r'href="([^"]+)"|\]\((https?://[^)\s]+)\)|\((https?://[^)\s]+)\)')


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # [text](url) -> text
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[🛂🇺🇸🔒🎓🔥]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_markdown_table(md, source):
    out, last_company = [], ""
    for line in md.splitlines():
        if not ROW.match(line) or set(line.strip()) <= set("|-: "):
            continue
        cells = _cells(line)
        if len(cells) < 4 or cells[0].lower() in ("company",):
            continue
        company = _clean(cells[0]) or last_company
        if company in ("↳", "") :
            company = last_company
        last_company = company
        title = _clean(cells[1])
        location = _clean(cells[2])
        link = ""
        for m in HREF.finditer(line):
            link = next(g for g in m.groups() if g)
            break
        if not link or not title:
            continue
        link = link.split("?utm_source")[0]
        posted = ""
        for cell in reversed(cells[3:]):
            if "http" not in cell and _clean(cell):
                posted = _clean(cell)
                break
        out.append(dict(company=company, title=title, location=location, url=link, posted=posted,
                        description="", source=f"github:{source}"))
    return out


def fetch_all(log=print):
    jobs = []
    for name, url in LISTS:
        try:
            md = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "coop-scraper"}), timeout=40).read().decode()
            rows = parse_markdown_table(md, name)
            log(f"  github:{name}: {len(rows)} rows")
            jobs += rows
        except Exception as e:  # noqa
            log(f"  github:{name}: FAILED {e}")
    return jobs
