import json
import asyncio
import asyncpg

TASK_ID = '3baa0c9b-1d54-4953-a774-b263dd146d69'
SQL = '''
select
  id,
  bill_name,
  light_status,
  openclaw_review_status,
  review_status,
  confidence,
  quotas,
  alternatives,
  openclaw_suggested_quotas,
  openclaw_review_note,
  openclaw_decision_type
from match_results
where task_id = $1
order by "index"
'''

async def main():
    conn = await asyncpg.connect('postgresql://autoquota:autoquota@localhost:5432/autoquota')
    try:
        rows = await conn.fetch(SQL, TASK_ID)
        data = [dict(r) for r in rows]
        print(json.dumps(data, ensure_ascii=False, default=str))
    finally:
        await conn.close()

asyncio.run(main())
