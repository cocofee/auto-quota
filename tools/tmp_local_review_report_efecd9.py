import asyncio
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / 'tools' / 'jarvis_review_task.py'
spec = importlib.util.spec_from_file_location('jarvis_review_task', MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

TASK_ID = 'efecd9c5-2327-43e7-8534-d36339969ecf'
DB_URL = 'postgresql+asyncpg://autoquota:autoquota2026@localhost:5432/autoquota'

async def main():
    engine = create_async_engine(DB_URL)
    async with engine.connect() as conn:
        task_row = (await conn.execute(text("""
            select id, name, province, original_filename, status, created_at
            from tasks where id=:task_id
        """), {'task_id': TASK_ID})).mappings().first()
        if not task_row:
            raise RuntimeError(f'task not found: {TASK_ID}')
        task = dict(task_row)
        task['pricing_name'] = task.get('province')
        result_rows = (await conn.execute(text("""
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
            from match_results where task_id=:task_id order by index
        """), {'task_id': TASK_ID})).mappings().all()
        items = [dict(row) for row in result_rows]
    await engine.dispose()
    report = mod.generate_report([(task, {'items': items})])
    outs = [
        PROJECT_ROOT / 'reports' / 'lobster_audit' / f'{TASK_ID}_审核报告_v6.1.md',
        PROJECT_ROOT / 'output' / 'tasks' / TASK_ID / '审核报告_v6.1.md',
    ]
    for out in outs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding='utf-8')
        print(out)

asyncio.run(main())
