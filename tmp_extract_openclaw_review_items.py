import json
import sys

sys.path.append(r'C:\Users\Administrator\Documents\trae_projects\auto-quota\tools\openclaw-skill\scripts')
sys.path.append(r'C:\Users\Administrator\Documents\trae_projects\auto-quota\web\backend')
import auto_match
from app.text_utils import repair_mojibake_data

TASK_ID = '3baa0c9b-1d54-4953-a774-b263dd146d69'
API_KEY = 'un_MwK3PEcof0eSWKu7jni3ik8nf2tTmi108ZQkNvU4'
api = auto_match.AutoQuotaAPI('http://127.0.0.1:8000', API_KEY)
data = api._request('GET', f'/api/openclaw/tasks/{TASK_ID}/review-items') or {}
data = repair_mojibake_data(data, preserve_newlines=True)
items = data.get('items') or []
result = []
for item in items:
    quotas = repair_mojibake_data(item.get('quotas') or [], preserve_newlines=True)
    suggested = repair_mojibake_data(item.get('openclaw_suggested_quotas') or [], preserve_newlines=True)
    draft_payload = repair_mojibake_data(item.get('openclaw_review_payload') or {}, preserve_newlines=True)
    candidate_pool = ((draft_payload.get('review_context') or {}).get('candidate_pool') or [])
    top = suggested[:3] if suggested else quotas[:3]
    if not top and candidate_pool:
        top = candidate_pool[:3]
    summary_parts = []
    for q in top:
        if not isinstance(q, dict):
            continue
        q = repair_mojibake_data(q, preserve_newlines=True)
        qid = str(q.get('quota_id') or '').strip()
        name = str(q.get('name') or '').strip()
        unit = str(q.get('unit') or '').strip()
        source = str(q.get('source') or '').strip()
        part = ' | '.join([p for p in [qid, name, unit, source] if p])
        if part:
            summary_parts.append(part)
    if not summary_parts:
        note = str(item.get('openclaw_review_note') or item.get('explanation') or '').strip()
        if note:
            summary_parts.append(note)
    result.append({
        'result_id': item.get('id'),
        'bill_name': repair_mojibake_data(item.get('bill_name') or '', preserve_newlines=True),
        'light_status': item.get('light_status'),
        'openclaw_review_status': item.get('openclaw_review_status'),
        'suggested_quota_summary': '；'.join(summary_parts),
    })
print(json.dumps(result, ensure_ascii=False, indent=2))
