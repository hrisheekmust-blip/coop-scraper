"""Outlook content script against an OWA-like page: it opens only verification emails the worker is waiting for,
unwraps safelinks, and sends employer outcome rows without opening personal mail."""
from pathlib import Path

JS = (Path(__file__).resolve().parents[2] / "extension" / "outlook.js").read_text()

OWA = """<!doctype html><html><body>
<div role="listbox">
  <div role="option" data-convid="c1" aria-label="Acme Semiconductor Verify your candidate account 10:32 AM Click the link to verify">Acme</div>
  <div role="option" data-convid="c2" aria-label="Mom Dinner on Sunday? 9:00 AM Are you coming">Mom</div>
  <div role="option" data-convid="c3" aria-label="Acme Devices Thank you for applying to Hardware Co-op 8:15 AM We received">Acme Devices</div>
</div>
<div id="pane"></div>
<script>
document.querySelector('[data-convid=c1]').addEventListener('click', () => {
  document.getElementById('pane').innerHTML = '<div aria-label="Message body">Please verify your email. <a href="https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Facmesemi.wd1.myworkdayjobs.com%2FAcmeCareers%2Factivate%2Ftok123&data=x">Verify</a></div>';
});
document.querySelector('[data-convid=c2]').addEventListener('click', () => { window.__openedPersonal = true; });
</script></body></html>"""

STUB = """window.__sent = [];
window.chrome = {runtime: {sendMessage: (m, cb) => {
  window.__sent.push(m.msg);
  if (m.msg.type === 'mail.wanted') cb({ok: true, verifications: [{portal: 'workday', tenant: 'acmesemi', purpose: 'activation'}], employers: ['Acme Devices']});
  else cb({ok: true});
}}};
const _st = window.setTimeout; window.setTimeout = (f, ms) => _st(f, Math.min(ms, 50));"""


def test_outlook_bridge_reads_only_relevant_mail(pw_browser):
    ctx = pw_browser.new_context()
    page = ctx.new_page()
    page.add_init_script(STUB)
    page.set_content(OWA)
    page.evaluate(STUB)
    page.evaluate(JS)
    page.wait_for_function("window.__sent.filter(m => m.type === 'mail.ingest').length >= 2", timeout=10000)
    sent = page.evaluate("window.__sent")
    ingests = [m["message"] for m in sent if m["type"] == "mail.ingest"]
    verify = next(m for m in ingests if m["message_id"] == "owa:c1")
    assert verify["links"] == ["https://acmesemi.wd1.myworkdayjobs.com/AcmeCareers/activate/tok123"]
    assert any(m["message_id"] == "owa:c3" for m in ingests)
    assert not any(m["message_id"] == "owa:c2" for m in ingests)
    assert not page.evaluate("!!window.__openedPersonal")
    ctx.close()
