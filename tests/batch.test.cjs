const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const inline = file => [...read(file).matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const id = '1234567890abcdef';
const other = 'abcdef1234567890';
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
function storage(seed = {}) {
  const data = {...seed};
  return {getItem:k=>data[k]??null, setItem:(k,v)=>data[k]=String(v), removeItem:k=>delete data[k]};
}
function environment() {
  const elements = new Map();
  const listeners = {};
  const replaced = [];
  const timers = [];
  const c = {console, URL, URLSearchParams, TextEncoder, Uint8Array,
    btoa:s=>Buffer.from(s,'binary').toString('base64'), atob:s=>Buffer.from(s,'base64').toString('binary'),
    escape,unescape,encodeURIComponent,decodeURIComponent,
    crypto:require('node:crypto').webcrypto,
    localStorage:storage(), sessionStorage:storage(), history:{replaceState(){}},
    location:{hash:'',pathname:'/coop-scraper/relay.html',search:'',href:'https://jobs.lever.co/test',replace:u=>replaced.push(u)},
    setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout(){},
    setInterval:fn=>{timers.push(fn);return timers.length},clearInterval(){},
    document:{getElementById:k=>{if(!elements.has(k))elements.set(k,{value:'',textContent:'',innerHTML:'',style:{},classList:{add(){},toggle(){}},addEventListener(){}});return elements.get(k)},querySelectorAll:()=>[],querySelector:()=>null,body:{innerText:''},documentElement:{appendChild(){}},createElement:()=>({style:{}})},
    addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener(){},open:()=>({}),opener:null};
  c.window=c;
  vm.createContext(c);
  return {c,elements,listeners,replaced,timers,run:s=>vm.runInContext(s,c)};
}
function board() {
  const e=environment();
  e.run(inline('index.html').replace('load();setInterval(load,5*60*1000);',''));
  e.run('render=()=>{};drawRender=()=>{};');
  return e;
}
const payload={type:'coop-payload',id,batchId:'batch-1',job:{company:'Test',role:'Intern'},files:[]};

test('userscript consumes relay hash and reports success without window.opener',async()=>{
  const e=environment();e.c.location.hash='#coop='+id+'&p='+encode(payload);
  e.c.document.body.innerText='Thank you for applying';
  e.run(read('coop-apply.user.js'));
  await new Promise(setImmediate);
  assert.equal(e.replaced.length,1);
  const returned=new URL(e.replaced[0]);
  assert.equal(returned.pathname,'/coop-scraper/relay.html');
  const result=JSON.parse(Buffer.from(new URLSearchParams(returned.hash.slice(1)).get('done'),'base64url'));
  assert.equal(result.id,id);assert.equal(result.batchId,'batch-1');assert.equal(result.ok,true);
  assert.equal(e.c.sessionStorage.getItem('coop-payload'),null);
});

test('needs-you result leaves the form open',()=>{
  const e=environment();e.c.location.hash='#coop='+id+'&p='+encode(payload);
  const source=read('coop-apply.user.js').replace(/  run\(\)\.catch[\s\S]*?\n\}\)\(\);/, '  report({ok:false,status:"needs-you",need:["captcha"]});\n})();');
  e.run(source);assert.equal(e.replaced.length,0);
  assert.equal(JSON.parse(e.c.sessionStorage.getItem('coop-payload')).id,id);
});

test('relay advances once and opens the next staged application',async()=>{
  const e=environment();
  const q={batchId:'batch-1',ids:[id,other],names:['First','Second'],i:0,done:0,finished:false};
  e.c.localStorage.setItem('coop-batch',JSON.stringify(q));
  e.c.localStorage.setItem('coop-q-'+other,JSON.stringify({applyUrl:'https://jobs.lever.co/second#old',payload:{...payload,id:other}}));
  e.c.location.hash='#done='+encode({...payload,ok:true});
  e.run(inline('relay.html'));
  assert.equal(JSON.parse(e.c.localStorage.getItem('coop-batch')).i,1);
  assert.equal(JSON.parse(e.c.localStorage.getItem('coop-batch')).done,1);
  e.timers[0]();await new Promise(setImmediate);
  const target=new URL(e.replaced[0]);
  assert.equal(new URLSearchParams(target.hash.slice(1)).get('coop'),other);
  assert.ok(e.c.localStorage.getItem('coop-r-'+id));
  // A replay must not double count or advance the next job.
  const replay=environment();replay.c.localStorage=e.c.localStorage;replay.c.location.hash=e.c.location.hash;
  replay.run(inline('relay.html'));
  assert.equal(JSON.parse(replay.c.localStorage.getItem('coop-batch')).done,1);
});

test('relay rejects results from an older batch',()=>{
  const e=environment();e.c.localStorage.setItem('coop-batch',JSON.stringify({batchId:'new',ids:[id],i:0,done:0}));
  e.c.location.hash='#done='+encode({...payload,ok:true});e.run(inline('relay.html'));
  assert.equal(JSON.parse(e.c.localStorage.getItem('coop-batch')).i,0);
  assert.equal(e.c.localStorage.getItem('coop-r-'+id),null);
});

test('refreshing relay #go preserves queue progress',()=>{
  const e=environment();e.c.localStorage.setItem('coop-batch',JSON.stringify({batchId:'batch-1',ids:[id],i:1,done:1}));
  e.c.location.hash='#go';e.run(inline('relay.html'));
  assert.equal(JSON.parse(e.c.localStorage.getItem('coop-batch')).done,1);
  assert.equal(JSON.parse(e.c.localStorage.getItem('coop-batch')).finished,true);
});

