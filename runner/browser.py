"""Browser observation and the typed executor.

The executor is the only code that touches the page. Its actions are narrow and checked: fill a control with a
*validated* answer, choose an *observed* option, upload an *approved* material, type a credential *reference* on a
host the realm allows, click a *non-final* navigation button. The final submit button is refused here; only
submission.py may press it.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .evidence import scrub
from .questions import Question, norm

OBSERVER_JS = Path(__file__).with_name("observer.js").read_text(encoding="utf-8")

SUBMIT_RX = re.compile(r"^(submit( my| your)?( application)?|send( my)? application|submit & apply|finish( and submit)?|complete application)$", re.I)
NEXT_RX = re.compile(r"^(next( step| page)?|continue|save (and|&) continue|continue to next( step)?|proceed|save and next|next: .{1,40})$", re.I)
REVIEW_RX = re.compile(r"^(review( (your )?application)?|review and submit|preview)$", re.I)
APPLY_ENTRY_RX = re.compile(r"^(apply( now| for this job| to this job| for this position| online| here)?|i'm interested|start (your )?application|begin application|apply manually)$", re.I)
SIGNIN_RX = re.compile(r"^(sign in|log ?in|login|sign in to apply)$", re.I)
CREATE_RX = re.compile(r"^(create (an )?account|register|sign up|create my account)$", re.I)
AVOID_RX = re.compile(r"cancel|back|previous|withdraw|save (for )?later|save draft|delete|remove|sign out|log out|autofill|linkedin|indeed|share|refer|alert|add another|upload|attach", re.I)

CONFIRM_RX = re.compile(r"thank you for applying|thanks for applying|application (has been |was |is |successfully )?(submitted|received)|we(?:'ve| have) received your application|your application (was|has been) sent|application submitted|successfully applied", re.I)
CLOSED_RX = re.compile(r"(job|position|posting|requisition|opening) (is |has been )?(no longer (available|accepting|open)|closed|filled|expired)|no longer accepting applications|this job (is )?not available|page (you are looking for )?(can'?t|cannot) be found|job not found", re.I)
VERIFY_EMAIL_RX = re.compile(r"verify your (e-?mail|account)|check your (e-?mail|inbox)|we('ve| have)? sent (you )?(an? )?(e-?mail|verification|link|code)|activate your account|confirm your e-?mail", re.I)
CODE_RX = re.compile(r"(enter|type) the (\d+-digit )?(verification |one[- ]time |security )?(code|passcode|pin)|verification code|one[- ]time (pass)?code", re.I)
EXISTS_RX = re.compile(r"(account|user|e-?mail).{0,40}already (exists|registered|in use|taken)|already have an account|is already associated", re.I)
BAD_LOGIN_RX = re.compile(r"(invalid|incorrect|wrong|doesn'?t match|do not match).{0,40}(password|credentials|e-?mail|username|sign in)|(password|credentials).{0,20}(invalid|incorrect)", re.I)
PASSWORD_POLICY_RX = re.compile(r"password (must|should|needs to) (contain|include|be at least|have)|password (does not|doesn'?t) meet|password requirements", re.I)
USERNAME_TAKEN_RX = re.compile(r"user ?name .{0,30}(taken|in use|not available|already exists)", re.I)
MFA_RX = re.compile(r"authenticator app|two[- ]factor|2-step verification|multi[- ]factor", re.I)


@dataclass
class Control:
    frame: int
    ref: str
    control: str
    label: str
    options: list = field(default_factory=list)
    required: bool = False
    multiple: bool = False
    value: object = None
    placeholder: str = ""
    context: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.frame}:{self.ref}"

    def empty(self) -> bool:
        v = self.value
        if isinstance(v, list):
            return not any(str(x).strip() for x in v)
        return not str(v or "").strip()

    def question(self, job: dict) -> Question:
        ctl = self.control
        if ctl in ("text", "email", "tel", "url", "number", "date", "textarea", "password", "file", "select", "radio", "checkbox", "combobox"):
            pass
        else:
            ctl = "text"
        return Question(label=self.label, control=ctl, options=[o["label"] for o in self.options], required=self.required,
                        multiple=self.multiple, placeholder=self.placeholder, context=self.context, max_len=self.raw.get("max_len"),
                        ref=self.key, job_id=job.get("id", ""), employer=job.get("company", ""), job_title=job.get("title", ""))


@dataclass
class Button:
    frame: int
    ref: str
    text: str
    automation: str = ""
    disabled: bool = False
    href: str = ""
    kind: str = "other"
    in_form: bool = False

    @property
    def key(self):
        return f"{self.frame}:{self.ref}"


@dataclass
class Observation:
    url: str
    title: str
    controls: list
    buttons: list
    errors: list
    body: str
    captcha: bool
    headings: list
    password_fields: int
    frames: int
    progress: str = ""

    def signature(self) -> str:
        """Changes when the page meaningfully progresses (new URL, new question set, new step)."""
        labels = "|".join(sorted(f"{c.label}:{c.control}" for c in self.controls))
        base = f"{self.url.split('#')[0]}|{self.progress}|{'|'.join(self.headings[:4])}|{labels}"
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def fingerprint(self) -> str:
        """The form's question schema (for form_versions)."""
        s = "|".join(f"{c.label}:{c.control}:{','.join(o['label'] for o in c.options)}:{int(c.required)}" for c in self.controls)
        return hashlib.sha1(s.encode()).hexdigest()[:16]

    def text(self) -> str:
        return " ".join([self.title, " ".join(self.headings), self.body])

    def find_buttons(self, rx) -> list:
        return [b for b in self.buttons if rx.search(b.text) and not b.disabled]


