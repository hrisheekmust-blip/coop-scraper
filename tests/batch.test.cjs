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
    location:{hash:'',hostname:'jobs.lever.co',pathname:'/test/apply',search:'',href:'https://jobs.lever.co/test/apply',replace:u=>replaced.push(u)},
    getComputedStyle:()=>({visibility:'visible'}),
    Event:class {constructor(type,options){this.type=type;Object.assign(this,options)}},
    setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout(){},
    setInterval:fn=>{timers.push(fn);return timers.length},clearInterval(){},
    document:{readyState:'complete',getElementById:k=>{if(!elements.has(k))elements.set(k,{value:'',textContent:'',innerHTML:'',style:{},classList:{add(){},toggle(){}},addEventListener(){}});return elements.get(k)},querySelectorAll:()=>[],querySelector:()=>null,body:{innerText:'',querySelectorAll:()=>[]},documentElement:{appendChild(){}},createElement:()=>({style:{},appendChild(){}})},
    addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener(){},open:()=>({}),opener:null};
  c.window=c;c.top=c;c.MouseEvent=c.PointerEvent=c.KeyboardEvent=c.FocusEvent=c.Event;
  vm.createContext(c);
  vm.runInContext(read("engine.js"),c);
  c.document.addEventListener=(name,fn)=>listeners["doc:"+name]=fn;
  c.document.removeEventListener=()=>{};
  return {c,elements,listeners,replaced,timers,run:s=>vm.runInContext(s,c)};
}
function board() {
  const e=environment();
  e.run(inline('index.html').replace('load();setInterval(load,5*60*1000);',''));
  e.run('render=()=>{};drawRender=()=>{};');
  return e;
}
const payload={type:'coop-payload',id,batchId:'batch-1',job:{company:'Test',role:'Intern'},files:[]};
async function until(done) {
  for(let i=0;i<100&&!done();i++)await new Promise(setImmediate);
  assert.ok(done(),'userscript did not reach expected state');
}
function formEnvironment() {
  const e=environment();e.c.setTimeout=fn=>setImmediate(fn);
  e.c.location.hash='#coop='+id+'&p='+encode(payload);
  e.c.CoopEngine={VERSION:9,answerFor:()=>({k:'need'})};
  let clicks=0;
  const button={textContent:'Submit application',offsetWidth:100,getAttribute:()=>null,
    focus(){},scrollIntoView(){},getBoundingClientRect:()=>({left:0,top:0,width:100,height:30}),
    dispatchEvent(){},click:()=>clicks++};
  e.c.document.querySelectorAll=s=>s==='button, input[type=submit], a[role=button], [role=button]'?[button]:[];
  return {...e,button,clicks:()=>clicks};
}
function expose(e) {
  e.run(read('coop-apply.user.js').replace(/  run\(\)\.catch[\s\S]*?\n\}\)\(\);/,
    '  window.testing={finish,report,stallActions,fill,ownOptions,comboShows,matchOption,realClick,labelOf,trackEdit,captureEdited,programmaticWrite};\n})();'));
  return e.c.testing;
}

