/**
 * Spring 27 Co-op Board — Google Sheets auto-updater.
 *
 * Pulls the scraper's output from GitHub every hour and rebuilds four tabs:
 *   APPLY NOW    spring / Jan-2027 postings, freshest first, colour-banded by urgency
 *   All postings everything the scraper is tracking
 *   Openings     companies that opened their spring/co-op reqs in the last 7 days
 *   Watchlist    target companies that have not posted a spring role yet
 *   Events       employer events worth showing up to
 * Your own columns (Status, Notes) live in the "My status" tab keyed by link and are never overwritten.
 *
 * One-time setup: paste this into Extensions > Apps Script, set REPO below, run setup() once, approve the permissions.
 */

const REPO = "hrisheekmust-blip/coop-scraper";   // GitHub user/repo the scraper commits to
const BRANCH = "main";
const RAW = `https://raw.githubusercontent.com/${REPO}/${BRANCH}/data/`;

const BAND = {  // background, font colour, bold
  "APPLY NOW": ["#ffe0e0", "#b00020", true],
  "APPLY":     ["#fff1d6", "#8a5a00", true],
  "SOON":      ["#e8f1ff", "#1d4ed8", false],
  "WATCH":     ["#ffffff", "#555555", false],
};
const FIT_CHIP = {"CHIP": ["#0f766e", "#ffffff"], "HARDWARE": ["#cbd5e1", "#334155"], "MAYBE": ["#fef3c7", "#92400e"], "ADJACENT": ["#f1f5f9", "#94a3b8"]};
const STATUS_OPTIONS = ["", "applied", "interview", "offer", "skip"];

function setup() {
  refresh();
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger("refresh").timeBased().everyMinutes(20).create();
  SpreadsheetApp.getActive().toast("Set up. The board refreshes itself every 20 minutes.", "Co-op board");
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu("Co-op board").addItem("Refresh now", "refresh").addItem("Install 20-min refresh", "setup").addToUi();
}

function fetchCsv(name) {
  const res = UrlFetchApp.fetch(RAW + name + "?t=" + Date.now(), {muteHttpExceptions: true});
  if (res.getResponseCode() !== 200) throw new Error(`${name}: HTTP ${res.getResponseCode()} — is the repo pushed and public, and REPO set right?`);
  const rows = Utilities.parseCsv(res.getContentText());
  const head = rows.shift();
  return rows.filter(r => r.length === head.length).map(r => Object.fromEntries(head.map((h, i) => [h, r[i]])));
}

function refresh() {
  const ss = SpreadsheetApp.getActive();
  const my = myStatus_(ss);
  const sheet = fetchCsv("sheet.csv");
  const watch = safe_(() => fetchCsv("watchlist.csv"), []);
  const events = safe_(() => fetchCsv("events.csv"), []);
  const openings = safe_(() => fetchCsv("openings.csv"), []);

  // the short list: chip and hardware roles worth acting on. Medical-device / consulting EE co-ops
  // still live in "All postings", they just do not belong at the top of the queue.
  // If the CSV predates the fit column, treat every row as a match rather than emptying the tab.
  const hasFit = sheet.some(r => r.fit);
  const apply = sheet.filter(r => (!hasFit || r.fit === "CHIP" || r.fit === "HARDWARE" || r.fit === "MAYBE") &&
                                  (r.urgency === "APPLY NOW" || r.urgency === "APPLY" || r.urgency === "SOON"));
  if (!hasFit) ss.toast("sheet.csv has no fit column yet — push the scraper update and re-run the workflow.", "Co-op board", 8);
  writePostings_(ss, "APPLY NOW", apply, my);
  writePostings_(ss, "All postings", sheet, my);
  writeTable_(ss, "Openings", ["company", "opened", "n", "fit", "sample"], openings, {company: 24, opened: 14, n: 6, fit: 12, sample: 60});
  writeTable_(ss, "Watchlist", ["company", "tier", "ats", "status", "expect"], watch, {company: 22, expect: 40, status: 26});
  writeTable_(ss, "Events", ["date", "time", "title", "type"], events, {title: 50, type: 24});
  ensureMyStatus_(ss, sheet, my);
  order_(ss, ["APPLY NOW", "Openings", "All postings", "Watchlist", "Events", "My status"]);
  safe_(() => alerts_(ss, apply, openings), null);
  ss.getSheetByName("APPLY NOW").getRange("A1").setNote("Last refresh " + new Date().toLocaleString());
}

