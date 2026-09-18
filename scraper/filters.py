"""Filtering + classification shared by every source.

Everything here works on a normalized job dict:
  {company, title, location, url, posted, description(optional), source}
"""
import re

# ---- what counts as an intern/co-op posting -------------------------------
LEVEL_RE = re.compile(r"\b(interns?|internships?|co-?ops?|coops?|student|university|undergrad(uate)?)\b", re.I)

# ---- hardware / semiconductor relevance -------------------------------------
STRONG_HW = [
    "asic", "fpga", "rtl", "verilog", "systemverilog", "vlsi", "physical design", "dft", "design verification",
    "verification", "dv", "silicon", "analog", "mixed-signal", "mixed signal", "rf", "rfic", "serdes", "ic design",
    "chip", "soc", "system on chip", "semiconductor", "hardware", "electrical engineer", "electrical engineering",
    "electrical", "circuit", "circuits", "layout", "signal integrity", "power integrity", "memory design", "timing",
    "synthesis", "sta", "post-silicon", "pre-silicon", "device engineer", "photonics", "mems", "digital design",
    "logic design", "microarchitecture", "computer architecture", "power electronics", "wafer", "yield",
    "failure analysis", "quantum hardware", "hw", "eda", "ate", "characterization", "product engineer",
    "product engineering", "test engineer", "test engineering", "emulation", "cad", "ee",
    "test technician", "test development", "test systems", "test automation engineer", "systems test", "hardware test",
    "electronics", "electronic", "controls engineer", "sensor", "sensors", "optics", "optical engineer", "electro-optic",
    "electromechanical", "electro-mechanical", "packaging engineer", "metrology", "lab engineer", "field programmable",
]
WEAK_HW = ["validation", "firmware", "embedded", "process engineer", "reliability", "instrumentation"]
_re = lambda words: re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in words) + r")(?![a-z])", re.I)
STRONG_RE, WEAK_RE = _re(STRONG_HW), _re(WEAK_HW)
# titles that are clearly not what he wants even if a hardware word sneaks in
EXCLUDE_TITLE = re.compile(
    r"\b(ai product|mechanical|civil|structural|supply chain|sales|marketing|recruit|hr\b|human resources|finance|accounting|"
    r"legal|data analyst|business|procurement|logistics|customer|technical writer|graphic|ux|ui\b|product manager|"
    r"program manager|project manager|product management|program management|pcb|environmental|facilities|construction|welding|machinist|"
    r"manufacturing associate|operator|security clearance|nurse|chemist|biology|materials science|chemical)\b", re.I)
# software-only titles: drop unless a hardware word is also present
SOFTWARE_ONLY = re.compile(r"\b(software|swe|full[- ]stack|frontend|front-end|backend|back-end|web|mobile|ios|android|"
                           r"devops|cloud|site reliability|software test|data engineer|data science|machine learning|ml\b|ai\b|research scientist)\b", re.I)
# words a chip/semiconductor company uses to describe ITSELF (checked in the description when the title says nothing)
CHIP_COMPANY_RE = re.compile(r"\b(semiconductor|chip design|chip[- ]?maker|silicon|asic|soc|eda|electronic design automation|tape-?out|"
                             r"rtl|verilog|photonic|analog|mixed[- ]signal|rfic|wafer|foundry|fabless|ic design|chiplet|hbm|serdes|"
                             r"accelerator chip|ai chip|inference chip|transistor|cmos)\b", re.I)
PHD_ONLY = re.compile(r"\b(phd|ph\.d|ms/phd|masters?/phd|masters|graduate student|doctoral|\bms\b)\b", re.I)

# ---- term detection -----------------------------------------------------------
SPRING_RE = re.compile(
    r"spring\s*'?(20)?27|winter\s*'?(20)?27|jan(uary)?\s*'?(20)?27|winter\s*[/&-]\s*spring|\bw/s\b|spring\s*/\s*summer|"
    r"spring\s+(semester|term|co-?op|intern|internship|session|start)|6[- ]month|six[- ]month|january\s+(20)?27|"
    r"jan(uary)?\.?\s*(?:[-–—]|to|through|thru|until)\s*(feb|mar|apr|may|jun)|feb(ruary)?\.?\s*(?:[-–—]|to|through)\s*(apr|may|jun)|"
    r"(feb(ruary)?|mar(ch)?|apr(il)?)\s*'?(20)?27|q1\s*(20)?27|winter\s+(intern|internship|co-?op|session|term|quarter)|"
    r"off[- ]season|fall\s*/\s*spring|spring\s*20?27|(3|three|4|four)[- ]month.{0,60}(jan|feb|mar|winter|spring)|"
    r"(jan|feb|mar|winter|spring).{0,60}(3|three|4|four)[- ]month", re.I)
SUMMER_RE = re.compile(r"summer\s*'?(20)?27|summer\s*/\s*fall|summer\s+(intern|co-?op)|may\s*[-–]\s*aug|june\s*[-–]\s*aug|summer 2026|summer 2027", re.I)
FALL_RE = re.compile(r"fall\s*'?(20)?26|fall 2026|sept?(ember)?\s*[-–]\s*dec", re.I)

