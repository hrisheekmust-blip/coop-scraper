// ==UserScript==
// @name         Co-op board: Outlook confirmations
// @namespace    coop-hrisheek
// @version      1.5
// @description  Reads the Outlook web inbox list, matches application emails to companies you applied to on the co-op board, and records them (confirmation / rejection / interview) in the board's private repo.
// @match        https://outlook.office.com/*
// @match        https://outlook.office365.com/*
// @match        https://outlook.live.com/*
// @match        https://outlook.cloud.microsoft/*
// @match        https://*.cloud.microsoft/mail*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      api.github.com
// @connect      hrisheekmust-blip.github.io
// @run-at       document-idle
// ==/UserScript==
(function () {
  "use strict";
  const REPO = "hrisheekmust-blip/coop-apps";
  const SHEET = "https://hrisheekmust-blip.github.io/coop-scraper/data/sheet.csv";
  const EVERY = 2 * 60 * 1000;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || "").replace(/\s+/g, " ").trim();

  function req(url, opt = {}) {
    return new Promise((resolve, reject) => GM_xmlhttpRequest({
      method: opt.method || "GET", url, headers: opt.headers || {}, data: opt.body,
      onload: r => r.status < 300 ? resolve(r.responseText) : reject(new Error(url + " HTTP " + r.status)), onerror: () => reject(new Error("network " + url))
    }));
  }
  const b64 = s => btoa(unescape(encodeURIComponent(s))), unb64 = s => decodeURIComponent(escape(atob(s.replace(/\n/g, ""))));
  async function token() { return GM_getValue("gh_token", ""); }
  function askToken() {
    if (document.getElementById("coop-token-box")) return;
    const d = document.createElement("div"); d.id = "coop-token-box";
    d.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:2147483647;background:#1d4ed8;color:#fff;padding:12px 14px;border-radius:8px;font:13px system-ui;box-shadow:0 4px 14px rgba(0,0,0,.3);display:flex;gap:8px;align-items:center";
    // Outlook enforces Trusted Types, so no innerHTML: build the nodes by hand
    const span = document.createElement("span"); span.textContent = "Co-op board: paste your GitHub token (same one as the board settings)";
    const inp = document.createElement("input"); inp.type = "password"; inp.placeholder = "github_pat_…"; inp.style.cssText = "width:260px;padding:4px 6px;border-radius:4px;border:0;color:#111";
    const btn = document.createElement("button"); btn.textContent = "Save"; btn.style.cssText = "padding:4px 10px;border-radius:4px;border:0;cursor:pointer";
    d.append(span, inp, btn);
    const save = () => { const v = inp.value.trim().replace(/^Bearer\s+/i, ""); if (!/^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(v)) { span.textContent = "That doesn't look like a GitHub token (should start with github_pat_ or ghp_). Paste it again."; return; } GM_setValue("gh_token", v); d.remove(); toast("token saved; scanning the inbox now"); setTimeout(scan, 500); };
    btn.onclick = save; inp.onkeydown = e => { if (e.key === "Enter") save(); };
    document.body.appendChild(d); inp.focus();
  }
  try { GM_registerMenuCommand("Set GitHub token", () => { GM_setValue("gh_token", ""); askToken(); }); } catch (e) {}
  async function gh(path, opt = {}) {
    const t = await token(); if (!t) throw new Error("no token");
    const txt = await req(`https://api.github.com/repos/${REPO}/contents/${path}?t=${Date.now()}`, { method: opt.method || "GET", headers: { Authorization: "Bearer " + t, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", ...(opt.body ? { "Content-Type": "application/json" } : {}) }, body: opt.body ? JSON.stringify(opt.body) : undefined });
    return JSON.parse(txt);
  }
  function csv(t) { const rows = []; let row = [], cur = "", q = false; for (let i = 0; i < t.length; i++) { const c = t[i]; if (q) { if (c == '"') { if (t[i + 1] == '"') { cur += '"'; i++; } else q = false; } else cur += c; } else if (c == '"') q = true; else if (c == ',') { row.push(cur); cur = ""; } else if (c == '\n') { row.push(cur); rows.push(row); row = []; cur = ""; } else if (c != '\r') cur += c; } if (cur || row.length) { row.push(cur); rows.push(row); } const h = rows.shift() || []; return rows.filter(r => r.length === h.length).map(r => Object.fromEntries(h.map((k, i) => [k, r[i]]))); }
  async function sha1_16(s) { const b = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(s)); return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, "0")).join("").slice(0, 16); }

  // ---------------------------------------------------------------- read what Outlook has rendered
  function inboxRows() {
    const out = [];
    for (const el of document.querySelectorAll('[role="option"], [role="row"], [data-convid], div[aria-label][tabindex="0"]')) {
      const label = norm(el.getAttribute("aria-label") || ""); const text = norm(el.innerText || el.textContent || "");
      const s = label.length > 20 ? label : text;
      if (s.length < 20 || s.length > 1200) continue;
      out.push({ text: s, el });
    }
    // de-dupe
    const seen = new Set(); return out.filter(r => { if (seen.has(r.text)) return false; seen.add(r.text); return true; });
  }
  const classify = s => /unfortunately|not (be )?moving forward|regret|other candidates|decided not to|will not be (moving|proceeding)|no longer under consideration|position has been filled/i.test(s) ? "rejected"
    : /interview|schedule (a )?(call|time|conversation)|next steps?|assessment|coding challenge|hackerrank|codesignal|phone screen|availability for a call/i.test(s) ? "interview"
    : /thank you for (applying|your application|your interest)|application (has been |was )?(received|submitted)|we('ve| have) received your application|received your application|confirm(ation|ing) (of )?your application|your application to/i.test(s) ? "confirmation" : "";

  // ---------------------------------------------------------------- match + record
  const companyRx = c => { const n = c.replace(/[^\w\s&.-]/g, "").replace(/\b(inc|corp|corporation|llc|ltd|technologies|technology|systems|labs|the)\b\.?/gi, "").trim(); const first = n.split(/\s+/)[0]; return n.length >= 4 ? new RegExp("\\b" + n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b", "i") : new RegExp("\\b" + first.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b", "i"); };
  let busy = false, first = true;
  async function scan() {
    if (busy) return; busy = true;
    try {
      const rows = inboxRows(); if (!rows.length) { if (first) toast("no inbox rows visible yet; I re-check every 2 minutes", "warn"); return; }
      if (!(await token())) { askToken(); return; }
      const sheet = csv(await req(SHEET + "?t=" + Date.now()));
      const st = await gh("data/state.json"); const state = JSON.parse(unb64(st.content)) || {};
      const ids = {}; for (const r of sheet) ids[await sha1_16(r.link)] = r;
      const applied = Object.entries(state).filter(([id, v]) => ["applied", "interview", "rejected", "offer"].includes(v.status) && ids[id]).map(([id, v]) => ({ id, ...v, ...ids[id] }));
      if (first) { first = false; toast(`watching this inbox: ${rows.length} rows on screen, ${applied.length} applied companies to match`); }
      if (!applied.length) return;
      let changed = 0;
      for (const j of applied) {
        const rx = companyRx(j.company);
        for (const r of rows) {
          if (!rx.test(r.text)) continue;
          const kind = classify(r.text); if (!kind) continue;
          const key = r.text.slice(0, 120);
          const cur = state[j.id] || {};
          if (cur.email && cur.email.key === key) continue;
          const m = r.text.match(/\b(\d{1,2}\/\d{1,2}\/\d{2,4}|[A-Z][a-z]{2} \d{1,2}(, \d{4})?|\d{1,2}:\d{2} (AM|PM))\b/); const date = m ? m[0] : new Date().toISOString().slice(0, 10);
          const upd = { ...cur, email: { key, subject: r.text.slice(0, 140), date, snippet: r.text.slice(0, 300), kind, seen: new Date().toISOString() } };
          if (kind === "rejected" && cur.status !== "offer") upd.status = "rejected";
          if (kind === "interview" && !["offer", "rejected"].includes(cur.status)) upd.status = "interview";
          state[j.id] = upd; changed++;
        }
      }
      if (changed) {
        await gh("data/state.json", { method: "PUT", body: { message: "outlook: " + changed + " email(s) matched " + new Date().toISOString(), content: b64(JSON.stringify(state, null, 1)), sha: st.sha } });
        toast(`recorded ${changed} application email(s) on the board`);
      }
    } catch (e) { console.warn("coop-mail", e); if (/HTTP 401/.test(e.message || "")) { GM_setValue("gh_token", ""); toast("GitHub rejected that token (401): it was pasted wrong or expired. Paste it again.", "err"); setTimeout(askToken, 1500); } else toast("error: " + (e.message || e), "err"); } finally { busy = false; }
  }
  function toast(msg, kind) { const old = document.getElementById("coop-toast"); if (old) old.remove(); const d = document.createElement("div"); d.id = "coop-toast"; d.textContent = "Co-op board: " + msg; d.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:2147483647;background:" + (kind === "err" ? "#b3261e" : kind === "warn" ? "#b45309" : "#1e7d3c") + ";color:#fff;padding:10px 14px;border-radius:8px;font:13px system-ui;box-shadow:0 4px 14px rgba(0,0,0,.3)"; document.body.appendChild(d); setTimeout(() => d.remove(), 6000); }

  setTimeout(scan, 8000); setInterval(scan, EVERY);
})();
