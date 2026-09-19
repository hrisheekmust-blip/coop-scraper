// ==UserScript==
// @name         Co-op board: one-click apply
// @namespace    coop-hrisheek
// @version      2.5
// @description  Opened by the co-op board: walks any application form (Ashby, Greenhouse, Lever, LinkedIn Easy Apply, Workday, Oracle, iCIMS, SuccessFactors, Phenom, ...) page by page, fills it from the board's answers, attaches the files, submits, and reports back.
// @match        *://*/*
// @require      https://hrisheekmust-blip.github.io/coop-scraper/engine.js?v=5
// @grant        none
// @run-at       document-idle
// ==/UserScript==
(function () {
  "use strict";
  const KEY = "coop-payload";
  const RELAY = "https://hrisheekmust-blip.github.io/coop-scraper/relay.html";
  const b64e = x => btoa(unescape(encodeURIComponent(x))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const b64d = x => decodeURIComponent(escape(atob(x.replace(/-/g, "+").replace(/_/g, "/"))));
  const hashId = (location.hash.match(/coop=([0-9a-f]{16})/) || [])[1];
  const inlineP = (location.hash.match(/[#&]p=([A-Za-z0-9_-]+)/) || [])[1];
  let payload = null;
  if (inlineP) {                                         // the relay tab hands the whole payload over in the hash
    try { payload = JSON.parse(b64d(inlineP)); } catch (e) {}
    if (payload) {
      try { sessionStorage.setItem(KEY, JSON.stringify(payload)); } catch (e) {}
      try { history.replaceState(null, "", location.href.split("#")[0] + "#coop=" + payload.id); } catch (e) {}
    }
  }
  if (!payload) { try { payload = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch (e) {} }
  if (location.href.indexOf(RELAY.split("#")[0]) === 0) return;   // the relay page itself drives the queue; nothing to fill there
  if (!hashId && !payload) return;                       // not opened by the board: do nothing on this page
  if (hashId && payload && payload.id !== hashId) payload = null;
  if (window.__coopRunning) return; window.__coopRunning = true;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || "").replace(/\s+/g, " ").replace(/[✱*]/g, "").trim();
  const txt = el => norm(el ? (el.innerText ?? el.textContent) : "");
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length)) && getComputedStyle(el).visibility !== "hidden";
  const host = location.hostname;
  const PORTAL = /ashbyhq/.test(host) ? "ashby" : /greenhouse/.test(host) ? "greenhouse" : /lever\.co/.test(host) ? "lever" : /linkedin/.test(host) ? "linkedin"
    : /myworkday/.test(host) ? "workday" : /icims/.test(host) ? "icims" : /oraclecloud/.test(host) ? "oracle" : /successfactors|\.sap\./.test(host) ? "successfactors" : "other";
  const opener = () => { try { return window.opener || (window.top !== window ? window.top.opener : null); } catch (e) { return null; } };
  const DRY = !!window.__coopDry;                        // test harness (tools/dryrun.mjs): fill everything, never press Submit, never wait on a human

  // ---------------------------------------------------------------- banner + reporting
  let bar;
  function banner(msg, kind, acts) {
    if (window.top !== window) { try { window.top.postMessage({ type: "coop-banner", msg, kind }, "*"); } catch (e) {} return; }
    if (!bar) { bar = document.createElement("div"); bar.id = "coop-banner"; document.documentElement.appendChild(bar); }
    bar.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;font:14px/1.4 system-ui,sans-serif;color:#fff;background:${kind === "err" ? "#b3261e" : kind === "ok" ? "#1e7d3c" : kind === "wait" ? "#b45309" : "#1d4ed8"};box-shadow:0 2px 8px rgba(0,0,0,.25)`;
    bar.textContent = "Co-op board: " + msg;
    for (const a of acts || []) {
      const b = document.createElement("button"); b.textContent = a.label;
      b.style.cssText = "margin-left:10px;padding:3px 11px;border:0;border-radius:5px;background:#fff;color:#111;font:inherit;font-size:13px;cursor:pointer";
      b.onclick = a.fn; bar.appendChild(b);
    }
  }
  window.addEventListener("message", e => { const d = e.data || {}; if (d.type === "coop-banner" && window.top === window) banner(d.msg, d.kind); });
  const report = m => { try { window.__coopLast = m; (window.__coopReports = window.__coopReports || []).push(m); } catch (e) {} try { const o = opener(); if (o) o.postMessage({ ...m, type: "coop-result", id: payload ? payload.id : hashId, url: location.href, at: new Date().toISOString() }, "*"); } catch (e) {} };
  let reported = false;
  function finish(m) {                                   // last word on this application: tell the board, then hand the tab back to the relay
    report(m);
    if (reported || DRY) return; reported = true;
    if (opener()) return;                                // the board tab heard it directly
    const r = { ok: !!m.ok, status: m.status, need: (m.need || []).slice(0, 12), done: (m.done || []).slice(-40), id: payload ? payload.id : hashId, url: location.href, at: new Date().toISOString() };
    setTimeout(() => { try { location.href = RELAY + "#done=" + b64e(JSON.stringify(r)); } catch (e) {} }, 500);
  }
  const stallActions = (need, done) => [
    { label: "I finished it — continue", fn: () => finish({ ok: true, status: "applied", done: (done || []).concat("finished by hand in the form tab") }) },
    { label: "Skip this one", fn: () => finish({ ok: false, status: "needs-you", need: need || ["skipped by you"], done }) }];

  // ---------------------------------------------------------------- payload handshake with the board tab
  async function getPayload() {
    if (payload) return payload;
    banner("waiting for your answers from the board tab…");
    return new Promise(resolve => {
      const onMsg = e => { const d = e.data || {}; if (d.type === "coop-payload" && d.id === hashId) { window.removeEventListener("message", onMsg); payload = d; try { sessionStorage.setItem(KEY, JSON.stringify(d)); } catch (x) {} resolve(d); } };
      window.addEventListener("message", onMsg);
      let n = 0; const t = setInterval(() => { const o = opener(); if (!o || n++ > 60) { clearInterval(t); resolve(null); return; } o.postMessage({ type: "coop-ready", id: hashId }, "*"); }, 500);
    });
  }
  const keepHash = url => { try { const u = new URL(url, location.href); u.hash = "coop=" + (payload ? payload.id : hashId) + (payload ? "&p=" + b64e(JSON.stringify(payload)) : ""); return u.href; } catch (e) { return url; } };

  // ---------------------------------------------------------------- React-aware setters
  const reactProps = el => { const k = Object.keys(el).find(k => k.startsWith("__reactProps$")); return k ? el[k] : null; };
  const synth = (el, type) => ({ target: el, currentTarget: el, type, bubbles: true, nativeEvent: new Event(type, { bubbles: true }), preventDefault() {}, stopPropagation() {}, persist() {}, isDefaultPrevented: () => false, isPropagationStopped: () => false });
  function fireReact(el, names, type) { const p = reactProps(el); if (!p) return false; let hit = false; for (const n of names) if (typeof p[n] === "function") { try { p[n](synth(el, type)); hit = true; } catch (e) {} } return hit; }
  const valDesc = el => Object.getOwnPropertyDescriptor(el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value");
  function setNative(el, v) { el.focus(); valDesc(el).set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); }
  function setText(el, v) {
    el.focus();
    try { el.select && el.select(); } catch (e) {}
    if (el.value) { try { document.execCommand("delete", false); } catch (e) {} if (el.value) valDesc(el).set.call(el, ""); el.dispatchEvent(new Event("input", { bubbles: true })); }
    let ok = false; try { ok = document.execCommand("insertText", false, v); } catch (e) {}
    if (!ok || el.value !== v) setNative(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true })); el.dispatchEvent(new FocusEvent("blur", { bubbles: true })); el.blur();
  }
  async function setValueVerified(el, v) {
    setText(el, v); await sleep(80);
    let p = reactProps(el);
    if (p && "value" in p && p.value !== v) {
      el.focus(); try { el.select(); document.execCommand("delete", false); } catch (e) {} valDesc(el).set.call(el, ""); el.dispatchEvent(new Event("input", { bubbles: true })); fireReact(el, ["onInput", "onChange"], "input"); await sleep(120);
      try { document.execCommand("insertText", false, v); } catch (e) {} if (el.value !== v) valDesc(el).set.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true })); fireReact(el, ["onInput", "onChange"], "input"); await sleep(80); fireReact(el, ["onBlur"], "blur"); el.blur();
    }
    await sleep(300);
    const opts = [...document.querySelectorAll("[role=option], [role=listbox] li, [class*='suggestion'], [class*='Suggestion'], [class*='typeahead'] li, [data-automation-id='promptOption']")].filter(visible);
    const opt = opts.find(o => txt(o).toLowerCase() === v.toLowerCase()) || opts.find(o => txt(o).toLowerCase().includes(v.toLowerCase().slice(0, 12)));
    if (opt) { realClick(opt); await sleep(200); }
    p = reactProps(el); return !p || !("value" in p) || p.value === v || el.value === v;
  }
  async function setCheckedVerified(el, on) {
    const says = () => { const q = reactProps(el); return q && typeof q.checked === "boolean" ? q.checked : el.checked; };
    if (el.checked !== on) { el.click(); await sleep(80); }
    if (says() !== on && el.labels && el.labels[0]) { realClick(el.labels[0]); await sleep(80); }
    if (says() !== on && on) { el.click(); await sleep(80); el.click(); await sleep(80); }
    if (says() !== on) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked").set.call(el, on); fireReact(el, ["onChange", "onClick"], "change"); el.dispatchEvent(new Event("change", { bubbles: true })); await sleep(80); }
    return says() === on;
  }
  function b64file(f) { const bin = atob(f.b64); const u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new File([u8], f.name, { type: f.type || "application/pdf" }); }
  function setFile(input, file) { const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files; input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true })); }
  function realClick(el) {
    try { el.scrollIntoView({ block: "center" }); } catch (e) {} try { el.focus(); } catch (e) {}
    const r = el.getBoundingClientRect(); const o = { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0, buttons: 1 };
    for (const t of ["pointerover", "pointerenter", "mouseover", "pointerdown", "mousedown"]) el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, pointerType: "mouse", isPrimary: true }) : new MouseEvent(t, o));
    for (const t of ["pointerup", "mouseup"]) el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: 0 }) : new MouseEvent(t, { ...o, buttons: 0 }));
    el.dispatchEvent(new MouseEvent("click", { ...o, buttons: 0 })); try { el.click(); } catch (e) {}
  }

  // ---------------------------------------------------------------- labels
  const entryOf = el => el.closest(".ashby-application-form-field-entry, fieldset, .field, .application-question, .application-field, li, .form-field, [class*='field-entry'], [class*='FieldEntry'], [class*='question'], [data-automation-id*='formField'], [data-automation-id*='FormField'], .jobs-easy-apply-form-element, .fb-dash-form-element, .iCIMS_TableRow, .oj-flex-item, .rcrtField") || el.parentElement;
  function labelOf(el) {
    if (el.labels && el.labels[0] && txt(el.labels[0])) return txt(el.labels[0]);
    const lb = el.getAttribute("aria-labelledby"); if (lb) { const t = lb.split(/\s+/).map(i => txt(document.getElementById(i))).join(" "); if (t) return t; }
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));
    let en = entryOf(el);
    for (let i = 0; en && i < 4; i++, en = en.parentElement) {
      const l = [...en.querySelectorAll("label, legend, .application-label, [class*='label']:not(input):not(select), [data-automation-id*='label'], span[class*='title'], [class*='question-text'], [class*='questionText']")].find(x => txt(x) && !x.contains(el) && txt(x).length < 400);
      if (l) return txt(l);
      if (en.querySelectorAll("input, select, textarea").length > 3) break;   // walked out of this field's box
    }
    if (el.placeholder && !/^(select|choose|type your|enter|your answer|search)/i.test(el.placeholder)) return norm(el.placeholder);
    let p = el.previousElementSibling; while (p) { if (txt(p) && txt(p).length < 200) return txt(p); p = p.previousElementSibling; }
    return "";
  }
  const raw = el => { try { const en = entryOf(el); const small = en && en.querySelectorAll("input, select, textarea, button").length <= 4 ? en : null; return ((el.labels && el.labels[0] ? el.labels[0].textContent : "") + " " + (el.getAttribute && el.getAttribute("aria-label") || "") + " " + ((small || el).textContent || "").slice(0, 400)); } catch (e) { return ""; } };
  const isRequired = el => !!(el.required || (el.getAttribute && el.getAttribute("aria-required") === "true") || /[*✱]|\brequired\b|\bmandatory\b/i.test(raw(el)) && !/not required|optional/i.test(raw(el)));
  const groupLabel = fs => { const lg = fs.querySelector("legend, .application-label, [data-automation-id*='label'], label:not(:has(input))"); const t = lg ? txt(lg) : ""; if (t) return t; let p = fs.previousElementSibling; while (p) { if (txt(p)) return txt(p); p = p.previousElementSibling; } return labelOf(fs); };

  // ---------------------------------------------------------------- fill everything visible on the current page
  async function fill(P) {
    const ctx = { job: P.job, answers: P.answers, profile: P.profile, cover: P.cover, coverText: P.coverText || "" };
    const A = f => window.CoopEngine.answerFor(f, ctx);
    const need = [], done = [], seen = new Set();
    const files = { resume: P.files.find(f => /resume/i.test(f.kind)), cover: P.cover ? P.files.find(f => /cover/i.test(f.kind)) : null };
    const root = document.querySelector(".jobs-easy-apply-modal") || document.querySelector("[role=dialog] form") || document.querySelector("form") || document.body;

    // 1. checkbox / radio groups
    for (const fs of root.querySelectorAll("fieldset, [role=group], [role=radiogroup], .application-question, .field, [data-automation-id*='radioGroup'], [data-automation-id*='checkboxGroup'], .fb-dash-form-element")) {
      const opts = [...fs.querySelectorAll("input[type=checkbox], input[type=radio]")].filter(o => visible(o) || visible(o.parentElement));
      if (!opts.length) continue;
      if (opts.length < 2 && !(opts[0].type === "checkbox" && /agree|consent|acknowledg|certify|terms|privacy|confirm/i.test(txt(fs)))) continue;
      if (opts.some(o => seen.has(o))) continue;
      opts.forEach(o => seen.add(o));
      const q = groupLabel(fs); const labels = opts.map(o => txt(o.labels && o.labels[0]) || txt(o.parentElement));
      const r = A({ label: q, options: labels, type: opts[0].type });
      if (r.k === "need" || !r.a) { if (isRequired(fs)) need.push(q); else done.push("left blank (optional): " + q); continue; }
      const want = r.a.split(/\s*\+\s*/).map(norm);
      for (let i = 0; i < opts.length; i++) { const o = opts[i]; const on = opts.length === 1 ? true : want.some(w => w && (labels[i] === w || labels[i].toLowerCase().includes(w.toLowerCase()) || w.toLowerCase().includes(labels[i].toLowerCase())));
        const ok = await setCheckedVerified(o, on); if (!ok && on) need.push(q + " (" + labels[i] + ")"); }
      done.push(q + " = " + r.a);
    }
    // 2. Ashby yes/no button pairs
    for (const en of root.querySelectorAll(".ashby-application-form-field-entry")) {
      const btns = [...en.querySelectorAll("button")].filter(b => /^(yes|no)$/i.test(txt(b)));
      if (btns.length !== 2) continue;
      const q = txt(en.querySelector("label")); const r = A({ label: q, options: ["Yes", "No"], type: "Boolean" });
      if (r.k === "need" || !r.a) { if (isRequired(en)) need.push(q); else done.push("left blank (optional): " + q); continue; }
      const b = btns.find(x => r.a.toLowerCase().startsWith(txt(x).toLowerCase())) || btns[0]; realClick(b); done.push(q + " = " + txt(b));
    }
    // 3. custom dropdown buttons (Workday, Oracle, LinkedIn)
    for (const btn of root.querySelectorAll("button[aria-haspopup='listbox'], button[aria-haspopup='true'][data-automation-id], [role=combobox]:not(input):not(select), [data-automation-id*='selectinput'], [data-automation-id='dropdownButton']")) {
      if (!visible(btn) || seen.has(btn)) continue; seen.add(btn);
      const q = labelOf(btn); if (!q) continue;
      const cur = txt(btn); if (cur && !/select|choose|--|^$/i.test(cur) && cur !== q) continue;   // already set
      const r0 = A({ label: q, options: [], type: "select" });
      realClick(btn); await sleep(450);
      const opts = [...document.querySelectorAll("[role=option], [role=listbox] li, [data-automation-id='promptOption'], ul[role=listbox] > li, .oj-listbox-result")].filter(visible);
      const labels = opts.map(txt); const r = A({ label: q, options: labels, type: "select" });
      let pick = labels.findIndex(l => l.toLowerCase() === (r.a || "").toLowerCase());
      if (pick < 0) pick = labels.findIndex(l => r.a && (l.toLowerCase().includes(r.a.toLowerCase().slice(0, 10)) || r.a.toLowerCase().includes(l.toLowerCase())));
      if (r.k === "need" || pick < 0) { document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); if (isRequired(btn) || (r.a && r.k !== "need")) need.push(r0.k === "need" || !r.a ? q : q + " (no option matched '" + r.a + "')"); else done.push("left blank (optional): " + q); await sleep(150); continue; }
      realClick(opts[pick]); await sleep(250); done.push(q + " = " + labels[pick]);
    }
    // 4. text-like inputs, textareas, native selects, comboboxes, files
    for (const el of root.querySelectorAll("input, textarea, select")) {
      if (seen.has(el)) continue;
      const type = (el.getAttribute("type") || el.tagName).toLowerCase();
      if (["checkbox", "radio", "hidden", "submit", "button", "search", "password", "reset", "image"].includes(type)) continue;
      if (type === "file") {
        const around = txt(entryOf(el)); const q = labelOf(el) || around.slice(0, 80);
        if (/autofill|auto-fill|parse|prefill/i.test(around) && !/^resume/i.test(q)) continue;
        const isCover = /cover/i.test(q + around);
        const f = isCover ? files.cover : /resume|cv|attach|upload|document/i.test(q + around) ? files.resume : null;
        if (isCover && !P.cover) { done.push("cover letter skipped"); continue; }
        if (el.files && el.files.length) continue;
        if (f) { setFile(el, b64file(f)); done.push(q + " = " + f.name); await sleep(900); } else if (/required|\*/.test(around)) need.push(q || "file upload");
        continue;
      }
      if (!visible(el) || el.disabled || el.readOnly) continue;
      const q = labelOf(el); if (!q) continue;
      const opts = el.tagName === "SELECT" ? [...el.options].map(o => txt(o)).filter(o => o && !/^(select|choose|--|please)/i.test(o)) : [];
      const isDate = type === "date" || /pick date|mm\/dd|yyyy|mm\/yyyy/i.test(el.placeholder || "") || /date/i.test(el.getAttribute("data-automation-id") || "");
      const r = A({ label: q, options: opts, type: isDate ? "date" : type });
      if (r.k === "need") { if (!(el.value || "").trim()) { if (isRequired(el)) need.push(q); else done.push("left blank (optional): " + q); } continue; }
      if (r.k === "file" || r.a === "(leave blank)" || r.a === "") continue;
      if (el.tagName === "SELECT") {
        const o = [...el.options].find(x => txt(x).toLowerCase() === r.a.toLowerCase()) || [...el.options].find(x => txt(x).toLowerCase().includes(r.a.toLowerCase().slice(0, 12)));
        if (o) { if (el.value !== o.value) { el.value = o.value; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); fireReact(el, ["onChange"], "change"); } done.push(q + " = " + txt(o)); } else if (isRequired(el) || el.selectedIndex <= 0) need.push(q + " (no option matched '" + r.a + "')");
        continue;
      }
      if ((el.value || "").trim() && !isDate && (el.value.trim() === r.a || /^(name|first|last|email|phone|preferred)/i.test(q))) continue;   // portal prefilled it from the account
      if (el.getAttribute("role") === "combobox" || /select__input|react-select|autocomplete|typeahead/i.test(el.className + " " + (el.parentElement && el.parentElement.className))) {
        // react-select style: the chosen value lives in a sibling, the options only exist while the menu is open
        const ctrl = el.closest("[class*='control'], [class*='Control']") || el.parentElement.parentElement;
        const cur = norm(txt(ctrl).replace(q, "")).toLowerCase();
        const guess = (r.a || "").toLowerCase();
        if (cur && !/^(select|choose|search|type|--|start typing)/i.test(cur) && (cur === guess || (guess && cur.includes(guess.slice(0, 8))) || /^(name|first|last|email|phone|school|universit)/i.test(q))) { done.push(q + " = " + cur); continue; }   // already right
        const menuOpts = () => [...document.querySelectorAll("[role=option], .select__option, [class*='select__option'], [role=listbox] li, [data-automation-id='promptOption']")].filter(visible);
        const pickFrom = (labels, want) => { const w = (want || "").toLowerCase(); if (!w) return -1; let i = labels.findIndex(l => l.toLowerCase() === w); if (i < 0) i = labels.findIndex(l => l.toLowerCase().includes(w.slice(0, 10)) || (w.length > 3 && w.includes(l.toLowerCase()))); return i; };
        const esc = () => { el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true })); el.blur(); };
        realClick(el); el.focus(); el.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", keyCode: 40, bubbles: true })); await sleep(450);
        let opts2 = menuOpts(), labels2 = opts2.map(txt), r2 = labels2.length ? A({ label: q, options: labels2, type: "select" }) : r;
        if (r2.k === "need" || !r2.a) { esc(); if (isRequired(el)) need.push(q); else done.push("left blank (optional): " + q); await sleep(120); continue; }
        let pick = pickFrom(labels2, r2.a);
        if (pick < 0) {   // long or lazy list: type to filter, then look again
          setNative(el, r2.a.split(/[\s,(]/)[0]); await sleep(600);
          opts2 = menuOpts(); labels2 = opts2.map(txt); const r3 = labels2.length ? A({ label: q, options: labels2, type: "select" }) : r2; pick = pickFrom(labels2, r3.a);
          if (pick < 0 && labels2.length === 1 && !/no (options|results)/i.test(labels2[0])) pick = 0;
        }
        if (pick < 0) { esc(); if (isRequired(el)) need.push(q + (labels2.length ? " (no option matched '" + r2.a + "')" : " (no options appeared)")); else done.push("left blank (optional): " + q); await sleep(120); continue; }
        realClick(opts2[pick]); await sleep(250); fireReact(el, ["onBlur"], "blur"); el.blur(); done.push(q + " = " + labels2[pick]); continue;
      }
      if (isDate) {
        const dm = (el.getAttribute("data-automation-id") || "") + " " + q.toLowerCase();
        const start = /^(start|available|availability|earliest)/i.test(q);
        const mo = start ? "01" : "12", dy = start ? "04" : "01";
        let v = /month/i.test(dm) ? mo : /year/i.test(dm) ? "2027" : /\bday\b/i.test(dm) ? dy : type === "date" ? `2027-${mo}-${dy}` : /mm\/yyyy/i.test(el.placeholder || "") ? `${mo}/2027` : `${mo}/${dy}/2027`;
        el.focus(); setText(el, v); el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true })); await sleep(250); document.body.click();
        if (!el.value) need.push(q + " (pick it in the calendar)"); else done.push(q + " = " + el.value); continue;
      }
      const ok = await setValueVerified(el, r.a); done.push(q + " = " + r.a.slice(0, 40)); if (!ok) need.push(q);
    }
    return { need, done };
  }

  // ---------------------------------------------------------------- page-walking helpers
  const complaints = () => [...document.querySelectorAll("li, p, span, div, label")].filter(visible).map(txt).filter(t => t.length < 160 && /^missing entry for required field:|is required$|required field|please (complete|enter|select|fill)|this field is required|cannot be blank|must be/i.test(t)).map(t => t.replace(/^missing entry for required field:\s*/i, "").replace(/\s*is required$/i, "").trim()).filter((t, i, a) => t && a.indexOf(t) === i).slice(0, 12);
  const captchaUp = () => [...document.querySelectorAll("iframe[src*='hcaptcha'], iframe[src*='recaptcha'], iframe[src*='turnstile']")].some(f => visible(f) && f.getBoundingClientRect().height > 100);
  const bodyText = () => (document.body.innerText || "").slice(0, 20000);
  const succeeded = () => /thank you for (applying|your application|your interest)|thanks for (applying|your application)|application (has been |was |is |successfully )?(submitted|received|complete)|we('ve| have) received your application|successfully (submitted|applied)|application submitted|you('ve| have) (successfully )?applied|your application was sent|we will be in touch|we'll be in touch|application confirmation/i.test(bodyText()) || /thanks|confirmation|success|submitted|applied/i.test(location.pathname + location.search);
  const loginPage = () => [...document.querySelectorAll("input[type=password]")].some(visible);
  function primaryButton() {
    const cands = [...document.querySelectorAll("button, input[type=submit], a[role=button], [role=button]")].filter(visible).filter(b => !b.disabled && b.getAttribute("aria-disabled") !== "true");
    const label = b => (b.getAttribute("aria-label") || "") + " " + (b.value || "") + " " + txt(b) + " " + (b.getAttribute("data-automation-id") || "");
    const order = [/submit application|submit my application|^submit$|\bsubmit\b(?!.*(resume|another))/i, /review (your )?application|review and submit|^review$/i, /save and continue|continue to next|^continue$|^next\b|next step|proceed|save & continue|bottom-navigation-next-button|pageFooterNextButton/i, /^apply( now)?$|easy apply|apply for this job|start application|begin application|apply to job|utilityButtonApply|applyButton/i];
    const links = [...document.querySelectorAll("a[href]")].filter(visible);
    for (const rx of order) { const pool = rx === order[order.length - 1] ? cands.concat(links) : cands; const b = pool.find(x => rx.test(label(x)) && !/upload|attach|add another|cancel|back|previous|withdraw|save (for )?later|save draft|dismiss|linkedin|indeed|autofill|alert/i.test(label(x))); if (b) return b; }
    return null;
  }
  const signature = () => location.href.split("#")[0] + "|" + bodyText().replace(/\d/g, "").slice(0, 3000);
  const spinner = () => [...document.querySelectorAll("[aria-busy='true'], [class*='spinner'], [class*='Spinner'], [class*='loading']:not([class*='loaded'])")].some(visible);
  async function settle(ms = 1200) { await sleep(ms); for (let i = 0; i < 20 && spinner(); i++) await sleep(300); }
  async function waitChange() { const sig = signature(); while (signature() === sig) await sleep(1000); }

  // ---------------------------------------------------------------- portal quirks
  const findBtn = rx => [...document.querySelectorAll("a, button, input[type=button], input[type=submit], [role=button]")].filter(visible).find(x => rx.test(txt(x) + " " + (x.getAttribute("aria-label") || "") + " " + (x.value || "")) && !/alert|share|refer a friend|save job/i.test(txt(x)));
  // are we looking at the application form itself (not a job description with a job-alert email box)?
  const onForm = () => [...document.querySelectorAll("input[type=file], input[name*='first' i], input[id*='first' i], input[autocomplete='given-name'], input[name*='last' i], input[name='name'], input[id='name'], input[name*='phone' i], input[type=tel]")].some(visible)
    || [...document.querySelectorAll("form")].some(f => [...f.querySelectorAll("input:not([type=hidden]):not([type=search]), textarea, select")].filter(visible).length >= 3 && !/job alert|search jobs|subscribe|sign up for/i.test(txt(f).slice(0, 300)));
  const APPLY_RX = /^apply( now| for this job| to job| to this job| online| here)?$|^i'm interested$|^start application$|^begin application$|^apply for (this )?(job|position|role)$/i;
  function acceptCookies() { const b = [...document.querySelectorAll("button, a, [role=button]")].filter(visible).find(x => /^(accept( all)?( cookies)?|allow all( cookies)?|i (accept|agree)|agree|got it|ok(ay)?)$/i.test(txt(x)) && /cookie|consent|privacy/i.test(txt(x.closest("div, section, aside, footer") || x.parentElement).slice(0, 800))); if (b) { realClick(b); return true; } return false; }
  const embeddedForm = () => [...document.querySelectorAll("iframe#grnhse_iframe, iframe[src*='greenhouse.io'], iframe[src*='lever.co'], iframe[src*='ashbyhq'], iframe[src*='workable'], iframe[src*='bamboohr'], iframe[src*='jobvite'], iframe[src*='applytojob'], iframe[src*='smartrecruiters']")].some(visible);
  const M = {
    linkedin: {
      async pre() {
        if (document.querySelector(".jobs-easy-apply-modal, [role=dialog] .jobs-easy-apply-content")) return true;
        const b = findBtn(/easy apply|^apply$|apply now/i);
        if (!b) return "no Apply button on this LinkedIn page (already applied, or the posting closed)";
        const before = location.href; const o = window.open; let ext = null; window.open = u => { ext = u; return null; };
        realClick(b); await sleep(1800); window.open = o;
        if (ext) { location.href = keepHash(ext); return "navigating"; }
        if (location.href !== before && !/linkedin/.test(location.hostname)) return "navigating";
        await sleep(600); return true;
      },
      async fix() { const f = [...document.querySelectorAll("input[type=checkbox]")].find(c => visible(c) && /follow/i.test(txt(c.labels && c.labels[0]) + " " + (c.getAttribute("aria-label") || ""))); if (f && f.checked) f.click(); }
    },
    workday: {
      async pre(P) {
        if (!document.querySelector("[data-automation-id='applyManually'], [data-automation-id='autofillWithResume'], [data-automation-id*='formField']")) {
          const b = document.querySelector("[data-automation-id='adventureButton'], a[data-automation-id='applyButton'], button[data-automation-id='applyButton']") || findBtn(/^apply$/i);
          if (b) { realClick(b); await settle(1500); }
        }
        const auto = document.querySelector("[data-automation-id='autofillWithResume']"); const manual = document.querySelector("[data-automation-id='applyManually']");
        if (auto) { realClick(auto); await settle(1200); const f = P.files.find(x => /resume/i.test(x.kind)); const inp = document.querySelector("input[type=file]"); if (inp && f) { setFile(inp, b64file(f)); await settle(3000); } }
        else if (manual) { realClick(manual); await settle(1200); }
        return true;
      },
      async fix() {
        for (const b of document.querySelectorAll("button[aria-haspopup='listbox']")) { if (!visible(b)) continue; const q = labelOf(b); if (/device type/i.test(q) && /select/i.test(txt(b))) { realClick(b); await sleep(400); const o = [...document.querySelectorAll("[role=option]")].find(x => /mobile/i.test(txt(x))); if (o) realClick(o); await sleep(200); } }
      }
    },
    icims: { async pre() { const b = findBtn(APPLY_RX); if (b && !onForm()) { realClick(b); await settle(2500); } return true; } },
    oracle: { async pre() { const b = findBtn(APPLY_RX); if (b && !onForm()) { realClick(b); await settle(2500); } return true; } },
    successfactors: { async pre() { const b = findBtn(APPLY_RX); if (b && !onForm()) { realClick(b); await settle(2500); } return true; } },
    ashby: { async pre() { if (!/\/application/.test(location.pathname)) { const t = [...document.querySelectorAll("a")].find(a => /\/application$/.test(a.getAttribute("href") || "")); if (t) { location.href = keepHash(t.href); return "navigating"; } } return true; } },
    greenhouse: { async pre() { const b = findBtn(APPLY_RX); if (b && !onForm() && !embeddedForm()) { realClick(b); await settle(1500); } return true; } },
    lever: { async pre() { if (!/\/apply/.test(location.pathname)) { const a = document.querySelector("a[href$='/apply'], a.postings-btn"); if (a) { location.href = keepHash(a.href); return "navigating"; } } return true; } },
    other: { async pre() { if (onForm() || embeddedForm()) return true; const b = findBtn(APPLY_RX); if (!b) return true; const o = window.open; let ext = null; window.open = u => { ext = u; return null; }; realClick(b); await settle(1500); window.open = o; if (ext) { location.href = keepHash(ext); return "navigating"; } return true; } },
  };

  // ---------------------------------------------------------------- the walk
  async function run() {
    const P = await getPayload();
    if (!P) { banner("no answers for this posting reached this tab; go back to the board and click Apply again", "err"); return; }
    for (let i = 0; i < 40 && document.readyState !== "complete"; i++) await sleep(250);
    await settle(800);
    if (succeeded() && sessionStorage.getItem("coop-clicked") === P.id) { banner("submitted ✓ recorded on the board", "ok"); sessionStorage.removeItem(KEY); finish({ ok: true, status: "applied" }); return; }
    const mod = M[PORTAL] || M.other;
    acceptCookies();
    banner("opening the application for " + P.job.company + "…");
    const pre = await mod.pre(P);
    if (pre === "navigating") { banner("following the apply link…"); return; }
    if (pre !== true) { banner(pre, "err", stallActions([pre], [])); finish({ ok: false, status: "needs-you", need: [pre] }); return; }
    const allDone = [];
    for (let step = 0; step < 14; step++) {
      await settle(600);
      if (sessionStorage.getItem("coop-clicked") === P.id && succeeded()) { banner("submitted ✓ recorded on the board", "ok"); sessionStorage.removeItem(KEY); finish({ ok: true, status: "applied", done: allDone }); return; }
      if (loginPage()) {
        const em = [...document.querySelectorAll("input[type=email], input[name*='email' i], input[id*='email' i]")].find(visible); if (em && !em.value) setText(em, P.profile.email);
        const pw = [...document.querySelectorAll("input[type=password]")].filter(visible);
        if (pw.length === 1 && pw[0].value) { const b = primaryButton() || findBtn(/sign in|log in/i); if (b) { realClick(b); await settle(2500); continue; } }
        banner("sign in or create your account here (type the password once, Chrome remembers it). I continue by myself after.", "wait", stallActions(["sign in on the portal (first time only)"], allDone));
        report({ ok: false, status: DRY ? "login" : "needs-you", need: ["sign in on the portal (first time only)"], done: allDone });
        if (DRY) return;
        while (loginPage()) await sleep(1000);
        continue;
      }
      if (captchaUp()) { banner("captcha: solve it and I'll continue", "wait"); if (DRY) { report({ ok: false, status: "captcha", done: allDone }); return; } while (captchaUp()) await sleep(1000); }
      if (window.top === window && embeddedForm()) { banner("the form is embedded on this page; filling it inside the frame…"); await sleep(4000); if (embeddedForm()) { for (let k = 0; k < 600; k++) { await sleep(1000); if (!embeddedForm() || succeeded()) break; } continue; } }
      if (acceptCookies()) await sleep(400);
      banner(`page ${step + 1}: filling ${P.job.company} · ${P.job.role}…`);
      const { need, done } = await fill(P); allDone.push(...done);
      if (mod.fix) await mod.fix(P);
      const btn = primaryButton();
      if (!btn) { banner("can't find the Next / Submit button on this page; press it yourself and I'll keep going", "wait", stallActions(need.concat(["next button"]), allDone)); report({ ok: false, status: DRY ? "no-button" : "needs-you", need: need.concat(["next button"]), done: allDone }); if (DRY) return; await waitChange(); continue; }
      const isSubmit = /submit/i.test((btn.getAttribute("aria-label") || "") + txt(btn) + (btn.value || ""));
      if (need.length && isSubmit) { banner(`${need.length} field(s) need you before submit: ${need.join(" · ")}`, "err", stallActions(need, allDone)); report({ ok: false, status: "needs-you", need, done: allDone }); if (DRY) return; await waitChange(); continue; }
      if (isSubmit && DRY) { banner("dry run: would submit now", "ok"); report({ ok: true, status: "dry-submit", need, done: allDone, button: txt(btn) }); return; }
      const before = signature(); if (isSubmit) sessionStorage.setItem("coop-clicked", P.id);
      realClick(btn); await settle(1500);
      if (isSubmit) { for (let k = 0; k < 40; k++) { if (succeeded()) break; await sleep(500); } if (succeeded()) { banner("submitted ✓ recorded on the board", "ok"); sessionStorage.removeItem(KEY); finish({ ok: true, status: "applied", done: allDone }); return; } }
      if (signature() === before) {
        const names = complaints();
        if (names.length) { banner(`form flagged: ${names.join(", ")}; retrying…`); await fill(P); if (mod.fix) await mod.fix(P); realClick(primaryButton() || btn); await settle(1500); }
        if (signature() === before) { const n2 = complaints(); banner(`stuck on this page${n2.length ? ": " + n2.join(", ") : ""}. Fix it and press Next; I'll continue.`, "err", stallActions(n2.length ? n2 : need, allDone)); report({ ok: false, status: DRY ? "stuck" : "needs-you", need: n2.length ? n2 : need.length ? need : ["page did not advance"], done: allDone, button: txt(btn) }); if (DRY) return; await waitChange(); }
      }
    }
    banner("too many pages without a confirmation; check the tab", "err", stallActions(["no confirmation after 14 pages"], allDone)); finish({ ok: false, status: "needs-you", need: ["no confirmation after 14 pages"], done: allDone });
  }
  run().catch(e => { banner("error: " + e.message, "err", stallActions(["script error: " + e.message], [])); finish({ ok: false, status: "error", need: ["script error: " + e.message] }); });
})();
