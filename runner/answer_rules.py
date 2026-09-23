"""Deterministic answer rules: direct fact mappings and derivations with a documented logical direction.

Each rule matches an anchored pattern (so extra words such as "not", "professional", "3+ years" or a city make it
miss and fall through), and returns a Proposal with the fact ids it relied on, or a need. A missing fact is
never turned into "No".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .policy import Proposal, need
from .questions import Question, norm

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
MON = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}
US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
             "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
             "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
             "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
             "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
             "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
             "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
             "WY": "Wyoming", "DC": "District of Columbia"}
COUNTRY_SYNONYMS = {"united states": [r"^united states( of america)?$", r"^usa$", r"^us$", r"^u\.s\.a?\.?$", r"^united states( of america)? \(\+1\)$", r"^united states( of america)? \+1$"]}


@dataclass
class Ctx:
    facts: object
    today: date = field(default_factory=date.today)
    employer: str = ""
    job_id: str = ""
    job_title: str = ""
    job_location: str = ""
    consents: list = field(default_factory=list)       # consent categories you pre-approved
    cover_letter: bool = False
    materials: dict = field(default_factory=dict)      # kind -> artifact id


def _f(ctx, pred):
    return ctx.facts.get(pred, {"employer": ctx.employer, "job": ctx.job_id}, on=ctx.today)


def fact(ctx, pred, key, *, synonyms=None, transform=None, sensitive=False):
    f = _f(ctx, pred)
    if not f or f["value"] in (None, "", []):
        return need("not saved in your profile", [pred], key)
    v = transform(f["value"]) if transform else f["value"]
    if v is None:
        return need("the saved value can't answer this form's question", [pred], key)
    return Proposal(value=v, basis="fact", evidence=[f["id"]], key=key, synonyms=list(synonyms or []), sensitive=sensitive)


def yesno(ctx, pred, key, invert=False, sensitive=False):
    f = _f(ctx, pred)
    if not f or not isinstance(f["value"], bool):
        return need("this fact isn't confirmed", [pred], key)
    v = f["value"] ^ invert
    return Proposal(value="Yes" if v else "No", basis="fact", evidence=[f["id"]], key=key, sensitive=sensitive)


# ------------------------------------------------------------------------------------------- derivations
def grad(ctx: Ctx, q: Question, key: str, part: str):
    """Graduation keeps the precision you gave: YYYY-MM never becomes a specific day."""
    f = _f(ctx, "education.graduation")
    if not f or not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", str(f["value"])):
        return need("graduation date not saved", ["education.graduation"], key)
    y, m = int(f["value"][:4]), int(f["value"][5:7])
    day = int(f["value"][8:10]) if len(f["value"]) == 10 else None
    ev = [f["id"]]
    if part == "year":
        return Proposal(value=str(y), basis="derived", evidence=ev, derivation="year_of_graduation", key=key)
    if part == "month":
        opts = q.option_list()
        syn = [rf"^0?{m}$", rf"^{MONTHS[m-1]}$", rf"^{MONTHS[m-1][:3]}\.?$"]
        return Proposal(value=MONTHS[m - 1].capitalize() if opts else f"{m:02d}", basis="derived", evidence=ev,
                        derivation="month_of_graduation", key=key, synonyms=syn)
    # full "graduation date"
    ph = (q.placeholder or "").lower()
    if q.control == "date" or re.search(r"dd", ph):
        if day is None:
            return need("the form wants an exact day; only the month and year are saved", ["education.graduation"], key)
        return Proposal(value=f"{y:04d}-{m:02d}-{day:02d}" if q.control == "date" else f"{m:02d}/{day:02d}/{y:04d}",
                        basis="derived", evidence=ev, derivation="full_graduation_date", key=key)
    if q.option_list():
        syn = [rf"^{MONTHS[m-1]},? {y}$", rf"^{MONTHS[m-1][:3]}\.?,? {y}$", rf"^0?{m}/{y}$", rf"^{y}-0?{m}$"]
        return Proposal(value=f"{MONTHS[m-1].capitalize()} {y}", basis="derived", evidence=ev, derivation="month_year_of_graduation",
                        key=key, synonyms=syn)
    if "mm/yyyy" in ph or "mm / yyyy" in ph:
        return Proposal(value=f"{m:02d}/{y}", basis="derived", evidence=ev, derivation="month_year_of_graduation", key=key)
    return Proposal(value=f"{MONTHS[m-1].capitalize()} {y}", basis="derived", evidence=ev, derivation="month_year_of_graduation", key=key)


def _month_index(tok):
    return MON.get(tok[:3].lower()) if tok else None


INTERVAL = re.compile(r"(jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)\.?\s*(\d{4})?\s*(?:-|–|—|to|through|until)\s*(jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)\.?\s*(\d{4})", re.I)


def interval_in(text: str):
    m = INTERVAL.search(text or "")
    if not m:
        return None
    m1, y1, m2, y2 = _month_index(m.group(1)), m.group(2), _month_index(m.group(3)), int(m.group(4))
    y1 = int(y1) if y1 else (y2 if m1 <= m2 else y2 - 1)
    return (y1, m1), (y2, m2)


def availability_covers(ctx: Ctx, q: Question, key: str):
    """Yes only when a saved availability interval fully covers the asked one; hours/location aren't implied."""
    asked = interval_in(q.label) or interval_in(q.context)
    if not asked:
        return need("the question's dates couldn't be read exactly", [], key)
    f = _f(ctx, "availability.intervals")
    if not f:
        return need("your availability dates aren't saved", ["availability.intervals"], key)
    (ay, am), (by, bm) = asked
    for iv in f["value"] or []:
        try:
            fy, fm = map(int, iv["from"][:7].split("-"))
            ty, tm = map(int, iv["to"][:7].split("-"))
        except (KeyError, ValueError, AttributeError):
            continue
        if (fy, fm) <= (ay, am) and (by, bm) <= (ty, tm):
            return Proposal(value="Yes", basis="derived", evidence=[f["id"]], derivation="availability_interval_covers", key=key)
    return need("your saved availability doesn't cover these exact dates", ["availability.intervals"], key)


