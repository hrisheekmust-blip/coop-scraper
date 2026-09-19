// Dry-run the apply userscript against real application forms in headless Chromium.
// Fills every page exactly like the real script, but NEVER presses Submit and never waits on a human.
// Uses a fake applicant, so nothing real is typed anywhere. Writes data/dryrun.json (+ screenshots of failures).
//
//   node tools/dryrun.mjs                 # sample per portal (PER_PORTAL, default 6)
//   node tools/dryrun.mjs <id> [<id>...]  # specific posting ids from data/forms.json
//   PORTALS=greenhouse,ashby node tools/dryrun.mjs
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const forms = JSON.parse(fs.readFileSync(path.join(ROOT, "data/forms.json"), "utf8"));
const engine = fs.readFileSync(path.join(ROOT, "engine.js"), "utf8");
const script = fs.readFileSync(path.join(ROOT, "coop-apply.user.js"), "utf8").replace(/^\/\/ ==UserScript==[\s\S]*?==\/UserScript==\s*/, "");
const PER = +(process.env.PER_PORTAL || 14);
const PORTALS = (process.env.PORTALS || "greenhouse,ashby,lever,smartrecruiters").split(",");   // the four that need no account: the set the board applies to unattended
const TIMEOUT = +(process.env.TIMEOUT || 100000);

// fake applicant: the harness must never type real personal data into a form it doesn't own
const profile = { name: "Test Applicant", preferred: "Test", first: "Test", last: "Applicant", email: "test.applicant@example.com", phone: "617-555-0100", location: "Boston, MA", zip: "02115", address: "1 Main St", state: "Massachusetts", country: "United States", linkedin: "https://www.linkedin.com/in/test-applicant", github: "https://github.com/test-applicant", portfolio: "https://example.com", school: "Northeastern University", degree: "Bachelor of Science", major: "Electrical and Computer Engineering", grad: "December 2027", gpa: "3.96", year: "Third year (junior)", availability: "January through June 2027", citizen: "Yes", clearance: "No", remote: "Any", salary: "Open", referral: "No", eeo: "Decline to self-identify", test_scores: "ACT 36", fulltime_after: "Yes", hear: "Company careers page", languages: "English" };
const answers = { why: "Dry-run answer: why this company.", top_two: [{ title: "Project A", body: "Dry-run body A." }, { title: "Project B", body: "Dry-run body B." }], about: "Dry-run about paragraph.", cover_text: "Dry-run cover letter text." };
const pdf = Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n160\n%%EOF\n").toString("base64");

function sample() {
  const ids = process.argv.slice(2);
  if (ids.length) return ids.filter(i => forms[i]);
  const by = {}; for (const [id, f] of Object.entries(forms)) { if (!f.apply_url || /linkedin\.com|symplicity/.test(f.apply_url)) continue; (by[f.portal || "other"] ||= []).push(id); }
  const out = [];
  for (const p of PORTALS) {
    const l = by[p] || []; const picked = [], hosts = new Set();
    for (const id of l) { const h = new URL(forms[id].apply_url).hostname; if (hosts.has(h)) continue; hosts.add(h); picked.push(id); if (picked.length >= PER) break; }   // one per host first
    for (const id of l) { if (picked.length >= PER) break; if (!picked.includes(id)) picked.push(id); }
    out.push(...picked);
  }
  return out;
}

