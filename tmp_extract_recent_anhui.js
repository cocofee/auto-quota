const fs=require('fs');
const paths=[
 'output/tasks/925dea7d-9bec-44a5-9f36-8748272062c3/results.json',
 'output/tasks/637f43d6-f313-40a7-9199-b9e649454bef/results.json',
 'output/tasks/715f7911-69e0-47cf-8c74-ee215ec28ad6/results.json'
];
function walk(v, out, currentTaskId=''){
  if(Array.isArray(v)) return v.forEach(x=>walk(x,out,currentTaskId));
  if(!v || typeof v!=='object') return;
  const inheritedTaskId = currentTaskId || String(v.task_id||v.id||'');
  const province = String(v.province||v.task_province||v.source_province||v.project_province||'');
  const bill = String(v.bill_name||'');
  const resultId = String(v.result_id||v.id||'');
  const note = String(v.openclaw_review_note||'');
  const decision = String(v.openclaw_decision_type||'');
  const confirm = String(v.openclaw_review_confirm_status||'');
  const status = String(v.openclaw_review_status||'');
  const hasReviewField = decision || confirm || status || note || Array.isArray(v.openclaw_suggested_quotas);
  const text = JSON.stringify(v);
  if(/安徽/.test(text) && (bill || hasReviewField)){
    const sugg = Array.isArray(v.openclaw_suggested_quotas) ? v.openclaw_suggested_quotas.slice(0,3).map(x=>`${x.quota_id||''} ${x.name||''}`.trim()) : [];
    out.push({taskId: String(v.task_id||inheritedTaskId||''),resultId,bill,province,decision,confirm,status,note,sugg,light_status:v.light_status,confidence:v.confidence,index:v.index});
  }
  for(const k of Object.keys(v)) walk(v[k],out,inheritedTaskId);
}
for(const p of paths){
  try{
    const data=JSON.parse(fs.readFileSync(p,'utf8'));
    const out=[]; walk(data,out);
    console.log('FILE',p,'COUNT',out.length);
    const uniq=[]; const seen=new Set();
    for(const item of out){ const key=[item.taskId,item.resultId,item.bill,item.index].join('|'); if(!seen.has(key)){seen.add(key); uniq.push(item);} }
    for(const item of uniq.slice(0,40)) console.log(JSON.stringify(item));
  }catch(e){ console.log('ERR',p,e.message); }
}