def currently_student(ctx: Ctx, q: Question, key: str):
    f = _f(ctx, "education.enrolled")
    if not f or f["value"] is not True:
        return need("current enrollment isn't confirmed", ["education.enrolled"], key)
    return Proposal(value="Yes", basis="derived", evidence=[f["id"]], derivation="current_enrollment_implies_current_student", key=key)


def sponsorship(ctx: Ctx, q: Question, key: str, when: str):
    """'now or in the future' needs the combined fact. A 'No' to the combined question implies 'No' to each part;
    a 'Yes' to it says nothing about either part alone."""
    exact = _f(ctx, f"work_auth.sponsorship_{when}")
    if exact and isinstance(exact["value"], bool):
        return Proposal(value="Yes" if exact["value"] else "No", basis="fact", evidence=[exact["id"]], key=key, sensitive=True)
    if when in ("now", "future"):
        both = _f(ctx, "work_auth.sponsorship_now_or_future")
        if both and both["value"] is False:
            return Proposal(value="No", basis="derived", evidence=[both["id"]], derivation="no_sponsorship_now_or_future_implies_none_" + when,
                            key=key, sensitive=True)
    if when == "now_or_future":
        now, fut = _f(ctx, "work_auth.sponsorship_now"), _f(ctx, "work_auth.sponsorship_future")
        if now and fut and now["value"] is False and fut["value"] is False:
            return Proposal(value="No", basis="derived", evidence=[now["id"], fut["id"]], derivation="neither_now_nor_future", key=key, sensitive=True)
        if (now and now["value"] is True) or (fut and fut["value"] is True):
            src = now if now and now["value"] is True else fut
            return Proposal(value="Yes", basis="derived", evidence=[src["id"]], derivation="either_implies_now_or_future", key=key, sensitive=True)
    return need("sponsorship isn't confirmed for this timeframe", ["work_auth.sponsorship_" + when], key)


