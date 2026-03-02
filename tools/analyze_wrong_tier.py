# -*- coding: utf-8 -*-
"""分析跨省benchmark中wrong_tier错误的具体案例"""
import json
import re

with open('tests/cross_province_tests/_latest_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

examples = []
for r in data['results']:
    prov = r['province']
    for d in r['details']:
        if not d['is_match']:
            examples.append({
                'province': prov[:10],
                'bill': d['bill_name'][:25],
                'algo': d['algo_name'][:30],
                'stored': d['stored_names'][0][:30] if d['stored_names'] else '?',
                'algo_id': d['algo_id'],
                'stored_id': d['stored_ids'][0] if d['stored_ids'] else '?',
                'confidence': d['confidence'],
            })

def get_book(qid):
    m = re.match(r'(C?\d+)-', qid)
    if m:
        return m.group(1)
    return ''

for ex in examples:
    stored_kw = set(ex['stored'].replace('(', ' ').replace(')', ' ').split())
    algo_kw = set(ex['algo'].replace('(', ' ').replace(')', ' ').split())
    ignore = {'安装', '制作', '周长', 'mm', 'm2', '以内'}
    stored_kw -= ignore
    algo_kw -= ignore
    overlap = stored_kw & algo_kw
    if len(overlap) > 0:
        ex['cause'] = 'wrong_tier'
    else:
        sb = get_book(ex['stored_id'])
        ab = get_book(ex['algo_id'])
        if sb and ab and sb != ab:
            ex['cause'] = 'wrong_book'
        else:
            ex['cause'] = 'synonym_gap'

wt = [e for e in examples if e['cause'] == 'wrong_tier']
print(f"wrong_tier案例数: {len(wt)}")
print()
for e in wt[:40]:
    print(f"{e['province']:10s} | {e['bill']:25s} | 算:{e['algo']:30s} | 正:{e['stored']:30s}")