def classify_button(b: Button) -> str:
    t = b.text.strip()
    a = (b.automation or "").lower()
    if a in ("bottom-navigation-next-button", "pagefooternextbutton") or NEXT_RX.match(t):
        return "next"
    if REVIEW_RX.match(t):
        return "review"
    if SUBMIT_RX.match(t):
        return "submit"
    if SIGNIN_RX.match(t) or a in ("signinsubmitbutton",):
        return "signin"
    if CREATE_RX.match(t) or a in ("createaccountsubmitbutton",):
        return "create_account"
    if APPLY_ENTRY_RX.match(t) or a in ("adventurebutton", "applybutton", "applymanually"):
        return "apply_entry"
    if AVOID_RX.search(t):
        return "avoid"
    return "other"


def observe(page) -> Observation:
    controls, buttons, errors, headings, body = [], [], [], [], []
    url, title, captcha, pw, progress = page.url, "", False, 0, ""
    frames = page.frames
    for i, fr in enumerate(frames):
        try:
            d = fr.evaluate(OBSERVER_JS)
        except Exception:
            continue
        if i == 0:
            url, title = d["url"], d["title"]
        for c in d["controls"]:
            controls.append(Control(i, c["ref"], c["control"], c.get("label") or "", c.get("options") or [], bool(c.get("required")),
                                    bool(c.get("multiple")), c.get("value"), c.get("placeholder") or "", c.get("context") or "", c))
        for b in d["buttons"]:
            bt = Button(i, b["ref"], b["text"], b.get("automation") or "", bool(b.get("disabled")), b.get("href") or "", in_form=bool(b.get("in_form")))
            bt.kind = classify_button(bt)
            buttons.append(bt)
        errors += d["errors"]
        headings += d["headings"]
        body.append(d["body"])
        captcha |= bool(d["captcha"])
        pw += d["password_fields"]
        progress = progress or d.get("progress") or ""
    return Observation(url, title, controls, buttons, errors, "\n".join(body)[:12000], captcha, headings, pw, len(frames), progress)


# ---------------------------------------------------------------------------------------------- executor
class ActionRefused(Exception):
    pass