def prior_employer(ctx: Ctx, q: Question, key: str):
    emp = norm(ctx.employer)
    lst = _f(ctx, "history.employers")
    complete = _f(ctx, "history.employers_complete")
    if not emp or not lst:
        return need("employment history isn't saved", ["history.employers"], key)
    names = [norm(x) for x in (lst["value"] or [])]
    if any(n and (n == emp or n in emp or emp in n) for n in names):
        return Proposal(value="Yes", basis="derived", evidence=[lst["id"]], derivation="employer_in_history", key=key)
    if complete and complete["value"] is True:
        return Proposal(value="No", basis="derived", evidence=[lst["id"], complete["id"]], derivation="complete_history_excludes_employer", key=key)
    return need("your employment history isn't marked complete", ["history.employers_complete"], key)


def relocate_to(ctx: Ctx, q: Question, key: str):
    gen = _f(ctx, "prefs.relocate_general")
    if gen and gen["value"] is True:
        return Proposal(value="Yes", basis="derived", evidence=[gen["id"]], derivation="general_relocation_rule", key=key)
    locs = _f(ctx, "prefs.locations")
    place = norm(re.sub(r".*\brelocat\w* (to|for) ", "", q.label))
    if locs and any(norm(x).split(",")[0] and norm(x).split(",")[0] in place for x in locs["value"] or []):
        return Proposal(value="Yes", basis="derived", evidence=[locs["id"]], derivation="location_in_preferences", key=key)
    return need("relocation to this place isn't confirmed", ["prefs.relocate_general"], key)


def skill_experience(ctx: Ctx, q: Question, key: str, skill: str):
    f = _f(ctx, "skills.list")
    if not f:
        return need("your skills aren't saved", ["skills.list"], key)
    for s in f["value"] or []:
        names = [norm(s.get("name", ""))] + [norm(a) for a in s.get("aliases", [])]
        if norm(skill) in names:
            return Proposal(value="Yes", basis="derived", evidence=[f["id"]], derivation="documented_use_of_skill", key=key)
    return need(f"experience with {skill} isn't documented", ["skills.list"], key)


def consent(ctx: Ctx, q: Question, key: str):
    text = norm(q.label + " " + q.context)
    if re.search(r"marketing|newsletter|promotional|job alerts?|text messages|sms|talent community|future (job )?opportunities|keep me (informed|updated)", text):
        if q.required:
            return need("a required marketing/communications consent isn't in your saved policy", ["policy.optional_marketing_consent"], key)
        return Proposal(value=None, basis="policy", kind="skip", key=key, evidence=["policy:optional_marketing_consent"])
    cats = []
    if re.search(r"privacy (policy|notice|statement)|data privacy", text):
        cats.append("privacy_policy")
    if re.search(r"terms (of use|and conditions|of service)", text):
        cats.append("terms_of_use")
    if re.search(r"(process|store|use) (my|your) (personal )?(data|information)|data processing|gdpr", text):
        cats.append("data_processing")
    if re.search(r"(certify|attest|confirm).{0,60}(true|accurate|complete|correct)|information .{0,40}(true|accurate)", text):
        cats.append("accuracy_certification")
    if not cats or re.search(r"background check|drug (test|screen)|arbitration|non-?compete|credit check", text):
        return need("this consent isn't covered by your saved policy", ["policy.approved_consents"], key)
    if not all(c in ctx.consents for c in cats):
        return need("this consent isn't covered by your saved policy", ["policy.approved_consents"], key)
    opts = q.option_list()
    value = opts[0] if len(opts) == 1 else "Yes"      # a single consent checkbox: check that box
    return Proposal(value=value, basis="policy", kind="consent", evidence=[f"policy:{c}" for c in cats], key=key,
                    synonyms=[r"^(yes|i agree|agree|i accept|accept|i acknowledge|acknowledged|i certify)\b"])


