from sqlalchemy import create_engine, text

TASK_ID = '5272b746-d098-4037-af57-c0779c8b6875'
engine = create_engine('postgresql://autoquota:autoquota@localhost:5432/autoquota')

with engine.connect() as conn:
    decision = conn.execute(
        text('select openclaw_decision_type, count(*) from match_results where task_id=:tid group by openclaw_decision_type order by count(*) desc'),
        {'tid': TASK_ID},
    ).fetchall()
    error = conn.execute(
        text('select openclaw_error_type, count(*) from match_results where task_id=:tid group by openclaw_error_type order by count(*) desc'),
        {'tid': TASK_ID},
    ).fetchall()
    sample = conn.execute(
        text('select bill_name, openclaw_decision_type, openclaw_error_type, openclaw_reason_codes from match_results where task_id=:tid order by "index" limit 15'),
        {'tid': TASK_ID},
    ).fetchall()

print('decision', decision)
print('error', error)
print('sample', sample)
