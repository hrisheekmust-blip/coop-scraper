// Background worker: the only part of the extension that talks to the local service (through the native host).
// It decides which channel a message belongs to from the sender Chrome reports, never from the message itself.
"use strict";
const HOST = "com.coop.apply_runner";
const BOARD_PREFIX = "https://hrisheekmust-blip.github.io/coop-scraper/";
const OUTLOOK = [/^https:\/\/outlook\.office\.com\//, /^https:\/\/outlook\.office365\.com\//, /^https:\/\/outlook\.cloud\.microsoft\//, /^https:\/\/outlook\.live\.com\//];
const SETTINGS_URL = chrome.runtime.getURL("settings.html");

let port = null;
const pending = new Map();
let seq = 0;

function connect() {
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener(m => { const cb = pending.get(m.rid); if (cb) { pending.delete(m.rid); delete m.rid; cb(m); } });
  port.onDisconnect.addListener(() => {
    const err = (chrome.runtime.lastError && chrome.runtime.lastError.message) || "the worker bridge disconnected";
    for (const cb of pending.values()) cb({ ok: false, offline: true, error: err });
    pending.clear();
    port = null;
  });
}

function send(channel, msg) {
  return new Promise(resolve => {
    if (!port) { try { connect(); } catch (e) { resolve({ ok: false, offline: true, error: String(e) }); return; } }
    const rid = ++seq;
    pending.set(rid, resolve);
    try { port.postMessage({ rid, channel, msg }); }
    catch (e) { pending.delete(rid); resolve({ ok: false, offline: true, error: String(e) }); return; }
    setTimeout(() => { if (pending.has(rid)) { pending.delete(rid); resolve({ ok: false, offline: true, error: "the worker didn't answer in time" }); } }, 20000);
  });
}

function channelOf(sender) {
  if (sender.id !== chrome.runtime.id) return null;
  const url = sender.url || "";
  if (url.startsWith(SETTINGS_URL)) return "settings";
  if (!sender.tab || sender.frameId !== 0) return null;          // top frame of a real tab only
  if (url.startsWith(BOARD_PREFIX)) return "board";
  if (OUTLOOK.some(rx => rx.test(url))) return "outlook";
  return null;
}

chrome.runtime.onMessage.addListener((m, sender, reply) => {
  const ch = channelOf(sender);
  if (!ch || !m || typeof m.msg !== "object") { reply({ ok: false, error: "unrecognized sender" }); return; }
  send(ch, m.msg).then(reply);
  return true;
});