def eeo(ctx: Ctx, q: Question, key: str):
    if not q.option_list():
        return need("self-identification is only answered by choosing a decline option", [], key)
    f = _f(ctx, "prefs.eeo_policy")
    if not f or not re.search(r"decline|not.*(answer|disclos)|self.identify", str(f["value"]), re.I):
        return need("no saved self-identification preference", ["prefs.eeo_policy"], key)
    return Proposal(value="decline", basis="fact", evidence=[f["id"]], key=key, sensitive=True,
                    synonyms=[r"decline|don'?t wish|do not wish|prefer not|not to (answer|say|disclose)|rather not|no answer|choose not|(do not|don'?t) want to (answer|disclose|self.identify)"])


def education_date(ctx: Ctx, q: Question, key: str):
    """'From'/'To' (month/year) inside an Education section: start from education.start, end from the graduation date.
    Outside an Education section the same words mean something else, so they stay unanswered."""
    if not re.search(r"educat|school|universit|degree", (q.context or "") + " " + q.label, re.I):
        return need("a From/To date outside an education section", [], key)
    n = q.norm
    end = bool(re.match(r"^(to|end|expected end|to \(actual or expected\)|graduation)", n))
    pred = "education.graduation" if end else "education.start"
    f = _f(ctx, pred)
    if not f or not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", str(f["value"])):
        return need("education dates aren't saved", [pred], key)
    y, m = int(f["value"][:4]), int(f["value"][5:7])
    part = "year" if n.endswith("year") else "month" if n.endswith("month") else "both"
    ev = [f["id"]]
    if part == "year":
        return Proposal(value=str(y), basis="derived", evidence=ev, derivation="education_date_year", key=key)
    if part == "month":
        return Proposal(value=f"{m:02d}" if not q.option_list() else MONTHS[m - 1].capitalize(), basis="derived", evidence=ev,
                        derivation="education_date_month", key=key, synonyms=[rf"^0?{m}$", rf"^{MONTHS[m-1]}$", rf"^{MONTHS[m-1][:3]}$"])
    if q.control == "date":
        return need("an exact day is needed; only month and year are saved", [pred], key)
    return Proposal(value=f"{m:02d}/{y}", basis="derived", evidence=ev, derivation="education_date_month_year", key=key)


def signature_date(ctx: Ctx, q: Question, key: str):
    """The date on a self-identification form is the day you sign it: today."""
    if not re.search(r"self.?identif|disabilit|voluntary|signature|sign", (q.context or "") + " " + q.label, re.I):
        return need("a date field whose meaning isn't clear", [], key)
    d = ctx.today
    if q.control == "date":
        v = d.isoformat()
    else:
        v = f"{d.month:02d}/{d.day:02d}/{d.year:04d}"
    return Proposal(value=v, basis="policy", evidence=["policy:signature_date_is_today"], derivation="signature_date_today", key=key)


def state_value(v):
    v = str(v).strip()
    return US_STATES.get(v.upper(), v)


def state_synonyms(v):
    v = str(v).strip()
    full = US_STATES.get(v.upper(), v)
    abbr = next((k for k, n in US_STATES.items() if n.lower() == full.lower()), v)
    return [rf"^{re.escape(full.lower())}$", rf"^{re.escape(abbr.lower())}$", rf"^{re.escape(abbr.lower())} ?- ?{re.escape(full.lower())}$"]


def degree_synonyms(v):
    if re.search(r"bachelor", str(v), re.I):
        return [r"^bachelor'?s( degree)?$", r"^bachelor of science( \(b\.?s\.?\))?$", r"^b\.?s\.?$", r"^undergraduate$", r"^bachelor'?s degree \(.*\)$"]
    return []