test('conflicting GitHub status write reapplies the mutation to fresh state',async()=>{
  const e=board();const writes=[];let puts=0;
  e.c.fetch=async(url,opt)=>{
    if(opt.method==='PUT'){writes.push(JSON.parse(Buffer.from(JSON.parse(opt.body).content,'base64')));if(!puts++)return {ok:false,status:409};return {ok:true,json:async()=>({content:{sha:'saved'}})}}
    return {ok:true,json:async()=>({sha:'latest',content:Buffer.from(JSON.stringify({remote:{status:'interview'}})).toString('base64')})};
  };
  await e.run('cfg.token="test";writeState(s=>s.mine={status:"applied"})');
  assert.equal(writes.length,2);assert.equal(writes[1].mine.status,'applied');assert.equal(writes[1].remote.status,'interview');
});

test('overlapping status writes are serialized',async()=>{
  const e=board();let active=0,max=0;const writes=[];
  e.c.fetch=async(url,opt)=>{active++;max=Math.max(max,active);await new Promise(setImmediate);writes.push(JSON.parse(Buffer.from(JSON.parse(opt.body).content,'base64')));active--;return {ok:true,json:async()=>({content:{sha:String(writes.length)}})}};
  await e.run('cfg.token="test";Promise.all([writeState(s=>s.first={status:"applied"}),writeState(s=>s.second={status:"applying"})])');
  assert.equal(max,1);assert.equal(writes[1].first.status,'applied');assert.equal(writes[1].second.status,'applying');
});

test('blocked popup keeps the selection and resets batch for retry',async()=>{
  const e=board();e.c.open=()=>null;
  await e.run(`cfg.token="test";rows=[{id:"${id}",company:"Test",role:"Intern",link:"https://jobs.lever.co/test"}];apps["${id}"]={materials:[{name:"Resume",path:"resume.pdf"}]};selected.add("${id}");startBatch(false)`);
  assert.equal(e.run('batch'),null);assert.equal(e.run('selected.size'),1);assert.equal(e.c.localStorage.getItem('coop-batch'),null);
});

test('unsupported and lookalike portal hosts cannot start automation',()=>{
  const e=board();e.run('cfg.token="test"');
  for(const link of ['https://example.myworkdayjobs.com/job','https://jobs.ashbyhq.com.evil.test/job','https://jobs.smartrecruiters.com/job'])assert.equal(e.run(`canOneClick({link:${JSON.stringify(link)},materials:[{}]})`),false);
});

test('failed result sync retains receipt for retry',async()=>{
  const e=board();e.c.fetch=async()=>({ok:false,status:403});
  const raw=JSON.stringify({id,ok:true,at:'2026-09-21',done:[]});
  e.c.localStorage.setItem('coop-r-'+id,raw);e.run('cfg.token="test"');
  await e.run(`consumeResult("coop-r-${id}",${JSON.stringify(raw)})`);
  assert.equal(e.c.localStorage.getItem('coop-r-'+id),raw);
  assert.match(e.elements.get('sub').textContent,/sync failed/);
});

test('failed CSV refresh preserves existing rows and exposes the error',async()=>{
  const e=board();e.c.fetch=async()=>({ok:false,status:503});e.run('rows=[{id:"existing"}]');
  await e.run('load()');assert.equal(e.run('rows[0].id'),'existing');assert.match(e.elements.get('sub').innerHTML,/couldn.*refresh/);
});

test('dry-run flag records submit readiness without clicking submit',async()=>{
  const e=environment();e.c.__coopDry=true;
  e.c.location.hash='#coop='+id;
  e.c.sessionStorage.setItem('coop-payload',JSON.stringify(payload));
  e.c.CoopEngine={answerFor:()=>({})};
  e.c.setTimeout=fn=>setImmediate(fn);
  let clicks=0;
  const button={textContent:'Submit application',offsetWidth:100,click:()=>clicks++};
  e.c.document.querySelector=()=>({});
  e.c.document.querySelectorAll=selector=>selector==='button, input[type=submit]'?[button]:[];
  e.run(read('coop-apply.user.js'));
  for(let i=0;i<8&&!e.c.__coopLast;i++)await new Promise(setImmediate);
  assert.equal(e.c.__coopLast.status,'dry-submit');assert.equal(clicks,0);assert.equal(e.replaced.length,0);
});

test('staging does not release an application when status cannot be saved',async()=>{
  const e=board();e.c.fetch=async()=>({ok:false,status:403});
  e.run(`cfg.token="test";batch={batchId:"batch-1",ids:["${id}"],i:0};buildPayload=async()=>(${JSON.stringify(payload)})`);
  await assert.rejects(e.run(`stage({id:"${id}"})`),/403/);
  assert.equal(e.c.localStorage.getItem('coop-q-'+id),null);
});

test('stopping during payload preparation prevents a late handoff',async()=>{
  const e=board();let release;
  e.c.prepare=()=>new Promise(resolve=>release=resolve);
  e.run(`batch={batchId:"batch-1",ids:["${id}"],i:0};buildPayload=prepare`);
  const staged=e.run(`stage({id:"${id}"})`);
  e.run('batch.finished=true');release({...payload});await staged;
  assert.equal(e.c.localStorage.getItem('coop-q-'+id),null);
});

test('stopped queue records its in-flight result but does not open another job',()=>{
  const e=environment();e.c.localStorage.setItem('coop-batch',JSON.stringify({batchId:'batch-1',ids:[id,other],i:0,done:0,finished:true}));
  e.c.location.hash='#done='+encode({...payload,ok:true});e.run(inline('relay.html'));e.timers[0]();
  assert.equal(JSON.parse(e.c.localStorage.getItem('coop-batch')).done,1);
  assert.equal(e.replaced.length,0);
});
