"""A Workday-like tenant: data-automation-id hooks, div role=button controls, listbox dropdowns, searchable
prompts, split month/year dates, account creation with email activation, six-step application, candidate home."""
from __future__ import annotations

import json
import secrets

from . import E, Fixture, json_resp, page

TENANT = "acmesemi"
HOST = f"{TENANT}.wd1.myworkdayjobs.com"
SITE = "AcmeCareers"
REQ = "R12345"
TITLE = "Test Engineering Co-op"
JOB_PATH = f"/en-US/{SITE}/job/Boston-MA/Test-Engineering-Co-op_{REQ}"
SENDER = f"Acme Semiconductor <{TENANT}@myworkday.com>"

WD_JS = r"""
function wdDropdowns(){
  document.querySelectorAll('button[aria-haspopup=listbox]').forEach(btn => {
    const list = document.getElementById(btn.getAttribute('aria-controls'));
    btn.addEventListener('click', () => { list.classList.toggle('hidden'); });
    list.querySelectorAll('[role=option]').forEach(li => li.addEventListener('click', () => {
      btn.textContent = li.textContent; btn.dataset.value = li.textContent; list.classList.add('hidden'); }));
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('[role=listbox]').forEach(l => l.classList.add('hidden')); });
  document.querySelectorAll('[data-prompt]').forEach(box => {
    const input = box.querySelector('input'), list = box.querySelector('[role=listbox]'), all = JSON.parse(box.dataset.prompt);
    const render = () => { const q = input.value.toLowerCase(); list.innerHTML = '';
      all.filter(o => q && o.toLowerCase().includes(q)).forEach(o => { const li = document.createElement('div'); li.setAttribute('role','option');
        li.setAttribute('data-automation-id','promptOption'); li.textContent = o;
        li.onclick = () => { input.value = ''; input.dataset.value = o; list.classList.add('hidden');
          let pill = box.querySelector('[data-automation-id=selectedItem]'); if (!pill) { pill = document.createElement('div'); pill.setAttribute('data-automation-id','selectedItem'); box.appendChild(pill); }
          pill.textContent = o; }; list.appendChild(li); });
      list.classList.toggle('hidden', !list.children.length); };
    input.addEventListener('input', render); input.addEventListener('click', render);
  });
}
function wdValues(){
  const out = {};
  document.querySelectorAll('button[aria-haspopup=listbox]').forEach(b => out[b.id] = b.dataset.value || '');
  document.querySelectorAll('input:not([type=radio]):not([type=checkbox]):not([type=file]), textarea').forEach(i => { if (i.id) out[i.id] = i.dataset.value || i.value; });
  document.querySelectorAll('input[type=radio]:checked').forEach(r => out[r.name] = r.value);
  document.querySelectorAll('input[type=checkbox]').forEach(c => out[c.id] = c.checked);
  document.querySelectorAll('input[type=file]').forEach(f => out[f.id] = f.files.length ? f.files[0].name : (f.dataset.done || ''));
  return out;
}
function wdButtons(){ document.querySelectorAll('[role=button][data-href]').forEach(b => b.addEventListener('click', () => location.href = b.dataset.href)); }
"""


def dd(id_, label, options, required=True):
    items = "".join(f'<div role="option" data-automation-id="promptOption">{E(o)}</div>' for o in options)
    return f"""<div data-automation-id="formField-{id_}" class="wdfield"><label id="lbl-{id_}">{E(label)}{'*' if required else ''}</label>
      <button type="button" id="{id_}" aria-haspopup="listbox" aria-labelledby="lbl-{id_}" aria-controls="lb-{id_}" aria-required="{str(required).lower()}">Select One</button>
      <div role="listbox" id="lb-{id_}" class="hidden">{items}</div></div>"""


def prompt(id_, label, options, required=True):
    return f"""<div data-automation-id="formField-{id_}" class="wdfield" data-prompt='{E(json.dumps(options))}'><label for="{id_}">{E(label)}{'*' if required else ''}</label>
      <input id="{id_}" data-automation-id="searchBox" role="combobox" aria-required="{str(required).lower()}" autocomplete="off"><div role="listbox" class="hidden"></div></div>"""


def inp(id_, label, required=True, value="", readonly=False, kind="text", aria=None):
    lab = f'<label for="{id_}">{E(label)}{"*" if required else ""}</label>' if not aria else ""
    return f"""<div data-automation-id="formField-{id_}" class="wdfield">{lab}<input id="{id_}" type="{kind}" data-automation-id="{id_}" {'required' if required else ''} {'readonly' if readonly else ''} value="{E(value)}" {f'aria-label="{E(aria)}"' if aria else ''}></div>"""


