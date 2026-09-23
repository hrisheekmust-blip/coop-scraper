"""Greenhouse-, Ashby- and Lever-like fixture portals (no accounts)."""
from __future__ import annotations

import json
import re

from . import (COLLECT_JS, COMBO_JS, E, Fixture, checkbox, combo, file_input, json_resp, page, radios, select, text, textarea)

GH_JOB = "5374627008"
ASHBY_JOB = "11111111-2222-3333-4444-555555555555"
LEVER_JOB = "99999999-8888-7777-6666-555555555555"


def greenhouse(fx: Fixture, company="Acme Photonics", board="acme", job=GH_JOB, extra_required=None, closed=False):
    base = f"/{board}/jobs/{job}"

    def h(req):
        if closed and req["path"].startswith(base):
            return page("Job not found", "<h1>Sorry, this job is no longer available</h1><p>The job you are looking for is no longer accepting applications.</p>")
        if req["path"] == base and req["method"] == "GET":
            extra = extra_required or ""
            form = f"""<form id="application_form">
              <h2>Apply for this Job</h2>
              {text('first_name', 'First Name')}{text('last_name', 'Last Name')}{text('email', 'Email', kind='email')}{text('phone', 'Phone', kind='tel')}
              <div class="field file-upload" role="group" aria-labelledby="resume-label"><div id="resume-label">Resume/CV *</div>
                <input type="file" id="resume" name="resume" required><label for="resume">Attach</label></div>
              {text('linkedin', 'LinkedIn Profile', required=False)}
              {combo('authorized', 'Are you legally authorized to work in the United States?', ['Yes', 'No'])}
              <div class="field hidden" id="over18box">{select('over18', 'Are you at least 18 years of age?', ['Yes', 'No'])}</div>
              {select('sponsor', 'Will you now or in the future require sponsorship?', ['Yes', 'No'])}
              {textarea('why', f'Why are you interested in {company}?')}
              {select('gender', 'Gender', ['Male', 'Female', 'Decline To Self Identify'], required=False)}
              {text('hear', 'How did you hear about this job?', required=False)}
              {extra}
              {checkbox('privacy', 'I have read and agree to the Privacy Policy', required=True)}
              <div class="errors" id="errs"></div>
              <button type="submit" id="submit_app">Submit Application</button></form>"""
            js = COMBO_JS + COLLECT_JS + f"""
              document.querySelector('.combo').addEventListener('change', () => {{
                document.getElementById('over18box').classList.toggle('hidden', document.querySelector('input[name=authorized]').value !== 'Yes'); }});
              document.getElementById('application_form').addEventListener('submit', async e => {{
                e.preventDefault(); const f=e.target; const bad=missing(f);
                if (bad.length) {{ document.getElementById('errs').innerHTML = bad.map(b=>'<div class="error" role="alert">'+b+' is required</div>').join(''); return; }}
                const r = await fetch('{base}/submit', {{method:'POST', body: JSON.stringify(collect(f))}});
                const j = await r.json(); if (j.ok) location.href = '{base}/confirmation';
              }});"""
            return page(f"Job Application for Intern at {company}", f"<h1>Hardware Intern</h1><p>{E(company)} is hiring.</p>{form}", js)
        if req["path"] == base + "/submit":
            fx.submissions.append(("greenhouse", job, req["form"]))
            return json_resp({"ok": True})
        if req["path"] == base + "/confirmation":
            return page("Thank you", f"<h1>Thank you for applying.</h1><p>Your application has been received by {E(company)}.</p>")
        return 404, "text/plain", "not found"
    fx.add("boards.greenhouse.io", h)
    fx.add("job-boards.greenhouse.io", h)
    return f"https://boards.greenhouse.io{base}"


