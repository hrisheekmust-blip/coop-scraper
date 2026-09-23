"""Synthetic employer portals for automated tests. Served through Playwright request routing on the real-looking
https hostnames, so realm/host checks run exactly as in production, with no network and no real employer.

Each portal keeps server-side state (accounts, sessions, submissions) and an outbox of emails it "sent", which
tests feed through the Outlook-bridge ingestion path.
"""
from __future__ import annotations

import html
import json
import re
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

E = html.escape


class Fixture:
    def __init__(self):
        self.handlers = {}
        self.outbox = []            # emails: {message_id, to, from, subject, body_text, links, received_at}
        self.submissions = []       # (portal, requisition, fields)
        self.requests = []
        self.lock = threading.Lock()
        self.fail_next = {}         # path -> count of 500s to return (network/site error tests)

    def add(self, host, handler):
        self.handlers[host] = handler

    def install(self, context):
        context.route("**/*", self._route)

    def _route(self, route, request):
        u = urlsplit(request.url)
        host = (u.hostname or "").lower()
        with self.lock:
            self.requests.append((request.method, request.url))
        h = self.handlers.get(host)
        if not h:
            return route.fulfill(status=404, body="no such host in fixtures", headers={"content-type": "text/plain"})
        try:
            headers = request.all_headers()
        except Exception:
            headers = request.headers
        cookies = dict(re.findall(r"([\w-]+)=([^;]*)", headers.get("cookie", "")))
        body = request.post_data or ""
        form = {}
        if body:
            try:
                form = json.loads(body)
            except ValueError:
                form = {k: v[0] for k, v in parse_qs(body).items()}
        req = {"method": request.method, "path": u.path or "/", "query": {k: v[0] for k, v in parse_qs(u.query).items()},
               "form": form, "cookies": cookies, "url": request.url, "host": host}
        key = req["path"]
        if self.fail_next.get(key):
            self.fail_next[key] -= 1
            return route.fulfill(status=500, body="server error", headers={"content-type": "text/plain"})
        status, ctype, out = h(req)
        return route.fulfill(status=status, body=out, headers={"content-type": ctype})

    def email(self, to, sender, subject, body, links=(), when=None):
        from datetime import datetime, timezone
        m = {"message_id": "fx-" + secrets.token_hex(6), "to": to, "from": sender, "subject": subject, "body_text": body,
             "links": list(links), "received_at": (when or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")}
        with self.lock:
            self.outbox.append(m)
        return m


def page(title, body, script=""):
    return 200, "text/html", f"""<!doctype html><html><head><meta charset="utf-8"><title>{E(title)}</title>
<style>body{{font:14px sans-serif;margin:24px}} .field{{margin:10px 0}} .error{{color:#b00}} .hidden{{display:none}}
[role=listbox]{{border:1px solid #999;background:#fff}} [role=option]{{padding:4px;cursor:pointer}} .spinner{{width:10px;height:10px;background:#999}}</style>
</head><body>{body}<script>{script}</script></body></html>"""


def json_resp(obj, status=200):
    return status, "application/json", json.dumps(obj)


# ------------------------------------------------------------------------------------------ shared widgets
COMBO_JS = r"""
// A React-Select-like searchable dropdown: input[role=combobox] inside .select__control, options appear on click.
function combo(root){
  const input = root.querySelector('input[role=combobox]'), list = document.getElementById(input.getAttribute('aria-controls'));
  const value = root.querySelector('.select__single-value'), hidden = root.querySelector('input[type=hidden]');
  const opts = JSON.parse(root.dataset.options);
  const render = q => { list.innerHTML=''; opts.filter(o => !q || o.toLowerCase().includes(q.toLowerCase())).forEach(o => {
      const d=document.createElement('div'); d.setAttribute('role','option'); d.className='select__option'; d.textContent=o;
      d.onclick=()=>{ value.textContent=o; hidden.value=o; input.value=''; list.classList.add('hidden'); root.dispatchEvent(new Event('change',{bubbles:true})); };
      list.appendChild(d); }); list.classList.toggle('hidden', false); };
  root.querySelector('.select__control').addEventListener('click', () => render(input.value));
  input.addEventListener('input', () => render(input.value));
  input.addEventListener('keydown', e => { if (e.key === 'Escape') list.classList.add('hidden'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') list.classList.add('hidden'); });
}
document.querySelectorAll('.combo').forEach(combo);
"""


def combo(name, label, options, required=True):
    lid = f"lb-{name}"
    return f"""<div class="field combo" data-options='{E(json.dumps(options))}'><label for="in-{name}">{E(label)}{' *' if required else ''}</label>
<div class="select__control"><div class="select__single-value"></div><input id="in-{name}" role="combobox" aria-controls="{lid}" aria-required="{str(required).lower()}" autocomplete="off"></div>
<div id="{lid}" role="listbox" class="hidden"></div><input type="hidden" name="{name}"></div>"""


def text(name, label, required=True, kind="text", placeholder=""):
    return f"""<div class="field"><label for="{name}">{E(label)}{' *' if required else ''}</label><input id="{name}" name="{name}" type="{kind}" {'required' if required else ''} placeholder="{E(placeholder)}"></div>"""


def select(name, label, options, required=True):
    opts = "".join(f"<option>{E(o)}</option>" for o in options)
    return f"""<div class="field"><label for="{name}">{E(label)}{' *' if required else ''}</label><select id="{name}" name="{name}" {'required' if required else ''}><option value="">Select...</option>{opts}</select></div>"""


def radios(name, label, options, required=True):
    items = "".join(f"""<label><input type="radio" name="{name}" value="{E(o)}" {'required' if required else ''}> {E(o)}</label>""" for o in options)
    return f"""<fieldset class="field"><legend>{E(label)}{' *' if required else ''}</legend>{items}</fieldset>"""


def checkbox(name, label, required=False):
    return f"""<div class="field"><label><input type="checkbox" name="{name}" {'required' if required else ''}> {E(label)}</label></div>"""


def textarea(name, label, required=True):
    return f"""<div class="field"><label for="{name}">{E(label)}{' *' if required else ''}</label><textarea id="{name}" name="{name}" {'required' if required else ''}></textarea></div>"""


def file_input(name, label, required=True):
    return f"""<div class="field"><label for="{name}">{E(label)}{' *' if required else ''}</label><input type="file" id="{name}" name="{name}" {'required' if required else ''}></div>"""


COLLECT_JS = r"""
function collect(form){
  const out={};
  for (const el of form.querySelectorAll('input, select, textarea')) {
    if (!el.name) continue;
    if (el.type==='radio') { if (el.checked) out[el.name]=el.value; continue; }
    if (el.type==='checkbox') { out[el.name]=el.checked; continue; }
    if (el.type==='file') { out[el.name]= el.files && el.files.length ? el.files[0].name : ''; continue; }
    out[el.name]=el.value;
  }
  return out;
}
function missing(form){
  const bad=[];
  for (const el of form.querySelectorAll('[required], [aria-required=true]')) {
    const box = el.closest('.field, fieldset'); if (box && box.offsetParent === null) continue;
    if (el.type==='radio') { if (!form.querySelector(`input[name="${el.name}"]:checked`)) bad.push(el.name); continue; }
    if (el.type==='checkbox') { if (!el.checked) bad.push(el.name); continue; }
    if (el.type==='file') { if (!el.files || !el.files.length) bad.push(el.name); continue; }
    if (el.getAttribute('role')==='combobox') { const h = el.closest('.combo').querySelector('input[type=hidden]'); if (!h.value) bad.push(h.name); continue; }
    if (!el.value) bad.push(el.name);
  }
  return [...new Set(bad)];
}
"""
