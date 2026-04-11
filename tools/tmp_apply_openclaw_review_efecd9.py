import json
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

TASK_ID = 'efecd9c5-2327-43e7-8534-d36339969ecf'
DB_DSN = 'postgresql://autoquota:autoquota2026@localhost:5432/autoquota'
SUMMARY_PATH = Path(r'C:\Users\Administrator\Documents\trae_projects\auto-quota\output\tasks\efecd9c5-2327-43e7-8534-d36339969ecf\openclaw_review_summary.json')

summary = json.loads(SUMMARY_PATH.read_text(encoding='utf-8'))
confirm = set(summary['confirm_idx'])
review = set(summary['review_idx'])
wrong = set(summary['wrong_idx'])

conn = psycopg2.connect(DB_DSN)
try:
    with conn, conn.cursor() as cur:
        cur.execute("select index, bill_name, quota_name from match_results where task_id=%s order by index", (TASK_ID,))
        rows = cur.fetchall()
        updated = 0
        for idx, bill_name, quota_name in rows:
            if idx in confirm:
                review_status = 'confirmed'
                review_note = 'OpenClaw二审：当前匹配与清单名称/材质规格/连接方式一致，离线复审通过，确认保留当前结果。'
                oc_status = 'reviewed'
                oc_note = 'OpenClaw二审：低风险可确认，建议直接作为最终结果保留。'
                oc_conf = 0.92
                decision = 'agree'
                err_stage = 'unknown'
                err_type = 'low_confidence_override'
                reason_codes = ['offline_confirmed', 'name_spec_consistent']
                confirm_status = 'pending'
            elif idx in wrong:
                review_status = 'pending'
                review_note = f'OpenClaw二审：当前匹配疑似错误，暂不确认。清单“{bill_name}”与定额“{quota_name or "空候选"}”存在明显错配，建议人工改判。'
                oc_status = 'reviewed'
                oc_note = 'OpenClaw二审：识别为明显错配，保留待人工复核，不建议直接采用当前结果。'
                oc_conf = 0.28
                decision = 'abstain'
                err_stage = 'arbiter'
                err_type = 'wrong_family' if (not quota_name or '安装' not in (quota_name or '') and bill_name not in ['螺纹阀门','法兰阀门','套管']) else 'wrong_param'
                reason_codes = ['offline_wrong_match', 'needs_manual_review']
                confirm_status = 'pending'
            else:
                review_status = 'pending'
                review_note = 'OpenClaw二审：当前结果存在一定合理性，但仍需人工复核后定稿，暂不直接确认。'
                oc_status = 'reviewed'
                oc_note = 'OpenClaw二审：方向基本可用，但证据不够稳，先保留人工复核。'
                oc_conf = 0.55
                decision = 'abstain'
                err_stage = 'unknown'
                err_type = 'unknown'
                reason_codes = ['offline_borderline', 'needs_manual_review']
                confirm_status = 'pending'

            cur.execute(
                """
                update match_results
                   set review_status=%s,
                       review_note=%s,
                       openclaw_review_status=%s,
                       openclaw_review_note=%s,
                       openclaw_review_confidence=%s,
                       openclaw_review_actor='openclaw@system.local',
                       openclaw_review_time=now(),
                       openclaw_decision_type=%s,
                       openclaw_error_stage=%s,
                       openclaw_error_type=%s,
                       openclaw_reason_codes=%s,
                       openclaw_review_confirm_status=%s
                 where task_id=%s and index=%s
                """,
                (
                    review_status,
                    review_note,
                    oc_status,
                    oc_note,
                    oc_conf,
                    decision,
                    err_stage,
                    err_type,
                    Json(reason_codes),
                    confirm_status,
                    TASK_ID,
                    idx,
                ),
            )
            updated += 1
        print(updated)
finally:
    conn.close()
