// Regression tests for the reliability review (Sept 23, 2026): payload reuse across postings, duplicate
// listings of one requisition, one email changing several roles, and late confirmations after a timeout.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const inline = file => [...read(file).matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const id = '1234567890abcdef', other = 'abcdef1234567890';
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
const decodeDone = url => JSON.parse(Buffer.from(new URLSearchParams(new URL(url).hash.slice(1)).get('done'), 'base64url'));
const LEVER_A = 'https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555/apply';
const LEVER_B = 'https://jobs.lever.co/acme/99999999-2222-3333-4444-555555555555/apply';
function storage(seed = {}) { const data = {...seed}; return {getItem:k=>data[k]??null, setItem:(k,v)=>data[k]=String(v), removeItem:k=>delete data[k], _data:data}; }
function environment(href = LEVER_A) {
  const elements = new Map(), listeners = {}, replaced = [], created = [];
  const u = new URL(href);
  const c = {console, URL, URLSearchParams, TextEncoder, Uint8Array, escape, unescape, encodeURIComponent, decodeURIComponent,
    btoa:s=>Buffer.from(s,'binary').toString('base64'), atob:s=>Buffer.from(s,'base64').toString('binary'),
    crypto:require('node:crypto').webcrypto, localStorage:storage(), sessionStorage:storage(), history:{replaceState(){}},
    location:{hash:'',hostname:u.hostname,pathname:u.pathname,search:'',href,replace:x=>replaced.push(x)},
    getComputedStyle:()=>({visibility:'visible'}), Event:class {constructor(type,o){this.type=type;Object.assign(this,o)}},
    setTimeout:fn=>{setImmediate(fn);return 1}, clearTimeout(){}, setInterval:()=>1, clearInterval(){},
    document:{readyState:'complete', getElementById:k=>{if(!elements.has(k))elements.set(k,{value:'',textContent:'',innerHTML:'',style:{},classList:{add(){},toggle(){}},addEventListener(){}});return elements.get(k)},
      querySelectorAll:()=>[], querySelector:()=>null, body:{innerText:'',querySelectorAll:()=>[]}, documentElement:{appendChild(){}},
      createElement:()=>{const el={style:{},textContent:'',appendChild(){}};created.push(el);return el}},
    addEventListener:(n,fn)=>listeners[n]=fn, removeEventListener(){}, open:()=>({}), opener:null};
  c.window=c;c.top=c;c.MouseEvent=c.PointerEvent=c.KeyboardEvent=c.FocusEvent=c.Event;
  vm.createContext(c); vm.runInContext(read('engine.js'), c);
  c.document.addEventListener=(n,fn)=>listeners['doc:'+n]=fn; c.document.removeEventListener=()=>{};
  return {c, elements, listeners, replaced, created, run:s=>vm.runInContext(s,c)};
}
async function until(done, n = 5000) { for (let i = 0; i < n && !done(); i++) await new Promise(setImmediate); assert.ok(done(), 'did not reach expected state'); }
const payload = {type:'coop-payload', id, batchId:'batch-1', reqKey:'lever:acme:11111111-2222-3333-4444-555555555555', job:{company:'Acme', role:'Hardware Intern'}, files:[], autoSubmit:true};
function submitForm(e, onClick) {
  e.c.CoopEngine = {...e.c.CoopEngine, answerFor:()=>({k:'need'})};
  let clicks = 0;
  const button = {textContent:'Submit application', offsetWidth:100, getAttribute:()=>null, focus(){}, scrollIntoView(){},
    getBoundingClientRect:()=>({left:0,top:0,width:100,height:30}), dispatchEvent(){}, click:()=>{clicks++; onClick && onClick()}};
  e.c.document.querySelectorAll = s => s === 'button, input[type=submit], a[role=button], [role=button]' ? [button]
    : s.startsWith('input[type=file], input[name') ? [{offsetWidth:10}] : [];
  return () => clicks;
}
const pressBanner = (e, label) => { const b = e.created.find(x => x.textContent === label && x.onclick); assert.ok(b, 'no banner button ' + label); b.onclick(); };

test('requisition key treats aliases of one job as the same application', () => {
  const {c} = environment();
  const k = c.CoopEngine.reqKey;
  assert.equal(k('https://boards.greenhouse.io/SpaceX/jobs/8636134002?gh_jid=8636134002'), k('https://job-boards.greenhouse.io/spacex/jobs/8636134002/confirmation'));
  assert.equal(k('https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555'), k('https://jobs.ashbyhq.com/acme/11111111-2222-3333-4444-555555555555/application'));
  assert.equal(k(LEVER_A), 'lever:acme:11111111-2222-3333-4444-555555555555');
  assert.notEqual(k(LEVER_A), k(LEVER_B));
  assert.equal(k('https://www.linkedin.com/jobs/view/123'), '');
});

