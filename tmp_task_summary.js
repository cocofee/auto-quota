const fs = require('fs');
const path = 'C:/Users/Administrator/Documents/trae_projects/auto-quota/output/tasks/efecd9c5-2327-43e7-8534-d36339969ecf/results.json';
const raw = fs.readFileSync(path, 'utf8');
const data = JSON.parse(raw);
const items = Array.isArray(data.results) ? data.results : [];
const groupCount = (arr, getter) => {
  const m = new Map();
  for (const item of arr) {
    const k = getter(item) ?? '';
    m.set(k, (m.get(k) || 0) + 1);
  }
  return Array.from(m.entries()).map(([key, count]) => ({ key, count })).sort((a,b)=>String(a.key).localeCompare(String(b.key)));
};
const focus = items.filter(item => {
  const fv = item.final_validation || {};
  return item.light_status === 'yellow' || item.light_status === 'red' || (fv.status && fv.status !== 'ok');
}).map(item => ({
  index: item.bill_item?.index,
  code: item.bill_item?.code,
  bill_name: item.bill_item?.name,
  description: item.bill_item?.description,
  section: item.bill_item?.section,
  quantity: item.bill_item?.quantity,
  unit: item.bill_item?.unit,
  confidence: item.confidence_score,
  light_status: item.light_status,
  final_status: item.final_validation?.status || '',
  issues: Array.isArray(item.final_validation?.issues) ? item.final_validation.issues.map(x => ({type:x.type, severity:x.severity, message:x.message})) : [],
  top1: Array.isArray(item.quotas) && item.quotas[0] ? {quota_id:item.quotas[0].quota_id, name:item.quotas[0].name, unit:item.quotas[0].unit, reason:item.quotas[0].reason} : null,
  alternatives: Array.isArray(item.alternatives) ? item.alternatives.slice(0,5).map(x => ({quota_id:x.quota_id, name:x.name, unit:x.unit, confidence:x.confidence, reason:x.reason})) : [],
  explanation: item.explanation,
  reason_tags: item.reason_tags || [],
  primary_reason: item.primary_reason || '',
  no_match_reason: item.no_match_reason || ''
}));
const summary = {
  total: items.length,
  stats: data.stats || {},
  light_status: groupCount(items, x => x.light_status),
  final_validation_status: groupCount(items, x => x.final_validation?.status || ''),
  focus_count: focus.length,
  focus
};
console.log(JSON.stringify(summary, null, 2));
