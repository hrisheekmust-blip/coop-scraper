/* shared answer engine: portal question -> what gets typed. Used by the site and the apply userscript. */
(function(){
const STD_FIELDS=[["Name","input_text"],["Email","input_text"],["Phone","input_text"],["Location (city)","input_text"],["LinkedIn profile","input_text"],["Resume","file"],["Cover letter","file"],["School / University","input_text"],["Degree","input_text"],["Major","input_text"],["Expected graduation date","input_text"],["GPA","input_text"],["Are you legally authorized to work in the United States?","yes_no"],["Will you now or in the future require sponsorship?","yes_no"],["Are you willing to relocate?","yes_no"],["Earliest start date / availability","input_text"],["How did you hear about us?","input_text"],["Gender / Race / Veteran / Disability (voluntary)","eeo"]];
const pickOpt=(opts,rx,fallback)=>{const o=(opts||[]).find(x=>rx.test(x));return o||fallback};
/* "how much experience in C++?" against whatever buckets the portal offers
   ("Less than 3 months" / "3-5 months" / "10 months +" / "0-1 years" / "1-3 years" ...) */
function monthsOf(opt){const t=opt.toLowerCase();const yr=/year/.test(t);const n=(t.match(/\d+(?:\.\d+)?/g)||[]).map(Number).map(x=>yr?x*12:x);
  if(/less than|under|<|no experience|none|^0\b/.test(t)&&n.length<2)return[0,n[0]!==undefined?n[0]:0.01];
  if(/\+|more than|over|at least|>/.test(t)&&n.length)return[n[0],1e4];
  if(n.length>=2)return[n[0],n[1]]; if(n.length===1)return[n[0],n[0]]; return null}
function pickMonths(opts,months){let best=-1,bd=1e9;
  opts.forEach((o,i)=>{const r=monthsOf(o);if(!r)return;
    if(months>=r[0]&&months<=r[1]){const cur=best>=0&&bd===0?(monthsOf(opts[best])||[0])[0]:-1;if(bd>0||r[0]>cur){bd=0;best=i}return}
    const d=months<r[0]?r[0]-months:months-r[1];if(d<bd){bd=d;best=i}});
  return best}
const LANGQ=/(?:experience|proficien|familiar|worked|used|skill level|rate your)[^?]*?\b(systemverilog|verilog|vhdl|matlab|python|javascript|typescript|java|c\+\+|c#|golang|go|rust|c|labview|altium|cadence|spice|simulink)(?![\w+#])/;
function answerFor(f,ctx){const j=ctx.job||{},a=ctx.answers||{},l=(f.label||"").toLowerCase().replace(/\s+/g," ").trim(),o=f.options||[],p=ctx.profile||{},t=(f.type||"").toLowerCase();
  const yes=v=>o.length?pickOpt(o,v?/^(yes|y\b|i am|true)/i:/^(no\b|n\b|i am not|false)/i,v?"Yes":"No"):(v?"Yes":"No");
  if(f.eeo||/gender|race|ethnicit|hispanic|latino|veteran|disabilit|pronoun|self.?identif|sexual orientation|transgender/.test(l))return{a:pickOpt(o,/decline|don.t wish|prefer not|do not wish|do not want|don.t want|not to answer|rather not|no answer|not disclose/i,p.eeo||"Decline to self-identify"),k:"eeo"};
  if(/cover letter/.test(l)){if(!ctx.cover)return{a:"skip (cover letter off for this application)",k:"file"};if(/file|upload|attach/.test(t)||/attach|upload/.test(l))return{a:"upload: Hrisheek_Mustyala_Cover_Letter.pdf",k:"file"};return ctx.coverText?{a:ctx.coverText,k:"long"}:{a:"upload: Hrisheek_Mustyala_Cover_Letter.pdf",k:"file"}}
  if(/\b(resume|cv)\b/.test(l))return{a:"upload: "+((j.materials||[])[0]||{}).path?.split("/").pop()||"resume PDF",k:"file"};
  if(/\bsat\b/.test(l)&&!/\bact\b/.test(l))return{a:o.length?pickOpt(o,/not applicable|n\/a|did not|none|prefer not|no sat/i,""):"",k:o.length?"":"need"};
  if(/\bact\b|test score/.test(l))return{a:o.length?pickOpt(o,/\b3[4-6]\b|^36|33-36|30\+/,""):(p.test_scores||"ACT 36"),k:o.length?"":""};
  if(LANGQ.test(l)){const key=l.match(LANGQ)[1].replace(/^\s+|\s+$/g,"");const months=(p.lang||{})[key];
    if(months===undefined)return{a:"",k:"need"};
    if(!o.length)return{a:months>=12?Math.round(months/12)+" year"+(months>=24?"s":""):months+" months"};
    const i=pickMonths(o,months);return i<0?{a:"",k:"need"}:{a:o[i]}}
  if(/\bgre\b|gmat/.test(l))return{a:"",k:"need"};
  if(/gpa|grade point/.test(l))return{a:p.gpa||"3.96"};
  if(/full-?time (immediately|after|upon|following)|return offer|convert(ing)? to full/.test(l))return{a:o.length?yes(true):(p.fulltime_after||"Yes")};
  if(/preferred (first )?name|nickname|what should we call/.test(l))return{a:p.preferred||"Hrisheek"};
  if(/^(first name|given name)/.test(l))return{a:p.first||"Hrisheek"};
  if(/^(last name|surname|family name)/.test(l))return{a:p.last||"Mustyala"};
  if(/^(full )?(legal )?name$|^name\b|your name/.test(l))return{a:p.name||"Hrisheek Mustyala"};
  if(/e-?mail/.test(l))return{a:p.email||"mustyala.h@northeastern.edu"};
  if(/phone|mobile/.test(l))return{a:p.phone||"858-868-0863"};
  if(/linkedin/.test(l))return{a:p.linkedin||""};
  if(/github/.test(l))return{a:p.github||""};
  if(/portfolio|website|personal site|url/.test(l))return{a:p.portfolio||p.github||""};
  if(/street|address line|mailing address/.test(l))return{a:"",k:"need"};
  if(/\bcity\b|current location|where (are you|do you) (located|based|live)|^location|address|your location/.test(l))return{a:o.length?pickOpt(o,/^united states|^usa?\b|^u\.s\./i,pickOpt(o,/boston|massachusetts/i,o[0])):(p.location||"Boston, MA")};
  if(/countr/.test(l))return{a:p.country||"United States"};
  if(/zip|postal/.test(l))return{a:p.zip||"02115"};
  if(/school|universit|college|institution/.test(l))return{a:p.school||"Northeastern University"};
  if(/degree|education level|highest level|program type|level of study|type of program/.test(l))return{a:pickOpt(o,/bachelor|b\.?s\b|undergrad/i,p.degree||"Bachelor of Science")};
  if(/major|field of study|discipline|concentration/.test(l))return{a:pickOpt(o,/electrical|computer eng/i,p.major||"Electrical and Computer Engineering")};
  if(/(end|graduation|completion|finish)( date)?.*\bmonth\b/.test(l))return{a:pickOpt(o,/^dec/i,"December")};
  if(/(end|graduation|completion|finish)( date)?.*\byear\b/.test(l))return{a:pickOpt(o,/^2027$/,"2027")};
  if(/^(education |school )?start date.*\bmonth\b/.test(l))return{a:pickOpt(o,/^sep/i,"September")};
  if(/^(education |school )?start date.*\byear\b/.test(l))return{a:pickOpt(o,/^2024$/,"2024")};
  if(/location preference|preferred (office|location|work location)|which (office|location|site)|office location/.test(l))return{a:o.length?pickOpt(o,/boston|massachusetts|\bma\b/i,pickOpt(o,/any|no preference|open|flexible|all/i,o[0])):(p.location||"Boston, MA")};
  if(/export control|\bitar\b|protected individual|u\.?s\.? person/.test(l))return{a:o.length?pickOpt(o,/citizen|^yes|u\.?s\.? person|protected/i,"Yes"):"Yes, I am a U.S. citizen"};
  if(/graduat|completion date|expected.*date/.test(l))return{a:o.length?pickOpt(o,/2027/,p.grad||"December 2027"):(p.grad||"December 2027")};
  if(/\byear\b|class standing|academic level|current level|student status/.test(l))return{a:pickOpt(o,/junior|third|3rd|undergrad/i,p.year||"Third year (junior)")};
  if(/term|season|semester|quarter|which.*(period|session)|when.*available|availab|start date|earliest/.test(l)){
    if(o.length&&o.length<=3&&o.some(x=>/^yes/i.test(x)))return{a:yes(true)};
    if(o.length){const hits=o.filter(x=>/winter|spring|\bjan|co-?op|6.?month|jan(uary)?\s*[-–]/i.test(x)&&!/summer|fall|autumn/i.test(x));return{a:hits.length?hits.join(" + "):"(none match Jan–Jun 2027)",k:hits.length?"":"need"}}
    return{a:p.availability||"January through June 2027"}}
  if(/sponsor|visa/.test(l))return{a:yes(false)};
  if(/citizen/.test(l))return{a:o.length?pickOpt(o,/^(yes|u\.?s\.? citizen|united states)/i,"Yes"):(p.citizen||"Yes")};
  if(/authori[sz]ed|legally|eligible to work|work permit|right to work|work authorization/.test(l))return{a:o.length?pickOpt(o,/citizen|authorized|^yes/i,"Yes"):"Yes"};
  if(/(willing|able) to (obtain|get|maintain|hold|undergo|submit|complete|pass)|drug (test|screen)|background (check|investigation|screen)|hours per week|full.?time hours|commit to|for the (full|entire) duration/.test(l))return{a:yes(true)};
  if(/scholarship|fellowship|participant of|member of (the|any) (following|program)|affiliated with/.test(l))return{a:o.length?pickOpt(o,/none|^no\b|not applicable|n\/a|not a /i,o[o.length-1]):"None"};
  if(/clearance/.test(l))return{a:o.length?pickOpt(o,/^(no|none)/i,"No"):(p.clearance||"No")};
  if(/relocat/.test(l))return{a:yes(true)};
  if(/\bstate\b|province/.test(l)&&!/united states|statement/.test(l))return{a:p.state||"Massachusetts"};
  if(/able to work|willing to work|can you work|commute/.test(l)&&/in.?person|on-?site|office|relocat/.test(l))return{a:yes(true)};
  if(/remote|hybrid|on-?site|in.?person|office/.test(l))return{a:o.length?pickOpt(o,/on-?site|in.?person|any|either|open/i,o[0]):(p.remote||"Any")};
  if(/salary|compensation|pay|hourly|wage/.test(l))return{a:p.salary||"Open"};
  if(/previously|former|current(ly)? (employee|work)|worked (at|for|here)|ever (been )?employed/.test(l))return{a:yes(false)};
  if(/refer|know anyone|employee (name|referral)/.test(l))return{a:o.length?yes(false):(p.referral||"No")};
  if(/18|age of majority|legal age/.test(l))return{a:yes(true)};
  if(/how did you .*(hear|find|learn|discover)|source|where did you/.test(l))return{a:o.length?pickOpt(o,/career|website|linkedin|company|job board/i,o[0]):(p.hear||"Company careers page")};
  if(/language/.test(l)&&/spoken|speak|fluent|native|foreign|bilingual|verbal/.test(l))return{a:p.languages||"English"};
  if(/^why\b|why (do you want|are you interested|.*company|.*us\b|.*role|.*position)|interest(ed)? in|what (draws|attracts|excites)|motivat/.test(l))return{a:a.why||"",k:"long"};
  if(/achievement|proud|accomplish|technical (project|work)|projects? (you|that)|most (impressive|significant)|built|hardest|bullet|exceptional|stand ?out|impressive/.test(l))return{a:a.top_two?a.top_two.map((x,i)=>`${i+1}. ${x.title}\n${x.body}`).join("\n\n"):"",k:"long"};
  if(/about yourself|summary|introduce|background|tell us/.test(l))return{a:a.about||"",k:"long"};
  if(/agree|consent|acknowledg|certify|privacy|terms/.test(l))return{a:yes(true)};
  if(/additional|anything else|comments|notes|questions for us|if (you answered|other)|please specify|if applicable/.test(l))return{a:"(leave blank)"};
  return{a:"",k:"need"}}

window.CoopEngine={answerFor,pickOpt,STD_FIELDS};
})();