# ------------------------------------------------------------------------------------------- the rule table
L = lambda rx: re.compile(rx)   # noqa: E731  anchored patterns only

RULES = [
    ("contact.first_name", L(r"^(first name|given name|legal first name|first)$"), lambda c, q, k: fact(c, "contact.first_name", k)),
    ("contact.last_name", L(r"^(last name|surname|family name|legal last name|last)$"), lambda c, q, k: fact(c, "contact.last_name", k)),
    ("contact.preferred_name", L(r"^(preferred (first )?name|nickname)$"), lambda c, q, k: fact(c, "contact.preferred_name", k)),
    ("contact.full_name", L(r"^(name|full name|legal name|full legal name|your name)$"), lambda c, q, k: fact(c, "contact.full_name", k)),
    ("contact.email", L(r"^(e-?mail( address)?|your e-?mail( address)?|confirm e-?mail( address)?|email address confirmation)$"), lambda c, q, k: fact(c, "contact.application_email", k)),
    ("contact.phone", L(r"^(phone( number)?|mobile( phone)?( number)?|telephone( number)?|cell phone( number)?|primary phone( number)?)$"), lambda c, q, k: fact(c, "contact.phone", k)),
    ("contact.linkedin", L(r"^(linkedin( profile)?( url)?|linkedin link)$"), lambda c, q, k: fact(c, "contact.linkedin", k)),
    ("contact.github", L(r"^(github( profile)?( url)?|github link)$"), lambda c, q, k: fact(c, "contact.github", k)),
    ("contact.portfolio", L(r"^(website|websites|portfolio( url)?|personal (website|site)|github or portfolio url|other website)$"), lambda c, q, k: fact(c, "contact.portfolio", k)),
    ("address.city", L(r"^(city|current city|city of residence)$"), lambda c, q, k: fact(c, "address.city", k)),
    ("address.state", L(r"^(state|province|state/province|state or province|region)$"),
     lambda c, q, k: fact(c, "address.state", k, transform=state_value, synonyms=state_synonyms(_f(c, "address.state")["value"]) if _f(c, "address.state") else [])),
    ("address.zip", L(r"^(zip( code)?|postal code|zip/postal code|postcode|what is the zip code of your primary residence)$"), lambda c, q, k: fact(c, "address.zip", k)),
    ("address.country", L(r"^(country|country of residence|current country|country/territory|country/region|country/region of residence|country of residence/region)$"),
     lambda c, q, k: fact(c, "address.country", k, synonyms=COUNTRY_SYNONYMS.get(norm(str((_f(c, "address.country") or {}).get("value", ""))), []))),
    ("address.line1", L(r"^(address( line)? ?1|address line one|street address|street|address)$"), lambda c, q, k: fact(c, "address.line1", k)),
    ("address.line2", L(r"^(address( line)? ?2|apartment, suite, etc|apt/suite)$"), lambda c, q, k: fact(c, "address.line2", k)),
    ("contact.phone_type", L(r"^((phone )?device type|phone type|type of phone)$"), lambda c, q, k: fact(c, "contact.phone_type", k)),
    ("contact.phone_country", L(r"^(country phone code|phone country( code)?|country code|phone code)$"),
     lambda c, q, k: fact(c, "address.country", k, synonyms=[rf"^{re.escape(norm(str((_f(c, 'address.country') or {}).get('value', ''))))}( of america)? \(\+\d+\)$",
                                                              rf"^{re.escape(norm(str((_f(c, 'address.country') or {}).get('value', ''))))}( of america)? \+\d+$", r"^\+1$"])),
    ("education.dates", L(r"^(from|start|start date|from date|attended from|to|end|end date|to \(actual or expected\)|expected end date|to date)( month| year)?$"), education_date),
    ("self_id.date", L(r"^(date|today'?s date|signature date|date signed)$"), signature_date),
    ("address.location", L(r"^(location( \(city\))?|current location|your location|where are you (located|based)|where do you live)$"), lambda c, q, k: fact(c, "address.location_text", k)),
    ("education.school", L(r"^(school|university|college|college or university|school / university|school name|university name|institution|school or university)$"), lambda c, q, k: fact(c, "education.school", k)),
    ("education.major", L(r"^(major|field of study|major or field of study|discipline|concentration|area of study)$"), lambda c, q, k: fact(c, "education.major", k)),
    ("education.degree", L(r"^(degree|education level|highest (education|degree) level|current program type|program type|level of study|type of program|degree type|degree pursuing)$"),
     lambda c, q, k: fact(c, "education.degree", k, synonyms=degree_synonyms((_f(c, "education.degree") or {}).get("value", "")))),
    ("education.year_standing", L(r"^(academic year|class standing|year in school|current year of study|current year in school)$"), lambda c, q, k: fact(c, "education.year_standing", k)),
    ("education.gpa", L(r"^(current |cumulative |overall |undergraduate |undergrad )?(gpa|grade point average)( \(undergraduate\))?( \(4\.0 scale\))?$"), lambda c, q, k: fact(c, "education.gpa", k)),
    ("education.graduation.month_year", L(r"^(expected |anticipated )?(graduation date|date of graduation|graduation)( \(mm/yyyy\))?$|^select your anticipated bachelor'?s degree graduation date$|^when do you (expect to )?graduate$"),
     lambda c, q, k: grad(c, q, k, "full")),
    ("education.graduation.month", L(r"^(expected |anticipated )?graduation month$"), lambda c, q, k: grad(c, q, k, "month")),
    ("education.graduation.year", L(r"^(expected |anticipated )?graduation year$|^(expected )?year of graduation$"), lambda c, q, k: grad(c, q, k, "year")),
    ("education.currently_student", L(r"^(are you (currently )?(a )?(current |enrolled )?(student|enrolled( in (a|an) (degree|university|college) program)?)|are you currently enrolled in (a|an) (accredited )?(degree|university|college|bachelor'?s) program)$"),
     currently_student),
    ("test_scores.act", L(r"^(act( score)?|act composite score)$"), lambda c, q, k: fact(c, "test_scores.act", k, transform=str)),
    ("test_scores.summary", L(r"^(sat or act score|test scores?)$"), lambda c, q, k: fact(c, "test_scores.summary", k)),
    ("work_auth.us_authorized", L(r"^(are you (legally |currently )?authorized to work in (the )?(united states|u\.?s\.?)(,? on a full[- ]time basis)?( without restriction)?|are you legally eligible to work in (the )?(united states|u\.?s\.?))$"),
     lambda c, q, k: yesno(c, "work_auth.us_authorized", k, sensitive=True)),
    ("work_auth.sponsorship_now_or_future", L(r"^(will you now or in the future require (visa |employment )?sponsorship( for employment visa status)?( \(e\.g\.,? h-1b visa status\))?|do you( now or in the future)? require( visa| employment)? sponsorship|will you now,? or at any point in the future,? require sponsorship)$"),
     lambda c, q, k: sponsorship(c, q, k, "now_or_future")),
    ("work_auth.sponsorship_future", L(r"^(will you (in the future )?require (visa |employment )?sponsorship in the future|will you require sponsorship in the future)$"), lambda c, q, k: sponsorship(c, q, k, "future")),
    ("work_auth.sponsorship_now", L(r"^(do you currently require (visa |employment )?sponsorship|do you require sponsorship now)$"), lambda c, q, k: sponsorship(c, q, k, "now")),
    ("citizenship.us_citizen", L(r"^(are you (a )?(u\.?s\.?|united states) citizen)$"), lambda c, q, k: yesno(c, "citizenship.us_citizen", k, sensitive=True)),
    ("prefs.relocate_general", L(r"^(are you willing to relocate|would you be willing to relocate|are you open to relocation|willing to relocate)$"),
     lambda c, q, k: yesno(c, "prefs.relocate_general", k)),
    ("prefs.relocate_to", L(r"^(are you|would you be) (willing|able|open) to relocate (to|for) .+$"), relocate_to),
    ("person.over_18", L(r"^(are you (at least |over )18( years (old|of age))?( or older)?|are you 18 years (of age )?or older)$"), lambda c, q, k: yesno(c, "person.over_18", k)),
    ("history.prior_employee", L(r"^(have you (previously|ever) (worked for|been employed by|worked at) (us|this company|[\w .&'-]+)|are you a (current or )?former employee( of [\w .&'-]+)?)$"),
     prior_employer),
    ("clearance.active", L(r"^(do you (currently )?have an? (active )?(u\.?s\.? )?security clearance|do you hold an active security clearance)$"), lambda c, q, k: yesno(c, "clearance.active", k)),
    ("prefs.hear_about", L(r"^how did you (hear about|find|learn about|discover) (us|this (job|role|position|opportunity)|[\w .&'-]+)$|^source$|^how did you hear about this (job|position)$"),
     lambda c, q, k: fact(c, "prefs.hear_about", k, synonyms=[r"^(company|corporate) (website|careers? (page|site|website))$", r"^careers? (page|site|website)$", r"^company website$"] if norm(str((_f(c, "prefs.hear_about") or {}).get("value", ""))) in ("company careers page", "company website") else [])),
    ("prefs.salary", L(r"^(desired salary|salary expectations?|expected salary|desired compensation|desired pay|compensation expectations?)$"), lambda c, q, k: fact(c, "prefs.salary", k)),
    ("availability.start_date", L(r"^(earliest start date|when can you start|available start date|start date)$"),
     lambda c, q, k: fact(c, "availability.start_date", k)),
    ("availability.summary", L(r"^(availability|when are you available|what is your availability)$"), lambda c, q, k: fact(c, "availability.summary", k)),
    ("availability.covers_interval", L(r"^(are you|will you be|would you be) (available|able) to (work|intern|complete|participate)[^?]*(jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)[^?]*\d{4}[^?]*$"),
     availability_covers),
    ("availability.terms", L(r"^(which term\(s\) would you like to be considered for|preferred term|which (term|semester|season)( are you applying for)?|term|co-?op term)$"),
     lambda c, q, k: _terms(c, q, k)),
    ("prefs.locations", L(r"^(select the location you can commute or relocate to|location preference|preferred (office|location|work location)s?|which (office|location|site)s?( would you prefer)?)$"),
     lambda c, q, k: _locations(c, q, k)),
    ("eeo", L(r"^(gender|race|ethnicity|race/ethnicity|veteran ?status|protected veteran status|disability ?status|pronouns|sexual orientation|are you hispanic or latino|hispanic/latino|do you identify as transgender|how would you describe your (gender identity|racial|sexual orientation).*|please select the veteran status which (most accurately )?describes you|voluntary self-identification of disability)$"),
     eeo),
    ("skills.experience", L(r"^(do you have|have you had|have you gained) (any |prior |hands-on )?experience (with|using|in|working with) ([a-z0-9+#/ .-]{2,40})$"),
     lambda c, q, k: skill_experience(c, q, k, re.match(r".*experience (?:with|using|in|working with) (.+)$", q.norm).group(1))),
]


