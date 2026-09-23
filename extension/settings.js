"use strict";
const $ = id => document.getElementById(id);
const rpc = msg => new Promise(r => chrome.runtime.sendMessage({ msg }, resp => r(resp || { ok: false, error: "no answer" })));
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const CONSENTS = [["privacy_policy", "Privacy policy / notice"], ["terms_of_use", "Terms of use / conditions"], ["data_processing", "Processing my application data"],
                  ["accuracy_certification", "Certify my answers are accurate"]];

async function load() {
  const st = await rpc({ type: "worker.status" });
  $("conn").innerHTML = st.ok ? `<span class="ok">Connected</span> · worker ${esc(st.version)} · ${st.workers} running · ${st.materials ? "materials folder set" : '<span class="bad">materials folder not set (run setup)</span>'}`
                              : `<span class="bad">Worker offline</span> · ${esc(st.error || "")}<div class="mute">Start it from the Start menu shortcut or run: python -m runner service</div>`;
  if (!st.ok) return;
  const cs = await rpc({ type: "credentials.status" });
  $("credstate").innerHTML = cs.ok ? `Application email: <b>${esc(cs.application_email || "not set")}</b> · password ${cs.password ? '<span class="ok">saved</span>' : '<span class="bad">not saved</span>'} · username ${cs.username ? "saved" : "not set"}` : "";
  const pol = await rpc({ type: "settings.get" });
  if (pol.ok) {
    const p = pol.policy;
    $("policy").innerHTML = CONSENTS.map(([k, t]) => `<label class="row"><input type="checkbox" data-c="${k}" ${p.approved_consents.includes(k) ? "checked" : ""}> ${esc(t)}</label>`).join("")
      + `<label class="row"><input type="checkbox" id="create" ${p.allow_required_account_creation ? "checked" : ""}> Create an account when a portal requires one</label>`
      + `<label class="row"><input type="checkbox" id="mkt" ${p.optional_marketing_consent ? "checked" : ""}> Opt in to optional marketing/job alerts (off = leave unchecked)</label>`
      + `<button id="savepol">Save</button><span id="polmsg" class="mute"></span>`
      + `<div class="mute">Anything else a portal asks you to accept (background checks, arbitration, …) stops and asks you.</div>`;
    $("savepol").onclick = async () => {
      const values = { approved_consents: [...document.querySelectorAll("[data-c]")].filter(x => x.checked).map(x => x.dataset.c),
                       allow_required_account_creation: $("create").checked, optional_marketing_consent: $("mkt").checked };
      const r = await rpc({ type: "settings.set", values }); $("polmsg").textContent = r.ok ? " saved" : " " + r.error;
    };
  }
  const acc = await rpc({ type: "account.list" });
  $("accounts").innerHTML = !acc.ok ? esc(acc.error) : acc.accounts.length ? acc.accounts.map(a => `<div class="card"><b>${esc(a.realm)}</b> · ${esc(a.status)}
      ${a.reason ? `<div class="mute">${esc(a.reason)}</div>` : ""}<div class="mute">login pages allowed on: ${a.hosts.map(esc).join(", ")}</div>
      <div class="row">${a.pending_host ? `<button class="primary" data-approve="${a.account_id}">Approve sign-in on ${esc(a.pending_host)}</button>` : ""}
      ${["needs_credentials", "existing_account", "locked"].includes(a.status) ? `<input type="password" placeholder="password for this portal only" data-pw="${a.account_id}" style="max-width:240px"><button data-setpw="${a.account_id}">Save for this portal</button>` : ""}
      ${a.registration_outstanding ? `<button data-act="registration_not_created" data-id="${a.account_id}">No account was created</button><button data-act="existing_account_password_set" data-id="${a.account_id}">The account exists (my password works)</button>` : ""}
      ${a.status !== "active" && !a.registration_outstanding && !a.pending_host ? `<button data-act="retry" data-id="${a.account_id}">Retry</button>` : ""}</div></div>`).join("") : "No portal accounts yet.";
  document.querySelectorAll("[data-approve]").forEach(b => b.onclick = async () => { await rpc({ type: "account.resolve", account_id: b.dataset.approve, action: "approve_host" }); load(); });
  document.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => { await rpc({ type: "account.resolve", account_id: b.dataset.id, action: b.dataset.act }); load(); });
  document.querySelectorAll("[data-setpw]").forEach(b => b.onclick = async () => {
    const v = document.querySelector(`[data-pw="${b.dataset.setpw}"]`).value; if (!v) return;
    await rpc({ type: "account.resolve", account_id: b.dataset.setpw, action: "set_password", value: v }); load(); });
  const pend = await rpc({ type: "pending.list" });
  $("pending").innerHTML = pend.ok ? (pend.packet.questions.length ? `${pend.packet.questions.length} question${pend.packet.questions.length > 1 ? "s" : ""} need your answer. Run <code>python -m runner prep export</code> and bring the packet to a Claude session.<ul>` + pend.packet.questions.slice(0, 12).map(q => `<li>${esc(q.label)} <span class="mute">(${q.jobs.length} job${q.jobs.length > 1 ? "s" : ""})</span></li>`).join("") + "</ul>" : "Nothing waiting.") : esc(pend.error);
}
$("savepw").onclick = async () => {
  const a = $("pw").value, b = $("pw2").value;
  if (!a || a !== b) { $("credmsg").textContent = " the two entries don't match"; return; }
  const r = await rpc({ type: "credentials.set", what: "password", value: a });
  $("pw").value = $("pw2").value = ""; $("credmsg").textContent = r.ok ? " saved" : " " + r.error; load();
};
$("saveun").onclick = async () => { const v = $("un").value.trim(); if (!v) return; const r = await rpc({ type: "credentials.set", what: "username", value: v }); $("un").value = ""; $("credmsg").textContent = r.ok ? " saved" : " " + r.error; load(); };
load();
