const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const c={window:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../engine.js'),'utf8'),c);
const E=c.window.CoopEngine;
const profile={first:'Test',last:'Applicant',name:'Test Applicant',email:'test@example.com',phone:'555-555-0100',location:'Boston, MA',country:'United States',degree:'Bachelor of Science',grad:'December 2027',grad_month:'12',grad_year:'2027',school:'Example University',gpa:'3.96',authorized:'Yes',sponsorship:'No',citizen:'Yes, US citizen',relocate:'Yes',eeo:'Decline to self-identify',terms_wanted:['spring'],lang:{python:24}};
const answer=(label,options=[],type='input_text',p=profile)=>E.answerFor({label,options,type},{profile:p});
test('Lightmatter relocation maps to an actual city and never Yes',()=>{
 const r=answer('Select the location you can commute or relocate to',['Mountain View, CA','Boston, MA','Toronto, Canada','Remote'],'multi_value_multi_select');
 assert.equal(r.a,'Boston, MA');assert.deepEqual([...r.values],['Boston, MA']);
 assert.equal(answer('Select the location you can commute or relocate to',['London','Toronto'],'select').k,'need');
});
test('hidden location fields are not questions, visible coordinates require location selection',()=>{
 for(const label of ['Latitude','Longitude']){assert.equal(answer(label,[],'input_hidden').k,'auto');assert.equal(answer(label).k,'need');}
});
test('school keywords cannot answer enrollment or graduate-study plans',()=>{
 for(const q of ['Do you plan to go to graduate school?','Will you return to university after the internship?','Please confirm when you will complete your university studies.','Are you currently enrolled in a 2-year or 4-year college program?']) assert.equal(answer(q,['Yes','No']).k,'need',q);
 assert.equal(answer('School').a,'Example University');
});
test('generic location, work eligibility and mobility keywords cannot invent qualifications',()=>{
 for(const q of ['Are you legally authorized to work in Canada?','Are you legally authorized to work in the country where this job is located?','Are you able to work in-person in San Jose this summer?','Can you work 40 hours per week?','Which mobile robotics systems have you developed?','SpaceX Program Preference','Do you agree to the terms?','Are you willing to undergo a background check?'])assert.equal(answer(q,['Yes','No']).k,'need',q);
 assert.equal(answer('Which mobile robotics systems have you developed?').k,'need');
});
test('yes/no answers honor saved profile values, and never default to Yes',()=>{
 const q='Are you legally authorized to work in the United States?';
 assert.equal(answer(q,['Yes','No']).a,'Yes');
 assert.equal(answer(q,['Yes','No'],'select',{authorized:'No'}).a,'No');
 assert.equal(answer(q,['Yes','No'],'select',{}).k,'need');
 assert.equal(answer(q,['Yes, with restrictions','Yes, without restrictions','No']).k,'need');
});
test('dates do not become degrees and missing day is not invented',()=>{
 assert.equal(answer("Select your anticipated bachelor's degree graduation date",['01/2027','12/2027'],'select').a,'12/2027');
 assert.equal(answer('Expected graduation date',[],'date').k,'need');
 assert.equal(answer('Graduate GPA',['3.9 out of 4.0','Not applicable']).k,'need');
 assert.equal(answer('GPA',['3.9 out of 4.0','4.0 out of 4.0']).k,'need');
 assert.equal(answer('GPA').a,'3.96');
});
test('no nearest experience, first school, arbitrary referral, or first city fallback',()=>{
 for(const [q,opts] of [['School',['Different University','Other']],['Preferred location',['San Francisco','New York']],['How did you hear about us?',['LinkedIn','Referral']],['How many years of Python experience?',['3-5 years','5+ years']]])assert.equal(answer(q,opts,'select').k,'need',q);
});
test('declining sensitive questions requires an offered decline option',()=>{
 assert.equal(answer('Gender',['Woman','Man']).k,'need');
 assert.equal(answer('Gender',['Woman','Man','Prefer not to answer']).a,'Prefer not to answer');
});
test('single versus multiple choices and plus signs are validated without string splitting',()=>{
 const f={label:'Languages',type:'checkbox',options:['C++','Python']};
 const r=E.validateAnswer(f,{a:'C++ + Python',values:['C++','Python']});
 assert.deepEqual([...r.values],['C++','Python']);
 assert.equal(E.validateAnswer({...f,type:'radio'},{a:r.a,values:r.values}).k,'need');
 assert.equal(E.validateAnswer(f,{a:'Java'}).k,'need');
 assert.equal(E.validateAnswer({options:['Boston','boston'],type:'select'},{a:'Boston'}).k,'need');
});
test('question-specific saved answers also must satisfy field options and types',()=>{
 const ctx={answers:{field_answers:{'Favorite tool':'not an option','Start date':'January 2027'}}};
 assert.equal(E.answerFor({label:'Favorite tool',type:'select',options:['A','B']},ctx).k,'need');
 assert.equal(E.answerFor({label:'Start date',type:'date'},ctx).k,'need');
});
test('all stored portal choices are valid or explicitly require review',()=>{
 const forms=JSON.parse(fs.readFileSync(path.join(__dirname,'../data/forms.json')));
 let fields=0;
 for(const form of Object.values(forms))for(const f of form.fields||[]){
  fields++;const r=E.answerFor(f,{profile});
  if(E.hiddenField(f)){assert.equal(r.k,'auto');continue;}
  if(['need','file','skip','auto'].includes(r.k))continue;
  const opts=E.optionsFor(f);
  if(opts.length){assert.ok((r.values||[r.a]).every(a=>opts.includes(a)),f.label);if(!E.multiField(f))assert.equal(r.values.length,1,f.label);}
 }
 assert.ok(fields>1000);
});


test('phone country requires the exact country name, not a shared dial code',()=>{
 assert.equal(answer('Country',['Canada +1','United States +1','United Kingdom +44'],'select').a,'United States +1');
 assert.equal(answer('Country',['Canada +1','United Kingdom +44'],'select').k,'need');
 assert.equal(answer('Country',['United States +1','United States +1'],'select').k,'need');
});

test('city autocomplete matches confirmed city, full state and country without picking another Boston',()=>{
 const p={...profile,city:'Boston',state:'Massachusetts'};
 const opts=['Boston, England, United Kingdom','Boston, New York, United States','East Boston, Massachusetts, United States','Boston, Massachusetts, United States'];
 assert.equal(answer('Location (City)',opts,'select',p).a,opts[3]);
 assert.equal(answer('Location (City)',opts.slice(0,3),'select',p).k,'need');
 assert.equal(answer('Location (City)',['Boston'],'select',p).k,'need');
});
