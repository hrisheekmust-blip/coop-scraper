// NUWorks (Symplicity) crawl. Paste into the DevTools console of a LOGGED-IN NUWorks tab, or let the scheduled
// Claude task run it. Walks the job list newest-first, opens every hardware-looking row to read Applicant Type /
// Job Length / Deadline / Not-Qualified reason, then downloads coop_nuworks.json. Convert with:
//   python -m scraper.nuworks ~/Downloads/coop_nuworks.json
(async () => {
  const HW = /hardware|electrical|electronic|asic|fpga|analog|\brf\b|rfic|silicon|chip|circuit|embedded|firmware|dft|verification|test|validation|photonic|mems|mixed.signal|semicond|\bsoc\b|design|systems|sensor|optic|metrology|instrument|characterization|power|controls|engineer/i;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const txt = el => (el && (el.innerText || el.textContent) || "").replace(/\s+/g, " ").trim();
  const BASE = "https://northeastern-csm.symplicity.com/students/app/jobs/search?perPage=50&sort=!postdate&page=";
  const rows = [];
  const seen = new Set();
  // ---- list pages
  for (let page = 1; page <= 40; page++) {
    if (!location.href.startsWith(BASE + page)) { location.href = BASE + page; await sleep(3500); }
    let items = [];
    for (let i = 0; i < 20 && !items.length; i++) {  // wait for the SPA to render
      items = [...document.querySelectorAll('[class*="list-item"], [role="listitem"], .job-list-item, li')]
        .filter(el => el.querySelector('a, h3, h4, [class*="title"]') && /[A-Za-z]/.test(txt(el)) && txt(el).length > 20 && txt(el).length < 1200);
      if (!items.length) await sleep(500);
    }
    // the outermost matching element per row (avoid nested li/div duplicates)
    items = items.filter(el => !items.some(o => o !== el && o.contains(el)));
    if (!items.length) break;
    let fresh = 0;
    for (const el of items) {
      const title = txt(el.querySelector('h3, h4, [class*="title"], a'));
      const full = txt(el);
      const sub = txt(el.querySelector('[class*="sub"], [class*="employer"], [class*="company"], p, span'));
      const key = title + "|" + sub;
      if (!title || seen.has(key)) continue;
      seen.add(key); fresh++;
      rows.push({ el, title, sub, badge: /not qualified/i.test(full) ? "Not Qualified" : "", extra: full.replace(title, "").slice(0, 300) });
    }
    if (!fresh) break;
    // open the hardware-looking rows on this page
    for (const r of rows.filter(r => r.el && !r.id && HW.test(r.title + " " + r.sub))) {
      try {
        (r.el.querySelector('a, h3, h4, [class*="title"]') || r.el).click();
        let id = "";
        for (let i = 0; i < 20 && !id; i++) { await sleep(400); id = (location.href.match(/currentJobId=(\d+)|\/detail\/(\d+)/) || []).slice(1).find(Boolean) || ""; }
        await sleep(800);
        const body = document.body.innerText;
        const grab = label => { const m = body.match(new RegExp(label + "\\s*:?\\s*\\n?\\s*([^\\n]{1,160})", "i")); return m ? m[1].trim() : ""; };
        r.id = id;
        r.applicantType = grab("Applicant Type");
        r.length = grab("Job Length") || grab("Position Type") || grab("Duration");
        r.deadline = grab("Application Deadline") || grab("Deadline") || grab("Apply By");
        r.majors = grab("Desired Majors") || grab("Majors");
        r.posType = grab("Position Type") || grab("Job Type");
        r.why = (body.match(/not qualified[^\n]*\n([^\n]{1,300})/i) || [])[1] || "";
        history.back(); await sleep(1500);
      } catch (e) { r.err = String(e).slice(0, 80); }
    }
    rows.forEach(r => delete r.el);
  }
  const out = rows.map(({ el, ...r }) => r);
  const blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "coop_nuworks.json"; document.body.appendChild(a); a.click();
  console.log("NUWorks crawl:", out.length, "rows,", out.filter(r => r.id).length, "opened");
  window.__nu = out;
})();
