// Outlook bridge. Reads only what the worker asks about: rows that look like a verification email for an account
// the worker is waiting on, or an application outcome from an employer you applied to. Verification emails are
// opened so their link/code can be read; nothing is clicked inside an email, and links are only passed to the
// worker, which checks their host against the employer's approved hosts before using them.
"use strict";
(() => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || "").replace(/\s+/g, " ").trim();
  const seen = new Set();
  const VERIFY = /verify|verification|activate|confirm your (e-?mail|account)|one[- ]time|passcode|security code|login code|sign[- ]in code/i;
  const OUTCOME = /appl(y|ied|ication)|interview|unfortunately|thank you for|next steps|assessment|candidate/i;
  const rpc = msg => new Promise(r => { try { chrome.runtime.sendMessage({ msg }, resp => r(resp || { ok: false })); } catch (e) { r({ ok: false }); } });

  function rows() {
    const out = [];
    for (const el of document.querySelectorAll("[role=option][data-convid], [role=row][data-convid], div[data-convid]")) {
      const label = norm(el.getAttribute("aria-label") || el.innerText);
      if (label.length < 10) continue;
      out.push({ el, id: el.getAttribute("data-convid"), text: label.slice(0, 1200) });
    }
    return out;
  }
  function unwrap(href) {
    try {
      const u = new URL(href);
      if (/safelinks\.protection\.outlook\.com$/i.test(u.hostname) && u.searchParams.get("url")) return u.searchParams.get("url");
      return u.href;
    } catch (e) { return ""; }
  }
  // A stable id per message (a conversation can hold several codes): conversation id + a hash of the row.
  const hash = t => { let h = 5381; for (let i = 0; i < t.length; i++) h = ((h << 5) + h + t.charCodeAt(i)) >>> 0; return h.toString(16); };
  function receivedAt(text) {
    // Rows show a time for today's mail ("10:32 AM") and a date for older mail. Without a time: unknown (null);
    // the worker then won't use the message for verification or to settle an uncertain application.
    const m = text.match(/\b(\d{1,2}):(\d{2})\s?(AM|PM)\b/i);
    if (m) {
      const d = new Date(); let h = +m[1] % 12; if (/pm/i.test(m[3])) h += 12;
      d.setHours(h, +m[2], 0, 0);
      if (d.getTime() > Date.now() + 60000) d.setDate(d.getDate() - 1);
      return d.toISOString();
    }
    return null;
  }
  async function readOpen() {
    for (let i = 0; i < 20; i++) {
      const pane = document.querySelector("[aria-label='Message body'], div[role=document], #ReadingPaneContainerId [role=main]");
      if (pane && norm(pane.innerText).length > 5) {
        const header = document.querySelector("[aria-label*='From' i], [data-testid='SenderPersona'], .OZZZK") || null;
        const toEl = document.querySelector("[aria-label^='To' i], [data-testid='RecipientWell']") || null;
        const body = norm(pane.innerText).slice(0, 8000);
        return { body, links: [...pane.querySelectorAll("a[href]")].map(a => unwrap(a.href)).filter(u => u.startsWith("https://")).slice(0, 40),
                 codes: [],   // the worker extracts codes itself, only digits next to code wording
                 from: header ? norm(header.getAttribute("title") || header.innerText).slice(0, 300) : "",
                 to: toEl ? norm(toEl.innerText).slice(0, 300) : "" };
      }
      await sleep(250);
    }
    return null;
  }
  let busy = false;
  async function scan() {
    if (busy) return; busy = true;
    try {
      const want = await rpc({ type: "mail.wanted" });
      if (!want.ok) return;
      const employers = (want.employers || []).map(e => e.toLowerCase().replace(/\b(inc|corp|corporation|llc|ltd|technologies|systems|the)\b\.?/g, "").trim()).filter(e => e.length >= 3);
      const waiting = (want.verifications || []).length > 0;
      for (const r of rows()) {
        const key = r.id + "|" + r.text.slice(0, 120);
        if (seen.has(key)) continue;
        const low = r.text.toLowerCase();
        const isVerify = waiting && VERIFY.test(r.text);
        const isOutcome = employers.some(e => low.includes(e)) && OUTCOME.test(r.text);
        if (!isVerify && !isOutcome) continue;
        seen.add(key);
        const stable = r.text.replace(/\b(unread|read|flagged|pinned|has attachments?|replied|forwarded|collapsed|expanded|selected)\b/gi, "").replace(/\s+/g, " ").trim();
        const msg = { message_id: "owa:" + (r.id || "") + ":" + hash(stable.slice(0, 300)), subject: r.text.slice(0, 300), body_text: r.text, received_at: receivedAt(r.text), links: [], codes: [], from: "", to: "" };
        if (isVerify) {
          r.el.click();
          const opened = await readOpen();
          if (opened) Object.assign(msg, { body_text: opened.body, links: opened.links, codes: opened.codes, from: opened.from || r.text.slice(0, 120), to: opened.to });
        } else {
          msg.from = r.text.slice(0, 120);
        }
        await rpc({ type: "mail.ingest", message: msg });
      }
    } finally { busy = false; }
  }
  setTimeout(scan, 6000);
  setInterval(scan, 45000);
})();
