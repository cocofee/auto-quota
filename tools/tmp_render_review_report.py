import sys
import importlib.util
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

PROJECT_ROOT = Path('/app')
MODULE_PATH = PROJECT_ROOT / 'tools' / 'jarvis_review_task.py'
spec = importlib.util.spec_from_file_location('jarvis_review_task', MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

task_id = '637f43d6-f313-40a7-9199-b9e649454bef'
conn = psycopg2.connect('postgresql://autoquota:autoquota@postgres:5432/autoquota')
try:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("select id, name, province, original_filename, status, created_at from tasks where id=%s", (task_id,))
        task = dict(cur.fetchone())
        task['pricing_name'] = task.get('province')
        cur.execute("""
            select index, id, bill_code, bill_name, bill_description, bill_unit, bill_quantity,
                   specialty, sheet_name, section, quotas, corrected_quotas, confidence as confidence,
                   confidence_score, review_risk, light_status, match_source, explanation,
                   candidates_count, alternatives, is_measure_item, trace, review_status, review_note,
                   openclaw_review_status, openclaw_suggested_quotas, openclaw_review_note,
                   openclaw_review_confidence, openclaw_review_actor, openclaw_review_time,
                   openclaw_decision_type, openclaw_error_stage, openclaw_error_type,
                   openclaw_retry_query, openclaw_reason_codes, openclaw_review_payload,
                   openclaw_review_confirm_status, openclaw_review_confirmed_by,
                   openclaw_review_confirm_time, human_feedback_payload, created_at
            from match_results where task_id=%s order by index
        """, (task_id,))
        items = [dict(row) for row in cur.fetchall()]
    report = mod.generate_report([(task, {'items': items})])
    out1 = PROJECT_ROOT / 'reports' / 'lobster_audit' / f'{task_id}_审核报告_v6.1.md'
    out1.parent.mkdir(parents=True, exist_ok=True)
    out1.write_text(report, encoding='utf-8')
    out2 = PROJECT_ROOT / 'output' / 'tasks' / task_id / '审核报告_v6.1.md'
    out2.parent.mkdir(parents=True, exist_ok=True)
    out2.write_text(report, encoding='utf-8')
    print(str(out1))
    print(str(out2))
finally:
    conn.close()
