"""SuccessFactors-, Oracle- and iCIMS-like fixture portals."""
from __future__ import annotations

import secrets

from . import (COLLECT_JS, E, Fixture, checkbox, file_input, json_resp, page, radios, select, text)


# ------------------------------------------------------------------------------------------- SuccessFactors
class SFPortal:
    RMK = "careers.acmesemi.com"
    HOST = "career5.successfactors.com"
    CO = "acmesemiP"
    REQ = "12345"

    def __init__(self, fx: Fixture, dpcs=True):
        self.fx, self.dpcs = fx, dpcs
        self.accounts, self.sessions, self.consented = {}, {}, set()
        self.registrations = 0
        fx.add(self.RMK, self.rmk)
        fx.add(self.HOST, self.sf)

    def url(self):
        return f"https://{self.RMK}/job/Process-Engineering-Co-Op/78495-en_US/"

    def sf_url(self, ns="job_application"):
        return f"https://{self.HOST}/career?company={self.CO}&career_job_req_id={self.REQ}&career_ns={ns}"

    def rmk(self, req):
        if req["path"].startswith("/job/"):
            return page("Process Engineering Co-Op", f"""<h1>Process Engineering Co-Op (Jan-June '27)</h1><p>Req 12345</p>
              <a class="btn" href="{E(self.sf_url())}">Apply now</a>""")
        return 404, "text/plain", "nf"

    def sf(self, req):
        u = self.sessions.get(req["cookies"].get("sfsid", ""))
        ns = req["query"].get("career_ns", "")
        if req["path"] == "/career":
            if ns == "create_account":
                return page("Create an Account", f"""<h1>Create an Account</h1><form id="f">
                  {text('email', 'Email', kind='email')}{text('email2', 'Retype Email', kind='email')}
                  {text('pw', 'Choose Password', kind='password')}{text('pw2', 'Retype Password', kind='password')}
                  {text('first', 'First Name')}{text('last', 'Last Name')}{select('country', 'Country/Region of Residence', ['Canada', 'United States'])}
                  {checkbox('dps', 'I have read and accept the Data Privacy Statement', required=True)}
                  <div class="errors" id="errs"></div><button type="submit">Create Account</button></form>""", COLLECT_JS + f"""
                  document.getElementById('f').onsubmit = async e => {{ e.preventDefault(); if (missing(e.target).length) return;
                    const r = await fetch('/api/create', {{method:'POST', body: JSON.stringify(collect(e.target))}}); const j = await r.json();
                    if (j.error) {{ document.getElementById('errs').innerHTML = '<div class="error" role="alert">'+j.error+'</div>'; return; }}
                    document.cookie = 'sfsid=' + j.sid + '; path=/'; location.href = '{self.sf_url()}'; }};""")
            if not u:
                return page("Sign In", f"""<h1>Sign In</h1><form id="f">{text('user', 'Email')}{text('pw', 'Password', kind='password')}
                  <div class="errors" id="errs"></div><button type="submit">Sign In</button></form>
                  <a href="{E(self.sf_url('create_account'))}">Create an account</a>""", COLLECT_JS + f"""
                  document.getElementById('f').onsubmit = async e => {{ e.preventDefault();
                    const r = await fetch('/api/login', {{method:'POST', body: JSON.stringify(collect(e.target))}}); const j = await r.json();
                    if (j.error) {{ document.getElementById('errs').innerHTML = '<div class="error" role="alert">'+j.error+'</div>'; return; }}
                    document.cookie = 'sfsid=' + j.sid + '; path=/'; location.reload(); }};""")
            if self.dpcs and u not in self.consented:
                return page("Data Privacy Consent Statement", """<h1>Data Privacy Consent Statement</h1><p>We process your personal data to evaluate your application.
                  Please review the data privacy statement.</p><button id="a">Accept</button><button id="d">Decline</button>""",
                            "document.getElementById('a').onclick = async () => { await fetch('/api/consent', {method:'POST', body:'{}'}); location.reload(); };")
            if ns == "job_applications":
                mine = [s for s in self.fx.submissions if s[0] == "sf" and s[2].get("_user") == u]
                return page("Job Applications", "<h1>My Job Applications</h1><ul>" + "".join(f"<li>Process Engineering Co-Op ({self.REQ}) Applied</li>" for _ in mine) + "</ul>")
            return page("Apply", f"""<h1>Process Engineering Co-Op</h1><form id="f">
              <h2>Profile Information</h2>
              <div class="field"><label for="first">First Name *</label><input id="first" name="first" required value="{E(self.accounts[u]['first'])}"></div>
              <div class="field"><label for="last">Last Name *</label><input id="last" name="last" required value="{E(self.accounts[u]['last'])}"></div>
              {text('cell', 'Cell Phone', kind='tel')}{text('addr', 'Address')}{text('city', 'City')}{select('state', 'State', ['MA', 'NY', 'TX'])}{text('zip', 'Zip Code')}
              <h2>Resume</h2>{file_input('resume', 'Upload Resume')}
              <h2>Questions</h2>{select('auth', 'Are you legally authorized to work in the United States?', ['Yes', 'No'])}
              {select('spons', 'Will you now or in the future require sponsorship?', ['Yes', 'No'])}
              {radios('enrolled', "Are you currently enrolled in a Bachelor's program?", ['Yes', 'No'])}
              <div class="errors" id="errs"></div><button type="submit">Apply</button></form>""", COLLECT_JS + """
              document.getElementById('f').onsubmit = async e => { e.preventDefault(); const bad = missing(e.target);
                if (bad.length) { document.getElementById('errs').innerHTML = bad.map(b => '<div class="error" role="alert">'+b+' is required</div>').join(''); return; }
                await fetch('/api/apply', {method:'POST', body: JSON.stringify(collect(e.target))});
                document.body.innerHTML = '<h1>Thank you for applying</h1><p>Your application has been submitted successfully.</p>'; };""")
        if req["path"] == "/api/create":
            f = req["form"]
            if f["email"] != f["email2"] or f["pw"] != f["pw2"]:
                return json_resp({"error": "Fields do not match"})
            if f["email"] in self.accounts:
                return json_resp({"error": "A user with this email already exists"})
            self.registrations += 1
            self.accounts[f["email"]] = {"pw": f["pw"], "first": f["first"], "last": f["last"], "dps": f["dps"]}
            sid = secrets.token_hex(6)
            self.sessions[sid] = f["email"]
            return json_resp({"sid": sid})
        if req["path"] == "/api/login":
            f = req["form"]
            a = self.accounts.get(f.get("user"))
            if not a or a["pw"] != f.get("pw"):
                return json_resp({"error": "Invalid username or password"})
            sid = secrets.token_hex(6)
            self.sessions[sid] = f["user"]
            return json_resp({"sid": sid})
        if req["path"] == "/api/consent":
            self.consented.add(u)
            return json_resp({"ok": True})
        if req["path"] == "/api/apply":
            self.fx.submissions.append(("sf", self.REQ, {**req["form"], "_user": u}))
            return json_resp({"ok": True})
        return 404, "text/plain", "nf"