def _terms(ctx, q, key):
    f = _f(ctx, "availability.terms")
    if not f:
        return need("the terms you want aren't saved", ["availability.terms"], key)
    terms = [norm(t) for t in f["value"] or []]
    opts = q.option_list()
    matches = [o for o in opts if not re.search(r"\b20\d{2}\b", o) and any(t in re.split(r"[^a-z]+", norm(o)) for t in terms)]
    if q.multiple and matches:
        return Proposal(value=matches, basis="derived", evidence=[f["id"]], derivation="terms_wanted", key=key)
    if len(matches) == 1:
        return Proposal(value=matches[0], basis="derived", evidence=[f["id"]], derivation="terms_wanted", key=key)
    return need("confirm the term and its dates", ["availability.terms"], key)


def _locations(ctx, q, key):
    f = _f(ctx, "prefs.locations")
    if not f:
        return need("choose the office location(s) you want", ["prefs.locations"], key)
    want = {norm(x) for x in f["value"] or []}
    matches = [o for o in q.option_list() if norm(o) in want]
    if q.multiple and matches:
        return Proposal(value=matches, basis="fact", evidence=[f["id"]], key=key)
    if len(matches) == 1:
        return Proposal(value=matches[0], basis="fact", evidence=[f["id"]], key=key)
    return need("choose the office location(s) you want", ["prefs.locations"], key)


