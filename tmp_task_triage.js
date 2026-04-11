const fs = require('fs');
const path = 'C:/Users/Administrator/Documents/trae_projects/auto-quota/output/tasks/efecd9c5-2327-43e7-8534-d36339969ecf/results.json';
const data = JSON.parse(fs.readFileSync(path, 'utf8'));
const items = Array.isArray(data.results) ? data.results : [];
const groups = {
  agree_candidate: [],
  mismatch_needs_review: [],
  recall_failure: [],
  measure_items: [],
  other: []
};
for (const item of items) {
  const issues = (item.final_validation?.issues || []).map(x => x.type);
  const unique = [...new Set(issues)];
  const base = {
    index: item.bill_item?.index,
    code: item.bill_item?.code,
    bill_name: item.bill_item?.name,
    section: item.bill_item?.section,
    confidence: item.confidence_score,
    light_status: item.light_status,
    primary_reason: item.primary_reason,
    issue_types: unique,
    top1_id: item.quotas?.[0]?.quota_id || '',
    top1_name: item.quotas?.[0]?.name || '',
    alt_ids: (item.alternatives || []).slice(0,3).map(x => x.quota_id),
    no_match_reason: item.no_match_reason || '',
  };
  if (item.match_source === 'skip_measure' || item.primary_reason === 'measure_item') {
    groups.measure_items.push(base); continue;
  }
  if (item.primary_reason === 'recall_failure' || !item.quotas?.length) {
    groups.recall_failure.push(base); continue;
  }
  if (unique.length === 1 && unique[0] === 'ambiguity_review' && (item.confidence_score || 0) >= 85) {
    groups.agree_candidate.push(base); continue;
  }
  if (unique.some(x => ['category_mismatch','sleeve_mismatch','connection_mismatch','material_mismatch'].includes(x))) {
    groups.mismatch_needs_review.push(base); continue;
  }
  groups.other.push(base);
}
const result = {};
for (const [k,v] of Object.entries(groups)) {
  result[k] = { count: v.length, items: v.slice(0,40) };
}
console.log(JSON.stringify(result, null, 2));
