# -*- coding: utf-8 -*-
"""深入分析wrong_tier中"其他子类型"和"DN选档错"的具体模式"""
import json
import re
from collections import Counter

with open('tests/cross_province_tests/_latest_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

examples = []
for r in data['results']:
    prov = r['province']
    for d in r['details']:
        if not d['is_match']:
            stored_kw = set((d['stored_names'][0] if d['stored_names'] else '').replace('(', ' ').replace(')', ' ').split())
            algo_kw = set(d['algo_name'].replace('(', ' ').replace(')', ' ').split())
            ignore = {'安装', '制作', '周长', 'mm', 'm2', '以内'}
            stored_kw -= ignore
            algo_kw -= ignore
            overlap = stored_kw & algo_kw
            if len(overlap) > 0:
                examples.append({
                    'province': prov[:10],
                    'bill': d['bill_name'],
                    'algo': d['algo_name'],
                    'stored': d['stored_names'][0] if d['stored_names'] else '?',
                    'algo_id': d['algo_id'],
                    'stored_id': d['stored_ids'][0] if d['stored_ids'] else '?',
                })

# 分析"DN选档错(非最大档)" — 定额名里都有"直径"或"外径"或"管径"，但数值不同
print("=== DN选档错(非最大档) — 详细 ===")
dn_wrong = []
for e in examples:
    algo = e['algo']
    stored = e['stored']
    has_dn_algo = any(kw in algo for kw in ['公称直径', '管径', '外径', '介质管道公称直径'])
    has_dn_stored = any(kw in stored for kw in ['公称直径', '管径', '外径', '介质管道公称直径'])
    if has_dn_algo and has_dn_stored:
        # 提取两边DN值
        a_dn = re.search(r'(?:直径|外径|管径)\s*(?:\([^)]*\))?\s*[≤≥<>]?\s*(\d+)', algo)
        s_dn = re.search(r'(?:直径|外径|管径)\s*(?:\([^)]*\))?\s*[≤≥<>]?\s*(\d+)', stored)
        if a_dn and s_dn:
            dn_a = int(a_dn.group(1))
            dn_s = int(s_dn.group(1))
            if dn_a != dn_s:
                dn_wrong.append({**e, 'dn_algo': dn_a, 'dn_stored': dn_s})

print(f"DN不同的案例: {len(dn_wrong)}条")
# 按差异方向分类
dn_too_big = [e for e in dn_wrong if e['dn_algo'] > e['dn_stored']]
dn_too_small = [e for e in dn_wrong if e['dn_algo'] < e['dn_stored']]
print(f"  算法DN > 正确DN（选了大档）: {len(dn_too_big)}条")
print(f"  算法DN < 正确DN（选了小档）: {len(dn_too_small)}条")
print()

for e in dn_too_big[:10]:
    print(f"  {e['province']:8s} | DN{e['dn_algo']:>4d}→应{e['dn_stored']:>4d} | {e['bill'][:20]:20s} | 算:{e['algo'][:50]}")
print()
for e in dn_too_small[:5]:
    print(f"  {e['province']:8s} | DN{e['dn_algo']:>4d}→应{e['dn_stored']:>4d} | {e['bill'][:20]:20s} | 算:{e['algo'][:50]}")

# 分析"其他子类型" — 不含DN/截面等数值差异的wrong_tier
print()
print("=" * 80)
print("=== 其他子类型 — 找共性模式 ===")
other = []
for e in examples:
    algo = e['algo']
    stored = e['stored']
    has_num_param = any(kw in algo and kw in stored for kw in ['公称直径', '管径', '外径', '截面', '周长', '半周长'])
    if not has_num_param:
        other.append(e)

# 按省份统计
prov_count = Counter(e['province'] for e in other)
print(f"其他子类型: {len(other)}条")
print(f"按省份: {dict(prov_count.most_common())}")
print()

# 打印前30条
for e in other[:30]:
    print(f"  {e['province']:8s} | {e['bill'][:20]:20s} | 算:{e['algo'][:45]:45s} | 正:{e['stored'][:45]}")
