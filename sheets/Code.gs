/**
 * Spring 27 Co-op Board — Google Sheets auto-updater.
 *
 * Pulls the scraper's output from GitHub every hour and rebuilds four tabs:
 *   APPLY NOW    spring / Jan-2027 postings, freshest first, colour-banded by urgency
 *   All postings everything the scraper is tracking
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
const STATUS_OPTIONS = ["", "applied", "interview", "offer", "skip"];

function setup() {
  refresh();
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger("refresh").timeBased().everyHours(1).create();
  SpreadsheetApp.getActive().toast("Set up. The board refreshes itself every hour.", "Co-op board");
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu("Co-op board").addItem("Refresh now", "refresh").addItem("Install hourly refresh", "setup").addToUi();
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

  const apply = sheet.filter(r => r.urgency === "APPLY NOW" || r.urgency === "APPLY" || r.urgency === "SOON");
  writePostings_(ss, "APPLY NOW", apply, my);
  writePostings_(ss, "All postings", sheet, my);
  writeTable_(ss, "Watchlist", ["company", "tier", "ats", "status", "expect"], watch, {company: 22, expect: 40, status: 26});
  writeTable_(ss, "Events", ["date", "time", "title", "type"], events, {title: 50, type: 24});
  ensureMyStatus_(ss, sheet, my);
  order_(ss, ["APPLY NOW", "All postings", "Watchlist", "Events", "My status"]);
  ss.getSheetByName("APPLY NOW").getRange("A1").setNote("Last refresh " + new Date().toLocaleString());
}

// ---------------------------------------------------------------- postings tabs
const COLS = ["urgency", "new", "status", "company", "role", "location", "posted", "days_open", "deadline", "term", "rank", "nuworks", "nu_eligible", "link", "nuworks_link", "notes", "source"];
const HEAD = ["Urgency", "New", "My status", "Company", "Role", "Location", "Posted", "Days open", "Deadline", "Term", "Rank", "NUWorks", "NU eligible", "Link", "NUWorks link", "Notes", "Source"];
const WIDTH = {urgency: 92, new: 46, status: 90, company: 170, role: 300, location: 170, posted: 90, days_open: 70, deadline: 90, term: 110, rank: 46, nuworks: 66, nu_eligible: 110, link: 260, nuworks_link: 200, notes: 220, source: 140};

function writePostings_(ss, name, rows, my) {
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clear({contentsOnly: true}); sh.clearFormats(); sh.clearNotes();
  const values = [HEAD].concat(rows.map(r => COLS.map(c => {
    if (c === "status") return (my[r.link] || {}).status || "";
    if (c === "notes") return (my[r.link] || {}).notes || "";
    return r[c] || "";
  })));
  sh.getRange(1, 1, values.length, COLS.length).setValues(values);
  // header
  sh.getRange(1, 1, 1, COLS.length).setFontWeight("bold").setBackground("#1f2937").setFontColor("#ffffff");
  sh.setFrozenRows(1); sh.setFrozenColumns(5);
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
  rows.forEach((r, i) => {
    if (r.new === "NEW") sh.getRange(i + 2, 2).setBackground("#16a34a").setFontColor("#ffffff").setFontWeight("bold").setHorizontalAlignment("center");
    if (r.nu_eligible === "NOT QUALIFIED") sh.getRange(i + 2, 13).setFontColor("#b00020");
    if (r.deadline) { const d = new Date(r.deadline); const days = (d - new Date()) / 864e5; if (days <= 14) sh.getRange(i + 2, 9).setFontColor("#b00020").setFontWeight("bold"); }
  });
  // links as clickable text
  const linkCol = COLS.indexOf("link") + 1, nuCol = COLS.indexOf("nuworks_link") + 1;
  rows.forEach((r, i) => {
    if (r.link) sh.getRange(i + 2, linkCol).setRichTextValue(SpreadsheetApp.newRichTextValue().setText("open ↗").setLinkUrl(r.link).build());
    if (r.nuworks_link) sh.getRange(i + 2, nuCol).setRichTextValue(SpreadsheetApp.newRichTextValue().setText("NUWorks ↗").setLinkUrl(r.nuworks_link).build());
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
  if (name === "Events") rows.forEach((r, i) => { if (/SpaceX|Teradyne|Dell|Lunar/i.test(r.title)) sh.getRange(i + 2, 1, 1, cols.length).setBackground("#fff1d6").setFontWeight("bold"); });
}

// ---------------------------------------------------------------- your own edits, keyed by link
function myStatus_(ss) {
  const out = {};
  ["APPLY NOW", "All postings"].forEach(name => {   // pick up edits made in the postings tabs since last refresh
    const sh = ss.getSheetByName(name); if (!sh || sh.getLastRow() < 2) return;
    const v = sh.getRange(2, 1, sh.getLastRow() - 1, COLS.length).getValues();
    const rt = sh.getRange(2, COLS.indexOf("link") + 1, sh.getLastRow() - 1, 1).getRichTextValues();
    v.forEach((row, i) => { const link = (rt[i][0] && rt[i][0].getLinkUrl()) || row[COLS.indexOf("link")]; if (!link) return;
      const s = row[COLS.indexOf("status")], n = row[COLS.indexOf("notes")];
      if (s || n) out[link] = {status: s, notes: n}; });
  });
  const sh = ss.getSheetByName("My status");
  if (sh && sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, 3).getValues().forEach(([link, s, n]) => { if (link && !out[link] && (s || n)) out[link] = {status: s, notes: n}; });
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
