/* Shared conservative answers. Unknown facts and ambiguous choices require review. */
(function () {
  const VERSION = 9;
  const STD_FIELDS = [["Name","input_text"],["Email","input_text"],["Phone","input_text"],["Location (city)","input_text"],["LinkedIn profile","input_text"],["Resume","file"],["Cover letter","file"],["School / University","input_text"],["Degree","input_text"],["Major","input_text"],["Expected graduation date","input_text"],["GPA","input_text"]];
  const norm = s => String(s ?? "").normalize("NFKC").toLowerCase().replace(/[✱*]/g, "").replace(/[’‘]/g, "'").replace(/\s+/g, " ").trim();
  const labelKey = s => norm(s).replace(/[?:]+$/, "").trim();
  const need = reason => ({a:"", k:"need", reason:reason || "No confirmed answer for this question"});
  const hiddenField = f => /^(input_)?hidden$/i.test(f.type || "");
  const multiField = f => /^(checkbox|multivalueselect|multi_value_multi_select|select-multiple)$/i.test(f.type || "") || f.multiple === true;
  const booleanField = f => /^(boolean|yes_no)$/i.test(f.type || "");
  function optionsFor(f) {
    return Array.isArray(f.options) && f.options.length ? f.options.map(String) : booleanField(f) ? ["Yes","No"] : [];
  }
  const uniqueMatch = (opts, predicate) => { const hits = opts.filter(predicate); return hits.length === 1 ? hits[0] : null; };
  const pickOpt = (opts, rx, fallback = "") => uniqueMatch(opts || [], x => rx.test(x)) || fallback;
  function validateAnswer(f, answer) {
    if (!answer || ["need","auto","file","skip"].includes(answer.k)) return answer || need();
    const opts = optionsFor(f), values = answer.values || [answer.a];
    if (!values.length || values.some(v => v === undefined || v === null || !String(v).trim())) return need("No saved answer");
    if (opts.length) {
      if (values.length > 1 && !multiField(f)) return need("A single choice is required");
      const mapped = values.map(v => uniqueMatch(opts, o => norm(o) === norm(v)));
      if (mapped.some(v => v === null)) return need("Saved answer does not match one unambiguous option");
      return {...answer, a:mapped.join(" + "), values:mapped};
    }
    if (/select|radio|checkbox/i.test(f.type || "")) return need("Read the available choices on the application form");
    if (/^(number|input_number)$/i.test(f.type || "") && !/^-?\d+(\.\d+)?$/.test(String(answer.a))) return need("An exact numeric answer is needed");
    if (/^(date|input_date)$/i.test(f.type || "") && !/^\d{4}-\d{2}-\d{2}$/.test(String(answer.a))) return need("Choose the exact date; a day has not been confirmed");
    return answer;
  }
  const bool = v => /^(yes|true)(\b|$)/i.test(String(v)) ? true : /^(no|false)(\b|$)/i.test(String(v)) ? false : null;
  // Only explicit user answers enter this memory; never learn generated guesses.
  const memoryKey = (label, jobId = "") => JSON.stringify([labelKey(label), jobId]);
  const privateQuestion = label => /password|passcode|one.time|verification code|social security|ssn|passport|bank account|routing number|credit card|api key|access token|signature/i.test(label);
  const contextualQuestion = label => /\b(this|our|us|here|role|position|company|employer|office|relocat\w*|commut\w*|salary|compensation|start date|availability|available|willing|agree|consent|certify|acknowledge|previously|ever)\b/i.test(label);
  function learnedRecord(label, value, job, type = "text", options = []) {
    if (!labelKey(label) || privateQuestion(label)) return null;
    const values = (Array.isArray(value) ? value : [value]).map(v => String(v).trim()).filter(Boolean);
    if (!values.length || values.some(v => v.length > 6000)) return null;
    return {label:String(label).slice(0,1000),values,jobId:contextualQuestion(label)?String(job?.id||""):"",type,options:options.map(String).slice(0,300),updatedAt:new Date().toISOString()};
  }
  function mergeLearned(...lists) {
    const out = new Map();
    for (const r of lists.flat()) {
      if (!r || typeof r.label !== "string" || privateQuestion(r.label) || !Array.isArray(r.values)) continue;
      const key = memoryKey(r.label, r.jobId || ""), old = out.get(key);
      if (!old || String(r.updatedAt||"") >= String(old.updatedAt||"")) out.set(key,r);
    }
    return [...out.values()];
  }
  function remembered(f, ctx) {
    const matches = (ctx.learnedAnswers || []).filter(r => !r.deleted && labelKey(r.label) === labelKey(f.label) && (!r.jobId || r.jobId === ctx.job?.id));
    const r = matches.find(r => r.jobId === ctx.job?.id) || matches.find(r => !r.jobId);
    return r ? validateAnswer(f,{a:r.values.join(" + "),values:r.values,source:"your saved answer"}) : null;
  }
  function propose(f, ctx) {
    const l = labelKey(f.label), p = ctx.profile || {}, a = ctx.answers || {}, o = optionsFor(f);
    const saved = key => p[key] === undefined || p[key] === "" ? need("Not saved in your profile") : {a:String(p[key]), source:key};
    const choose = (value, predicate) => {
      if (value === undefined || value === null || value === "") return need("Not saved in your profile");
      if (!o.length) return {a:String(value)};
      const exact = uniqueMatch(o, x => norm(x) === norm(value));
      const match = exact || (predicate && uniqueMatch(o, predicate));
      return match ? {a:match, values:[match]} : need("No unambiguous choice matches your profile");
    };
    const yesNo = key => { const v = bool(p[key]); return v === null ? need("This preference or fact is not confirmed") : choose(v ? "Yes" : "No"); };
    if (hiddenField(f)) return {a:"Filled by the application form", k:"auto"};
    if (/^(latitude|longitude|country_short_name)$/.test(l)) return need("Select your location on the application form; do not guess coordinates");
    const overrides = a.field_answers || {};
    const override = Object.keys(overrides).find(k => labelKey(k) === l);
    const memoryAnswer = remembered(f,ctx); if (memoryAnswer) return memoryAnswer;
    if (override) { const value = overrides[override]; return Array.isArray(value) ? {a:value.join(" + "),values:value} : {a:String(value)}; }
    if (f.eeo || /^(gender|race|ethnicity|veteran ?status|disability ?status|pronouns)$/.test(l) || /^(how would you describe your (gender identity|racial|sexual orientation)|do you identify as transgender)/.test(l)) {
      if (!/decline|not.*(answer|disclos)|self.identify/i.test(p.eeo || "")) return need("No saved disclosure preference");
      const decline = uniqueMatch(o, x => /decline|don.t wish|prefer not|do not wish|do not want|don.t want|not to answer|rather not|no answer|not disclose/i.test(x));
      return decline ? {a:decline,k:"eeo"} : need("No matching decline-to-answer choice");
    }
    if (/^(resume(\/cv)?|cv|upload (your )?(resume|cv)|attach (your )?(resume|cv))$/.test(l)) return {a:"Prepared resume PDF",k:"file"};
    if (/^(cover letter|upload (your )?cover letter|attach (your )?cover letter)$/.test(l)) {
      if (!ctx.cover) return {a:"Cover letter off",k:"skip"};
      return /file|upload|attach/i.test(f.type || "") ? {a:"Prepared cover letter PDF",k:"file"} : ctx.coverText ? {a:ctx.coverText,k:"long"} : need("No prepared cover letter text");
    }
    // A graduate GPA is not an undergraduate GPA; never round to the nearest option.
    if (/^(current |cumulative |undergraduate |undergrad )?(gpa|grade point average)( \(undergraduate\))?$/.test(l)) return saved("gpa");
    if (/gpa|grade point/.test(l)) return need("This GPA question needs an exact scale and degree-level answer");
    if (/^(expected |anticipated )?(graduation date|date of graduation)$/.test(l) || /^select your anticipated bachelor'?s degree graduation date$/.test(l)) return choose(p.grad, x => p.grad_month && p.grad_year && x === `${String(p.grad_month).padStart(2,"0")}/${p.grad_year}`);
    if (/^(expected |anticipated )?graduation (month|year)$/.test(l)) return saved(l.endsWith("year") ? "grad_year" : "grad_month");
    if (/graduat|completion date|finish.*stud|complete.*stud/.test(l)) return need("Confirm the exact education dates or plans requested");
    const fields = [
      [/^(first name|given name|legal first name)$/, "first"],
      [/^(last name|surname|family name|legal last name)$/, "last"],
      [/^(preferred (first )?name|nickname)$/, "preferred"],
      [/^(name|full name|legal name|full legal name)$/, "name"],
      [/^(e-?mail( address)?|your e-?mail( address)?)$/, "email"],
      [/^(phone( number)?|mobile( phone)?( number)?|telephone( number)?)$/, "phone"],
      [/^(linkedin( profile)?( url)?|linkedin link)$/, "linkedin"],
      [/^(github( profile)?( url)?|github link)$/, "github"],
      [/^(website|websites|portfolio( url)?|personal (website|site)|github or portfolio url)$/, "portfolio"],
      [/^(city|current city)$/, "city"],
      [/^(state|province|state\/province)$/, "state"],

      [/^(zip( code)?|postal code|zip\/postal code|what is the zip code of your primary residence)$/, "zip"],
      [/^(school|university|college|college or university|school \/ university|school name|university name|institution)$/, "school"],
      [/^(major|field of study|major or field of study|discipline|concentration)$/, "major"],
      [/^(academic year|class standing|year in school|current year of study)$/, "year"],
      [/^(desired salary|salary expectations|expected salary|desired compensation)$/, "salary"]
    ];
    // A phone-country choice includes its dialing code; match its country name,
    // never the dialing code alone (many countries share +1).
    if (/^(country|country of residence|current country)$/.test(l)) return choose(p.country, x => norm(x.replace(/\s+\+\d+$/, "")) === norm(p.country));
    for (const [rx,key] of fields) if (rx.test(l)) return saved(key);
    if (/^(location( \(city\))?|current location|your location|where are you (located|based)|where do you live)$/.test(l)) return choose(p.location, x => p.location && [p.location,`${p.location}, ${p.country}`, ...(p.city && p.state && p.country ? [`${p.city}, ${p.state}, ${p.country}`] : [])].some(v => norm(x) === norm(v)));
    if (/^(select the location you can commute or relocate to|location preference|preferred (office|location|work location)|which (office|location|site)( would you prefer)?)$/.test(l)) {
      const locations = p.preferred_locations || (p.location ? [p.location] : []);
      const matches = o.filter(x => locations.some(v => norm(v) === norm(x)));
      if (multiField(f) && matches.length) return {a:matches.join(" + "),values:matches};
      return matches.length === 1 ? {a:matches[0]} : need("Choose the office location(s) you want");
    }
    if (/^(degree|education level|highest (education|degree) level|current program type|program type|level of study|type of program)$/.test(l)) return choose(p.degree, x => /bachelor/i.test(p.degree || "") && /^(bachelor'?s( degree)?|bachelor of science|undergraduate)$/.test(norm(x)));
    if (/^(sat or act score|test scores?)$/.test(l)) return saved("test_scores");
    if (/^(act( score)?|act composite score)$/.test(l)) return saved("act");
    if (/^(earliest start date|availability|when are you available|when can you start)$/.test(l)) return saved("availability");
    if (/^(which term\(s\) would you like to be considered for|preferred term|which (term|semester|season))$/.test(l)) {
      const terms = (p.terms_wanted || []).map(norm);
      const matches = o.filter(x => !/\b20\d{2}\b/.test(x) && terms.some(t => norm(x).split(/[^a-z]+/).includes(t)));
      if (multiField(f) && matches.length) return {a:matches.join(" + "),values:matches};
      return matches.length === 1 ? {a:matches[0]} : need("Confirm the term and dates");
    }
    // Only unconditional saved yes/no facts. Never infer other countries, dates,
    // time commitments, consents, background checks or hypothetical qualifications.
    if (/^(are you (legally |currently )?authorized to work in (the )?(united states|u\.?s\.?)(, on a full time basis without restriction)?|are you legally eligible to work in (the )?united states)$/.test(l)) return yesNo("authorized");
    if (/^(will you now or in the future require sponsorship( for employment visa status)?|do you( now or in the future)? require( visa| employment)? sponsorship)$/.test(l)) return yesNo("sponsorship");
    if (/^(are you (a )?(u\.?s\.?|united states) citizen)$/.test(l)) return yesNo("citizen");
    if (/^(citizenship status|what is your work authorization status)$/.test(l)) {
      if (bool(p.citizen) !== true || !/us citizen|u\.s\. citizen|united states/i.test(p.citizen || "")) return need("Confirm your citizenship/authorization category");
      return choose("US Citizen", x => /^(\([a-z]\) )?(u\.?s\.?|united states) citizen( or (national of the united states|permanent resident))?$/i.test(x));
    }
    if (/^(are you willing to relocate|would you be willing to relocate)$/.test(l)) return yesNo("relocate");
    if (/^(are you (at least |over )18( years (old|of age))?( or older)?)$/.test(l)) return yesNo("over18");
    if (/^(have you (previously|ever) (worked for|been employed by) (us|this company))$/.test(l)) return yesNo("prior_employee");
    if (/^(do you have an? (active )?security clearance)$/.test(l)) return yesNo("clearance");
    if (/^how did you (hear about|find|learn about|discover) (us|this (job|role|position|opportunity)|[\w .&-]+)$/.test(l)) return choose(p.hear, x => norm(p.hear) === "company careers page" && /^(company website|company careers page|careers (page|website))$/.test(norm(x)));
    if (/^why (do you want to (join|work (at|for)) [\w .&-]+|are you interested in (this (role|position)|working (at|for) [\w .&-]+))$/.test(l)) return a.why ? {a:a.why,k:"long"} : need("No prepared answer to this question");
    if (/^please provide 2-3 bullet points showcasing exceptional ability\.?$/.test(l)) return a.top_two?.length ? {a:a.top_two.map((x,i)=>`${i+1}. ${x.title}\n${x.body}`).join("\n\n"),k:"long"} : need();
    return need();
  }
  function answerFor(f, ctx = {}) { return validateAnswer(f, propose(f,ctx)); }
  window.CoopEngine = {memoryKey,learnedRecord,mergeLearned,remembered,contextualQuestion,VERSION,answerFor,validateAnswer,hiddenField,multiField,optionsFor,pickOpt,STD_FIELDS};
})();