# ------------------------------------------------------------------------------------------- Oracle
class OraclePortal:
    HOST = "acme.fa.us2.oraclecloud.com"
    BASE = "/hcmUI/CandidateExperience/en/sites/CX/job/12345"
    SENDER = "Acme Careers <no-reply@us2.oraclecloud.com>"

    def __init__(self, fx: Fixture):
        self.fx = fx
        self.codes, self.sessions = {}, {}
        fx.add(self.HOST, self.h)

    def url(self):
        return f"https://{self.HOST}{self.BASE}"

    def h(self, req):
        p, u = req["path"], self.sessions.get(req["cookies"].get("osid", ""))
        if p == self.BASE:
            return page("Test Engineer Intern", f"""<h1>Test Engineer Intern</h1><a class="btn" href="{self.BASE}/apply/email">Apply Now</a>""")
        if p == self.BASE + "/apply/email":
            if u:
                return self.form(req, u)
            return page("Apply", f"""<h1>Apply</h1><p>Enter your email address to get started.</p><form id="f">{text('email', 'Email Address', kind='email')}
              {checkbox('terms', 'I agree with the terms and conditions', required=True)}<button type="submit">Next</button></form>""", COLLECT_JS + f"""
              document.getElementById('f').onsubmit = async e => {{ e.preventDefault(); if (missing(e.target).length) return;
                await fetch('/api/code', {{method:'POST', body: JSON.stringify(collect(e.target))}});
                document.body.innerHTML = '<h1>Confirm your identity</h1><p>Enter the verification code we sent to your email.</p><form id="c"><div class="field"><label for="code">Verification Code</label><input id="code" name="code"></div><button type="submit">Verify</button></form>';
                document.getElementById('c').onsubmit = async ev => {{ ev.preventDefault();
                  const r = await fetch('/api/verify', {{method:'POST', body: JSON.stringify({{code: document.getElementById('code').value}})}}); const j = await r.json();
                  if (j.sid) {{ document.cookie = 'osid=' + j.sid + '; path=/'; location.reload(); }} }}; }};""")
        if p == "/api/code":
            code = f"{secrets.randbelow(900000) + 100000}"
            self.codes[code] = req["form"]["email"]
            self.fx.email(req["form"]["email"], self.SENDER, "Confirm your identity", f"Your one-time passcode is {code}. It expires in 10 minutes.")
            return json_resp({"ok": True})
        if p == "/api/verify":
            e = self.codes.pop(req["form"].get("code"), None)
            if not e:
                return json_resp({"error": "bad code"})
            sid = secrets.token_hex(6)
            self.sessions[sid] = e
            return json_resp({"sid": sid})
        if p == "/api/apply":
            self.fx.submissions.append(("oracle", "12345", req["form"]))
            return json_resp({"ok": True})
        return 404, "text/plain", "nf"

    def form(self, req, email):
        return page("Apply - Test Engineer Intern", f"""<h1>Application</h1><form id="f">
          {text('first', 'First Name')}{text('last', 'Last Name')}{text('phone', 'Phone Number', kind='tel')}
          {select('auth', 'Are you legally authorized to work in the United States?', ['Yes', 'No'])}
          {file_input('resume', 'Resume')}<button type="submit">Submit</button></form>""", COLLECT_JS + """
          document.getElementById('f').onsubmit = async e => { e.preventDefault(); if (missing(e.target).length) return;
            await fetch('/api/apply', {method:'POST', body: JSON.stringify(collect(e.target))});
            document.body.innerHTML = '<h1>Thank you for your job application.</h1>'; };""")


