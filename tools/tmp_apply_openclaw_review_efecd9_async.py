import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TASK_ID = 'efecd9c5-2327-43e7-8534-d36339969ecf'
DB_URL = 'postgresql+asyncpg://autoquota:autoquota2026@localhost:5432/autoquota'
SUMMARY_PATH = Path(r'C:\Users\Administrator\Documents\trae_projects\auto-quota\output\tasks\efecd9c5-2327-43e7-8534-d36339969ecf\openclaw_review_summary.json')

summary = json.loads(SUMMARY_PATH.read_text(encoding='utf-8'))
confirm = set(summary['confirm_idx'])
wrong = set(summary['wrong_idx'])

async def main():
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        rows = (await conn.execute(text("select index, bill_name, quotas from match_results where task_id=:task_id order by index"), {'task_id': TASK_ID})).all()
        updated = 0
        for idx, bill_name, quotas in rows:
            quota_name = ''
            if isinstance(quotas, list) and quotas:
                first = quotas[0] or {}
                quota_name = first.get('name') or first.get('quota_name') or ''
            if idx in confirm:
                payload = dict(
                    review_status='confirmed',
                    review_note='OpenClaw二审：当前匹配与清单名称/材质规格/连接方式一致，离线复审通过，确认保留当前结果。',
                    openclaw_review_status='reviewed',
                    openclaw_review_note='OpenClaw二审：低风险可确认，建议直接作为最终结果保留。',
                    openclaw_review_confidence=0.92,
                    openclaw_decision_type='agree',
                    openclaw_error_stage='unknown',
                    openclaw_error_type='low_confidence_override',
                    openclaw_reason_codes=json.dumps(['offline_confirmed','name_spec_consistent'], ensure_ascii=False),
                    openclaw_review_confirm_status='pending',
                )
            elif idx in wrong:
                err_type = 'wrong_family'
                if quota_name and any(k in quota_name for k in ['公称直径','外径','法兰','螺纹']) and any(k in (bill_name or '') for k in ['阀门','套管']):
                    err_type = 'wrong_param'
                payload = dict(
                    review_status='pending',
                    review_note=f'OpenClaw二审：当前匹配疑似错误，暂不确认。清单“{bill_name}”与定额“{quota_name or "空候选"}”存在明显错配，建议人工改判。',
                    openclaw_review_status='reviewed',
                    openclaw_review_note='OpenClaw二审：识别为明显错配，保留待人工复核，不建议直接采用当前结果。',
                    openclaw_review_confidence=0.28,
                    openclaw_decision_type='abstain',
                    openclaw_error_stage='arbiter',
                    openclaw_error_type=err_type,
                    openclaw_reason_codes=json.dumps(['offline_wrong_match','needs_manual_review'], ensure_ascii=False),
                    openclaw_review_confirm_status='pending',
                )
            else:
                payload = dict(
                    review_status='pending',
                    review_note='OpenClaw二审：当前结果存在一定合理性，但仍需人工复核后定稿，暂不直接确认。',
                    openclaw_review_status='reviewed',
                    openclaw_review_note='OpenClaw二审：方向基本可用，但证据不够稳，先保留人工复核。',
                    openclaw_review_confidence=0.55,
                    openclaw_decision_type='abstain',
                    openclaw_error_stage='unknown',
                    openclaw_error_type='unknown',
                    openclaw_reason_codes=json.dumps(['offline_borderline','needs_manual_review'], ensure_ascii=False),
                    openclaw_review_confirm_status='pending',
                )
            await conn.execute(text("""
                update match_results
                   set review_status=:review_status,
                       review_note=:review_note,
                       openclaw_review_status=:openclaw_review_status,
                       openclaw_review_note=:openclaw_review_note,
                       openclaw_review_confidence=:openclaw_review_confidence,
                       openclaw_review_actor='openclaw@system.local',
                       openclaw_review_time=now(),
                       openclaw_decision_type=:openclaw_decision_type,
                       openclaw_error_stage=:openclaw_error_stage,
                       openclaw_error_type=:openclaw_error_type,
                       openclaw_reason_codes=cast(:openclaw_reason_codes as json),
                       openclaw_review_confirm_status=:openclaw_review_confirm_status
                 where task_id=:task_id and index=:idx
            """), {**payload, 'task_id': TASK_ID, 'idx': idx})
            updated += 1
    await engine.dispose()
    print(updated)

asyncio.run(main())