test('a stored payload is never reused on a different posting in the same tab', () => {
  const e = environment(LEVER_B);
  e.c.sessionStorage.setItem('coop-payload', JSON.stringify({...payload, boundHost:'jobs.lever.co', boundAt:Date.now()}));
  e.run(read('coop-apply.user.js'));
  assert.equal(e.c.__coopRunning, undefined);
  assert.equal(e.c.sessionStorage.getItem('coop-payload'), null);
});

test('a hand-off that lands on a different posting is refused', () => {
  const e = environment(LEVER_B);
  e.c.location.hash = '#coop=' + id + '&p=' + encode(payload);
  e.run(read('coop-apply.user.js'));
  assert.equal(e.c.__coopRunning, undefined);
});

test('the stored payload still follows the same posting to its confirmation page', async () => {
  const e = environment('https://jobs.lever.co/acme/11111111-2222-3333-4444-555555555555/thanks');
  e.c.sessionStorage.setItem('coop-payload', JSON.stringify({...payload, boundHost:'jobs.lever.co', boundAt:Date.now()}));
  e.c.sessionStorage.setItem('coop-clicked', 'coop-attempt:' + payload.reqKey);
  e.c.document.body.innerText = 'Thank you for applying';
  submitForm(e);
  e.run(read('coop-apply.user.js'));
  await until(() => e.replaced.length);
  assert.equal(decodeDone(e.replaced[0]).ok, true);
});

test('a late confirmation after the submit timeout is recorded without a second submit', async () => {
  const e = environment(); e.c.location.hash = '#coop=' + id + '&p=' + encode(payload);
  const clicks = submitForm(e);
  e.run(read('coop-apply.user.js'));
  await until(() => e.c.__coopLast && e.c.__coopLast.status === 'uncertain');
  assert.equal(e.replaced.length, 0);
  e.c.document.body.innerText = 'Your application has been submitted';   // confirmation arrives late
  await until(() => e.replaced.length);
  const r = decodeDone(e.replaced[0]);
  assert.equal(r.ok, true); assert.equal(r.status, 'applied'); assert.match(r.evidence.text, /submitted/);
  assert.equal(clicks(), 1);
});

test('after a submit timeout the continue button records uncertain and advances the batch', async () => {
  const e = environment(); e.c.location.hash = '#coop=' + id + '&p=' + encode(payload);
  const clicks = submitForm(e);
  e.run(read('coop-apply.user.js'));
  await until(() => e.c.__coopLast && e.c.__coopLast.status === 'uncertain');
  pressBanner(e, 'Record as uncertain & continue');
  await until(() => e.replaced.length);
  const r = decodeDone(e.replaced[0]);
  assert.equal(r.ok, false); assert.equal(r.status, 'uncertain'); assert.equal(clicks(), 1);
});

test('a new batch on the same portal never presses Submit again for a requisition already attempted', async () => {
  const e = environment(); e.c.location.hash = '#coop=' + id + '&p=' + encode({...payload, batchId:'batch-2'});
  e.c.localStorage.setItem('coop-attempt:' + payload.reqKey, '2026-09-23T00:00:00Z');
  const clicks = submitForm(e);
  e.run(read('coop-apply.user.js'));
  await until(() => e.c.__coopLast && e.c.__coopLast.status === 'uncertain');
  assert.equal(clicks(), 0);
});

function board() {
  const e = environment('https://hrisheekmust-blip.github.io/coop-scraper/');
  e.run(inline('index.html').replace('load();setInterval(load,5*60*1000);', ''));
  e.run('render=()=>{};drawRender=()=>{};cfg.token="t";writeState=async m=>{m(state)};');
  e.run(`forms={"${id}":{apply_url:"https://boards.greenhouse.io/lightmatter/jobs/5374627008?gh_jid=5374627008"},"${other}":{apply_url:"https://job-boards.greenhouse.io/lightmatter/jobs/5374627008"}};
    rows=[{id:"${id}",company:"Lightmatter",role:"Photonics Intern",link:"https://www.linkedin.com/jobs/view/1",source:"jobspy:linkedin",materials:[{name:"Resume",path:"r.pdf"}]},
          {id:"${other}",company:"Lightmatter",role:"Photonics Intern",link:"https://boards.greenhouse.io/lightmatter/jobs/5374627008",source:"greenhouse",materials:[{name:"Resume",path:"r.pdf"}]}];
    apps={"${id}":{materials:[{name:"Resume",path:"r.pdf"}]},"${other}":{materials:[{name:"Resume",path:"r.pdf"}]}};`);
  return e;
}