test('userscript consumes relay hash and reports success without window.opener',async()=>{
  const e=environment();e.c.location.hash='#coop='+id+'&p='+encode(payload);
  e.c.setTimeout=fn=>setImmediate(fn);
  e.c.sessionStorage.setItem('coop-clicked','batch-1:'+id);
  e.c.document.body.innerText='Thank you for applying';
  e.run(read('coop-apply.user.js'));
  await until(()=>e.replaced.length);
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
  const e=formEnvironment();e.c.__coopDry=true;
  e.run(read('coop-apply.user.js'));
  await until(()=>e.c.__coopLast);
  assert.equal(e.c.__coopLast.status,'needs-you');assert.equal(e.clicks(),0);assert.equal(e.replaced.length,0);
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

test('successful submission always returns to relay once even with an opener',()=>{
  const e=formEnvironment(),sent=[];e.c.opener={postMessage:(...m)=>sent.push(m)};
  const api=expose(e);api.finish({ok:true,status:'applied'});api.finish({ok:true,status:'applied'});
  assert.equal(sent.length,0);assert.equal(e.replaced.length,1);
  const r=JSON.parse(Buffer.from(new URLSearchParams(new URL(e.replaced[0]).hash.slice(1)).get('done'),'base64url'));
  assert.equal(r.batchId,payload.batchId);assert.equal(r.id,id);
  assert.equal(e.c.sessionStorage.getItem('coop-payload'),null);
});

test('errors stay open and an explicit skip returns a non-submitted receipt',()=>{
  const e=formEnvironment(),api=expose(e);
  api.finish({ok:false,status:'error',need:['missing answer']});assert.equal(e.replaced.length,0);
  assert.ok(e.c.sessionStorage.getItem('coop-payload'));
  api.stallActions(['missing answer'],[])[1].fn();assert.equal(e.replaced.length,1);
  const r=JSON.parse(Buffer.from(new URLSearchParams(new URL(e.replaced[0]).hash.slice(1)).get('done'),'base64url'));
  assert.equal(r.ok,false);assert.equal(r.batchId,payload.batchId);
});

test('dry-run completion never posts results to the board or redirects',()=>{
  const e=formEnvironment(),sent=[];e.c.__coopDry=true;e.c.opener={postMessage:m=>sent.push(m)};
  expose(e).finish({ok:true,status:'applied'});
  assert.equal(sent.length,0);assert.equal(e.replaced.length,0);assert.ok(e.c.sessionStorage.getItem('coop-payload'));
});

test('invalid or mismatched relay payload cannot reuse stored answers',()=>{
  for(const p of ['broken',encode({...payload,id:other}),encode({...payload,files:null})]) {
    const e=formEnvironment();e.c.sessionStorage.setItem('coop-payload',JSON.stringify(payload));
    e.c.location.hash='#coop='+id+'&p='+p;e.run(read('coop-apply.user.js'));
    assert.equal(e.c.__coopRunning,undefined);assert.equal(e.replaced.length,0);
  }
});

test('confirmation text from a previous batch is not a successful current application',async()=>{
  const e=formEnvironment();e.c.__coopDry=true;e.c.document.body.innerText='Thank you for applying';
  e.c.sessionStorage.setItem('coop-clicked','old-batch:'+id);
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.equal(e.c.__coopLast.status,'needs-you');assert.equal(e.replaced.length,0);
});

test('multi-page dry run advances Next but does not activate Submit',async()=>{
  const e=formEnvironment();e.c.__coopDry=true;let nextClicks=0;
  e.button.textContent='Next'; // Real plain buttons have no aria-label or value.
  e.button.click=()=>{nextClicks++;e.button.textContent='Submit application';e.c.document.body.innerText='Review your application'};
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.equal(e.c.__coopLast.status,'needs-you');assert.equal(nextClicks,1);
});

test('submission waits for user review and a trusted manual submission',async()=>{
  const e=formEnvironment();let activations=0;
  e.button.dispatchEvent=ev=>{if(ev.type==='click')activations++};
  e.button.click=()=>{activations++;e.c.document.body.innerText='Your application has been submitted'};
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.equal(activations,0);assert.equal(e.c.__coopLast.status,'needs-you');
  e.listeners['doc:submit']({isTrusted:true});e.button.click();
  await until(()=>e.replaced.length);assert.equal(activations,1);
  const r=JSON.parse(Buffer.from(new URLSearchParams(new URL(e.replaced[0]).hash.slice(1)).get('done'),'base64url'));
  assert.equal(r.ok,true);assert.equal(r.batchId,payload.batchId);
});

test('empty resume attachment stops submission',async()=>{
  const e=formEnvironment();e.c.__coopDry=true;
  const parent={textContent:'Resume',querySelectorAll:()=>[],querySelector:()=>null};
  const file={tagName:'INPUT',files:[],offsetWidth:100,labels:[{textContent:'Resume'}],parentElement:parent,
    getAttribute:n=>n==='type'?'file':null,closest:()=>parent};
  e.c.document.body.querySelectorAll=s=>s==='input, textarea, select'?[file]:[];
  const query=e.c.document.querySelectorAll;e.c.document.querySelectorAll=s=>s==='input[type=file]'?[file]:query(s);
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.equal(e.c.__coopLast.status,'needs-you');assert.match(e.c.__coopLast.need.join(' '),/resume didn't attach/);
  assert.equal(e.clicks(),0);
});

test('dropdown options are scoped to their control and committed phone values count',()=>{
  const e=formEnvironment(),api=expose(e);
  const option=textContent=>({textContent,offsetWidth:100});
  const correct=[option('United States')],wrong=[option('Previous menu')];
  e.c.document.getElementById=()=>({querySelectorAll:()=>correct});
  e.c.document.querySelectorAll=()=>wrong;
  const input={getAttribute:n=>n==='aria-controls'?'countries':null,closest:()=>null};
  assert.equal(api.ownOptions(input)[0],correct[0]);
  assert.equal(api.comboShows({querySelectorAll:()=>[{textContent:'Canada (+1)'}]},'United States (+1)'),false);
  assert.equal(api.comboShows({querySelectorAll:()=>[{textContent:'United States (+1)'}]},'United States (+1)'),true);
  assert.deepEqual([...api.ownOptions({getAttribute:()=>null,closest:()=>null})],[]);
  assert.equal(api.matchOption(['Yes, need sponsorship','Yes, no sponsorship'],'Yes'),-1);
  assert.equal(api.matchOption(['3.8 out of 4.0','3.9 out of 4.0','4.0 out of 4.0'],'3.96'),-1);
});

test('board preview hides coordinates and shows the valid Lightmatter location',()=>{
  const e=board();
  e.run(`forms.lightmatter={portal:'greenhouse',fields:[
    {label:'Latitude',type:'input_hidden',required:true},
    {label:'Longitude',type:'input_hidden',required:true},
    {label:'Select the location you can commute or relocate to',type:'multi_value_multi_select',required:true,options:['Boston, MA','Toronto, Canada']}
  ]};profile={location:'Boston, MA',relocate:'Yes'}`);
  const html=e.run("qaHtml({id:'lightmatter'}, {})");
  assert.doesNotMatch(html,/Latitude|Longitude|needs your answer/);
  assert.match(html,/>Boston, MA<\/div>/);assert.doesNotMatch(html,/>Yes<\/div>/);
});

test('an outdated answer engine prevents any form activation',async()=>{
  const e=formEnvironment();e.c.__coopDry=true;e.c.CoopEngine.VERSION=6;
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.match(e.c.__coopLast.need.join(' '),/update required/);assert.equal(e.clicks(),0);
});

test('unknown required answers block Next, not just final Submit',async()=>{
  const e=formEnvironment();e.c.__coopDry=true;e.button.textContent='Next';
  const parent={textContent:'Describe your clearance history',querySelectorAll:()=>[],querySelector:()=>null};
  const field={tagName:'INPUT',value:'',required:true,offsetWidth:100,labels:[{textContent:'Describe your clearance history'}],parentElement:parent,
    getAttribute:n=>n==='type'?'text':null,closest:s=>s.includes('aria-hidden')?null:parent};
  e.c.document.body.querySelectorAll=s=>s==='input, textarea, select'?[field]:[];
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.match(e.c.__coopLast.need.join(' '),/clearance history/);assert.equal(e.clicks(),0);
});

test('manual submission cannot be claimed by a synthetic click',async()=>{
  const e=formEnvironment();
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  e.listeners['doc:submit']({isTrusted:false});
  assert.equal(e.c.sessionStorage.getItem('coop-clicked'),null);assert.equal(e.clicks(),0);
  // End the observer with a real user action so the test leaves no timers running.
  e.listeners['doc:submit']({isTrusted:true});e.c.document.body.innerText='Your application has been submitted';
  await until(()=>e.replaced.length);
});


test('wrapped dropdown choices are excluded from the question label',()=>{
  const e=formEnvironment(),api=expose(e);
  const copy={textContent:'Select the location you can commute or relocate to Select Boston, MA Toronto'};
  copy.querySelectorAll=()=>[{remove(){copy.textContent='Select the location you can commute or relocate to'}}];
  const label={textContent:copy.textContent,cloneNode:()=>copy};
  const q=api.labelOf({labels:[label]});
  assert.equal(q,'Select the location you can commute or relocate to');
  const real=environment().c.CoopEngine;
  assert.equal(real.answerFor({label:q,type:'select',options:['Boston, MA','Toronto']},{profile:{location:'Boston, MA'}}).a,'Boston, MA');
});


test('React Select hidden required proxy does not become an unanswered question',async()=>{
  const e=formEnvironment();e.c.__coopDry=true;
  const proxy={tagName:'INPUT',value:'',required:true,offsetWidth:1,getAttribute:n=>n==='type'?'text':n==='aria-hidden'?'true':null,closest:()=>proxy};
  e.c.document.body.querySelectorAll=s=>s==='input, textarea, select'?[proxy]:[];
  e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
  assert.deepEqual([...e.c.__coopLast.need],['final review and manual submission']);assert.equal(e.clicks(),0);
});

test('Greenhouse Attach upload uses its labelled group and not a neighboring file',()=>{
  const e=formEnvironment(),api=expose(e);
  e.c.document.getElementById=id=>({textContent:id==='resume-label'?'Resume/CV*':'Cover Letter'});
  const field=id=>({type:'file',id,labels:[{textContent:'Attach'}],closest:()=>({getAttribute:()=>id+'-label'})});
  assert.equal(api.labelOf(field('resume')),'Resume/CV');
  assert.equal(api.labelOf(field('cover')),'Cover Letter');
});
function automaticForm(){
 const e=formEnvironment();
 e.c.location.hash='#coop='+id+'&p='+encode({...payload,autoSubmit:true});
 const query=e.c.document.querySelectorAll;
 e.c.document.querySelectorAll=s=>s.startsWith('input[type=file], input[name')?[{offsetWidth:10}]:query(s);
 return e;
}
test('authorized automatic submission clicks once and records only confirmed success',async()=>{
 const e=automaticForm();let clicks=0;
 e.button.click=()=>{clicks++;e.c.document.body.innerText='Your application has been submitted'};
 e.run(read('coop-apply.user.js'));await until(()=>e.replaced.length);
 assert.equal(clicks,1);assert.equal(e.c.__coopLast.status,'applied');
});
test('automatic submit dry run never clicks Submit',async()=>{
 const e=automaticForm();e.c.__coopDry=true;
 e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
 assert.equal(e.c.__coopLast.status,'dry-submit');assert.equal(e.clicks(),0);
});
test('automatic submission timeout does not retry or mark applied',async()=>{
 const e=automaticForm();e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
 assert.equal(e.clicks(),1);assert.equal(e.c.__coopLast.status,'needs-you');assert.equal(e.replaced.length,0);
 assert.equal(e.c.sessionStorage.getItem('coop-auto-attempt'),'batch-1:'+id);
});
test('reload with an in-flight automatic attempt cannot click Submit again',async()=>{
 const e=automaticForm();e.c.sessionStorage.setItem('coop-auto-attempt','batch-1:'+id);
 e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
 assert.equal(e.clicks(),0);assert.equal(e.replaced.length,0);
});
test('automatic mode cannot activate an unknown final action',async()=>{
 const e=automaticForm();e.button.textContent='Review application';
 e.run(read('coop-apply.user.js'));await until(()=>e.c.__coopLast);
 assert.equal(e.clicks(),0);assert.match(e.c.__coopLast.need[0],/Review this final action/);
});
test('new board payload explicitly enables automatic submission',async()=>{
 const e=board();const p=await e.run(`buildPayload({id:'${id}',materials:[],answers_obj:{}})`);
 assert.equal(p.autoSubmit,true);
});

test('new payload carries saved answers and keeps employer-specific answers scoped',async()=>{
 const e=board();e.run(`cacheLearned([CoopEngine.learnedRecord('Favorite tool','Vim',{id:'a'})])`);
 const p=await e.run(`buildPayload({id:'${id}',materials:[],answers_obj:{}})`);
 assert.equal(p.learnedAnswers[0].values[0],'Vim');
});
test('batch preflight groups identical questions but separates job-specific commitments',async()=>{
 const e=board();e.run(`rows=[{id:'a',company:'A'},{id:'b',company:'B'}];forms={a:{fields:[{label:'Favorite tool',required:true},{label:'Can you attend our office?',required:true}]},b:{fields:[{label:'Favorite tool',required:true},{label:'Can you attend our office?',required:true}]}}`);
 await e.run(`prepareBatch(['a','b'])`);
 const html=e.elements.get('prepbody').innerHTML;
 assert.equal((html.match(/data-prep=/g)||[]).length,3);assert.match(html,/A, B/);
});
test('learned answers stay cached when private backup fails',async()=>{
 const e=board();e.run('cfg.token="test"');e.c.fetch=async()=>({ok:false,status:503});
 await assert.rejects(e.run(`saveLearned([CoopEngine.learnedRecord('Favorite tool','Vim',{id:'a'})])`));
 assert.match(e.c.localStorage.getItem('coop-learned:hrisheekmust-blip/coop-apps'),/Vim/);
 assert.equal(e.c.localStorage.getItem('coop-learned:hrisheekmust-blip/coop-apps:pending'),'1');
});
test('deferred batch question advances without claiming a submission',async()=>{
 const e=formEnvironment();e.c.location.hash='#coop='+id+'&p='+encode({...payload,deferMissing:true,autoSubmit:true});
 const parent={textContent:'Favorite tool',querySelectorAll:()=>[],querySelector:()=>null};
 const field={tagName:'INPUT',value:'',required:true,offsetWidth:10,labels:[{textContent:'Favorite tool'}],parentElement:parent,getAttribute:n=>n==='type'?'text':null,closest:s=>s.includes('aria-hidden')?null:parent};
 e.c.document.body.querySelectorAll=s=>s==='input, textarea, select'?[field]:[];
 e.run(read('coop-apply.user.js'));await until(()=>e.replaced.length);
 const r=JSON.parse(Buffer.from(new URLSearchParams(new URL(e.replaced[0]).hash.slice(1)).get('done'),'base64url'));
 assert.equal(r.ok,false);assert.equal(r.deferred,true);assert.equal(e.clicks(),0);assert.equal(r.questions[0].label,'Favorite tool');
});
test('Save answers button never marks an unsubmitted application as applied',()=>{
 const e=formEnvironment(),api=expose(e);api.stallActions(['missing'],[])[0].fn();
 assert.equal(e.replaced.length,0);assert.notEqual(e.c.__coopLast?.status,'applied');
});


test('only real user edits are learned, then reused with the same question',()=>{
 const e=formEnvironment();e.c.CoopEngine=environment().c.CoopEngine;const api=expose(e);
 const field={isConnected:true,tagName:'INPUT',type:'text',value:'Vim',labels:[{textContent:'Favorite tool'}],getAttribute:()=>null,closest:()=>null,matches:()=>true};
 api.trackEdit({isTrusted:false,type:'change',target:field});api.captureEdited();
 assert.equal(JSON.parse(e.c.sessionStorage.getItem('coop-payload')).learnedAnswers.length,0);
 api.trackEdit({isTrusted:true,type:'change',target:field});api.captureEdited();
 const records=JSON.parse(e.c.sessionStorage.getItem('coop-payload')).learnedAnswers;
 assert.equal(records.length,1);assert.equal(records[0].values[0],'Vim');
});
test('answer backup retries a conflict without discarding another saved answer',async()=>{
 const e=board();e.run('cfg.token="test"');let puts=0,posted;
 const remote=[{label:'Other question',values:['Other answer'],jobId:'',updatedAt:'2000-01-01'}];
 e.c.fetch=async(url,opt)=>{if(opt.method==='PUT'){puts++;posted=JSON.parse(Buffer.from(JSON.parse(opt.body).content,'base64').toString());return puts===1?{ok:false,status:409}:{ok:true,json:async()=>({})}}
 return {ok:true,json:async()=>({sha:'current',content:Buffer.from(JSON.stringify(remote)).toString('base64')})}};
 await e.run(`saveLearned([CoopEngine.learnedRecord('Favorite tool','Vim',{id:'a'})])`);
 assert.equal(puts,2);assert.equal(posted.length,2);assert.ok(posted.some(r=>r.label==='Other question'));
});


test('trusted input emitted by browser autofill commands is never learned',()=>{
 const e=formEnvironment();e.c.CoopEngine=environment().c.CoopEngine;const api=expose(e);
 const field={isConnected:true,tagName:'INPUT',type:'text',value:'Generated',labels:[{textContent:'Favorite tool'}],getAttribute:()=>null,closest:()=>null,matches:()=>true};
 api.programmaticWrite(field,()=>api.trackEdit({isTrusted:true,type:'input',target:field}));api.captureEdited();
 assert.equal(JSON.parse(e.c.sessionStorage.getItem('coop-payload')).learnedAnswers.length,0);
 api.trackEdit({isTrusted:true,type:'input',target:field});api.captureEdited();
 assert.equal(JSON.parse(e.c.sessionStorage.getItem('coop-payload')).learnedAnswers.length,1);
});


test('Ashby Yes/No buttons remember the most recent human choice',()=>{
 const e=formEnvironment();e.c.CoopEngine=environment().c.CoopEngine;const api=expose(e);
 const entry={querySelector:()=>({textContent:'Do you own a soldering iron?'})};
 const button=value=>({isConnected:true,tagName:'BUTTON',textContent:value,closest:s=>s==='.ashby-application-form-field-entry'?entry:null,getAttribute:()=>null});
 const yes=button('Yes'),no=button('No');
 for(const b of [yes,no,yes])api.trackEdit({isTrusted:true,type:'click',target:{closest:s=>s.includes('button')?b:null}});
 api.captureEdited();const records=JSON.parse(e.c.sessionStorage.getItem('coop-payload')).learnedAnswers;
 assert.equal(records.length,1);assert.equal(records[0].values[0],'Yes');
});
