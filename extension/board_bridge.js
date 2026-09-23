// Runs only on the paired board page. Relays a fixed set of board requests to the background worker and posts
// the answer back to the same page. Credentials are never accepted here and never sent back.
"use strict";
(() => {
  const ALLOWED = new Set(["worker.status", "application.enqueue", "application.status", "application.cancel", "application.resume",
    "application.check", "application.resolve_uncertain", "application.set_cover", "account.list", "pending.list"]);
  const post = (id, response) => window.postMessage({ channel: "coop-runner-response", id, response }, location.origin);
  window.addEventListener("message", e => {
    if (e.source !== window || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || d.channel !== "coop-runner-request" || typeof d.id !== "string" || !d.msg || typeof d.msg !== "object") return;
    if (!ALLOWED.has(d.msg.type)) { post(d.id, { ok: false, error: "not allowed from the board" }); return; }
    try {
      chrome.runtime.sendMessage({ msg: d.msg }, resp => {
        const err = chrome.runtime.lastError;
        post(d.id, resp || { ok: false, offline: true, error: (err && err.message) || "no answer from the extension" });
      });
    } catch (x) { post(d.id, { ok: false, offline: true, error: "the extension was reloaded; refresh this page" }); }
  });
  window.postMessage({ channel: "coop-runner-ready", version: "1.0.0" }, location.origin);
})();