def ashby(fx: Fixture, company="Acme Robotics", org="acme", job=ASHBY_JOB):
    base = f"/{org}/{job}"

    def h(req):
        if req["path"] == base:
            return page(f"{company} - Test Engineer Co-op", f"""<h1>Test Engineer Co-op</h1><nav><a href="{base}">Overview</a> <a href="{base}/application">Application</a></nav><p>About the role.</p>""")
        if req["path"] == base + "/application" and req["method"] == "GET":
            body = f"""<div id="root"><form id="f">
              <div class="ashby-application-form-field-entry">{text('_systemfield_name', 'Name')}</div>
              <div class="ashby-application-form-field-entry">{text('_systemfield_email', 'Email', kind='email')}</div>
              <div class="ashby-application-form-field-entry"><label for="resume">Resume *</label><input type="file" id="resume" name="resume" required><div id="upl"></div></div>
              <div class="ashby-application-form-field-entry" id="yn"><label>Are you currently a student? *</label><div><button type="button" data-v="Yes">Yes</button><button type="button" data-v="No">No</button></div><input type="hidden" name="student" id="student"></div>
              <div class="ashby-application-form-field-entry">{select('grad', 'Graduation Month', ['November', 'December'], required=True)}</div>
              <button type="submit">Submit Application</button></form></div>"""
            js = COLLECT_JS + """
              document.querySelectorAll('#yn button').forEach(b => b.onclick = () => { document.querySelectorAll('#yn button').forEach(x => x.setAttribute('aria-pressed','false')); b.setAttribute('aria-pressed','true'); document.getElementById('student').value=b.dataset.v; });
              document.getElementById('resume').addEventListener('change', e => { const u=document.getElementById('upl'); u.innerHTML='<div class="spinner" aria-busy="true"></div>';
                 setTimeout(()=>{ u.textContent = 'Uploaded ' + e.target.files[0].name; }, 900); });
              document.getElementById('f').addEventListener('submit', async e => { e.preventDefault(); const f=e.target;
                 if (!document.getElementById('student').value || missing(f).length) { return; }
                 const r = await fetch('""" + base + """/submit', {method:'POST', body: JSON.stringify(collect(f))});
                 if ((await r.json()).ok) document.getElementById('root').innerHTML = '<h2>Thank you for applying!</h2><p>Your application was successfully submitted.</p>'; });"""
            return page(f"{company} - Application", body, js)
        if req["path"] == base + "/submit":
            fx.submissions.append(("ashby", job, req["form"]))
            return json_resp({"ok": True})
        return 404, "text/plain", "nf"
    fx.add("jobs.ashbyhq.com", h)
    return f"https://jobs.ashbyhq.com{base}"


def lever(fx: Fixture, company="Acme Devices", org="acme", job=LEVER_JOB, hang_after_submit=False, reject_submit=False):
    base = f"/{org}/{job}"
    fx.lever_opts = {"reject": reject_submit}

    def h(req):
        if req["path"] == base:
            return page(f"{company} - Hardware Co-op", f"""<h1>Hardware Co-op</h1><a class="postings-btn" href="{base}/apply">Apply for this job</a>""")
        if req["path"] == base + "/apply" and req["method"] == "GET":
            body = f"""<form id="f"><h3>Submit your application</h3>
              {file_input('resume', 'Resume/CV')}{text('name', 'Full name')}{text('email', 'Email', kind='email')}{text('phone', 'Phone', kind='tel')}
              <div class="application-question">{radios('cards[0]', 'Are you available to work January - June 2027?', ['Yes', 'No'])}</div>
              {text('urls[LinkedIn]', 'LinkedIn URL', required=False)}
              <div class="errors" id="errs"></div>
              <button type="submit" class="template-btn-submit">Submit application</button></form>"""
            js = COLLECT_JS + f"""document.getElementById('f').addEventListener('submit', async e => {{ e.preventDefault(); const f=e.target;
                 if (missing(f).length) return;
                 const r = await fetch('{base}/apply/submit', {{method:'POST', body: JSON.stringify(collect(f))}});
                 const j = await r.json();
                 if (j.error) {{ document.getElementById('errs').innerHTML = '<div class="error" role="alert">'+j.error+'</div>'; return; }}
                 if (j.ok) location.href='{base}/thanks'; }});"""
            return page(f"{company} - Apply", body, js)
        if req["path"] == base + "/apply/submit":
            if fx.lever_opts["reject"]:
                return json_resp({"error": "Please enter a valid phone number"})
            fx.submissions.append(("lever", job, req["form"]))
            if hang_after_submit:
                return json_resp({"ok": False})       # accepted server-side, but the page never confirms
            return json_resp({"ok": True})
        if req["path"] == base + "/thanks":
            return page("Application submitted", "<h2>Application submitted!</h2><p>Thanks for applying. We'll be in touch.</p>")
        return 404, "text/plain", "nf"
    fx.add("jobs.lever.co", h)
    return f"https://jobs.lever.co{base}"