NON_US = re.compile(r"\b(india|bangalore|bengaluru|hyderabad|pune|noida|chennai|china|shanghai|beijing|shenzhen|suzhou|wuxi|nanjing|chengdu|hangzhou|xi'an|taiwan|hsinchu|kuala lumpur|"
                    r"taipei|germany|munich|dresden|israel|tel aviv|haifa|uk\b|united kingdom|london|bristol|manchester|edinburgh|cambridge, uk|ireland|emea|apac|"
                    r"netherlands|eindhoven|france|poland|romania|singapore|malaysia|penang|philippines|vietnam|japan|tokyo|"
                    r"korea|seoul|canada|toronto|vancouver|ottawa|montreal|mexico|brazil|austria|switzerland|zurich|belgium|"
                    r"sweden|finland|norway|denmark|spain|italy|czech|hungary|serbia|greece|turkey|egypt|australia|sydney)\b", re.I)


def is_intern(job):
    return bool(LEVEL_RE.search(job["title"]))


def hardware_score(job):
    """Strong hits in the title win. Weak hits only count when the title is not software-flavoured."""
    title = job["title"]
    strong = [m.group(1).lower() for m in STRONG_RE.finditer(title)]
    if strong:
        return sorted(set(strong))
    if SOFTWARE_ONLY.search(title):
        return []
    weak = [m.group(1).lower() for m in WEAK_RE.finditer(title)]
    return sorted(set(weak))


def term_of(job):
    text = job["title"] + " " + (job.get("description") or "")[:4000]
    spring, summer, fall = SPRING_RE.search(text), SUMMER_RE.search(text), FALL_RE.search(text)
    if spring:
        return "spring"
    if summer and not fall:
        return "summer"
    if fall:
        return "fall"
    if re.search(r"co-?op", job["title"], re.I):
        return "coop-unspecified"
    return "unspecified"


def classify(job):
    """Return (keep, reason, meta). meta has term, hw_hits, tier."""
    title = job["title"]
    if not is_intern(job):
        return False, "not intern", {}
    hits = hardware_score(job)
    # small tracked startups: keep every intern/co-op posting as a Maybe, whatever the title. A 30-person chip
    # startup titles its roles "Software Engineer Intern" as often as "Analog Design Intern", and the whole point of
    # tracking it is not to miss it. Summer-only still drops; the big companies keep the strict filter.
    if job.get("tier") == "startup" and not hits and not NON_US.search(job.get("location") or ""):
        t = term_of(job)
        if t not in ("summer", "fall"):
            return True, "ok", {"term": t, "hw": ["maybe"], "rank": "A" if t == "spring" else ("B" if t == "coop-unspecified" else "C"), "maybe": True}
    # unknown small company, plain title, but the description reads like a chip company (LinkedIn finds these): Maybe
    if not hits and not EXCLUDE_TITLE.search(title) and not NON_US.search(job.get("location") or ""):
        d = (job.get("description") or "")[:4000]
        if len(set(m.group(1).lower() for m in CHIP_COMPANY_RE.finditer(d))) >= 3:
            t = term_of(job)
            if t not in ("summer", "fall"):
                return True, "ok", {"term": t, "hw": ["maybe"], "rank": "A" if t == "spring" else ("B" if t == "coop-unspecified" else "C"), "maybe": True}
    if EXCLUDE_TITLE.search(title):
        # a hardware word next to an excluded word ("Electro-Mechanical Instrument", "Sales Engineer - RF") is a Maybe, not a drop
        if hits and job.get("tier"):
            t = term_of(job)
            if t in ("spring", "coop-unspecified"):
                return True, "ok", {"term": t, "hw": ["maybe"], "rank": "A" if t == "spring" else "B", "maybe": True}
        return False, "excluded title", {}
    if SOFTWARE_ONLY.search(title) and not hits:
        return False, "software only", {}
    if re.search(r"\bsoftware (test|qa|quality)\b", title, re.I):   # "software test engineer" is a software role even though "test engineer" is a hw keyword
        return False, "software only", {}
    if not hits:
        # "Maybe" rule: at a company we deliberately track (it is in companies.json), a spring-2027 or co-op posting with
        # an engineering-flavoured title is worth a look even without a hardware word (Draper "Systems Engineering Co-Op").
        if job.get("tier") and re.search(r"engineer|systems|technical|r&d|research|design", title, re.I) \
                and not NON_US.search(job.get("location") or "") and term_of(job) in ("spring", "coop-unspecified"):
            t = term_of(job)
            return True, "ok", {"term": t, "hw": ["maybe"], "rank": "A" if t == "spring" else "B", "maybe": True}
        return False, "no hardware keyword", {}
    if PHD_ONLY.search(title) and not re.search(r"\b(bs|bachelor|undergrad)", title, re.I):
        return False, "phd/ms only", {}
    if NON_US.search(job.get("location") or ""):
        return False, "non-US", {}
    term = term_of(job)
    if term in ("summer", "fall"):
        return False, f"{term} only", {"term": term}
    # rank: A = spring explicit, B = co-op (term unstated), C = intern with no term stated
    rank = "A" if term == "spring" else ("B" if term == "coop-unspecified" else "C")
    return True, "ok", {"term": term, "hw": hits[:4], "rank": rank}
