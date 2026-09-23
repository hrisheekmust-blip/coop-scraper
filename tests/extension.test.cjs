// Bridge extension: channel assignment in the background worker and the board content script's allowlist.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const src = f => fs.readFileSync(path.join(__dirname, '..', 'extension', f), 'utf8');

function background() {
  const sent = []; let listener;
  const port = {onMessage: {addListener(fn) { port.fn = fn; }}, onDisconnect: {addListener() {}},
    postMessage(m) { sent.push(m); setImmediate(() => port.fn({rid: m.rid, ok: true, echo: m.channel})); }};
  const chrome = {runtime: {id: 'EXT', getURL: p => 'chrome-extension://EXT/' + p, connectNative: () => port, lastError: null,
    onMessage: {addListener(fn) { listener = fn; }}}};
  const c = {chrome, setTimeout, console}; vm.createContext(c); vm.runInContext(src('background.js'), c);
  const ask = (sender, msg = {type: 'worker.status'}) => new Promise(r => listener({msg}, sender, r));
  return {sent, ask};
}

test('background assigns channels only from what Chrome reports about the sender', async () => {
  const b = background();
  const board = {id: 'EXT', tab: {id: 1}, frameId: 0, url: 'https://hrisheekmust-blip.github.io/coop-scraper/index.html'};
  assert.equal((await b.ask(board)).echo, 'board');
  assert.equal((await b.ask({id: 'EXT', tab: {id: 2}, frameId: 0, url: 'https://outlook.office.com/mail/'})).echo, 'outlook');
  assert.equal((await b.ask({id: 'EXT', url: 'chrome-extension://EXT/settings.html'})).echo, 'settings');
  // forged or unexpected senders
  assert.equal((await b.ask({...board, id: 'OTHER'})).ok, false);
  assert.equal((await b.ask({...board, frameId: 3})).ok, false);                       // an iframe inside the board
  assert.equal((await b.ask({...board, url: 'https://hrisheekmust-blip.github.io/other/'})).ok, false);
  assert.equal((await b.ask({id: 'EXT', tab: {id: 3}, frameId: 0, url: 'https://evil.example/'})).ok, false);
  assert.ok(b.sent.every(m => ['board', 'outlook', 'settings'].includes(m.channel)));
});

function boardBridge() {
  const listeners = [], posted = [], forwarded = [];
  const win = {addEventListener: (n, fn) => listeners.push(fn), postMessage: (m, o) => posted.push({m, o})};
  win.window = win;
  const c = {window: win, location: {origin: 'https://hrisheekmust-blip.github.io'},
    chrome: {runtime: {lastError: null, sendMessage: (m, cb) => { forwarded.push(m); cb({ok: true, fake: true}); }}}};
  vm.createContext(c); vm.runInContext(src('board_bridge.js'), c);
  const send = (data, source = win, origin = 'https://hrisheekmust-blip.github.io') => listeners.forEach(fn => fn({data, source, origin}));
  return {send, posted, forwarded, win};
}

test('board content script forwards only board requests from the page itself', () => {
  const b = boardBridge();
  assert.equal(b.posted[0].m.channel, 'coop-runner-ready');
  b.send({channel: 'coop-runner-request', id: 'a', msg: {type: 'application.enqueue', source_url: 'https://jobs.lever.co/x/y'}});
  assert.equal(b.forwarded.length, 1);
  b.send({channel: 'coop-runner-request', id: 'b', msg: {type: 'credentials.set', what: 'password', value: 'x'}});
  b.send({channel: 'coop-runner-request', id: 'c', msg: {type: 'account.resolve'}});
  assert.equal(b.forwarded.length, 1);
  assert.equal(b.posted.filter(p => p.m.id === 'b')[0].m.response.ok, false);
  b.send({channel: 'coop-runner-request', id: 'd', msg: {type: 'worker.status'}}, {}, 'https://hrisheekmust-blip.github.io');   // another window
  b.send({channel: 'coop-runner-request', id: 'e', msg: {type: 'worker.status'}}, b.win, 'https://evil.example');                 // another origin
  assert.equal(b.forwarded.length, 1);
  assert.ok(b.posted.every(p => p.o === 'https://hrisheekmust-blip.github.io'));
});

test('manifest pins the extension id the native host allows', () => {
  const m = JSON.parse(src('manifest.json'));
  const host = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'native_host', 'manifest.template.json'), 'utf8'));
  const crypto = require('node:crypto');
  const der = Buffer.from(m.key, 'base64');
  const id = [...crypto.createHash('sha256').update(der).digest('hex').slice(0, 32)].map(ch => String.fromCharCode(97 + parseInt(ch, 16))).join('');
  assert.deepEqual(host.allowed_origins, [`chrome-extension://${id}/`]);
  assert.deepEqual(m.permissions.sort(), ['nativeMessaging', 'storage']);
  assert.ok(m.content_scripts.every(cs => cs.matches.every(u => u.startsWith('https://'))));
});