CONSENT_RX = re.compile(r"\b(i agree|i accept|i acknowledge|i certify|i consent|i have read|i understand|i confirm|by (checking|submitting|clicking)|acknowledg\w+|consent|terms (of use|and conditions)|privacy (policy|notice|statement)|attest)\b", re.I)
RESUME_RX = re.compile(r"^(resume(/cv)?|cv|resume or cv|upload (your )?(resume|cv)|attach (your )?(resume|cv)|resume/cv upload)$")
COVER_RX = re.compile(r"^(cover letter|upload (your )?cover letter|attach (your )?cover letter)$")


MARKETING_RX = re.compile(r"marketing|newsletter|promotional|job alerts?|text messages|\bsms\b|talent community|future (job )?opportunities|keep me (informed|updated)|send me", re.I)
RULE_BY_KEY = {}


def builtin(q: Question, ctx: Ctx, key: str | None = None):
    """(key, proposal) from the rule table, or (None, None). `key` routes an equivalent wording (from the
    preparation bank) to a rule whose own pattern doesn't match it."""
    if not RULE_BY_KEY:
        RULE_BY_KEY.update({k: fn for k, _, fn in RULES})
    if key in RULE_BY_KEY:
        return key, RULE_BY_KEY[key](ctx, q, key)
    n = q.norm
    if q.control == "file" or RESUME_RX.match(n) or COVER_RX.match(n):
        if RESUME_RX.match(n) or (q.control == "file" and re.search(r"\b(resume|cv)\b", n)):
            aid = ctx.materials.get("resume")
            return "material.resume", (Proposal(value=aid, basis="material", kind="file", evidence=[f"material:{aid}"], key="material.resume")
                                       if aid else need("no resume selected for this job", ["materials.resume"], "material.resume"))
        if COVER_RX.match(n) or (q.control == "file" and "cover" in n):
            if not ctx.cover_letter:
                return "material.cover", Proposal(value=None, basis="policy", kind="skip", key="material.cover", evidence=["policy:cover_off"])
            aid = ctx.materials.get("cover_letter")
            if q.control == "file":
                return "material.cover", (Proposal(value=aid, basis="material", kind="file", evidence=[f"material:{aid}"], key="material.cover")
                                          if aid else need("no cover letter prepared", ["materials.cover_letter"], "material.cover"))
            return "material.cover_text", None      # a cover-letter text box: answered from generated text (bank)
        if q.control == "file":
            return "material.other", need("an upload the saved materials don't cover", [], "material.other")
    if q.control in ("checkbox", "boolean") and len(q.option_list()) <= 1 and (CONSENT_RX.search(q.label) or MARKETING_RX.search(q.label)):
        return "consent", consent(ctx, q, "consent:" + n[:80])
    for key, rx, fn in RULES:
        if rx.match(n):
            return key, fn(ctx, q, key)
    return None, None
