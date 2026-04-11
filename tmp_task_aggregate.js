const fs = require('fs');
const path = 'C:/Users/Administrator/Documents/trae_projects/auto-quota/output/tasks/efecd9c5-2327-43e7-8534-d36339969ecf/results.json';
const data = JSON.parse(fs.readFileSync(path, 'utf8'));
const items = Array.isArray(data.results) ? data.results : [];
const add = (m,k,v=1)=>m.set(k,(m.get(k)||0)+v);
const issueCounts=new Map();
const top1Counts=new Map();
const sectionCounts=new Map();
const finalStatusCounts=new Map();
const lightCounts=new Map();
const primaryReasonCounts=new Map();
const noMatchReasons=new Map();
const examplesByIssue={};
for(const item of items){
  add(lightCounts,item.light_status||'');
  add(finalStatusCounts,item.final_validation?.status||'');
  add(primaryReasonCounts,item.primary_reason||'');
  add(sectionCounts,item.bill_item?.section||'');
  if(item.no_match_reason) add(noMatchReasons,item.no_match_reason);
  const top1=item.quotas?.[0];
  if(top1?.quota_id) add(top1Counts, `${top1.quota_id} | ${top1.name}`);
  for(const iss of item.final_validation?.issues||[]){
    const key=iss.type||'';
    add(issueCounts,key);
    if(!examplesByIssue[key]) examplesByIssue[key]=[];
    if(examplesByIssue[key].length<5){
      examplesByIssue[key].push({
        index:item.bill_item?.index,
        code:item.bill_item?.code,
        name:item.bill_item?.name,
        section:item.bill_item?.section,
        confidence:item.confidence_score,
        top1_id:top1?.quota_id||'',
        top1_name:top1?.name||'',
        issue:iss.message||''
      });
    }
  }
}
const topN = (m,n=20)=>Array.from(m.entries()).sort((a,b)=>b[1]-a[1]).slice(0,n).map(([key,count])=>({key,count}));
const summary={
  total:items.length,
  stats:data.stats||{},
  lightCounts:topN(lightCounts,10),
  finalStatusCounts:topN(finalStatusCounts,10),
  primaryReasonCounts:topN(primaryReasonCounts,20),
  issueCounts:topN(issueCounts,20),
  sectionCounts:topN(sectionCounts,20),
  top1Counts:topN(top1Counts,20),
  noMatchReasons:topN(noMatchReasons,20),
  examplesByIssue
};
console.log(JSON.stringify(summary,null,2));