test('two listings of one requisition show as one row and queue once', async () => {
  const e = board();
  assert.equal(e.run('view="all";listFor().length'), 1);
  e.run(`stageAll=async()=>{};selected.add("${id}");selected.add("${other}")`);
  await e.run('startBatch(false)');
  assert.deepEqual([...e.run('batch.ids')], [id]);
});

test('an alias of a submitted application cannot be applied to again, even on retry', async () => {
  const e = board();
  e.run(`state["${id}"]={status:"applied"};stageAll=async()=>{};selected.add("${other}")`);
  assert.equal(e.run(`jobs().find(j=>j.id==="${other}").status`), 'applied');
  await e.run('startBatch(true)');
  assert.equal(e.run('batch'), null);
});

test('uncertain submissions are held until you confirm they were not sent', async () => {
  const e = board();
  e.run(`state["${id}"]={status:"uncertain"};stageAll=async()=>{};selected.add("${id}")`);
  await e.run('startBatch(true)'); assert.equal(e.run('batch'), null);
  e.run(`selected.add("${id}")`); await e.run('startBatch(true,{resubmit:true})');
  assert.deepEqual([...e.run('batch.ids')], [id]); assert.deepEqual([...e.run('batch.resubmit')], [id]);
});

test('a late failure report never downgrades a verified submission', async () => {
  const e = board();
  await e.run(`recordResult({id:"${id}",ok:true,at:"2026-09-23T10:00:00Z",url:"x",reqKey:"greenhouse:lightmatter:5374627008",evidence:{text:"Thank you for applying"}})`);
  await e.run(`recordResult({id:"${id}",ok:false,status:"uncertain",need:["no confirmation"]})`);
  assert.equal(e.run(`state["${id}"].status`), 'applied');
  assert.equal(e.run(`state["${id}"].submitted.evidence.text`), 'Thank you for applying');
});

function mail() {
  const c = {console, URL, TextEncoder, crypto:require('node:crypto').webcrypto, setTimeout(){}, setInterval(){}, document:{}, btoa, atob};
  c.globalThis = c; vm.createContext(c);
  vm.runInContext(read('coop-mail.user.js'), c);
  return c.CoopMail;
}
const acmeJobs = [{id:'a', company:'Acme Corp', role:'Analog Design Co-op', link:'https://boards.greenhouse.io/acme/jobs/111111', status:'applied'},
                  {id:'b', company:'Acme Corp', role:'Test Engineering Co-op', link:'https://boards.greenhouse.io/acme/jobs/222222', status:'applied'}];

test('a rejection that does not say which role changes no application', () => {
  const M = mail(); const state = {a:{status:'applied'}, b:{status:'applied'}};
  M.applyEmails([{text:'Acme Careers Update on your application Unfortunately we will not be moving forward at this time'}], acmeJobs, state);
  assert.equal(state.a.status, 'applied'); assert.equal(state.b.status, 'applied');
  assert.equal(state._mail.unassigned.length, 1);
});

test('a rejection naming one role changes only that role', () => {
  const M = mail(); const state = {a:{status:'applied'}, b:{status:'applied'}};
  M.applyEmails([{text:'Acme Careers Your application for Analog Design Co-op Unfortunately we will not be moving forward'}], acmeJobs, state);
  assert.equal(state.a.status, 'rejected'); assert.equal(state.b.status, 'applied');
});

test('a plain confirmation mentioning next steps is not an interview, and settles an uncertain submit', () => {
  const M = mail();
  assert.equal(M.classify('Thank you for applying to Acme. Next steps: our team will review your application'), 'confirmation');
  assert.equal(M.classify('We would like to invite you to a phone screen'), 'interview');
  const state = {a:{status:'uncertain'}};
  M.applyEmails([{text:'Acme Corp Thank you for applying to Analog Design Co-op'}], [{...acmeJobs[0], status:'uncertain'}], state);
  assert.equal(state.a.status, 'applied'); assert.match(state.a.submitted.evidence.text, /confirmation email/);
});

test('relay records a skipped job and keeps the batch moving', () => {
  const e = environment('https://hrisheekmust-blip.github.io/coop-scraper/relay.html');
  e.c.localStorage.setItem('coop-batch', JSON.stringify({batchId:'b1', ids:[id, other], names:['A','B'], i:0, done:0, finished:false}));
  e.c.localStorage.setItem('coop-q-' + id, JSON.stringify({error:'answers unavailable'}));
  e.c.location.hash = '#go';
  e.run(inline('relay.html'));
  return until(() => JSON.parse(e.c.localStorage.getItem('coop-batch')).i === 1).then(() => {
    assert.equal(JSON.parse(e.c.localStorage.getItem('coop-r-' + id)).status, 'needs-you');
  });
});
