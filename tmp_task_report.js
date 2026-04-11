const fs = require('fs');
const path = 'C:/Users/Administrator/Documents/trae_projects/auto-quota/output/tasks/efecd9c5-2327-43e7-8534-d36339969ecf/results.json';
const data = JSON.parse(fs.readFileSync(path, 'utf8'));
const items = Array.isArray(data.results) ? data.results : [];

function scoreText(t) {
  const cjk = (t.match(/[\u3400-\u9fff]/g) || []).length;
  const bad = (t.match(/[ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]/g) || []).length;
  const rep = (t.match(/�/g) || []).length;
  const q = (t.match(/\?/g) || []).length;
  return [cjk, -bad, -rep, -q];
}
function better(a, b) {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] > b[i];
  return false;
}
function repairText(text) {
  if (typeof text !== 'string' || !text) return text;
  const candidates = [text];
  for (const enc of ['latin1', 'binary']) {
    try {
      const repaired = Buffer.from(text, enc).toString('utf8');
      if (!candidates.includes(repaired)) candidates.push(repaired);
    } catch {}
  }
  let best = candidates[0], bestScore = scoreText(best);
  for (const cand of candidates.slice(1)) {
    const s = scoreText(cand);
    if (better(s, bestScore)) { best = cand; bestScore = s; }
  }
  return best;
}
function rt(v){ return typeof v === 'string' ? repairText(v).replace(/\s+/g,' ').trim() : v; }
function add(m,k){ k=rt(k)||''; m.set(k,(m.get(k)||0)+1); }
function topN(map,n=10){ return [...map.entries()].sort((a,b)=>b[1]-a[1]).slice(0,n); }
const light=new Map(), finalS=new Map(), primary=new Map(), issue=new Map(), section=new Map(), nomatch=new Map();
const examples={};
for(const item of items){
  add(light, item.light_status);
  add(finalS, item.final_validation?.status);
  add(primary, item.primary_reason);
  add(section, item.bill_item?.section);
  if(item.no_match_reason) add(nomatch, item.no_match_reason);
  for(const iss of item.final_validation?.issues || []){
    add(issue, iss.type);
    const key=rt(iss.type)||'';
    examples[key] ||= [];
    if(examples[key].length < 4){
      examples[key].push({
        idx: item.bill_item?.index,
        code: item.bill_item?.code,
        name: rt(item.bill_item?.name),
        section: rt(item.bill_item?.section),
        conf: item.confidence_score,
        top1: `${rt(item.quotas?.[0]?.quota_id||'')} ${rt(item.quotas?.[0]?.name||'')}`.trim(),
        msg: rt(iss.message)
      });
    }
  }
}
let out = [];
out.push(`TOTAL ${items.length}`);
out.push(`STATS matched=${data.stats?.matched} high=${data.stats?.high_conf} mid=${data.stats?.mid_conf} low=${data.stats?.low_conf}`);
out.push('LIGHT_STATUS');
for(const [k,v] of topN(light,10)) out.push(`- ${k}: ${v}`);
out.push('FINAL_STATUS');
for(const [k,v] of topN(finalS,10)) out.push(`- ${k}: ${v}`);
out.push('PRIMARY_REASONS');
for(const [k,v] of topN(primary,10)) out.push(`- ${k}: ${v}`);
out.push('ISSUES');
for(const [k,v] of topN(issue,10)) out.push(`- ${k}: ${v}`);
out.push('SECTIONS');
for(const [k,v] of topN(section,10)) out.push(`- ${k}: ${v}`);
out.push('NO_MATCH_REASONS');
for(const [k,v] of topN(nomatch,10)) out.push(`- ${k}: ${v}`);
out.push('EXAMPLES');
for (const [k, rows] of Object.entries(examples)) {
  out.push(`ISSUE ${k}`);
  for (const r of rows) out.push(`- idx=${r.idx} code=${r.code} section=${r.section} name=${r.name} conf=${r.conf} top1=${r.top1} msg=${r.msg}`);
}
console.log(out.join('\n'));