async function one(browser, id) {
  const f = forms[id];
  const payload = { type: "coop-payload", id, job: { id, company: f.company, role: f.role, link: f.link }, answers, profile, cover: false, coverText: "", files: [{ kind: "resume", name: "Test_Applicant_Resume.pdf", type: "application/pdf", b64: pdf }] };
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36", locale: "en-US" });
  const page = await ctx.newPage();
  const rec = { id, company: f.company, role: f.role, portal: f.portal, url: f.apply_url, status: "timeout", need: [], done: [], banner: "", final_url: "", pages: 0 };
  page.on("dialog", d => d.dismiss().catch(() => {}));
  const errs = [];
  page.on("pageerror", e => errs.push(String(e && e.message || e).slice(0, 180)));
  page.on("console", m => { if (m.type() === "error") errs.push(m.text().slice(0, 180)); });
  await ctx.addInitScript(({ p }) => { window.__coopDry = true; try { sessionStorage.setItem("coop-payload", JSON.stringify(p)); } catch (e) {} }, { p: payload });
  let injected = 0;
  const inject = async (fr = page.mainFrame()) => { try { await fr.evaluate(engine); await fr.evaluate(script); injected++; } catch (e) { rec.inject_error = String(e.message || e).slice(0, 200); } };
  page.on("domcontentloaded", () => { inject(); });
  // embedded forms (Greenhouse/Lever iframes on a company careers page): Tampermonkey runs in frames too
  page.on("framenavigated", fr => { if (fr !== page.mainFrame() && /greenhouse|lever|ashby|workable|bamboohr|jobvite|applytojob|smartrecruiters/.test(fr.url())) setTimeout(() => inject(fr), 1500); });
  try {
    await page.goto(f.apply_url + "#coop=" + id, { waitUntil: "domcontentloaded", timeout: 45000 });
  } catch (e) { rec.status = "nav-error"; rec.error = String(e.message).slice(0, 200); await ctx.close(); return rec; }
  const t0 = Date.now();
  while (Date.now() - t0 < TIMEOUT) {
    await page.waitForTimeout(1500);
    let last = null;
    try {
      last = await page.mainFrame().evaluate(() => window.__coopLast || null).catch(() => null);
      if (!last) for (const fr of page.frames()) { if (fr === page.mainFrame()) continue; const l = await fr.evaluate(() => window.__coopLast || null).catch(() => null); if (l && l.status) { last = l; break; } }
    } catch (e) { continue; }   // navigating
    if (last && last.status) { Object.assign(rec, { status: last.status, need: last.need || [], done: last.done || [], button: last.button || "", error: last.error }); break; }
  }
  // measure the frame that actually holds the form: Greenhouse and Lever are often embedded in a company careers page
  const formFrame = async () => { let best = page.mainFrame(), n = -1;
    for (const fr of page.frames()) { const c = await fr.evaluate(() => document.querySelectorAll("input:not([type=hidden]), select, textarea").length).catch(() => -1); if (c > n) { n = c; best = fr; } }
    return best; };
  let FR = page.mainFrame(); try { FR = await formFrame(); rec.frame_url = FR === page.mainFrame() ? "" : FR.url().slice(0, 120); } catch (e) {}
  try { rec.errors = errs.filter(e => !/Failed to load resource|net::|favicon/i.test(e)).slice(0, 5);
        rec.fields = await FR.evaluate(() => { const v = [...document.querySelectorAll("input:not([type=hidden]):not([type=search]), select, textarea")].filter(e => e.offsetWidth || e.offsetHeight); return { visible: v.length, filled: v.filter(e => (e.value || "").trim() || (e.files && e.files.length)).length }; }); } catch (e) {}
  try { rec.diag = await FR.evaluate(() => {
    const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const t = e => ((e && (e.innerText || e.textContent)) || "").replace(/\s+/g, " ").trim();
    const lab = e => { let l = e.labels && e.labels[0] ? t(e.labels[0]) : "";
      if (!l) { const a = e.getAttribute("aria-labelledby"); if (a) { const n = document.getElementById(a.split(" ")[0]); if (n) l = t(n); } }
      if (!l) l = e.getAttribute("aria-label") || e.placeholder || e.name || e.id || "";
      return l.slice(0, 70); };
    const fields = [...document.querySelectorAll("input:not([type=hidden]):not([type=search]), select, textarea")]
      .filter(e => vis(e) || (e.getAttribute("type") || "").toLowerCase() === "file")
      .map(e => { const ty = (e.getAttribute("type") || e.tagName).toLowerCase();
        const v = ty === "file" ? (e.files && e.files.length ? e.files[0].name : "") : (e.value || "");
        return { q: lab(e), ty, v: v.slice(0, 40), req: !!(e.required || e.getAttribute("aria-required") === "true") }; });
    const btns = [...document.querySelectorAll("button, input[type=submit], [role=button]")].filter(vis)
      .map(b => ((t(b) || b.value || b.getAttribute("aria-label") || "") + (b.disabled ? " [disabled]" : "")).slice(0, 44)).filter(x => x.trim()).slice(0, 25);
    return { fields: fields.slice(0, 70), btns, body: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 300),
             frames: [...document.querySelectorAll("iframe")].map(f => (f.src || "").slice(0, 90)).filter(Boolean).slice(0, 6) };
  }); } catch (e) { rec.diag_error = String(e.message || e).slice(0, 120); }
  try { rec.banner = await page.evaluate(() => (document.getElementById("coop-banner") || {}).textContent || ""); rec.final_url = page.url(); rec.title = await page.title(); } catch (e) {}
  if (rec.status !== "dry-submit") { try { fs.mkdirSync(path.join(ROOT, "data/dryrun"), { recursive: true }); await page.screenshot({ path: path.join(ROOT, "data/dryrun", id + ".jpg"), type: "jpeg", quality: 35, fullPage: false }); } catch (e) {} }
  await ctx.close();
  return rec;
}

const ids = sample();
console.log("dry-running", ids.length, "postings");
const browser = await chromium.launch({ headless: true, args: ["--disable-blink-features=AutomationControlled"] });
const results = [];
for (const id of ids) { const r = await one(browser, id); results.push(r); console.log(`${r.status.padEnd(12)} ${(r.portal || "").padEnd(14)} ${r.company} · ${r.role}${r.need.length ? "  NEED: " + r.need.join(" | ") : ""}${r.error ? "  ERR: " + r.error : ""}`); }
await browser.close();
const summary = {}; for (const r of results) { const k = r.portal || "other"; summary[k] ||= {}; summary[k][r.status] = (summary[k][r.status] || 0) + 1; }
fs.writeFileSync(path.join(ROOT, "data/dryrun.json"), JSON.stringify({ at: new Date().toISOString(), summary, results }, null, 1));
console.log(JSON.stringify(summary, null, 1));