# ------------------------------------------------------------------------------------------- iCIMS (iframe)
class ICIMSPortal:
    HOST = "careers-acme.icims.com"

    def __init__(self, fx: Fixture):
        self.fx = fx
        self.accounts, self.sessions = {}, {}
        fx.add(self.HOST, self.h)

    def url(self):
        return f"https://{self.HOST}/jobs/4321/test-engineer/job"

    def h(self, req):
        p, q = req["path"], req["query"]
        u = self.sessions.get(req["cookies"].get("isid", ""))
        if p == "/jobs/4321/test-engineer/job" and q.get("in_iframe") != "1":
            return page("Test Engineer | Acme", '<h1 class="brand">Acme Careers</h1><iframe id="icims_content_iframe" src="/jobs/4321/test-engineer/job?in_iframe=1" style="width:100%;height:1400px"></iframe>')
        if p == "/jobs/4321/test-engineer/job":
            return page("Test Engineer", '<h1>Test Engineer (Co-op)</h1><a class="iCIMS_ApplyOnlineButton" href="/jobs/4321/login?in_iframe=1">Apply for this job online</a>')
        if p == "/jobs/4321/login":
            if u:
                return self.form(req, u)
            return page("Login", f"""<h2>Login</h2><form id="f">{text('email', 'Email Address', kind='email')}{text('pw', 'Password', kind='password')}
              <div class="errors" id="errs"></div><button type="submit">Login</button></form><a href="/jobs/4321/register?in_iframe=1">New to our site? Create Account</a>""",
                        COLLECT_JS + """document.getElementById('f').onsubmit = async e => { e.preventDefault();
                          const r = await fetch('/api/login', {method:'POST', body: JSON.stringify(collect(e.target))}); const j = await r.json();
                          if (j.error) { document.getElementById('errs').innerHTML = '<div class="error" role="alert">'+j.error+'</div>'; return; }
                          document.cookie = 'isid=' + j.sid + '; path=/'; location.reload(); };""")
        if p == "/jobs/4321/register":
            return page("Create Account", f"""<h2>Create Account</h2><form id="f">{text('email', 'Email Address', kind='email')}{text('pw', 'Password', kind='password')}
              {text('pw2', 'Confirm Password', kind='password')}{text('first', 'First Name')}{text('last', 'Last Name')}<button type="submit">Create Account</button></form>""",
                        COLLECT_JS + """document.getElementById('f').onsubmit = async e => { e.preventDefault(); if (missing(e.target).length) return;
                          const r = await fetch('/api/register', {method:'POST', body: JSON.stringify(collect(e.target))}); const j = await r.json();
                          document.cookie = 'isid=' + j.sid + '; path=/'; location.href = '/jobs/4321/login?in_iframe=1'; };""")
        if p == "/api/register":
            f = req["form"]
            self.accounts[f["email"]] = f["pw"]
            sid = secrets.token_hex(6)
            self.sessions[sid] = f["email"]
            return json_resp({"sid": sid})
        if p == "/api/login":
            f = req["form"]
            if self.accounts.get(f.get("email")) != f.get("pw"):
                return json_resp({"error": "Invalid login"})
            sid = secrets.token_hex(6)
            self.sessions[sid] = f["email"]
            return json_resp({"sid": sid})
        if p == "/api/apply":
            self.fx.submissions.append(("icims", "4321", req["form"]))
            return json_resp({"ok": True})
        return 404, "text/plain", "nf"

    def form(self, req, email):
        step = req["query"].get("step", "1")
        if step == "1":
            inner = f"""{text('phone', 'Phone')}{text('addr', 'Address Line 1')}{text('city', 'City')}{select('state', 'State/Province', ['Massachusetts', 'New York'])}{text('zip', 'Zip/Postal Code')}
              <button type="submit">Next</button>"""
            after = "location.href = '/jobs/4321/login?in_iframe=1&step=2';"
        else:
            inner = f"""{file_input('resume', 'Resume')}{radios('spons', 'Will you now or in the future require sponsorship?', ['Yes', 'No'])}
              {select('avail', 'Are you available to work January - June 2027?', ['Yes', 'No'])}<button type="submit">Submit Application</button>"""
            after = "document.body.innerHTML = '<h2>Thank you for applying!</h2><p>Your application has been submitted.</p>';"
        return page("Application", f"""<h2>Application for Test Engineer</h2><form id="f">{inner}</form>""", COLLECT_JS + f"""
          document.getElementById('f').onsubmit = async e => {{ e.preventDefault(); if (missing(e.target).length) return;
            await fetch('/api/apply', {{method:'POST', body: JSON.stringify({{step: '{step}', ...collect(e.target)}})}}); {after} }};""")