def radio(name, label, options, required=True):
    items = "".join(f'<label><input type="radio" name="{name}" value="{E(o)}"> {E(o)}</label>' for o in options)
    return f"""<fieldset data-automation-id="formField-{name}" class="wdfield" aria-required="{str(required).lower()}"><legend>{E(label)}{'*' if required else ''}</legend>{items}</fieldset>"""


def date_group(id_, label):
    return f"""<fieldset data-automation-id="formField-{id_}" class="wdfield date-group"><legend>{E(label)}*</legend>
      <input id="{id_}Month" aria-label="Month" data-automation-id="dateSectionMonth-input" required>
      <input id="{id_}Year" aria-label="Year" data-automation-id="dateSectionYear-input" required></fieldset>"""


STEPS = ["My Information", "My Experience", "Application Questions", "Voluntary Disclosures", "Self Identify", "Review"]
REQUIRED = {
    1: ["hear", "prevWorked", "country", "firstName", "lastName", "addressLine1", "city", "state", "postalCode", "phoneType", "phoneCode", "phone"],
    2: ["school", "degree", "fieldOfStudy", "fromMonth", "fromYear", "toMonth", "toYear", "resume"],
    3: ["q1", "q2", "q3"],
    4: ["gender", "ethnicity", "veteran", "termsCheckbox"],
    5: ["sigName", "sigDate", "disability"],
}