// ---------------------------------------------------------------- postings tabs
const COLS = ["urgency", "fit", "new", "status", "company", "role", "location", "posted", "days_open", "deadline", "term", "rank", "nuworks", "nu_eligible", "link", "nuworks_link", "notes", "source"];
const HEAD = ["Urgency", "Fit", "New", "My status", "Company", "Role", "Location", "Posted", "Days open", "Deadline", "Term", "Rank", "NUWorks", "NU eligible", "Link", "NUWorks link", "Notes", "Source"];
const WIDTH = {urgency: 92, fit: 88, new: 46, status: 90, company: 170, role: 300, location: 170, posted: 90, days_open: 70, deadline: 90, term: 110, rank: 46, nuworks: 66, nu_eligible: 110, link: 260, nuworks_link: 200, notes: 220, source: 140};

function writePostings_(ss, name, rows, my) {
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clear({contentsOnly: true}); sh.clearFormats(); sh.clearNotes();
  if (sh.getFilter()) sh.getFilter().remove();   // a stale filter blocks createFilter() on the next refresh
  sh.getDataRange().clearDataValidations();      // clearFormats() leaves dropdowns behind
  const values = [HEAD].concat(rows.map(r => COLS.map(c => {
    if (c === "status") return (my[r.link] || {}).status || "";
    if (c === "notes") return (my[r.link] || {}).notes || "";
    return r[c] || "";
  })));
  sh.getRange(1, 1, values.length, COLS.length).setValues(values);
  // header
  sh.getRange(1, 1, 1, COLS.length).setFontWeight("bold").setBackground("#1f2937").setFontColor("#ffffff");
  sh.getRange(1, 1, values.length, COLS.length).createFilter();
  sh.setFrozenRows(1); sh.setFrozenColumns(6);
  COLS.forEach((c, i) => sh.setColumnWidth(i + 1, WIDTH[c] || 100));
  if (rows.length === 0) return;
  // band colours + bold per urgency; a NEW row gets a green left stripe on the New cell
  const bg = [], fc = [], fw = [];
  rows.forEach(r => {
    const [b, f, bold] = BAND[r.urgency] || BAND.WATCH;
    const s = (my[r.link] || {}).status || "";
    const done = s === "applied" || s === "interview" || s === "offer" || s === "skip";
    bg.push(COLS.map(() => done ? "#f3f4f6" : b));
    fc.push(COLS.map(() => done ? "#9ca3af" : f));
    fw.push(COLS.map(() => (bold && !done) ? "bold" : "normal"));
  });
  const body = sh.getRange(2, 1, rows.length, COLS.length);
  body.setBackgrounds(bg).setFontColors(fc).setFontWeights(fw).setVerticalAlignment("middle");
  const fitCol = COLS.indexOf("fit") + 1, newCol = COLS.indexOf("new") + 1;
  const dlCol = COLS.indexOf("deadline") + 1, elCol = COLS.indexOf("nu_eligible") + 1;
  rows.forEach((r, i) => {
    const [fb, ff] = FIT_CHIP[r.fit] || FIT_CHIP.ADJACENT;
    sh.getRange(i + 2, fitCol).setBackground(fb).setFontColor(ff).setFontWeight(r.fit === "CHIP" ? "bold" : "normal").setHorizontalAlignment("center");
    if (r.new === "NEW") sh.getRange(i + 2, newCol).setBackground("#16a34a").setFontColor("#ffffff").setFontWeight("bold").setHorizontalAlignment("center");
    if (r.nu_eligible === "NOT QUALIFIED") sh.getRange(i + 2, elCol).setFontColor("#b00020");
    if (r.deadline) { const d = new Date(r.deadline); const days = (d - new Date()) / 864e5; if (days <= 14) sh.getRange(i + 2, dlCol).setFontColor("#b00020").setFontWeight("bold"); }
  });
  // links as clickable text
  const linkCol = COLS.indexOf("link") + 1, nuCol = COLS.indexOf("nuworks_link") + 1;
  rows.forEach((r, i) => {
    if (r.link) sh.getRange(i + 2, linkCol).setRichTextValue(SpreadsheetApp.newRichTextValue().setText("open \u2197").setLinkUrl(r.link).build());
    if (r.nuworks_link) sh.getRange(i + 2, nuCol).setRichTextValue(SpreadsheetApp.newRichTextValue().setText("NUWorks \u2197").setLinkUrl(r.nuworks_link).build());
  });
  // status dropdown that writes through to My status
  const statusCol = COLS.indexOf("status") + 1;
  sh.getRange(2, statusCol, rows.length, 1).setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(STATUS_OPTIONS, true).build());
  sh.getRange(2, 1, rows.length, 1).setHorizontalAlignment("center");
}

