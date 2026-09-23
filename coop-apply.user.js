// ==UserScript==
// @name         Co-op board: one-click apply
// @namespace    coop-hrisheek
// @version      4.0
// @description  Opened by the co-op board: fills confirmed answers, attaches prepared files, and submits complete applications authorized from the board. The board controls which portals can be automated.
// @match        *://*/*
// @require      https://hrisheekmust-blip.github.io/coop-scraper/engine.js?v=10
// @grant        none
// @run-at       document-idle
// ==/UserScript==
(function () {
  "use strict";
  const KEY = "coop-payload";
  const BOARD = "https://hrisheekmust-blip.github.io";
  const RELAY = "https://hrisheekmust-blip.github.io/coop-scraper/relay.html";
  const b64e = x => btoa(unescape(encodeURIComponent(x))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const b64d = x => decodeURIComponent(escape(atob(x.replace(/-/g, "+").replace(/_/g, "/"))));
  const hashId = (location.hash.match(/coop=([0-9a-f]{16})/) || [])[1];
  const inlineP = new URLSearchParams(location.hash.slice(1)).get("p");
  const validPayload = p => p && p.type === "coop-payload" && /^[0-9a-f]{16}$/.test(p.id) && p.job && Array.isArray(p.files);
  // The requisition this page belongs to (portal:tenant:job). A payload is bound to one requisition
  // and is never reused on another posting, even in the same tab on the same portal.
  const pageKey = (window.CoopEngine && window.CoopEngine.reqKey) ? window.CoopEngine.reqKey(location.href) : "";
  const BIND_TTL = 6 * 3600e3;
  let payload = null;
  if (inlineP) {                                         // the relay tab hands the whole payload over in the hash
    try { const p = JSON.parse(b64d(inlineP)); if (validPayload(p) && p.id === hashId) payload = p; } catch (e) {}
    if (!payload) return; // Do not reuse an older application's answers after a bad handoff.
    if (payload.reqKey && pageKey && payload.reqKey !== pageKey) return; // the link landed on a different posting
    payload.boundHost = location.hostname; payload.boundAt = Date.now();
    try { sessionStorage.setItem(KEY, JSON.stringify(payload)); } catch (e) {}
    try { history.replaceState(null, "", location.href.split("#")[0]); } catch (e) {}
  }
  if (!payload) {
    let saved = null; try { saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch (e) {}
    const fresh = saved && Date.now() - (saved.boundAt || 0) < BIND_TTL;
    const samePosting = saved && (pageKey ? saved.reqKey === pageKey : saved.boundHost === location.hostname && !saved.reqKey);
    if (validPayload(saved) && fresh && samePosting && (!hashId || saved.id === hashId)) payload = saved;
    else if (saved) { try { sessionStorage.removeItem(KEY); } catch (e) {} }
  }
  if (!validPayload(payload)) payload = null;
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
  const result = m => ({ ...m, learnedAnswers: payload?.learnedAnswers || [], type: "coop-result", id: payload ? payload.id : hashId, company: payload?.job?.company || "", role: payload?.job?.role || "", files: (payload?.files || []).map(f => ({ name: f.name })), reqKey: payload?.reqKey || pageKey || "", batchId: payload && payload.batchId, url: location.href, at: new Date().toISOString() });
  // Durable "Submit was pressed" marker for this requisition. It lives in this portal's localStorage, so a
  // reload, a new batch, or a second tab on the same portal cannot press Submit for it again.
  const attemptKey = () => "coop-attempt:" + (payload?.reqKey || pageKey || payload?.id || hashId);
  const attempted = () => { try { return !!localStorage.getItem(attemptKey()) || sessionStorage.getItem("coop-auto-attempt") === attemptKey(); } catch (e) { return false; } };
  const markAttempt = () => { try { localStorage.setItem(attemptKey(), new Date().toISOString()); } catch (e) {} try { sessionStorage.setItem("coop-auto-attempt", attemptKey()); } catch (e) {} };
  const clearAttempt = () => { try { localStorage.removeItem(attemptKey()); } catch (e) {} try { sessionStorage.removeItem("coop-auto-attempt"); } catch (e) {} };
  const userClicked = () => { try { return sessionStorage.getItem("coop-clicked") === attemptKey(); } catch (e) { return false; } };
  const report = m => {
    window.__coopLast = m; (window.__coopReports = window.__coopReports || []).push(m);
    if (DRY || m.ok) return; // Successful results have one durable route through the relay.
    try { const o = opener(); if (o) o.postMessage(result(m), BOARD); } catch (e) {}
  };
  let reported = false;
  function finish(m, skipped = false) {
    if (reported) return;
    m = {...m,learnedAnswers:payload?.learnedAnswers||[]};
    report(m);
    if (DRY || (!m.ok && !skipped)) return; // Missing answers and errors stay on the form.
    reported = true;
    if (m.ok && !m.evidence) m = { ...m, evidence: confirmation() };
    sessionStorage.removeItem(KEY); sessionStorage.removeItem("coop-clicked"); sessionStorage.removeItem("coop-auto-attempt");
    const r = result({ ...m, need: (m.need || []).slice(0, 12), done: (m.done || []).slice(-40) });
    location.replace(RELAY + "#done=" + b64e(JSON.stringify(r)));
  }
  let resumeRequested = 0;
  const editedControls = new Set();
  const writingControls = new Set();
  function programmaticWrite(el, fn) {
    const nested=writingControls.has(el);writingControls.add(el);
    const done=()=>{if(!nested)writingControls.delete(el)};
    try {const value=fn();if(value?.finally)return value.finally(done);done();return value} catch(e){done();throw e}
  }
  function captureEdited() {
    if (!payload || !window.CoopEngine?.learnedRecord) return;
    const learned = [];
    for (const el of editedControls) {
      if (!el.isConnected || el.closest?.("#coop-banner") || /password|file|hidden/.test(el.type || "")) continue;
      let label = labelOf(el), value = el.value, type = el.type || "text", options = [];
      if (el.tagName === "BUTTON") {
        const entry=el.closest(".ashby-application-form-field-entry");
        if(!entry||!/^(yes|no)$/i.test(txt(el)))continue;
        label=txt(entry.querySelector("label"));value=txt(el);type="Boolean";options=["Yes","No"];
      } else if (el.tagName === "SELECT") { options=[...el.options].map(txt);value=[...el.selectedOptions].map(txt);type=el.multiple?"select-multiple":"select"; }
      else if (/checkbox|radio/.test(type)) {
        const fs=el.closest("fieldset,[role=group],[role=radiogroup],.application-question,.field");
        if (!fs) continue;
        const choices=[...fs.querySelectorAll("input[type=checkbox],input[type=radio]")];
        label=groupLabel(fs);options=choices.map(x=>txt(x.labels?.[0]));value=choices.filter(x=>x.checked).map(x=>txt(x.labels?.[0]));
      } else if (el.getAttribute("role") === "combobox") {
        const ctrl=el.closest("[class*='select__control'],[class*='control']");
        value=ctrl?[...ctrl.querySelectorAll("[class*='single-value'],[class*='multi-value__label']")].map(txt):[];
        if (!value.length) continue; // A typed autocomplete query is not an answer.
        type=value.length>1?"select-multiple":"text";
      }
      const record=window.CoopEngine.learnedRecord(label,value,{...payload.job,id:payload.id},type,options);
      if (record) learned.push(record);
    }
    payload.learnedAnswers=window.CoopEngine.mergeLearned(payload.learnedAnswers||[],learned);
    try { sessionStorage.setItem(KEY,JSON.stringify(payload)); } catch(e) {}
  }
  function trackEdit(e) {
    if (!e.isTrusted || writingControls.has(e.target) || e.target.closest?.("#coop-banner") || e.target.type === "search") return;
    if (e.target.matches?.("input,textarea,select")) editedControls.add(e.target);
    if(e.type === "click") {const b=e.target.closest?.(".ashby-application-form-field-entry button");if(b&&/^(yes|no)$/i.test(txt(b))){editedControls.delete(b);editedControls.add(b)}}
    // React Select options commit on click, then clear/blur the input.
    if (e.type === "pointerdown") {
      const active=document.activeElement;
      if (active?.getAttribute?.("role")==="combobox") editedControls.add(active);
    }
    setTimeout(captureEdited,100);
  }
  const stallActions = (need, done) => [
    { label: "Save answers & continue", fn: () => {
      captureEdited(); resumeRequested++;
      banner("answers saved; checking the form again…");
    } },
    { label: "Set aside & continue batch", fn: () => {
      captureEdited();
      // If Submit was pressed (by you or by me) the outcome is unknown, not "not applied".
      const pressed = attempted() || userClicked();
      finish({ ok:false,status:pressed?"uncertain":"needs-you",deferred:true,need:pressed?["Submit was pressed but no confirmation was seen; check the portal or your email before retrying"]:(need||["set aside by you"]),done },true);
    } }];

  // ---------------------------------------------------------------- payload handshake with the board tab
  async function getPayload() {
    if (payload) return payload;
    banner("waiting for your answers from the board tab…");
    return new Promise(resolve => {
      const onMsg = e => { const d = e.data || {}; if (e.origin === BOARD && e.source === opener() && validPayload(d) && d.id === hashId) { clearInterval(t); window.removeEventListener("message", onMsg); payload = d; try { sessionStorage.setItem(KEY, JSON.stringify(d)); } catch (x) {} resolve(d); } };
      window.addEventListener("message", onMsg);
      let n = 0; const t = setInterval(() => { const o = opener(); if (!o || n++ > 60) { clearInterval(t); window.removeEventListener("message", onMsg); resolve(null); return; } o.postMessage({ type: "coop-ready", id: hashId }, BOARD); }, 500);
    });
  }
  const keepHash = url => { try { const u = new URL(url, location.href); u.hash = "coop=" + (payload ? payload.id : hashId) + (payload ? "&p=" + b64e(JSON.stringify(payload)) : ""); return u.href; } catch (e) { return url; } };

  // ---------------------------------------------------------------- React-aware setters
  const reactProps = el => { const k = Object.keys(el).find(k => k.startsWith("__reactProps$")); return k ? el[k] : null; };
  const synth = (el, type) => ({ target: el, currentTarget: el, type, bubbles: true, nativeEvent: new Event(type, { bubbles: true }), preventDefault() {}, stopPropagation() {}, persist() {}, isDefaultPrevented: () => false, isPropagationStopped: () => false });
  function fireReact(el, names, type) { const p = reactProps(el); if (!p) return false; let hit = false; for (const n of names) if (typeof p[n] === "function") { try { p[n](synth(el, type)); hit = true; } catch (e) {} } return hit; }
  const valDesc = el => Object.getOwnPropertyDescriptor(el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value");
  function setNative(el, v) { el.focus(); valDesc(el).set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); }
  function setText(el,v) { return programmaticWrite(el,()=>writeText(el,v)); }
  function writeText(el, v) {
    el.focus();
    try { el.select && el.select(); } catch (e) {}
    if (el.value) { try { document.execCommand("delete", false); } catch (e) {} if (el.value) valDesc(el).set.call(el, ""); el.dispatchEvent(new Event("input", { bubbles: true })); }
    let ok = false; try { ok = document.execCommand("insertText", false, v); } catch (e) {}
    if (!ok || el.value !== v) setNative(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true })); el.dispatchEvent(new FocusEvent("blur", { bubbles: true })); el.blur();
  }
  function setValueVerified(el,v) { return programmaticWrite(el,()=>writeValueVerified(el,v)); }
  async function writeValueVerified(el, v) {
    setText(el, v); await sleep(80);
    let p = reactProps(el);
    if (p && "value" in p && p.value !== v) {
      el.focus(); try { el.select(); document.execCommand("delete", false); } catch (e) {} valDesc(el).set.call(el, ""); el.dispatchEvent(new Event("input", { bubbles: true })); fireReact(el, ["onInput", "onChange"], "input"); await sleep(120);
      try { document.execCommand("insertText", false, v); } catch (e) {} if (el.value !== v) valDesc(el).set.call(el, v);
      el.dispatchEvent(new Event("input", { bubbles: true })); fireReact(el, ["onInput", "onChange"], "input"); await sleep(80); fireReact(el, ["onBlur"], "blur"); el.blur();
    }
    await sleep(300);
    const opts = ownOptions(el);
    const opt = opts[matchOption(opts.map(txt), v)];
    if (opt) { realClick(opt); await sleep(200); }
    p = reactProps(el); return !p || !("value" in p) || p.value === v || el.value === v;
  }
  function setCheckedVerified(el,on) { return programmaticWrite(el,()=>writeCheckedVerified(el,on)); }
  async function writeCheckedVerified(el, on) {
    const says = () => { const q = reactProps(el); return q && typeof q.checked === "boolean" ? q.checked : el.checked; };
    if (el.checked !== on) { el.click(); await sleep(80); }
    if (says() !== on && el.labels && el.labels[0]) { realClick(el.labels[0]); await sleep(80); }
    if (says() !== on && on) { el.click(); await sleep(80); el.click(); await sleep(80); }
    if (says() !== on) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked").set.call(el, on); fireReact(el, ["onChange", "onClick"], "change"); el.dispatchEvent(new Event("change", { bubbles: true })); await sleep(80); }
    return says() === on;
  }
  function b64file(f) { const bin = atob(f.b64); const u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new File([u8], f.name, { type: f.type || "application/pdf" }); }
  function setFile(input, file) { const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files; input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true })); }
  const KEY_DOWN = el => el.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", keyCode: 40, which: 40, bubbles: true }));
  const KEY_ENTER = el => el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, which: 13, bubbles: true }));
  const KEY_ESC = el => el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, which: 27, bubbles: true }));
  const OPT = "[role=option], [class*='select__option'], [class*='Option'], [role=listbox] li, [data-automation-id='promptOption'], .oj-listbox-result";
  function closeMenus() { try { const a = document.activeElement; if (a) { KEY_ESC(a); a.blur(); } } catch (e) {} }
  // only the list this control owns: a stray menu from the previous field used to get read as this field's options
  function ownOptions(el) {
    const id = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
    const box = id ? document.getElementById(id) : null;
    const ctrl = el.closest("[class*='select__control'], [class*='select__'], [class*='control']");
    const scope = box || (ctrl && ctrl.parentElement);
    let o = scope ? [...scope.querySelectorAll(OPT)].filter(visible) : [];
    // Unowned menus are ambiguous: leave the control for manual review.
    return o.filter(x => !/^no option|^no result|^loading/i.test(txt(x)));
  }
  async function waitFor(f, ms) { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = f(); if (v && v.length) return v; await sleep(120); } return []; }
  function matchOption(labels, want) {
    const w = norm(want || "").toLowerCase();
    const hits = labels.map((label, i) => norm(label).toLowerCase() === w && w ? i : -1).filter(i => i >= 0);
    return hits.length === 1 ? hits[0] : -1;
  }
  const comboShows = (ctrl, want) => {
    if (!ctrl || !want) return false;
    const values = [...ctrl.querySelectorAll("[class*='single-value'], [class*='multi-value__label'], [class*='singleValue']")].map(txt);
    return matchOption(values, want) >= 0;
  };
  function realClick(el) {
    try { el.scrollIntoView({ block: "center" }); } catch (e) {} try { el.focus(); } catch (e) {}
    const r = el.getBoundingClientRect(); const o = { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0, buttons: 1 };
    for (const t of ["pointerover", "pointerenter", "mouseover", "pointerdown", "mousedown"]) el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, pointerType: "mouse", isPrimary: true }) : new MouseEvent(t, o));
    for (const t of ["pointerup", "mouseup"]) el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: 0 }) : new MouseEvent(t, { ...o, buttons: 0 }));
    el.click(); // One activation: dispatching a click and then calling click() submitted twice.
  }

  // ---------------------------------------------------------------- labels
  function questionText(el) {
    if (!el) return "";
    if (!el.cloneNode) return txt(el);
    const copy = el.cloneNode(true);
    for (const child of copy.querySelectorAll("input, select, textarea, button, [role=listbox], [role=option]")) child.remove();
    return norm(copy.textContent);
  }
  const entryOf = el => el.closest(".ashby-application-form-field-entry, fieldset, .file-upload, .field, .application-question, .application-field, li, .form-field, [class*='field-entry'], [class*='FieldEntry'], [class*='question'], [data-automation-id*='formField'], [data-automation-id*='FormField'], .jobs-easy-apply-form-element, .fb-dash-form-element, .iCIMS_TableRow, .oj-flex-item, .rcrtField") || el.parentElement;
  function labelOf(el) {
    // Greenhouse labels both upload inputs "Attach"; the group owns the question.
    if (el.type === "file") {
      const group = el.closest("[role=group][aria-labelledby], .file-upload");
      const ids = group?.getAttribute("aria-labelledby");
      const label = ids ? ids.split(/\s+/).map(id => questionText(document.getElementById(id))).join(" ") : "";
      if (label) return label;
      if (/^(resume|cv)$/i.test(el.id || el.name || "")) return "Resume";
      if (/^cover[_-]?letter$/i.test(el.id || el.name || "")) return "Cover letter";
    }
    if (el.labels && el.labels[0] && questionText(el.labels[0])) return questionText(el.labels[0]);
    const lb = el.getAttribute("aria-labelledby"); if (lb) { const t = lb.split(/\s+/).map(i => questionText(document.getElementById(i))).join(" "); if (t) return t; }
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));
    const en = entryOf(el);
    if (en && en.querySelectorAll("input, select, textarea").length <= 2) {   // only trust a container that holds this one field
      const l = en.querySelector("label, legend, .application-label, [class*='label']:not(input):not(select), [data-automation-id*='label'], span[class*='title']");
      if (l && questionText(l)) return questionText(l);
    }
    if (el.placeholder) return norm(el.placeholder);
    let p = el.previousElementSibling; while (p) { if (txt(p) && txt(p).length < 200) return txt(p); p = p.previousElementSibling; }
    return "";
  }
  const raw = el => { try { const en = entryOf(el); const small = en && en.querySelectorAll("input, select, textarea, button").length <= 4 ? en : null; return ((el.labels && el.labels[0] ? el.labels[0].textContent : "") + " " + (el.getAttribute && el.getAttribute("aria-label") || "") + " " + ((small || el).textContent || "").slice(0, 400)); } catch (e) { return ""; } };
  const isRequired = el => !!(el.required || (el.getAttribute && el.getAttribute("aria-required") === "true") || /[*✱]|\brequired\b|\bmandatory\b/i.test(raw(el)) && !/not required|optional/i.test(raw(el)));
  const groupLabel = fs => { const lg = fs.querySelector("legend, .application-label, [data-automation-id*='label'], label:not(:has(input))"); const t = lg ? txt(lg) : ""; if (t) return t; let p = fs.previousElementSibling; while (p) { if (txt(p)) return txt(p); p = p.previousElementSibling; } return labelOf(fs); };

  // ---------------------------------------------------------------- fill everything visible on the current page
  async function fill(P) {
    const ctx = { learnedAnswers:P.learnedAnswers||[], job: {...P.job,id:P.id}, answers: P.answers, profile: P.profile, cover: P.cover, coverText: P.coverText || "" };
    const questions = new Map();
    const A = f => {questions.set(norm(f.label),f);return window.CoopEngine.answerFor(f, ctx)};
    const need = [], done = [], seen = new Set(), asked = new Set();
    const files = { resume: P.files.find(f => /resume/i.test(f.kind)), cover: P.cover ? P.files.find(f => /cover/i.test(f.kind)) : null };
    const root = document.querySelector(".jobs-easy-apply-modal") || document.querySelector("[role=dialog] form") || document.querySelector("form") || document.body;

    // 1. checkbox / radio groups
    for (const fs of root.querySelectorAll("fieldset, [role=group], [role=radiogroup], .application-question, .field, [data-automation-id*='radioGroup'], [data-automation-id*='checkboxGroup'], .fb-dash-form-element")) {
      const opts = [...fs.querySelectorAll("input[type=checkbox], input[type=radio]")].filter(o => visible(o) || visible(o.parentElement));
      if (!opts.length) continue;
      if (opts.length < 2 && !(opts[0].type === "checkbox" && /agree|consent|acknowledg|certify|terms|privacy|confirm/i.test(txt(fs)))) continue;
      if (opts.some(o => seen.has(o))) continue;
      opts.forEach(o => seen.add(o));
      if (opts.some(o => o.checked)) { done.push("existing selection: " + groupLabel(fs)); continue; }
      const q = groupLabel(fs); const labels = opts.map(o => txt(o.labels && o.labels[0]) || txt(o.parentElement));
      const r = A({ label: q, options: labels, type: opts[0].type });
      if (r.k === "need" || !r.a) { if (isRequired(fs)) need.push(q); else done.push("left blank (optional): " + q); continue; }
      const want = r.values || [r.a];
      for (let i = 0; i < opts.length; i++) { const o = opts[i]; const on = want.some(w => matchOption([labels[i]], w) === 0);
        const ok = await setCheckedVerified(o, on); if (!ok && on) need.push(q + " (" + labels[i] + ")"); }
      done.push(q + " = " + r.a);
    }
    // 2. Ashby yes/no button pairs
    for (const en of root.querySelectorAll(".ashby-application-form-field-entry")) {
      const btns = [...en.querySelectorAll("button")].filter(b => /^(yes|no)$/i.test(txt(b)));
      if (btns.length !== 2) continue;
      const q = txt(en.querySelector("label")); const r = A({ label: q, options: ["Yes", "No"], type: "Boolean" });
      if (r.k === "need" || !r.a) { if (isRequired(en)) need.push(q); else done.push("left blank (optional): " + q); continue; }
      const b = btns[matchOption(btns.map(txt), r.a)]; if (!b) { need.push(q); continue; } realClick(b); done.push(q + " = " + txt(b));
    }
    // 3. custom dropdown buttons (Workday, Oracle, LinkedIn)
    for (const btn of root.querySelectorAll("button[aria-haspopup='listbox'], button[aria-haspopup='true'][data-automation-id], [role=combobox]:not(input):not(select), [data-automation-id*='selectinput'], [data-automation-id='dropdownButton']")) {
      if (!visible(btn) || seen.has(btn)) continue; seen.add(btn);
      const q = labelOf(btn); if (!q) continue;
      const cur = txt(btn); if (cur && !/select|choose|--|^$/i.test(cur) && cur !== q) continue;   // already set
      const r0 = A({ label: q, options: [], type: "select" });
      realClick(btn); await sleep(450);
      const opts = ownOptions(btn);
      const labels = opts.map(txt); const r = A({ label: q, options: labels, type: "select" });
      const pick = matchOption(labels, r.a);
      if (r.k === "need" || pick < 0) { document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); if (isRequired(btn) || (r.a && r.k !== "need")) need.push(r0.k === "need" || !r.a ? q : q + " (no option matched '" + r.a + "')"); else done.push("left blank (optional): " + q); await sleep(150); continue; }
      realClick(opts[pick]); await sleep(250); done.push(q + " = " + labels[pick]);
    }
    // 4. text-like inputs, textareas, native selects, comboboxes, files
    for (const el of root.querySelectorAll("input, textarea, select")) {
      if (seen.has(el)) continue;
      const type = (el.getAttribute("type") || el.tagName).toLowerCase();
      if (["checkbox", "radio"].includes(type)) {
        if ((visible(el) || visible(el.parentElement)) && isRequired(el) && !el.checked) need.push(labelOf(el) || "a required selection");
        continue;
      }
      if (["hidden", "submit", "button", "password", "reset", "image"].includes(type)) continue;
      if (type === "file") {
        const around = txt(entryOf(el)); const q = labelOf(el) || around.slice(0, 80);
        if (/autofill|auto-fill|parse|prefill/i.test(around) && !/^resume/i.test(q)) continue;
        const isCover = /cover/i.test(q + around);
        const f = isCover ? files.cover : /\b(resume|cv)\b/i.test(q) ? files.resume : null;
        if (isCover && !P.cover) { done.push("cover letter skipped"); continue; }
        if (el.files && el.files.length) continue;
        if (f) { setFile(el, b64file(f)); done.push(q + " = " + f.name); await sleep(900); } else if (/required|\*/.test(around)) need.push(q || "file upload");
        continue;
      }
      if (!visible(el) || el.disabled || el.readOnly || el.closest("[aria-hidden=\"true\"]")) continue;
      const q = labelOf(el);
      if (!q) { if (isRequired(el) && !(el.value || "").trim()) need.push("a required field I couldn't read the question for"); continue; }
      if (asked.has(q + "|" + type)) { if (isRequired(el) && !(el.value || "").trim()) need.push(q + " (a second field with the same question — I won't guess)"); continue; }
      asked.add(q + "|" + type);
      const opts = el.tagName === "SELECT" ? [...el.options].map(o => txt(o)).filter(o => o && !/^(select|choose|--|please)/i.test(o)) : [];
      const isDate = type === "date" || /pick date|mm\/dd|yyyy|mm\/yyyy/i.test(el.placeholder || "") || /date/i.test(el.getAttribute("data-automation-id") || "");
      const r = A({ label: q, options: opts, type: isDate ? "date" : type, multiple:el.multiple });
      const isCombo = el.getAttribute("role") === "combobox" || /select__input|react-select|autocomplete|typeahead/i.test(el.className + " " + (el.parentElement && el.parentElement.className));
      if (r.k === "need" && !isCombo) { if (!(el.value || "").trim()) { if (isRequired(el)) need.push(q); else done.push("left blank (optional): " + q); } continue; }
      if (["file","auto","skip"].includes(r.k) || (r.a === "" && !isCombo)) continue;
      if (el.tagName === "SELECT") {
        const wanted = r.values || [r.a], choices = [...el.options];
        const picks = wanted.map(w => matchOption(choices.map(txt), w));
        if (picks.some(i => i < 0) || (!el.multiple && picks.length !== 1)) { need.push(q + " (no exact option match)"); continue; }
        for (let i = 0; i < choices.length; i++) choices[i].selected = picks.includes(i);
        el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); fireReact(el,["onChange"],"change");
        done.push(q + " = " + r.a); continue;
      }
      if ((el.value || "").trim() && !isDate && (el.value.trim() === r.a || /^(name|first|last|email|phone|preferred)/i.test(q))) continue;   // portal prefilled it from the account
      if (isCombo) {
        const ctrl = el.closest("[class*='select__control'], [class*='control'], [class*='Control']") || el.parentElement.parentElement;
        const selected = [...ctrl.querySelectorAll("[class*='single-value'], [class*='multi-value__label']")].map(txt).filter(Boolean);
        if (selected.length) { done.push(q + " = " + selected.join(" + ")); continue; }   // the portal already set it
        closeMenus(); await sleep(150);
        realClick(ctrl); el.focus(); KEY_DOWN(el);                            // the list opens from the control, not from the input
        // Async city/school controls have no choices until a query is typed.
        let opts = await waitFor(() => ownOptions(el), 800), labels = opts.map(txt);
        let r2 = labels.length ? A({ label: q, options: labels, type: "select" }) : r;
        let pick = r2.k === "need" || (r2.values || []).length > 1 ? -1 : matchOption(labels, r2.a);
        if (pick < 0 && r.a && r.k !== "need" && (labels.length === 0 || labels.length > 12)) {
          setNative(el, norm(r.a.split(/[,(]/)[0]));
          fireReact(el, ["onChange"], "change");
          await sleep(500); KEY_DOWN(el);
          opts = await waitFor(() => ownOptions(el), 3500); labels = opts.map(txt);
          r2 = labels.length ? A({ label: q, options: labels, type: "select" }) : r;
          pick = r2.k === "need" || (r2.values || []).length > 1 ? -1 : matchOption(labels, r2.a);
        }
        if (pick < 0) {                                                        // nothing fits: leave it clean and say so, never park a half-typed answer in the box
          if (el.value) setNative(el, ""); KEY_ESC(el); el.blur(); closeMenus();
          if (isRequired(el)) need.push(q + (labels.length ? " (none of the options fit)" : " (couldn't open the list)")); else done.push("left blank (optional): " + q);
          await sleep(120); continue;
        }
        realClick(opts[pick]); await sleep(350);
        // Do not fall back to pressing Enter on a possibly different option.
        let committed = comboShows(ctrl, labels[pick]);
        // Phone selectors display only +1 after choosing a country. Reopen and
        // verify the full selected option instead of equating countries by code.
        if (!committed) {
          realClick(ctrl); el.focus(); KEY_DOWN(el);
          const check = await waitFor(() => ownOptions(el), 1200);
          const selectedOption = check[matchOption(check.map(txt), labels[pick])];
          committed = selectedOption?.getAttribute("aria-selected") === "true";
        }
        if (committed) done.push(q + " = " + labels[pick]);
        else { if (el.value) setNative(el, ""); if (isRequired(el)) need.push(q + " (the dropdown wouldn't take the answer)"); }
        KEY_ESC(el); el.blur(); continue;
      }
      if (isDate) {
        // The engine only supplies a complete, explicitly saved ISO date.
        if (type !== "date") { need.push(q + " (choose the date in the calendar)"); continue; }
        setText(el, r.a); if (el.value !== r.a) need.push(q); else done.push(q + " = " + r.a); continue;
      }
      const ok = await setValueVerified(el, r.a); done.push(q + " = " + r.a.slice(0, 40)); if (!ok) need.push(q);
    }
    const coords = [...root.querySelectorAll("input[name=latitude], input[name=longitude]")];
    if (coords.length && coords.some(el => !el.value?.trim())) need.push("Select your location from the location suggestions to complete its coordinates");
    captureEdited();
    return { need, done, questions:[...questions.values()].filter(f=>need.some(n=>n===f.label||n.startsWith(f.label+" ("))) };
  }

  // ---------------------------------------------------------------- page-walking helpers
  const complaints = () => [...document.querySelectorAll("li, p, span, div, label")].filter(visible).map(txt).filter(t => t.length < 160 && /^missing entry for required field:|is required$|required field|please (complete|enter|select|fill)|this field is required|cannot be blank|must be/i.test(t)).map(t => t.replace(/^missing entry for required field:\s*/i, "").replace(/\s*is required$/i, "").trim()).filter((t, i, a) => t && a.indexOf(t) === i).slice(0, 12);
  const captchaUp = () => [...document.querySelectorAll("iframe[src*='hcaptcha'], iframe[src*='recaptcha'], iframe[src*='turnstile']")].some(f => visible(f) && f.getBoundingClientRect().height > 100);
  const bodyText = () => (document.body.innerText || "").slice(0, 20000);
  const SUCCESS_RX = /thank you for applying|thanks for applying|(?:your )?application (?:has been |was |is |successfully )?(?:submitted|received)|we(?:'ve| have) received your application|your application was sent/i;
  const confirmation = () => { const m = bodyText().match(new RegExp(".{0,80}(?:" + SUCCESS_RX.source + ").{0,120}", "i")); return { url: location.href, text: m ? norm(m[0]) : "", at: new Date().toISOString() }; };
  const succeeded = () => /thank you for applying|thanks for applying|(?:your )?application (?:has been |was |is |successfully )?(?:submitted|received)|we(?:'ve| have) received your application|your application was sent/i.test(bodyText()) || /\/(?:thank-you|thanks|confirmation|application-submitted)(?:\/|$)/i.test(location.pathname);
  const loginPage = () => [...document.querySelectorAll("input[type=password]")].some(visible);
  function primaryButton() {
    const cands = [...document.querySelectorAll("button, input[type=submit], a[role=button], [role=button]")].filter(visible).filter(b => !b.disabled && b.getAttribute("aria-disabled") !== "true");
    const label = b => norm((b.getAttribute("aria-label") || "") + " " + (b.value || "") + " " + txt(b) + " " + (b.getAttribute("data-automation-id") || ""));
    const order = [/submit application|submit my application|^submit$|\bsubmit\b(?!.*(resume|another))/i, /review (your )?application|review and submit|^review$/i, /save and continue|continue to next|^continue$|^next\b|next step|proceed|save & continue|bottom-navigation-next-button|pageFooterNextButton/i, /^apply( now)?$|easy apply|apply for this job|start application|begin application|apply to job|utilityButtonApply|applyButton/i];
    const links = [...document.querySelectorAll("a[href]")].filter(visible);
    for (const rx of order) { const pool = rx === order[order.length - 1] ? cands.concat(links) : cands; const b = pool.find(x => rx.test(label(x)) && !/upload|attach|add another|cancel|back|previous|withdraw|save (for )?later|save draft|dismiss|linkedin|indeed|autofill|alert/i.test(label(x))); if (b) return b; }
    return null;
  }
  const signature = () => location.href.split("#")[0] + "|" + bodyText().replace(/\d/g, "").slice(0, 3000);
  const spinner = () => [...document.querySelectorAll("[aria-busy='true'], [class*='spinner'], [class*='Spinner'], [class*='loading']:not([class*='loaded'])")].some(visible);
  async function settle(ms = 1200) { await sleep(ms); for (let i = 0; i < 20 && spinner(); i++) await sleep(300); }
  async function waitChange() { const sig = signature(), revision=resumeRequested; while (signature() === sig && revision===resumeRequested && !reported) await sleep(500); }

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
    for (const event of ["input","change","pointerdown","click"]) document.addEventListener(event,trackEdit,true);
    const submissionKey = attemptKey();
    if (window.CoopEngine?.VERSION !== 10) { banner("update the apply script from board Settings before continuing", "err"); report({ok:false,status:"needs-you",need:["answer engine update required"]}); return; }
    for (let i = 0; i < 40 && document.readyState !== "complete"; i++) await sleep(250);
    await settle(800);
    if (succeeded() && sessionStorage.getItem("coop-clicked") === submissionKey) { banner("submitted ✓ recorded on the board", "ok"); finish({ ok: true, status: "applied" }); return; }
    // After Submit: wait for the portal's confirmation. If it is slow, keep watching (a late confirmation is
    // still recorded) and never press Submit again; you decide what happened, or it is recorded as uncertain.
    async function awaitSubmission(done, ms = 60000) {
      for (let i = 0; i < ms / 1000; i++) {
        if (reported) return;
        if (succeeded()) { finish({ ok: true, status: "applied", done }); return; }
        await sleep(1000);
      }
      const flagged = complaints();
      const why = captchaUp() ? "Complete the captcha on this page" : flagged.length ? "The form flagged: " + flagged.join(", ") : "No submission confirmation yet";
      let decided = "";
      const decide = v => () => { decided = v; };
      banner(why + ". I will not press Submit again.", "wait", [
        { label: "It went through", fn: decide("yes") },
        { label: "Not submitted: I'll fix it and submit", fn: decide("manual") },
        { label: "Record as uncertain & continue", fn: decide("uncertain") }]);
      report({ ok: false, status: "uncertain", need: [why], done });
      if (DRY) return;
      let waited = 0;
      while (!reported) {
        if (succeeded()) { finish({ ok: true, status: "applied", done }); return; }
        if (decided === "yes") { finish({ ok: true, status: "applied", done, evidence: { ...confirmation(), text: "confirmed by you in the form tab" } }); return; }
        if (decided === "manual") {
          decided = "watching";
          banner("Fix the form and press Submit yourself. I'll record it when the confirmation appears.", "wait", [{ label: "Record as uncertain & continue", fn: decide("uncertain") }]);
        }
        if (decided === "uncertain" || (decided !== "watching" && waited++ > 20 * 60)) {
          finish({ ok: false, status: "uncertain", deferred: true, need: [why + "; check the portal or your email before retrying"], done }, true); return;
        }
        await sleep(1000);
      }
    }
    // A reload, a relaunch, or a second batch must never submit this requisition twice.
    if (attempted() && !P.allowResubmit) {
      banner("Submit was already pressed for this posting; checking for the confirmation. I will not press Submit again.", "wait");
      await awaitSubmission([], 15000); return;
    }
    if (P.allowResubmit) clearAttempt();
    const mark = e => { if (e.isTrusted) { captureEdited(); sessionStorage.setItem("coop-clicked", submissionKey); } };
    const clickMark = e => { const b = e.target.closest?.("button, input[type=submit]"); if (b && /submit|^apply( now)?$/i.test(norm(txt(b) || b.value))) mark(e); };
    document.addEventListener("submit", mark, true); document.addEventListener("click", clickMark, true);
    const mod = M[PORTAL] || M.other;
    acceptCookies();
    banner("opening the application for " + P.job.company + "…");
    const pre = await mod.pre(P);
    if (pre === "navigating") { banner("following the apply link…"); return; }
    if (pre !== true) { banner(pre, "err", stallActions([pre], [])); finish({ ok: false, status: "needs-you", need: [pre] }); return; }
    const allDone = [];
    for (let step = 0; step < 14; step++) {
      if (reported) return;
      await settle(600);
      if (sessionStorage.getItem("coop-clicked") === submissionKey && succeeded()) { banner("submitted ✓ recorded on the board", "ok"); finish({ ok: true, status: "applied", done: allDone }); return; }
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
      if (captchaUp()) { if(P.deferMissing&&!DRY){finish({ok:false,status:"needs-you",deferred:true,need:["captcha needs your attention"],done:allDone},true);return;} banner("captcha: solve it and I'll continue", "wait"); if (DRY) { report({ ok: false, status: "captcha", done: allDone }); return; } while (captchaUp()) await sleep(1000); }
      if (window.top === window && embeddedForm()) { banner("the form is embedded on this page; filling it inside the frame…"); await sleep(4000); if (embeddedForm()) { for (let k = 0; k < 600; k++) { await sleep(1000); if (!embeddedForm() || succeeded()) break; } continue; } }
      if (acceptCookies()) await sleep(400);
      banner(`page ${step + 1}: filling ${P.job.company} · ${P.job.role}…`);
      const { need, done, questions } = await fill(P); allDone.push(...done);
      if (mod.fix) await mod.fix(P);
      const btn = primaryButton();
      if (!btn) { banner("can't find the Next / Submit button on this page; press it yourself and I'll keep going", "wait", stallActions(need.concat(["next button"]), allDone)); report({ ok: false, status: DRY ? "no-button" : "needs-you", need: need.concat(["next button"]), done: allDone }); if (DRY) return; await waitChange(); continue; }
      // Only explicit forward-navigation buttons may be clicked automatically.
      const buttonLabel = norm(btn.getAttribute("aria-label") || txt(btn) || btn.value || "");
      const isSubmit = !/^(next( step)?|continue|save (and|&) continue|continue to next( step)?)$/i.test(buttonLabel);
      if (isSubmit) {                                                        // a resume box that stayed empty means something went wrong: stop, don't send
        const fileBoxes = [...document.querySelectorAll("input[type=file]")].filter(f => visible(f) || visible(f.parentElement));
        const resumeBox = fileBoxes.find(f => /resume|cv/i.test(labelOf(f) + " " + txt(entryOf(f))));
        if (resumeBox && !(resumeBox.files && resumeBox.files.length) && !/\.pdf|\.doc/i.test(txt(entryOf(resumeBox)))) need.push("the resume didn't attach");
      }
      if (need.length && P.deferMissing && !DRY) { finish({ok:false,status:"needs-you",deferred:true,need,questions,done:allDone},true);return; }
      if (need.length) { banner(`${need.length} field(s) need your review: ${need.join(" · ")}`, "err", stallActions(need, allDone)); report({ ok: false, status: "needs-you", need, questions, done: allDone }); if (DRY) return; await waitChange(); continue; }
      if (isSubmit && P.autoSubmit === true) {
        if (!/^(submit( (my|your))? application|submit|send application|apply( now)?)$/i.test(buttonLabel) || !onForm()) {
          const reasons=["Review this final action: " + buttonLabel];
          banner(reasons[0],"wait",stallActions(reasons,allDone));
          report({ok:false,status:"needs-you",need:reasons,done:allDone});return;
        }
        if (succeeded()) { report({ok:false,status:"needs-you",need:["This page already contains confirmation text; check it before submitting"]});return; }
        if (DRY) { report({ok:false,status:"dry-submit",need:[],done:allDone});return; }
        if (attempted() || userClicked()) { banner("Submit was already pressed here; checking for the confirmation instead of pressing it again", "wait"); await awaitSubmission(allDone, 5000); return; }
        markAttempt();
        sessionStorage.setItem("coop-clicked", submissionKey);
        banner("submitting your application…");
        realClick(btn);
        await awaitSubmission(allDone); return;
      }
      if (isSubmit) {
        banner("Review every answer and attachment, then submit using the site's button. I will not submit automatically.", "wait", stallActions([], allDone));
        report({ok:false,status:"needs-you",need:["final review and manual submission"],done:allDone});
        if (DRY) return;

        while (!reported) {
          await sleep(1000);
          if (sessionStorage.getItem("coop-clicked") === submissionKey && succeeded()) {
            document.removeEventListener("submit", mark, true); document.removeEventListener("click", clickMark, true);
            finish({ok:true,status:"applied",done:allDone}); return;
          }
        }
        return;
      }
      const before = signature();
      realClick(btn); await settle(1500);
      if (signature() === before) {
        const names = complaints();
        if (names.length) { banner(`form flagged: ${names.join(", ")}; please review it`, "err"); }
        if (signature() === before) { const n2 = complaints(); banner(`stuck on this page${n2.length ? ": " + n2.join(", ") : ""}. Fix it and press Next; I'll continue.`, "err", stallActions(n2.length ? n2 : need, allDone)); report({ ok: false, status: DRY ? "stuck" : "needs-you", need: n2.length ? n2 : need.length ? need : ["page did not advance"], done: allDone, button: txt(btn) }); if (DRY) return; await waitChange(); }
      }
    }
    banner("too many pages without a confirmation; check the tab", "err", stallActions(["no confirmation after 14 pages"], allDone)); finish({ ok: false, status: "needs-you", need: ["no confirmation after 14 pages"], done: allDone });
  }
  run().catch(e => { banner("error: " + e.message, "err", stallActions(["script error: " + e.message], [])); finish({ ok: false, status: "error", need: ["script error: " + e.message] }); });
})();