class Executor:
    def __init__(self, page, accounts=None, vault=None, account_id: str | None = None, budget=None, log=None, materials=None):
        self.page = page
        self.accounts = accounts
        self.vault = vault
        self.account_id = account_id
        self.budget = budget          # callable -> bool (counts one action against the attempt budget)
        self.log = log or (lambda *a, **k: None)
        self.materials = materials or {}   # artifact id -> local file path (approved for this application only)

    def _spend(self):
        if self.budget and not self.budget():
            raise ActionRefused("action budget spent")

    def loc(self, key: str):
        fi, ref = key.split(":", 1)
        frames = self.page.frames
        fr = frames[int(fi)] if int(fi) < len(frames) else self.page.main_frame
        return fr.locator(f'[data-coop-ref="{ref}"]').first

    # ---- typed actions
    def fill_text(self, c: Control, value: str) -> bool:
        self._spend()
        el = self.loc(c.key)
        el.scroll_into_view_if_needed(timeout=5000)
        el.fill("", timeout=8000)
        el.fill(value, timeout=8000)
        try:
            el.dispatch_event("blur")
        except Exception:
            pass
        got = el.input_value(timeout=3000)
        ok = got == value
        self.log("fill", label=c.label, ok=ok)
        return ok

    def choose(self, c: Control, labels: list[str]) -> bool:
        """Select observed option(s) and read the selection back. Typed search text alone is not a selection."""
        self._spend()
        if c.control == "select":
            el = self.loc(c.key)
            vals = [o.get("value") for o in c.options if o["label"] in labels]
            el.select_option(value=vals if c.multiple else vals[0], timeout=8000)
            got = el.evaluate("e => [...e.selectedOptions].map(o => (o.innerText||o.textContent).replace(/\\s+/g,' ').trim())")
            ok = sorted(got) == sorted(labels)
        elif c.control in ("radio", "checkbox"):
            ok = True
            for o in c.options:
                want = o["label"] in labels
                if c.raw.get("kind") == "button_pair":
                    if want:
                        self.loc(f"{c.frame}:{o['ref']}").click(timeout=8000)
                    continue
                el = self.loc(f"{c.frame}:{o['ref']}")
                if el.is_checked() != want:
                    try:
                        el.set_checked(want, timeout=5000)
                    except Exception:
                        el.check(force=True, timeout=5000) if want else el.uncheck(force=True, timeout=5000)
                ok &= el.is_checked() == want
            if c.raw.get("kind") == "button_pair":
                time.sleep(0.2)
                ok = labels[0] in self._pressed(c)
        elif c.control == "combobox":
            ok = self._combo(c, labels[0])
        else:
            raise ActionRefused(f"can't choose on a {c.control}")
        self.log("choose", label=c.label, ok=ok)
        return ok

    def _pressed(self, c):
        box = self.loc(c.key)
        return box.evaluate("b => [...b.querySelectorAll('button')].filter(x => x.getAttribute('aria-pressed')==='true' || /selected|active/i.test(x.className)).map(x => x.innerText.trim())")

    def combo_options(self, c: Control, query: str | None = None) -> list[str]:
        """Open a custom dropdown and read its options (for the answer engine), then close it."""
        el = self.loc(c.key)
        el.click(timeout=8000)
        if query is not None and c.raw.get("tag") == "input":
            el.fill(query, timeout=5000)
        time.sleep(0.4)
        opts = self._visible_options(el)
        self.page.keyboard.press("Escape")
        return opts

    def _visible_options(self, el):
        owned = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
        sel = f"#{owned} [role=option]" if owned else "[role=option]:visible, [class*='select__option']:visible, [data-automation-id='promptOption']:visible"
        try:
            self.page.wait_for_selector(sel, timeout=2500)
        except Exception:
            return []
        return [re.sub(r"\s+", " ", t).strip() for t in self.page.locator(sel).all_inner_texts() if t.strip()]

    def _combo(self, c: Control, label: str) -> bool:
        el = self.loc(c.key)
        el.click(timeout=8000)
        if c.raw.get("tag") == "input":
            el.fill(label.split(",")[0][:40], timeout=5000)
        owned = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
        sel = f"#{owned} [role=option]" if owned else "[role=option], [class*='select__option'], [data-automation-id='promptOption']"
        opt = self.page.locator(sel).filter(has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
        try:
            opt.first.wait_for(state="visible", timeout=4000)
        except Exception:
            self.page.keyboard.press("Escape")
            return False
        if opt.count() != 1:
            self.page.keyboard.press("Escape")
            return False
        opt.first.click(timeout=5000)
        time.sleep(0.3)
        shown = self.loc(c.key).evaluate("""e => { const c = e.closest("[class*='select__control'], [class*='control'], [class*='Control']");
            const v = c ? [...c.querySelectorAll("[class*='single-value'], [class*='multi-value__label'], [class*='singleValue']")].map(x => x.innerText.trim()) : [];
            return v.length ? v : [ (e.tagName === 'BUTTON' ? e.innerText : e.value || '').trim() ]; }""")
        return any(norm(s) == norm(label) for s in shown)

    def upload(self, c: Control, artifact_id: str) -> bool:
        self._spend()
        path = self.materials.get(artifact_id)
        if not path or not Path(path).exists():
            raise ActionRefused("only materials approved for this application can be uploaded")
        el = self.loc(c.key)
        el.set_input_files(path, timeout=15000)
        name = Path(path).name
        # wait for the portal to show the file (async uploads) or at least for the input to hold it
        deadline = time.time() + 20
        while time.time() < deadline:
            has = el.evaluate("e => e.files && e.files.length ? e.files[0].name : ''")
            shown = name in (self.page.content() or "")
            busy = self.page.locator("[aria-busy=true], [class*='uploading' i], [class*='spinner' i]").count()
            if (has == name or shown) and not busy:
                self.log("upload", label=c.label, ok=True)
                return True
            time.sleep(0.3)
        self.log("upload", label=c.label, ok=False)
        return False

    def fill_credential(self, c: Control, what: str) -> bool:
        """Type a username/email/password on a host the realm allows. The value never leaves this method."""
        self._spend()
        if not self.accounts or not self.account_id:
            raise ActionRefused("no account for this realm")
        url = self.page.frames[c.frame].url if c.frame < len(self.page.frames) else self.page.url
        if not self.accounts.may_type_credentials(self.account_id, url):
            raise ActionRefused("credential destination not verified for this employer")
        if what == "password":
            if c.control != "password":
                raise ActionRefused("passwords only go into password fields")
            value = self.vault.reveal(self.accounts.password_ref(self.account_id))
        elif what in ("username", "email"):
            kind, v = self.accounts.username_for(self.account_id, email_login=(what == "email"))
            value = self.vault.reveal(v) if kind == "ref" else v
        else:
            raise ActionRefused("unknown credential")
        if not value:
            raise ActionRefused("credential missing from the vault")
        el = self.loc(c.key)
        el.fill("", timeout=8000)
        el.fill(value, timeout=8000)
        ok = el.input_value(timeout=3000) == value
        value = None
        self.log("credential", field=what, ok=ok)     # never the value
        return ok

    def click(self, b: Button, allow=("next", "review", "apply_entry", "signin", "other", "verify")) -> None:
        if b.kind == "submit" and "submit" not in allow:
            raise ActionRefused("the final submit is only pressed by the submission controller")
        if b.kind == "create_account" and "create_account" not in allow:
            raise ActionRefused("account creation goes through the account manager")
        if b.kind not in allow:
            raise ActionRefused(f"not a navigation button: {b.text}")
        self._spend()
        self.loc(b.key).click(timeout=10000)
        self.log("click", text=scrub(b.text), kind=b.kind)

    def settle(self, ms=600):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        end = time.time() + 10
        time.sleep(ms / 1000)
        while time.time() < end:
            try:
                busy = self.page.locator("[aria-busy=true]:visible, [data-automation-id='loadingSpinner']:visible, .spinner:visible").count()
            except Exception:
                busy = 0
            if not busy:
                return
            time.sleep(0.25)
