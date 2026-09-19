// ==UserScript==
// @name         Co-op board: one-click apply
// @namespace    coop-hrisheek
// @version      1.3
// @description  When the co-op board opens an Ashby / Greenhouse / Lever form, fill it from the board's answers, attach the files, submit, and report back.
// @match        https://jobs.ashbyhq.com/*
// @match        https://boards.greenhouse.io/*
// @match        https://job-boards.greenhouse.io/*
// @match        https://jobs.lever.co/*
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
  if (!hashId && !payload) return;                       // not opened by the board
  if (hashId && payload && payload.id !== hashId) payload = null;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || "").replace(/\s+/g, " ").replace(/[✱*]/g, "").trim();
  const txt = el => norm(el ? el.textContent : "");
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));

  // ---------------------------------------------------------------- banner
  let bar;
  function banner(msg, kind) {
    if (!bar) { bar = document.createElement("div"); bar.id = "coop-banner"; document.documentElement.appendChild(bar); }
    bar.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;font:14px/1.4 system-ui,sans-serif;color:#fff;background:${kind === "err" ? "#b3261e" : kind === "ok" ? "#1e7d3c" : "#1d4ed8"};box-shadow:0 2px 8px rgba(0,0,0,.25)`;
    bar.textContent = "Co-op board: " + msg;
  }
  const report = m => { try { if (window.opener) window.opener.postMessage({ ...m, type: "coop-result", id: payload ? payload.id : hashId, url: location.href, at: new Date().toISOString() }, "*"); } catch (e) {} };

  // ---------------------------------------------------------------- handshake with the board tab
  async function getPayload() {
    if (payload) return payload;
    banner("waiting for your answers from the board tab…");
    return new Promise(resolve => {
      const onMsg = e => { const d = e.data || {}; if (d.type === "coop-payload" && d.id === hashId) { window.removeEventListener("message", onMsg); try { sessionStorage.setItem(KEY, JSON.stringify(d)); } catch (x) {} resolve(d); } };
      window.addEventListener("message", onMsg);
      let n = 0; const t = setInterval(() => { if (!window.opener || n++ > 60) { clearInterval(t); resolve(null); return; } window.opener.postMessage({ type: "coop-ready", id: hashId }, "*"); }, 500);
    });
  }

  // ---------------------------------------------------------------- DOM helpers
  function setNative(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, "value");
    el.focus(); if (d && d.set) d.set.call(el, v); else el.value = v;
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); el.blur();
  }
  function setText(el, v) {
    el.focus();
    try { el.select && el.select(); if (el.setSelectionRange) el.setSelectionRange(0, (el.value || "").length); } catch (e) {}
    let ok = false;
    try { ok = document.execCommand("insertText", false, v); } catch (e) { ok = false; }
    if (!ok || el.value !== v) setNative(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new FocusEvent("blur", { bubbles: true })); el.blur();
  }
  function setNativeChecked(el, on) { const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked"); d.set.call(el, on); el.dispatchEvent(new Event("click", { bubbles: true })); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); }
  function b64file(f) { const bin = atob(f.b64); const u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new File([u8], f.name, { type: f.type || "application/pdf" }); }
  function setFile(input, file) { const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files; input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true })); }
  const entryOf = el => el.closest(".ashby-application-form-field-entry, fieldset, .field, .application-question, .application-field, li, .form-field, [class*='field-entry'], [class*='FieldEntry'], [class*='question']") || el.parentElement;
  function labelOf(el) {
    if (el.labels && el.labels[0] && txt(el.labels[0])) return txt(el.labels[0]);
    const lb = el.getAttribute("aria-labelledby"); if (lb) { const t = lb.split(/\s+/).map(i => txt(document.getElementById(i))).join(" "); if (t) return t; }
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));
    const en = entryOf(el);
    if (en) { const l = en.querySelector("label, legend, .application-label, [class*='label']"); if (l && txt(l)) return txt(l); }
    let p = el.previousElementSibling; while (p) { if (txt(p) && txt(p).length < 200) return txt(p); p = p.previousElementSibling; }
    return "";
  }
  const groupLabel = fs => { const lg = fs.querySelector("legend, .application-label, label:not(:has(input))"); const t = lg ? txt(lg) : ""; if (t) return t; let p = fs.previousElementSibling; while (p) { if (txt(p)) return txt(p); p = p.previousElementSibling; } return labelOf(fs); };

  // ---------------------------------------------------------------- fill
  async function fill(P) {
    const ctx = { job: P.job, answers: P.answers, profile: P.profile, cover: P.cover };
    const A = f => window.CoopEngine.answerFor(f, ctx);
    const need = [], done = [], seen = new Set(), filled = [];
    const files = { resume: P.files.find(f => /resume/i.test(f.kind)), cover: P.cover ? P.files.find(f => /cover/i.test(f.kind)) : null };

    // 1. checkbox / radio groups
    for (const fs of document.querySelectorAll("fieldset, [role=group], [role=radiogroup], .application-question, .field")) {
      const opts = [...fs.querySelectorAll("input[type=checkbox], input[type=radio]")].filter(visible);
      if (opts.length < 2 && !(opts.length === 1 && opts[0].type === "checkbox" && /agree|consent|acknowledg|certify/i.test(txt(fs)))) continue;
      if (opts.some(o => seen.has(o))) continue;
      opts.forEach(o => seen.add(o));
      const q = groupLabel(fs); const labels = opts.map(o => txt(o.labels && o.labels[0]) || txt(o.parentElement));
      const r = A({ label: q, options: labels, type: opts[0].type });
      if (r.k === "need" || !r.a) { need.push(q); continue; }
      const want = r.a.split(/\s*\+\s*/).map(norm);
      for (let i = 0; i < opts.length; i++) { const o = opts[i]; const on = want.some(w => w && (labels[i] === w || labels[i].toLowerCase().includes(w.toLowerCase()) || w.toLowerCase().includes(labels[i].toLowerCase())));
        if (on !== o.checked) { o.click(); await sleep(60); if (on !== o.checked && o.labels && o.labels[0]) { o.labels[0].click(); await sleep(60); } if (on !== o.checked) { setNativeChecked(o, on); } } }
      done.push(q + " = " + r.a);
    }
    // 2. Ashby yes/no button pairs
    for (const en of document.querySelectorAll(".ashby-application-form-field-entry")) {
      const btns = [...en.querySelectorAll("button")].filter(b => /^(yes|no)$/i.test(txt(b)));
      if (btns.length !== 2) continue;
      const q = txt(en.querySelector("label")); const r = A({ label: q, options: ["Yes", "No"], type: "Boolean" });
      if (r.k === "need" || !r.a) { need.push(q); continue; }
      const b = btns.find(x => txt(x).toLowerCase() === r.a.toLowerCase().slice(0, txt(x).length)) || btns[0]; b.click(); done.push(q + " = " + txt(b));
    }
    // 3. text-like inputs, textareas, native selects, comboboxes
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (!visible(el) && el.type !== "file") continue;
      if (seen.has(el)) continue;
      const type = (el.getAttribute("type") || el.tagName).toLowerCase();
      if (["checkbox", "radio", "hidden", "submit", "button", "search"].includes(type)) continue;
      if (type === "file") {
        if (!visible(el) && !entryOf(el)) continue;
        const q = labelOf(el) || txt(entryOf(el)).slice(0, 60);
        if (/autofill|upload your resume here to autofill|resume or cv to autofill/i.test(txt(entryOf(el))) && !/^resume/i.test(q)) continue;   // Ashby's "autofill" helper box
        const isCover = /cover/i.test(q);
        const f = isCover ? files.cover : /resume|cv|attach|upload/i.test(q) ? files.resume : null;
        if (isCover && !P.cover) { done.push(q + " = skipped"); continue; }
        if (f) { setFile(el, b64file(f)); done.push(q + " = " + f.name); await sleep(600); } else need.push(q || "file");
        continue;
      }
      const q = labelOf(el); if (!q) continue;
      const opts = el.tagName === "SELECT" ? [...el.options].map(o => txt(o)).filter(o => o && !/^(select|choose|--)/i.test(o)) : [];
      const r = A({ label: q, options: opts, type: type === "date" || /pick date|mm\/dd|yyyy/i.test(el.placeholder || "") ? "date" : type });
      if (r.k === "need" || r.a === "" && r.k !== "" ) { need.push(q); continue; }
      if (r.a === "(leave blank)" || r.a === "") continue;
      if (el.tagName === "SELECT") {
        const o = [...el.options].find(x => txt(x).toLowerCase() === r.a.toLowerCase()) || [...el.options].find(x => txt(x).toLowerCase().includes(r.a.toLowerCase().slice(0, 12)));
        if (o) { el.value = o.value; el.dispatchEvent(new Event("change", { bubbles: true })); done.push(q + " = " + txt(o)); } else need.push(q);
        continue;
      }
      if (el.getAttribute("role") === "combobox" || /select__input|react-select/i.test(el.className + " " + (el.parentElement && el.parentElement.className))) {
        el.focus(); setNative(el, r.a); await sleep(500);
        const opt = [...document.querySelectorAll("[role=option], .select__option")].find(x => txt(x).toLowerCase() === r.a.toLowerCase()) || [...document.querySelectorAll("[role=option], .select__option")].find(x => txt(x).toLowerCase().includes(r.a.toLowerCase().slice(0, 10)));
        if (opt) opt.click(); else el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
        await sleep(200); done.push(q + " ≈ " + r.a); continue;
      }
      if (type === "date") { setNative(el, "2027-12-01"); done.push(q + " = 2027-12-01"); continue; }
      if (/pick date|mm\/dd|date/i.test(el.placeholder || "") && /graduat|date/i.test(q)) {
        el.focus(); setNative(el, "12/01/2027"); el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true })); await sleep(300); document.body.click();
        if (!el.value) need.push(q + " (pick December 2027 in the calendar)"); else done.push(q + " = " + el.value); continue;
      }
      setText(el, r.a); done.push(q + " = " + r.a.slice(0, 40)); filled.push({ el, v: r.a });
    }
    await sleep(400);
    for (const { el, v } of filled) { if (el.value !== v) { setNative(el, v); await sleep(50); if (el.value !== v) setText(el, v); } }
    return { need, done, filled };
  }
  // Ashby / Greenhouse list the offending fields after a rejected submit: refill just those and try again
  function complaints() {
    return [...document.querySelectorAll("li, p, span, div")].filter(visible).map(txt).filter(t => /^missing entry for required field:|is required$|required field/i.test(t)).map(t => t.replace(/^missing entry for required field:\s*/i, "").replace(/\s*is required$/i, "").trim()).filter((t, i, a) => t && a.indexOf(t) === i);
  }
  async function refill(P, names) {
    const ctx = { job: P.job, answers: P.answers, profile: P.profile, cover: P.cover };
    const A = f => window.CoopEngine.answerFor(f, ctx);
    const left = [];
    for (const name of names) {
      const n = name.toLowerCase();
      let hit = false;
      for (const el of document.querySelectorAll("input, textarea, select")) {
        if (el.type === "file" || el.type === "hidden") continue;
        const q = el.type === "checkbox" || el.type === "radio" ? "" : labelOf(el).toLowerCase();
        if (q && (q === n || q.startsWith(n) || n.startsWith(q))) { const r = A({ label: labelOf(el), options: [], type: el.type }); if (r.a && r.k !== "need") { setNative(el, ""); await sleep(30); setText(el, r.a); hit = true; } break; }
      }
      if (!hit) for (const fs of document.querySelectorAll("fieldset, [role=group], [role=radiogroup]")) {
        const q = groupLabel(fs).toLowerCase(); if (!(q === n || q.startsWith(n) || n.startsWith(q))) continue;
        const opts = [...fs.querySelectorAll("input[type=checkbox], input[type=radio]")]; const labels = opts.map(o => txt(o.labels && o.labels[0]) || txt(o.parentElement));
        const r = A({ label: groupLabel(fs), options: labels, type: opts[0] && opts[0].type }); if (!r.a || r.k === "need") break;
        const want = r.a.split(/\s*\+\s*/).map(norm);
        for (let i = 0; i < opts.length; i++) { const on = want.some(w => w && (labels[i] === w || labels[i].toLowerCase().includes(w.toLowerCase()))); if (on && !opts[i].checked) { opts[i].click(); await sleep(60); if (!opts[i].checked) setNativeChecked(opts[i], true); } }
        hit = true; break;
      }
      if (!hit) left.push(name);
    }
    return left;
  }

  // ---------------------------------------------------------------- submit + watch
  function realClick(el) {
    el.scrollIntoView({ block: "center" }); el.focus();
    const r = el.getBoundingClientRect(); const o = { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0, buttons: 1 };
    for (const t of ["pointerover", "pointerenter", "mouseover", "pointerdown", "mousedown"]) el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, pointerType: "mouse", isPrimary: true }) : new MouseEvent(t, o));
    for (const t of ["pointerup", "mouseup"]) el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: 0 }) : new MouseEvent(t, { ...o, buttons: 0 }));
    el.dispatchEvent(new MouseEvent("click", { ...o, buttons: 0 })); try { el.click(); } catch (e) {}
  }
  function forceSubmit(btn) { const f = btn && btn.closest("form") || document.querySelector("form"); if (!f) return false; try { if (f.requestSubmit) f.requestSubmit(btn && btn.form === f ? btn : undefined); else f.submit(); return true; } catch (e) { try { f.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); return true; } catch (x) { return false; } } }
  const formGone = () => !document.querySelector("form input[type=email], form input[name=email], #email") && !submitButton();
  function submitButton() {
    return [...document.querySelectorAll("button, input[type=submit]")].find(b => visible(b) && /submit application|submit/i.test(b.value || txt(b)) && !/upload/i.test(txt(b)));
  }
  const busy = b => !b || b.disabled || b.getAttribute("aria-disabled") === "true" || b.getAttribute("aria-busy") === "true" || /uploading|submitting|loading/i.test(txt(b)) || !!document.querySelector("[class*='uploading'], [class*='Uploading'], [aria-busy='true']");
  async function waitReady(ms) { const t0 = Date.now(); while (Date.now() - t0 < ms) { const b = submitButton(); if (b && !busy(b)) return b; banner("waiting for the form to finish uploading…"); await sleep(400); } return submitButton(); }
  const captchaUp = () => [...document.querySelectorAll("iframe[src*='hcaptcha'], iframe[src*='recaptcha'], iframe[src*='turnstile']")].some(f => visible(f) && f.getBoundingClientRect().height > 100);
  const succeeded = () => /thank you|thanks for (applying|your application)|application (has been |was |is )?(submitted|received|complete)|we('ve| have) received your application|successfully (submitted|applied)|application submitted|you('ve| have) applied|we will be in touch|we'll be in touch/i.test(document.body.innerText) || /thanks|confirmation|success|submitted/i.test(location.pathname) || (window.__coopClicked && formGone());

  async function run() {
    const P = await getPayload();
    if (!P) { banner("board tab didn't answer; go back to the board and click Apply again", "err"); return; }
    for (let i = 0; i < 60 && !document.querySelector("input[type=email], input[name=email], #email, input[type=text]"); i++) await sleep(250);
    banner("filling " + P.job.company + " · " + P.job.role + "…");
    await sleep(300);
    const { need, done } = await fill(P);
    if (need.length) {
      banner(`filled ${done.length} fields; ${need.length} need you: ${need.join(" · ")}. Finish those and press Submit yourself.`, "err");
      report({ ok: false, status: "needs-you", need, done });
      watchForSuccess(P, done);
      return;
    }
    const btn = submitButton();
    if (!btn) { banner("filled everything but couldn't find the Submit button; press it yourself", "err"); report({ ok: false, status: "needs-you", need: ["submit button"], done }); watchForSuccess(P, done); return; }
    await sleep(400);
    for (let attempt = 0; attempt < 3; attempt++) {
      const b = await waitReady(20000) || btn;
      window.__coopClicked = true; realClick(b);
      setTimeout(() => { if (!succeeded() && !complaints().length && !captchaUp()) forceSubmit(b); }, 3000);
      banner(attempt ? `resubmitting (try ${attempt + 1})…` : "submitting…");
      let names = [];
      for (let i = 0; i < 60; i++) {
        await sleep(250);
        if (succeeded()) { banner("submitted ✓ recorded on the board", "ok"); report({ ok: true, status: "applied", done }); sessionStorage.removeItem(KEY); return; }
        if (captchaUp()) { banner("captcha: solve it, then press Submit", "err"); report({ ok: false, status: "needs-you", need: ["captcha"], done }); watchForSuccess(P, done); return; }
        names = complaints(); if (names.length && i > 4) break;
      }
      if (!names.length) break;
      banner(`form rejected ${names.length} field(s): ${names.join(", ")}; refilling…`);
      const left = await refill(P, names); await sleep(800);
      if (left.length) { banner(`still can't fill: ${left.join(" · ")}. Fill those and press Submit.`, "err"); report({ ok: false, status: "needs-you", need: left, done }); watchForSuccess(P, done); return; }
    }
    const errs = [...document.querySelectorAll("[class*='error'], [role=alert]")].filter(visible).map(txt).filter(Boolean).slice(0, 4);
    banner("submitted but no confirmation seen" + (errs.length ? ": " + errs.join(" · ") : "") + ". Check the page, then press Submit if needed.", "err");
    report({ ok: false, status: "needs-you", need: errs.length ? errs : ["no confirmation seen"], done });
    watchForSuccess(P, done);
  }
  function watchForSuccess(P, done) { const t = setInterval(() => { if (succeeded()) { clearInterval(t); banner("submitted ✓ recorded on the board", "ok"); report({ ok: true, status: "applied", done }); sessionStorage.removeItem(KEY); } }, 1000); }

  run().catch(e => { banner("error: " + e.message, "err"); report({ ok: false, status: "error", error: String(e) }); });
})();
