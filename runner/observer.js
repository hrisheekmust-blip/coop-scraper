// Page observer: runs inside every frame and returns a normalized description of the page.
// It only reads the DOM and tags elements with data-coop-ref so the executor can find them again.
// No values are ever typed from here.
(() => {
  const norm = s => (s || "").replace(/\s+/g, " ").trim();
  const txt = el => norm(el ? (el.innerText ?? el.textContent ?? "") : "");
  const style = el => { try { return getComputedStyle(el); } catch (e) { return null; } };
  function vis(el) {
    if (!el || !el.isConnected) return false;
    for (let p = el; p && p !== document.documentElement; p = p.parentElement) {
      const s = style(p); if (!s) return false;
      if (s.display === "none" || s.visibility === "hidden" || (p.getAttribute && p.getAttribute("aria-hidden") === "true" && p !== el)) return false;
    }
    const r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) || el.getClientRects().length > 0;
  }
  // File and styled radio/checkbox inputs are often visually hidden behind a label or button.
  const usable = el => vis(el) || (["file", "radio", "checkbox"].includes((el.type || "").toLowerCase()) && (vis(el.parentElement) || (el.labels && [...el.labels].some(vis))));
  let n = window.__coopRefN || 0;
  const ref = el => { if (!el.dataset.coopRef) { el.dataset.coopRef = "r" + (++n); } window.__coopRefN = n; return el.dataset.coopRef; };

  function qtext(el) {
    if (!el) return "";
    const c = el.cloneNode(true);
    for (const x of c.querySelectorAll("input, select, textarea, button, [role=listbox], [role=option], script, style, .sr-only-hint")) x.remove();
    return norm(c.textContent).replace(/\s*[*✱]\s*$/, "").replace(/^\s*[*✱]\s*/, "");
  }
  const ENTRY = ".ashby-application-form-field-entry, fieldset, .field, .application-question, .application-field, .form-field, [data-automation-id^='formField'], [data-automation-id*='FormField'], .jobs-easy-apply-form-element, .iCIMS_TableRow, .oj-flex-item, .rcrtField, [class*='field-entry'], [class*='FieldEntry'], [class*='question'], li";
  const entryOf = el => el.closest(ENTRY) || el.parentElement;
  function labelOf(el) {
    if (el.type === "file") {
      const g = el.closest("[role=group][aria-labelledby], .file-upload");
      const ids = g && g.getAttribute("aria-labelledby");
      const l = ids ? ids.split(/\s+/).map(i => qtext(document.getElementById(i))).join(" ").trim() : "";
      if (l) return l;
    }
    if (el.labels && el.labels.length) { const t = qtext(el.labels[0]); if (t) return t; }
    const lb = el.getAttribute("aria-labelledby");
    if (lb) { const t = lb.split(/\s+/).map(i => qtext(document.getElementById(i))).join(" ").trim(); if (t) return t; }
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));
    const en = entryOf(el);
    if (en && en.querySelectorAll("input:not([type=hidden]), select, textarea").length <= 2) {
      const l = en.querySelector("label, legend, .application-label, [data-automation-id*='label' i], [class*='label']:not(input):not(select)");
      if (l && qtext(l)) return qtext(l);
    }
    if (el.placeholder) return norm(el.placeholder);
    if (el.title) return norm(el.title);
    let p = el.previousElementSibling; while (p) { const t = txt(p); if (t && t.length < 200) return t; p = p.previousElementSibling; }
    return "";
  }
  function around(el) { const en = entryOf(el); return en ? txt(en).slice(0, 600) : ""; }
  function required(el, label) {
    if (el.required || el.getAttribute("aria-required") === "true") return true;
    const en = entryOf(el);
    const lab = el.labels && el.labels[0] ? el.labels[0].textContent : "";
    const blob = (lab || "") + " " + (en ? (en.querySelector("label, legend, [class*='label']") || {}).textContent || "" : "");
    if (/[*✱]/.test(blob) || /\brequired\b/i.test(blob)) return !/optional/i.test(blob);
    return false;
  }
  function section(el) {
    // nearest preceding heading, used as question context (e.g. "Education", "Voluntary Disclosures")
    let e = el;
    for (let i = 0; i < 8 && e; i++) {
      const box = e.closest("section, fieldset, [role=group], [data-automation-id*='Section' i], .section, div");
      if (!box) break;
      const h = box.querySelector("h1, h2, h3, h4, [role=heading]");
      if (h && !h.contains(el) && txt(h)) return txt(h).slice(0, 120);
      e = box.parentElement;
    }
    return "";
  }
  const controls = [];
  const taken = new Set();
  const fields = [...document.querySelectorAll("input, select, textarea, [role=combobox], button[aria-haspopup=listbox], [role=radiogroup], [role=group]")];

  // 1. radio + checkbox groups
  const groups = new Map();
  for (const el of document.querySelectorAll("input[type=radio], input[type=checkbox]")) {
    if (!usable(el)) continue;
    const box = el.closest("fieldset, [role=radiogroup], [role=group], .application-question, [data-automation-id*='radioGroup' i], [data-automation-id*='checkboxGroup' i]");
    const key = el.type === "radio" && el.name ? "name:" + el.name : box ? "box:" + ref(box) : "single:" + ref(el);
    if (!groups.has(key)) groups.set(key, { box, els: [] });
    groups.get(key).els.push(el);
  }
  for (const [key, g] of groups) {
    const els = g.els; els.forEach(e => taken.add(e));
    const optLabel = e => qtext(e.labels && e.labels[0]) || norm(e.getAttribute("aria-label")) || txt(e.parentElement);
    let label = "";
    if (g.box) { const lg = g.box.querySelector("legend, [data-automation-id*='label' i], .application-label, label:not(:has(input))"); label = lg ? qtext(lg) : ""; if (!label && g.box.getAttribute("aria-labelledby")) label = qtext(document.getElementById(g.box.getAttribute("aria-labelledby"))); }
    const single = els.length === 1 && els[0].type === "checkbox";
    if (single) { label = label && label !== optLabel(els[0]) ? label + " — " + optLabel(els[0]) : optLabel(els[0]); }
    if (!label) label = labelOf(els[0]);
    controls.push({
      ref: ref(g.box || els[0]), control: els[0].type === "radio" ? "radio" : "checkbox", label, context: section(els[0]),
      options: els.map(e => ({ label: optLabel(e), ref: ref(e), checked: e.checked })), multiple: els[0].type === "checkbox" && !single,
      required: els.some(e => e.required || e.getAttribute("aria-required") === "true") || (g.box && (g.box.getAttribute("aria-required") === "true" || /[*✱]|\brequired\b/i.test((g.box.querySelector("legend, label") || {}).textContent || ""))),
      value: els.filter(e => e.checked).map(optLabel), disabled: els.every(e => e.disabled),
    });
  }
  // 2. Ashby-style Yes/No button pairs
  for (const en of document.querySelectorAll(".ashby-application-form-field-entry, [class*='yesno' i]")) {
    const btns = [...en.querySelectorAll("button")].filter(b => /^(yes|no)$/i.test(txt(b)) && vis(b));
    if (btns.length !== 2) continue;
    btns.forEach(b => taken.add(b));
    const lab = en.querySelector("label, legend");
    controls.push({ ref: ref(en), control: "radio", kind: "button_pair", label: qtext(lab), context: section(en), required: /[*✱]|required/i.test(lab ? lab.textContent : ""),
      options: btns.map(b => ({ label: txt(b), ref: ref(b), checked: b.getAttribute("aria-pressed") === "true" || /selected|active/i.test(b.className) })),
      value: btns.filter(b => b.getAttribute("aria-pressed") === "true" || /selected|active/i.test(b.className)).map(txt) });
  }
  // 3. everything else
  for (const el of fields) {
    if (taken.has(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || (tag === "input" ? "text" : tag)).toLowerCase();
    if (["hidden", "submit", "button", "reset", "image", "radio", "checkbox", "search"].includes(type) && tag === "input") continue;
    if ((el.getAttribute("role") === "radiogroup" || el.getAttribute("role") === "group")) continue;
    if (!usable(el)) continue;
    if (el.closest("[aria-hidden='true']") && type !== "file") continue;
    let control = tag === "select" ? "select" : tag === "textarea" ? "textarea" : type;
    const isCombo = el.getAttribute("role") === "combobox" || el.getAttribute("aria-haspopup") === "listbox" || /select__input|react-select|autocomplete|typeahead/i.test((el.className || "") + " " + ((el.parentElement && el.parentElement.className) || ""));
    if (isCombo && tag !== "select") control = "combobox";
    if (type === "password") control = "password";
    const opts = tag === "select" ? [...el.options].map(o => ({ label: txt(o), value: o.value, ref: "" })).filter(o => o.label && !/^(select|choose|--|please select)/i.test(o.label)) : [];
    let value = tag === "select" ? [...el.selectedOptions].map(o => txt(o)).filter(v => !/^(select|choose|--|please select)/i.test(v)) : (el.value !== undefined ? el.value : txt(el));
    if (control === "combobox") {
      const ctrl = el.closest("[class*='select__control'], [class*='control'], [class*='Control']") || entryOf(el);
      // Only a rendered selection counts: typed search text in the input is not an answer.
      const shown = ctrl ? [...ctrl.querySelectorAll("[class*='single-value'], [class*='multi-value__label'], [class*='singleValue'], [data-automation-id='selectedItem']")].map(txt).filter(Boolean) : [];
      if (tag === "button") { const t = txt(el); value = /^(select( one)?|choose|--)$/i.test(t) ? [] : [t]; }
      else value = shown;
    }
    if (type === "file") value = el.files && el.files.length ? [...el.files].map(f => f.name) : [];
    let label = labelOf(el);
    // Split date parts ("Month" / "Year") take the group's question: "From Month", "To Year".
    if (/^(month|year|day|mm|yyyy|dd)$/i.test(label)) {
      const grp = el.closest("fieldset, [role=group], [data-automation-id^='formField'], .field, .date-group");
      const gl = grp && grp.querySelector("legend, label:not([for]), [data-automation-id*='label' i], .label");
      const g = gl ? qtext(gl) : "";
      if (g && g.toLowerCase() !== label.toLowerCase()) label = g + " " + label;
    }
    controls.push({
      ref: ref(el), control, tag, label, context: section(el), around: around(el).slice(0, 300), options: opts, multiple: !!el.multiple,
      required: required(el, label), value, placeholder: el.placeholder || "", name: el.name || "", id: el.id || "",
      autocomplete: el.getAttribute("autocomplete") || "", automation: el.getAttribute("data-automation-id") || (el.closest("[data-automation-id]") || { getAttribute: () => "" }).getAttribute("data-automation-id") || "",
      max_len: el.maxLength > 0 ? el.maxLength : null, disabled: !!el.disabled, readonly: !!el.readOnly,
      invalid: el.getAttribute("aria-invalid") === "true",
    });
  }
  // buttons / navigation
  const BTN = "button, input[type=submit], input[type=button], [role=button], a[href], a[role=button], [data-automation-id*='Button' i]";
  const buttons = [];
  const seenB = new Set();
  for (const b of document.querySelectorAll(BTN)) {
    if (taken.has(b) || seenB.has(b) || !vis(b)) continue;
    seenB.add(b);
    const t = norm((b.getAttribute("aria-label") || "") + " " + (b.value && b.tagName === "INPUT" ? b.value : txt(b)));
    if (!t || t.length > 120) continue;
    buttons.push({ ref: ref(b), text: t, tag: b.tagName.toLowerCase(), type: (b.getAttribute("type") || "").toLowerCase(), href: b.getAttribute("href") || "",
      automation: b.getAttribute("data-automation-id") || "", disabled: !!b.disabled || b.getAttribute("aria-disabled") === "true",
      in_form: !!b.closest("form") });
  }
  const errors = [...document.querySelectorAll("[role=alert], .error, .errors, .field-error, [class*='error' i], [data-automation-id*='error' i], [aria-live=assertive]")]
    .filter(vis).map(txt).filter(t => t && t.length < 300).slice(0, 20);
  const body = (document.body ? document.body.innerText || "" : "").slice(0, 6000);
  const captcha = [...document.querySelectorAll("iframe[src*='captcha'], iframe[src*='turnstile'], iframe[title*='challenge' i], .g-recaptcha, .h-captcha, #challenge-form")].some(e => vis(e));
  const headings = [...document.querySelectorAll("h1, h2, h3, [role=heading], [data-automation-id='pageHeaderTitle']")].filter(vis).map(txt).filter(Boolean).slice(0, 12);
  const progress = txt(document.querySelector("[data-automation-id='progressBar'], [aria-label*='progress' i], .progress, [class*='stepper' i]")).slice(0, 200);
  return { url: location.href, title: document.title, controls, buttons, errors, body, captcha, headings, progress,
           password_fields: [...document.querySelectorAll("input[type=password]")].filter(vis).length,
           forms: document.querySelectorAll("form").length };
})()