// ---------------------------------------------------------------- simple tables
function writeTable_(ss, name, cols, rows, widths) {
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clear({contentsOnly: true}); sh.clearFormats();
  const values = [cols.map(c => c.replace(/_/g, " "))].concat(rows.map(r => cols.map(c => r[c] || "")));
  sh.getRange(1, 1, values.length, cols.length).setValues(values);
  sh.getRange(1, 1, 1, cols.length).setFontWeight("bold").setBackground("#1f2937").setFontColor("#ffffff");
  sh.setFrozenRows(1);
  cols.forEach((c, i) => sh.setColumnWidth(i + 1, (widths[c] || 16) * 7));
  if (name === "Openings") rows.forEach((r, i) => { const [b, f] = FIT_CHIP[r.fit] || FIT_CHIP.ADJACENT; sh.getRange(i + 2, 4).setBackground(b).setFontColor(f).setFontWeight("bold").setHorizontalAlignment("center"); sh.getRange(i + 2, 1, 1, cols.length).setFontWeight(r.fit === "CHIP" ? "bold" : "normal"); });
  if (name === "Watchlist") rows.forEach((r, i) => { if (/FETCHER/.test(r.status)) sh.getRange(i + 2, 4).setFontColor("#b00020").setFontWeight("bold"); });
  if (name === "Events") rows.forEach((r, i) => { if (/SpaceX|Teradyne|Dell|Lunar/i.test(r.title)) sh.getRange(i + 2, 1, 1, cols.length).setBackground("#fff1d6").setFontWeight("bold"); });
}

