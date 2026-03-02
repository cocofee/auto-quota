# -*- coding: utf-8 -*-
"""分析跨省benchmark中wrong_tier错误的具体子类型"""
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
            examples.append({
                'province': prov[:10],
                'bill': d['bill_name'],
                'algo': d['algo_name'],
                'stored': d['stored_names'][0] if d['stored_names'] else '?',
                'algo_id': d['algo_id'],
                'stored_id': d['stored_ids'][0] if d['stored_ids'] else '?',
            })

def get_book(qid):
    m = re.match(r'(C?\d+)-', qid)
    if m:
        return m.group(1)
    return ''

# 分类根因
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

# 细分wrong_tier的子类型
sub_types = Counter()
sub_examples = {}

for e in wt:
    algo = e['algo']
    stored = e['stored']

    # 提取差异特征
    if any(kw in algo for kw in ['≤125', '≤150', '≤200', '≤300']) and any(kw in stored for kw in ['≤20', '≤25', '≤32', '≤40', '≤50', '≤65', '≤80', '≤100']):
        sub = 'DN选档过大'
    elif '截面' in algo and '截面' in stored:
        # 检查截面数值是否不同
        a_sec = re.search(r'截面.*?(\d+(?:\.\d+)?)', algo)
        s_sec = re.search(r'截面.*?(\d+(?:\.\d+)?)', stored)
        if a_sec and s_sec and a_sec.group(1) != s_sec.group(1):
            sub = '截面选档错'
        else:
            sub = '截面子类型错'
    elif ('明装' in algo and '暗装' in stored) or ('暗装' in algo and '明装' in stored) or \
         ('明配' in algo and '暗配' in stored) or ('暗配' in algo and '明配' in stored) or \
         ('落地' in algo and '墙上' in stored) or ('落地' in algo and '挂墙' in stored) or \
         ('墙上' in stored and '落地' in algo):
        sub = '安装方式错'
    elif ('单相' in algo and '三相' in stored) or ('三相' in algo and '单相' in stored) or \
         ('单联' in algo and '双联' in stored) or ('双联' in algo and '单联' in stored) or \
         ('单控' in algo and '双控' in stored) or ('双控' in algo and '单控' in stored):
        sub = '相数/联数/控数错'
    elif ('公称直径' in algo or '管径' in algo or '外径' in algo) and ('公称直径' in stored or '管径' in stored or '外径' in stored):
        # DN都有但数值不同
        a_dn = re.search(r'(\d+)\s*$', algo.split('直径')[-1] if '直径' in algo else algo.split('管径')[-1] if '管径' in algo else algo)
        s_dn = re.search(r'(\d+)\s*$', stored.split('直径')[-1] if '直径' in stored else stored.split('管径')[-1] if '管径' in stored else stored)
        sub = 'DN选档错(非最大档)'
    elif '周长' in algo and '周长' in stored:
        sub = '周长选档错'
    elif ('穿照明线' in algo and '穿动力线' in stored) or ('穿动力线' in algo and '穿照明线' in stored):
        sub = '照明/动力线错'
    elif ('砌筑' in algo and '混凝土' in stored) or ('混凝土' in algo and '砌筑' in stored):
        sub = '结构类型错(砌筑vs混凝土)'
    elif ('室内' in algo and '室外' in stored) or ('室外' in algo and '室内' in stored):
        sub = '室内/室外错'
    elif '防爆' in algo and '防爆' not in stored:
        sub = '误选防爆'
    elif '自闭阀' in algo or '连体水箱' in algo or '感应' in algo:
        sub = '卫生器具子类型错'
    elif ('岩棉' in algo and '橡塑' in stored) or ('橡塑' in algo and '岩棉' in stored):
        sub = '保温材料错'
    elif ('碳钢' in algo and '合金' in stored) or ('合金' in algo and '碳钢' in stored) or \
         ('中压' in algo and '低压' in stored) or ('低压' in algo and '中压' in stored):
        sub = '管道材质/压力等级错'
    elif ('离心' in algo and '轴流' in stored) or ('轴流' in algo and '离心' in stored):
        sub = '风机类型错'
    elif ('微穿孔' in algo and '管式' in stored) or ('管式' in algo and '微穿孔' in stored):
        sub = '消声器类型错'
    elif ('电缆沟' in algo and '室内' in stored) or ('室内' in algo and '电缆沟' in stored):
        sub = '电缆敷设方式错'
    elif '>24' in algo and '≤24' in stored:
        sub = '设备规格边界错'
    else:
        sub = '其他子类型'

    sub_types[sub] += 1
    if sub not in sub_examples:
        sub_examples[sub] = []
    sub_examples[sub].append(e)

# 汇总统计
total_errors = len([e for e in examples if e['cause'] != 'correct'])
print(f"=== 错误分布汇总 ===")
print(f"总错误: {len(examples)}条")
print(f"  synonym_gap: {len([e for e in examples if e['cause']=='synonym_gap'])}条")
print(f"  wrong_tier: {len(wt)}条")
print(f"  wrong_book: {len([e for e in examples if e['cause']=='wrong_book'])}条")
print()

print(f"=== wrong_tier子类型（{len(wt)}条） ===")
for sub, count in sub_types.most_common():
    pct = count / len(wt) * 100
    print(f"  {sub:25s}: {count:3d}条 ({pct:4.1f}%)")
    # 打印前2个例子
    for ex in sub_examples[sub][:2]:
        print(f"    {ex['province']:8s} | {ex['bill'][:18]:18s} | 算:{ex['algo'][:35]:35s} | 正:{ex['stored'][:35]}")
    print()
