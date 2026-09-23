// Board in worker mode: Apply sends one request through the bridge and shows what the worker reports.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const read = f => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');
const inline = f => [...read(f).matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];

function board(respond) {
  const listeners = {}, requests = [];
  const storage = {}; const ls = {getItem: k => storage[k] ?? null, setItem: (k, v) => storage[k] = String(v), removeItem: k => delete storage[k]};
  const elements = new Map();
  const el = k => { if (!elements.has(k)) elements.set(k, {value: '', textContent: '', innerHTML: '', style: {}, classList: {add() {}, toggle() {}, remove() {}}, addEventListener() {}, querySelector: () => null}); return elements.get(k); };
  const c = {console, URL, URLSearchParams, TextEncoder, Uint8Array, crypto: require('node:crypto').webcrypto, localStorage: ls, sessionStorage: ls,
    btoa: s => Buffer.from(s, 'binary').toString('base64'), atob: s => Buffer.from(s, 'base64').toString('binary'), escape, unescape, encodeURIComponent, decodeURIComponent,
    location: {origin: 'https://hrisheekmust-blip.github.io', href: 'https://hrisheekmust-blip.github.io/coop-scraper/'},
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms || 0, 50)), clearTimeout, setInterval: () => 0, clearInterval() {},
    document: {getElementById: el, querySelectorAll: () => [], querySelector: () => null, addEventListener() {}, body: {}},
    fetch: async () => ({ok: false, status: 404})};
  c.window = c;
  c.addEventListener = (n, fn) => (listeners[n] = listeners[n] || []).push(fn); c.__listeners = listeners;
  c.postMessage = (m, origin) => { if (m.channel === 'coop-runner-request') { requests.push(m.msg); const response = respond(m.msg);
    if (response !== undefined) setImmediate(() => (listeners.message || []).forEach(fn => fn({source: c.__self, origin: c.location.origin, data: {channel: 'coop-runner-response', id: m.id, response}}))); } };
  vm.createContext(c);
  c.__self = vm.runInContext('window', c);   // the context's global proxy is what page code sees as `window`
  vm.runInContext(read('engine.js'), c);
  vm.runInContext(inline('index.html').replace('load();setInterval(load,5*60*1000);startWorkerPolling();', ''), c);
  vm.runInContext('render=()=>{};drawRender=()=>{};cfg.mode="worker";', c);
  return {c, requests, run: s => vm.runInContext(s, c)};
}

const view = {application_id: 'app_1', job_id: 'job_1', board_ref: 'r1', state: 'queued', display: 'Queued', reason: '', needs: [], company: 'Acme'};

test('worker mode Apply sends one enqueue and shows the durable acknowledgement', async () => {
  const b = board(m => m.type === 'application.enqueue' ? {ok: true, application: view} : {ok: true, applications: [], now: 'x'});
  b.run(`rows=[{id:'r1',company:'Acme',role:'Hardware Intern',location:'Boston',link:'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555'}]`);
  await b.run(`applyOne(jobs()[0])`);
  const enq = b.requests.filter(r => r.type === 'application.enqueue');
  assert.equal(enq.length, 1);
  assert.equal(enq[0].source_url, 'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555');
  assert.equal(enq[0].job_ref, 'r1');
  assert.ok(!('files' in enq[0]) && !('profile' in enq[0]) && !('answers' in enq[0]));          // no materials in the page request
  assert.equal(b.run(`jobs()[0].status`), 'queued');
});

test('worker offline: nothing is shown as queued', async () => {
  const b = board(() => undefined);   // no extension answers
  b.run(`rows=[{id:'r1',company:'Acme',role:'X',location:'',link:'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555'}]`);
  await assert.rejects(b.run(`applyOne(jobs()[0])`), /offline/i);
  assert.equal(b.run(`jobs()[0].status`), '');
});

test('job-board links are not sent to the worker', () => {
  const b = board(() => ({ok: true, applications: []}));
  b.run(`rows=[{id:'r2',company:'Acme',role:'X',location:'',link:'https://www.linkedin.com/jobs/view/123456789'}]`);
  assert.equal(b.run(`workerEligible(jobs()[0])`), false);
});

test('status polling maps worker states onto the board', async () => {
  let apps = [{...view, state: 'submission_uncertain', display: 'Submission uncertain', reason: 'no confirmation yet'}];
  const b = board(m => ({ok: true, applications: apps, now: '2026-09-23'}));
  b.run(`rows=[{id:'r1',company:'Acme',role:'X',location:'',link:'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555'}]`);
  await b.run(`pollWorker()`);
  assert.equal(b.run(`jobs()[0].status`), 'uncertain');
  apps = [{...view, state: 'applied', display: 'Applied', receipt: {kind: 'page', text: 'Thanks', at: 't'}}];
  await b.run(`pollWorker()`);
  assert.equal(b.run(`jobs()[0].status`), 'applied');
  assert.equal(b.run(`isDone(jobs()[0])`), true);
});