// ---------------------------------------------------------------- email alerts for anything new worth acting on
// Emails the account that owns the sheet when a CHIP/HARDWARE row enters APPLY NOW / APPLY, or a company opens its
// spring reqs. Remembers what it already sent (Script Properties) so each posting is mailed once. The first run only
// seeds that memory — otherwise you'd get one email with the whole board in it.
function alerts_(ss, apply, openings) {
  const props = PropertiesService.getScriptProperties();
  const sent = new Set(JSON.parse(props.getProperty("alerted") || "[]"));
  const items = apply.filter(r => (r.urgency === "APPLY NOW" || r.urgency === "APPLY") && (r.fit === "CHIP" || r.fit === "HARDWARE")).map(r => ({key: r.link, r}));
  const opens = openings.map(o => ({key: "open:" + o.company + ":" + o.opened, o}));
  const fresh = items.filter(x => !sent.has(x.key)), freshOpen = opens.filter(x => !sent.has(x.key));
  const seeded = props.getProperty("alerted") !== null;
  [...items, ...opens].forEach(x => sent.add(x.key));
  props.setProperty("alerted", JSON.stringify([...sent].slice(-3000)));
  if (!seeded || (fresh.length === 0 && freshOpen.length === 0)) return;
  const to = Session.getEffectiveUser().getEmail();
  if (!to) return;
  const row = r => `<tr><td><b>${r.fit}</b></td><td>${r.urgency}</td><td>${r.company}</td><td><a href="${r.link}">${r.role}</a></td><td>${r.location}</td><td>${r.deadline || ""}</td></tr>`;
  let html = "";
  if (freshOpen.length) html += "<h3>Companies that just opened spring reqs</h3><ul>" + freshOpen.map(x => `<li><b>${x.o.company}</b> — ${x.o.n} posting(s), e.g. <a href="${x.o.link}">${x.o.sample}</a></li>`).join("") + "</ul>";
  if (fresh.length) html += `<h3>${fresh.length} new posting(s)</h3><table border="1" cellpadding="4" style="border-collapse:collapse;font-size:13px"><tr><th>Fit</th><th>Urgency</th><th>Company</th><th>Role</th><th>Location</th><th>Deadline</th></tr>` + fresh.map(x => row(x.r)).join("") + "</table>";
  html += `<p><a href="${ss.getUrl()}">Open the board</a></p>`;
  const subj = `Co-op board: ${fresh.length} new` + (freshOpen.length ? `, ${freshOpen.length} company opening(s)` : "") + (fresh.some(x => x.r.fit === "CHIP") ? " — CHIP roles inside" : "");
  MailApp.sendEmail({to, subject: subj, htmlBody: html});
}

// ---------------------------------------------------------------- your own edits, keyed by link
function myStatus_(ss) {
  const out = {};
  const ok = v => STATUS_OPTIONS.includes(String(v || ""));
  ["APPLY NOW", "All postings"].forEach(name => {   // pick up edits made in the postings tabs since last refresh
    const sh = ss.getSheetByName(name); if (!sh || sh.getLastRow() < 2) return;
    const head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
    const si = head.indexOf("My status"), ni = head.indexOf("Notes"), li = head.indexOf("Link");
    if (si < 0 || li < 0) return;                      // unknown layout, ignore rather than guess
    const v = sh.getRange(2, 1, sh.getLastRow() - 1, head.length).getValues();
    const rt = sh.getRange(2, li + 1, sh.getLastRow() - 1, 1).getRichTextValues();
    v.forEach((row, i) => { const link = (rt[i][0] && rt[i][0].getLinkUrl()) || row[li]; if (!link || !/^https?:/.test(link)) return;
      const st = ok(row[si]) ? row[si] : "", n = ni >= 0 ? row[ni] : "";
      if (st || n) out[link] = {status: st, notes: n}; });
  });
  const sh = ss.getSheetByName("My status");
  if (sh && sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, 3).getValues().forEach(([link, st, n]) => {
    if (link && /^https?:/.test(link) && !out[link] && (ok(st) && st || n)) out[link] = {status: ok(st) ? st : "", notes: n}; });
  return out;
}

function ensureMyStatus_(ss, rows, my) {
  const sh = ss.getSheetByName("My status") || ss.insertSheet("My status");
  sh.clear({contentsOnly: true});
  const values = [["link", "status", "notes", "company", "role"]];
  rows.forEach(r => { const m = my[r.link]; if (m) values.push([r.link, m.status || "", m.notes || "", r.company, r.role]); });
  sh.getRange(1, 1, values.length, 5).setValues(values);
  sh.getRange(1, 1, 1, 5).setFontWeight("bold");
}

function order_(ss, names) { names.forEach((n, i) => { const sh = ss.getSheetByName(n); if (sh) { ss.setActiveSheet(sh); ss.moveActiveSheet(i + 1); } }); ss.setActiveSheet(ss.getSheetByName(names[0])); }
function safe_(fn, dflt) { try { return fn(); } catch (e) { return dflt; } }
