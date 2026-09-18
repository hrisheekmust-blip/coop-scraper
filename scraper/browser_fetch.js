// Paste into DevTools console on https://example.com (a page with no CSP) to pull Greenhouse/Lever/Ashby
// boards without any server. Mirrors fetchers.py. Result lands in window.__jobs and is downloaded as raw_browser.json.
// Workday / Oracle / SuccessFactors / AMD need a tab on their own origin — see the *_ORIGIN snippets at the bottom.

const INTERN = /\b(intern|internship|co-?op|coop|student|university|undergrad)/i;
const HW = /(?<![a-z])(asic|fpga|rtl|verilog|systemverilog|vlsi|physical design|dft|design verification|verification|dv|silicon|analog|mixed-signal|mixed signal|rf|rfic|serdes|ic design|chip|soc|semiconductor|hardware|electrical engineer|electrical|circuit|circuits|layout|signal integrity|timing|synthesis|sta|post-silicon|pre-silicon|device engineer|photonics|mems|digital design|logic design|microarchitecture|computer architecture|power electronics|wafer|yield|failure analysis|hw|eda|ate|characterization|product engineer|product engineering|test engineer|test engineering|emulation|cad|ee|validation|firmware|embedded|process engineer|reliability|instrumentation)(?![a-z])/i;
const strip = s => (s || "").replace(/<[^>]+>/g, " ").replace(/&nbsp;|&amp;|&#39;|&quot;/g, " ").replace(/\s+/g, " ").slice(0, 1500);
const keep = j => INTERN.test(j.title) && HW.test(j.title);

async function greenhouse(c) {
  const j = await (await fetch(`https://boards-api.greenhouse.io/v1/boards/${c.token}/jobs?content=true`)).json();
  return j.jobs.map(x => ({company: c.name, title: x.title, location: (x.location || {}).name || "", url: x.absolute_url,
    posted: (x.updated_at || "").slice(0, 10), description: strip(x.content), source: "greenhouse", tier: c.tier}));
}
async function lever(c) {
  const j = await (await fetch(`https://api.lever.co/v0/postings/${c.token}?mode=json`)).json();
  return j.map(x => ({company: c.name, title: x.text, location: (x.categories || {}).location || "", url: x.hostedUrl,
    posted: String(x.createdAt || "").slice(0, 10), description: strip(x.descriptionPlain || x.description), source: "lever", tier: c.tier}));
}
async function ashby(c) {
  const j = await (await fetch(`https://api.ashbyhq.com/posting-api/job-board/${c.token}?includeCompensation=false`)).json();
  return j.jobs.map(x => ({company: c.name, title: x.title, location: x.location || "", url: x.jobUrl || x.applyUrl,
    posted: (x.publishedAt || "").slice(0, 10), description: strip(x.descriptionPlain), source: "ashby", tier: c.tier}));
}
const F = {greenhouse, lever, ashby};

async function runPublic(companies) {
  const out = [], log = [];
  for (let i = 0; i < companies.length; i += 8) {
    await Promise.all(companies.slice(i, i + 8).map(async c => {
      try { const jobs = await F[c.ats](c); const k = jobs.filter(keep); out.push(...k); log.push(`${c.name}:${jobs.length}/${k.length}`); }
      catch (e) { log.push(`${c.name}:ERR`); }
    }));
  }
  window.__jobs = (window.__jobs || []).concat(out);
  return log.join(" ");
}

// ---- WORKDAY_ORIGIN: run on https://<tenant>.<host>.myworkdayjobs.com/<site>
async function workdayHere(c) {
  const api = `/wday/cxs/${c.tenant}/${c.site}/jobs`, seen = new Set(), out = [];
  for (const term of ["intern", "co-op", "coop"]) {
    for (let offset = 0; offset < 400; offset += 20) {
      const j = await (await fetch(api, {method: "POST", headers: {"Content-Type": "application/json", Accept: "application/json"},
        body: JSON.stringify({appliedFacets: {}, limit: 20, offset, searchText: term})})).json();
      const posts = j.jobPostings || [];
      for (const p of posts) {
        if (seen.has(p.externalPath)) continue; seen.add(p.externalPath);
        out.push({company: c.name, title: p.title, location: p.locationsText || "", url: `${location.origin}/${c.site}${p.externalPath}`,
          posted: p.postedOn || "", description: "", source: "workday", tier: c.tier, _path: p.externalPath});
      }
      if (!posts.length || offset + 20 >= (j.total || 0)) break;
    }
  }
  const k = out.filter(keep);
  for (const j of k) {  // descriptions so spring/co-op detection works
    try { const d = await (await fetch(`/wday/cxs/${c.tenant}/${c.site}${j._path}`, {headers: {Accept: "application/json"}})).json();
      j.description = strip((d.jobPostingInfo || {}).jobDescription); } catch (e) {}
    delete j._path;
  }
  return {log: `${c.name}:${out.length}/${k.length}`, jobs: k};
}

// ---- ORACLE_ORIGIN: run on https://<host>/hcmUI/CandidateExperience/en/sites/<site>/jobs
async function oracleHere(c) {
  const out = [], seen = new Set();
  for (const term of ["intern", "co-op", "coop"]) {
    for (let offset = 0; offset < 500; offset += 100) {
      const finder = `findReqs;siteNumber=${c.site},keyword=${encodeURIComponent(term)},limit=100,offset=${offset},sortBy=POSTING_DATES_DESC`;
      const j = await (await fetch(`/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=${finder}`)).json();
      const reqs = ((j.items || [])[0] || {}).requisitionList || [];
      for (const r of reqs) { if (seen.has(r.Id)) continue; seen.add(r.Id);
        out.push({company: c.name, title: r.Title, location: r.PrimaryLocation || "", url: `${location.origin}/hcmUI/CandidateExperience/en/sites/${c.site}/job/${r.Id}`,
          posted: (r.PostedDate || "").slice(0, 10), description: strip(r.ShortDescriptionStr), source: "oracle_hcm", tier: c.tier}); }
      if (reqs.length < 100) break;
    }
  }
  const k = out.filter(keep);
  return {log: `${c.name}:${out.length}/${k.length}`, jobs: k};
}

// ---- SUCCESSFACTORS_ORIGIN: run on https://careers.<company>.com/
async function sfHere(c) {
  const out = [], seen = new Set();
  for (const term of ["intern", "co-op"]) {
    for (const start of [0, 25, 50]) {
      const html = await (await fetch(`/search/?q=${encodeURIComponent(term)}&sortColumn=referencedate&sortDirection=desc&startrow=${start}`)).text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const rows = [...doc.querySelectorAll("a.jobTitle-link")];
      for (const a of rows) { const url = new URL(a.getAttribute("href"), location.origin).href; if (seen.has(url)) continue; seen.add(url);
        const tr = a.closest("tr"); const loc = tr ? (tr.querySelector(".jobLocation") || {}).textContent || "" : "";
        out.push({company: c.name, title: a.textContent.trim(), location: loc.trim(), url, posted: "", description: "", source: "successfactors", tier: c.tier}); }
      if (rows.length < 25) break;
    }
  }
  const k = out.filter(keep);
  return {log: `${c.name}:${out.length}/${k.length}`, jobs: k};
}

// ---- JIBE_ORIGIN (AMD): run on https://careers.amd.com/
async function jibeHere(c) {
  const out = [], seen = new Set();
  for (const term of ["intern", "co-op"]) {
    for (let page = 1; page <= 6; page++) {
      const j = await (await fetch(`/api/jobs?keywords=${encodeURIComponent(term)}&page=${page}&sortBy=posted_date&descending=true`)).json();
      const jobs = j.jobs || [];
      for (const w of jobs) { const d = w.data || w; const id = d.slug || d.req_id; if (seen.has(id)) continue; seen.add(id);
        out.push({company: c.name, title: d.title, location: d.full_location || d.location || "", url: d.apply_url || `${location.origin}/careers-home/jobs/${d.req_id}`,
          posted: (d.posted_date || "").slice(0, 10), description: strip(d.description), source: "jibe", tier: c.tier}); }
      if (jobs.length < 10) break;
    }
  }
  const k = out.filter(keep);
  return {log: `${c.name}:${out.length}/${k.length}`, jobs: k};
}
