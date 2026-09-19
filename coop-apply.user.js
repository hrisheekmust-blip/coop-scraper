// ==UserScript==
// @name         Co-op board: one-click apply
// @namespace    coop-hrisheek
// @version      2.1
// @description  Opened by the co-op board: walks any application form (Ashby, Greenhouse, Lever, LinkedIn Easy Apply, Workday, Oracle, iCIMS, SuccessFactors, Phenom, ...) page by page, fills it from the board's answers, attaches the files, submits, and reports back.
// @match        *://*/*
// @require      https://hrisheekmust-blip.github.io/coop-scraper/engine.js
// @grant        none
// @run-at       document-idle
// ==/UserScript==
(function () {
  "use strict";
  const KEY = "coop-payload";
  const hashId = (location.hash.match(/coop=([0-9a-f]{16})/) || [])[1];
  let payload = null;
  try { payload = JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch (e) {}
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

  // ---------------------------------------------------------------- banner + reporting
  let bar;
  function banner(msg, kind) {
    if (window.top !== window) { try { window.top.postMessage({ type: "coop-banner", msg, kind }, "*"); } catch (e) {} return; }
    if (!bar) { bar = document.createElement("div"); bar.id = "coop-banner"; document.documentElement.appendChild(bar); }
    bar.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;font:14px/1.4 system-ui,sans-serif;color:#fff;background:${kind === "err" ? "#b3261e" : kind === "ok" ? "#1e7d3c" : kind === "wait" ? "#b45309" : "#1d4ed8"};box-shadow:0 2px 8px rgba(0,0,0,.25)`;
    bar.textContent = "Co-op board: " + msg;
  }
  window.addEventListener("message", e => { const d = e.data || {}; if (d.type === "coop-banner" && window.top === window) banner(d.msg, d.kind); });
  const report = m => { try { const o = opener(); if (o) o.postMessage({ ...m, type: "coop-result", id: payload ? payload.id : hashId, url: location.href, at: new Date().toISOString() }, "*"); } catch (e) {} };

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
  const keepHash = url => { try { const u = new URL(url, location.href); u.hash = "coop=" + (payload ? payload.id : hashId); return u.href; } catch (e) { return url; } };

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
    const en = entryOf(el);
    if (en) { const l = en.querySelector("label, legend, .application-label, [class*='label'], [data-automation-id*='label'], span[class*='title']"); if (l && txt(l)) return txt(l); }
    if (el.placeholder) return norm(el.placeholder);
    let p = el.previousElementSibling; while (p) { if (txt(p) && txt(p).length < 200) return txt(p); p = p.previousElementSibling; }
    return "";
  }
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
      if (r.k === "need" || !r.a) { need.push(q); continue; }
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
      if (r.k === "need" || !r.a) { need.push(q); continue; }
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
      if (r.k === "need" || pick < 0) { document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); need.push(r0.k === "need" || !r.a ? q : q + " (no option matched '" + r.a + "')"); await sleep(150); continue; }
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
      if (r.k === "need") { if (!(el.value || "").trim()) need.push(q); continue; }
      if (r.k === "file" || r.a === "(leave blank)" || r.a === "") continue;
      if (el.tagName === "SELECT") {
        const o = [...el.options].find(x => txt(x).toLowerCase() === r.a.toLowerCase()) || [...el.options].find(x => txt(x).toLowerCase().includes(r.a.toLowerCase().slice(0, 12)));
        if (o) { if (el.value !== o.value) { el.value = o.value; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); fireReact(el, ["onChange"], "change"); } done.push(q + " = " + txt(o)); } else need.push(q);
        continue;
      }
      if ((el.value || "").trim() && !isDate && (el.value.trim() === r.a || /^(name|first|last|email|phone|preferred)/i.test(q))) continue;   // portal prefilled it from the account
      if (el.getAttribute("role") === "combobox" || /select__input|react-select|autocomplete|typeahead/i.test(el.className + " " + (el.parentElement && el.parentElement.className))) {
        el.focus(); setNative(el, r.a); await sleep(500);
        const opts2 = [...document.querySelectorAll("[role=option], .select__option, [role=listbox] li, [data-automation-id='promptOption']")].filter(visible);
        const opt = opts2.find(x => txt(x).toLowerCase() === r.a.toLowerCase()) || opts2.find(x => txt(x).toLowerCase().includes(r.a.toLowerCase().slice(0, 10)));
        if (opt) realClick(opt); else el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
        await sleep(200); done.push(q + " ≈ " + r.a); continue;
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
    for (const rx of order) { const b = cands.find(x => rx.test(label(x)) && !/upload|attach|add another|cancel|back|previous|withdraw|save (for )?later|save draft|dismiss|linkedin|indeed|autofill/i.test(label(x))); if (b) return b; }
    return null;
  }
  const signature = () => location.href.split("#")[0] + "|" + bodyText().replace(/\d/g, "").slice(0, 3000);
  const spinner = () => [...document.querySelectorAll("[aria-busy='true'], [class*='spinner'], [class*='Spinner'], [class*='loading']:not([class*='loaded'])")].some(visible);
  async function settle(ms = 1200) { await sleep(ms); for (let i = 0; i < 20 && spinner(); i++) await sleep(300); }
  async function waitChange() { const sig = signature(); while (signature() === sig) await sleep(1000); }

  // ---------------------------------------------------------------- portal quirks
  const findBtn = rx => [...document.querySelectorAll("a, button")].filter(visible).find(x => rx.test(txt(x) + " " + (x.getAttribute("aria-label") || "")));
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
    icims: { async pre() { const b = findBtn(/apply for this job|^apply$|apply now/i); if (b && !document.querySelector("input[type=email], input[name*='email' i]")) { realClick(b); await settle(1500); } return true; } },
    oracle: { async pre() { const b = findBtn(/^apply( now)?$|i'm interested/i); if (b && !document.querySelector("input[type=email]")) { realClick(b); await settle(1500); } return true; } },
    successfactors: { async pre() { const b = findBtn(/^apply( now)?$/i); if (b && !document.querySelector("input[type=email]")) { realClick(b); await settle(1500); } return true; } },
    ashby: { async pre() { if (!/\/application/.test(location.pathname)) { const t = [...document.querySelectorAll("a")].find(a => /\/application$/.test(a.getAttribute("href") || "")); if (t) { location.href = keepHash(t.href); return "navigating"; } } return true; } },
    greenhouse: { async pre() { const b = findBtn(/^apply( now| for this job)?$/i); if (b && !document.querySelector("#first_name, input[name='first_name'], input[type=email]")) { realClick(b); await settle(1200); } return true; } },
    lever: { async pre() { if (!/\/apply/.test(location.pathname)) { const a = document.querySelector("a[href$='/apply'], a.postings-btn"); if (a) { location.href = keepHash(a.href); return "navigating"; } } return true; } },
    other: { async pre() { if (document.querySelector("input[type=email], input[name*='email' i]")) return true; const b = findBtn(/^apply( now| for this job| to job)?$|start application|begin application/i); if (!b) return true; const o = window.open; let ext = null; window.open = u => { ext = u; return null; }; realClick(b); await settle(1500); window.open = o; if (ext) { location.href = keepHash(ext); return "navigating"; } return true; } },
  };

  // ---------------------------------------------------------------- the walk
  async function run() {
    const P = await getPayload();
    if (!P) { banner("board tab didn't answer; go back to the board and click Apply again", "err"); return; }
    for (let i = 0; i < 40 && document.readyState !== "complete"; i++) await sleep(250);
    await settle(800);
    if (succeeded() && sessionStorage.getItem("coop-clicked") === P.id) { banner("submitted ✓ recorded on the board", "ok"); report({ ok: true, status: "applied" }); sessionStorage.removeItem(KEY); return; }
    const mod = M[PORTAL] || M.other;
    banner("opening the application for " + P.job.company + "…");
    const pre = await mod.pre(P);
    if (pre === "navigating") { banner("following the apply link…"); return; }
    if (pre !== true) { banner(pre, "err"); report({ ok: false, status: "needs-you", need: [pre] }); return; }
    const allDone = [];
    for (let step = 0; step < 14; step++) {
      await settle(600);
      if (sessionStorage.getItem("coop-clicked") === P.id && succeeded()) { banner("submitted ✓ recorded on the board", "ok"); report({ ok: true, status: "applied", done: allDone }); sessionStorage.removeItem(KEY); return; }
      if (loginPage()) {
        const em = [...document.querySelectorAll("input[type=email], input[name*='email' i], input[id*='email' i]")].find(visible); if (em && !em.value) setText(em, P.profile.email);
        const pw = [...document.querySelectorAll("input[type=password]")].filter(visible);
        if (pw.length === 1 && pw[0].value) { const b = primaryButton() || findBtn(/sign in|log in/i); if (b) { realClick(b); await settle(2500); continue; } }
        banner("sign in or create your account here (type the password once, Chrome remembers it). I continue by myself after.", "wait");
        report({ ok: false, status: "needs-you", need: ["sign in on the portal (first time only)"], done: allDone });
        while (loginPage()) await sleep(1000);
        continue;
      }
      if (captchaUp()) { banner("captcha: solve it and I'll continue", "wait"); while (captchaUp()) await sleep(1000); }
      banner(`page ${step + 1}: filling ${P.job.company} · ${P.job.role}…`);
      const { need, done } = await fill(P); allDone.push(...done);
      if (mod.fix) await mod.fix(P);
      const btn = primaryButton();
      if (!btn) { banner("can't find the Next / Submit button on this page; press it yourself and I'll keep going", "wait"); report({ ok: false, status: "needs-you", need: ["next button"], done: allDone }); await waitChange(); continue; }
      const isSubmit = /submit/i.test((btn.getAttribute("aria-label") || "") + txt(btn) + (btn.value || ""));
      if (need.length && isSubmit) { banner(`${need.length} field(s) need you before submit: ${need.join(" · ")}`, "err"); report({ ok: false, status: "needs-you", need, done: allDone }); await waitChange(); continue; }
      const before = signature(); if (isSubmit) sessionStorage.setItem("coop-clicked", P.id);
      realClick(btn); await settle(1500);
      if (isSubmit) { for (let k = 0; k < 40; k++) { if (succeeded()) break; await sleep(500); } if (succeeded()) { banner("submitted ✓ recorded on the board", "ok"); report({ ok: true, status: "applied", done: allDone }); sessionStorage.removeItem(KEY); return; } }
      if (signature() === before) {
        const names = complaints();
        if (names.length) { banner(`form flagged: ${names.join(", ")}; retrying…`); await fill(P); if (mod.fix) await mod.fix(P); realClick(primaryButton() || btn); await settle(1500); }
        if (signature() === before) { const n2 = complaints(); banner(`stuck on this page${n2.length ? ": " + n2.join(", ") : ""}. Fix it and press Next; I'll continue.`, "err"); report({ ok: false, status: "needs-you", need: n2.length ? n2 : need.length ? need : ["page did not advance"], done: allDone }); await waitChange(); }
      }
    }
    banner("too many pages without a confirmation; check the tab", "err"); report({ ok: false, status: "needs-you", need: ["no confirmation after 14 pages"], done: allDone });
  }
  run().catch(e => { banner("error: " + e.message, "err"); report({ ok: false, status: "error", error: String(e) }); });
})();