class WorkdayTenant:
    def __init__(self, fx: Fixture, verify_email=True, hang_confirm=False):
        self.hang_confirm = hang_confirm
        self.fx = fx
        self.accounts = {}
        self.sessions = {}
        self.data = {}
        self.applications = []
        self.registrations = 0
        self.verify_email = verify_email
        fx.add(HOST, self.h)

    def url(self):
        return f"https://{HOST}{JOB_PATH}"

    def user(self, req):
        return self.sessions.get(req["cookies"].get("wd_sid", ""))

    def shell(self, title, inner, script=""):
        return page(title, f"""<div data-automation-id="pageHeader"><h1 data-automation-id="pageHeaderTitle">Acme Semiconductor Careers</h1></div>{inner}""",
                    WD_JS + "wdDropdowns(); wdButtons();" + script)

    def signin(self, nxt, msg=""):
        return self.shell("Sign In", f"""<div data-automation-id="signInContent"><h2>Sign In</h2>{f'<p>{E(msg)}</p>' if msg else ''}
          {inp('email', 'Email Address', kind='email')}{inp('password', 'Password', kind='password')}
          <div data-automation-id="errorMessage" id="err" role="alert"></div>
          <div role="button" tabindex="0" data-automation-id="signInSubmitButton" id="go">Sign In</div>
          <div role="button" tabindex="0" data-automation-id="createAccountLink" data-href="/{SITE}/createAccount?next={E(nxt)}">Create Account</div></div>""",
            f"""document.getElementById('go').onclick = async () => {{ const v = wdValues();
              const r = await fetch('/api/signin', {{method:'POST', body: JSON.stringify(v)}}); const j = await r.json();
              if (j.error) {{ document.getElementById('err').textContent = j.error; return; }}
              document.cookie = 'wd_sid=' + j.sid + '; path=/'; location.href = '{nxt}'; }};""")

    def h(self, req):
        p, u = req["path"], self.user(req)
        if p == JOB_PATH:
            return self.shell(TITLE, f"""<h2>{TITLE}</h2><p>Spring 2027 co-op.</p>
              <a data-automation-id="adventureButton" role="button" href="{JOB_PATH}/apply">Apply</a>""")
        if p == JOB_PATH + "/apply":
            return self.shell("Start Your Application", f"""<div role="dialog"><h2>Start Your Application</h2>
              <a role="button" data-automation-id="autofillWithResume" href="{JOB_PATH}/apply/autofillWithResume">Autofill with Resume</a>
              <a role="button" data-automation-id="applyManually" href="{JOB_PATH}/apply/applyManually">Apply Manually</a></div>""")
        if p == JOB_PATH + "/apply/applyManually":
            if not u:
                return self.signin(JOB_PATH + "/apply/applyManually")
            return self.step(req, u)
        if p == f"/{SITE}/createAccount":
            nxt = req["query"].get("next", JOB_PATH)
            return self.shell("Create Account", f"""<h2>Create Account</h2>
              {inp('email', 'Email Address', kind='email')}{inp('password', 'Password', kind='password')}{inp('verifyPassword', 'Verify New Password', kind='password')}
              <div class="wdfield"><label><input type="checkbox" id="createAccountCheckbox" data-automation-id="createAccountCheckbox" required> Yes, I have read and consent to the terms and conditions</label></div>
              <div data-automation-id="errorMessage" id="err" role="alert"></div>
              <div role="button" tabindex="0" data-automation-id="createAccountSubmitButton" id="go">Create Account</div>""",
                f"""document.getElementById('go').onclick = async () => {{ const v = wdValues(); v.next = '{nxt}';
                  const r = await fetch('/api/createAccount', {{method:'POST', body: JSON.stringify(v)}}); const j = await r.json();
                  if (j.error) {{ document.getElementById('err').textContent = j.error; return; }}
                  if (j.sid) {{ document.cookie = 'wd_sid=' + j.sid + '; path=/'; location.href = '{nxt}'; return; }}
                  document.body.innerHTML = '<h2>Verify your account</h2><p>An email has been sent to verify your account. Please check your email.</p>'; }};""")
        if p == "/api/createAccount":
            f = req["form"]
            if f["email"] in self.accounts:
                return json_resp({"error": "An account already exists for this email address."})
            if f["password"] != f["verifyPassword"]:
                return json_resp({"error": "Passwords do not match"})
            if not f.get("createAccountCheckbox"):
                return json_resp({"error": "You must accept the terms"})
            self.registrations += 1
            tok = secrets.token_hex(8)
            self.accounts[f["email"]] = {"password": f["password"], "verified": not self.verify_email, "token": tok}
            if not self.verify_email:
                sid = secrets.token_hex(8)
                self.sessions[sid] = f["email"]
                return json_resp({"sid": sid})
            self.fx.email(f["email"], SENDER, "Verify your candidate account", "Click the link below to verify your email and activate your account.",
                          [f"https://{HOST}/{SITE}/activate/{tok}?next={f['next']}"])
            return json_resp({"ok": True})
        if p.startswith(f"/{SITE}/activate/"):
            tok = p.rsplit("/", 1)[1]
            for a in self.accounts.values():
                if a["token"] == tok:
                    a["verified"] = True
                    return self.signin(req["query"].get("next", JOB_PATH), "Your account has been verified. Sign in to continue.")
            return self.shell("Invalid", "<h2>This link is invalid or expired</h2>")
        if p == "/api/signin":
            f = req["form"]
            a = self.accounts.get(f.get("email"))
            if not a or a["password"] != f.get("password"):
                return json_resp({"error": "Wrong email address or password."})
            if not a["verified"]:
                return json_resp({"error": "Please verify your email before signing in."})
            sid = secrets.token_hex(8)
            self.sessions[sid] = f["email"]
            return json_resp({"sid": sid})
        if p == "/api/step":
            if not u:
                return json_resp({"error": "session expired"}, 401)
            f = req["form"]
            n = int(f.pop("_step"))
            missing = [k for k in REQUIRED.get(n, []) if f.get(k) in (None, "", False)]
            if missing:
                return json_resp({"errors": [f"{k}: The field is required and must have a value." for k in missing]})
            self.data.setdefault(u, {}).update(f)
            if n == 6:
                self.applications.append((u, REQ, TITLE))
                self.fx.submissions.append(("workday", REQ, dict(self.data[u])))
            return json_resp({"ok": True})
        if p == f"/{SITE}/userHome":
            if not u:
                return self.signin(f"/{SITE}/userHome")
            rows = "".join(f"<li>{E(t)} ({E(r)}) - Submitted</li>" for (e, r, t) in self.applications if e == u)
            return self.shell("Candidate Home", f"<h2>Candidate Home</h2><h3>My Applications</h3><ul>{rows or '<li>No applications</li>'}</ul>")
        return 404, "text/plain", "nf"

    def step(self, req, email):
        n = int(req["query"].get("step", "1"))
        progress = " / ".join(f"[{s}]" if i + 1 == n else s for i, s in enumerate(STEPS))
        if n == 1:
            body = f"""<h2>My Information</h2>
              {dd('hear', 'How Did You Hear About Us?', ['Company Website', 'Job Board', 'Referral'])}
              {radio('prevWorked', 'Have you previously worked for Acme Semiconductor?', ['Yes', 'No'])}
              {dd('country', 'Country', ['Canada', 'United States of America'])}
              <h3>Legal Name</h3>{inp('firstName', 'First Name')}{inp('lastName', 'Last Name')}
              <h3>Address</h3>{inp('addressLine1', 'Address Line 1')}{inp('city', 'City')}{dd('state', 'State', ['Maine', 'Massachusetts', 'New York'])}{inp('postalCode', 'Postal Code')}
              {inp('emailRO', 'Email Address', required=False, value=email, readonly=True)}
              {dd('phoneType', 'Phone Device Type', ['Home', 'Mobile', 'Work'])}{dd('phoneCode', 'Country Phone Code', ['Canada (+1)', 'United States of America (+1)'])}
              {inp('phone', 'Phone Number')}"""
        elif n == 2:
            body = f"""<h2>My Experience</h2><section><h3>Education</h3>
              {prompt('school', 'School or University', ['Test University', 'Test University of Technology', 'Other State University'])}
              {dd('degree', 'Degree', ['High School Diploma', "Bachelor's Degree", "Master's Degree"])}
              {prompt('fieldOfStudy', 'Field of Study', ['Electrical Engineering', 'Electrical and Computer Engineering', 'Mechanical Engineering'])}
              {date_group('from', 'From')}{date_group('to', 'To (Actual or Expected)')}
              {inp('gpa', 'Overall Result (GPA)', required=False)}</section>
              <section><h3>Resume/CV</h3><div class="wdfield"><label for="resume">Upload Resume*</label><input type="file" id="resume" data-automation-id="file-upload-input-ref" required></div></section>
              {inp('website', 'Websites', required=False)}"""
        elif n == 3:
            body = f"""<h2>Application Questions</h2>
              {dd('q1', 'Are you legally authorized to work in the United States?', ['Yes', 'No'])}
              {dd('q2', 'Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?', ['Yes', 'No'])}
              {dd('q3', 'Are you available to work January through June 2027?', ['Yes', 'No'])}"""
        elif n == 4:
            body = f"""<h2>Voluntary Disclosures</h2>
              {dd('gender', 'Gender', ['Male', 'Female', 'I do not wish to answer'])}
              {dd('ethnicity', 'Race/Ethnicity', ['Asian', 'White', 'I do not wish to answer'])}
              {dd('veteran', 'Please select the veteran status which most accurately describes you', ['I am not a protected veteran', 'I identify as one or more of the classifications of protected veteran', 'I do not wish to self-identify'])}
              <div class="wdfield"><label><input type="checkbox" id="termsCheckbox" required> I have read and consent to the terms and conditions</label></div>"""
        elif n == 5:
            body = f"""<section><h2>Self Identify</h2><p>Voluntary self-identification of disability.</p>
              {inp('sigName', 'Name')}{inp('sigDate', 'Date')}
              {radio('disability', 'Disability Status', ['Yes, I have a disability (or previously had a disability)', 'No, I do not have a disability and have not had one in the past', 'I do not want to answer'])}</section>"""
        else:
            d = self.data.get(email, {})
            body = "<h2>Review</h2><ul>" + "".join(f"<li>{E(k)}: {E(str(v))}</li>" for k, v in d.items()) + "</ul>"
        btn = "Submit" if n == 6 else "Save and Continue"
        done = ("document.body.innerHTML = '<h2>Application Submitted</h2><p>Thank you for applying to " + TITLE + ". You can track it on your candidate home.</p>';" if n == 6
                else f"location.href = '?step={n + 1}';")
        if n == 6 and self.hang_confirm:
            done = "/* the portal accepted it but the page never updates */"
        return self.shell(f"Apply - {STEPS[n - 1]}", f"""<div data-automation-id="progressBar">{E(progress)}</div><main>{body}</main>
          <div data-automation-id="errorMessage" id="err" role="alert"></div>
          <div role="button" tabindex="0" data-automation-id="bottom-navigation-next-button" id="next">{btn}</div>""",
            f"""document.querySelectorAll('input[type=file]').forEach(f => f.addEventListener('change', () => {{ f.dataset.done = f.files[0].name; }}));
            document.getElementById('next').onclick = async () => {{ const v = wdValues(); v._step = {n};
              const r = await fetch('/api/step', {{method:'POST', body: JSON.stringify(v)}}); const j = await r.json();
              if (j.errors) {{ document.getElementById('err').innerHTML = '<h3>Errors Found</h3>' + j.errors.map(e => '<p>' + e + '</p>').join(''); return; }}
              {done} }};""")
