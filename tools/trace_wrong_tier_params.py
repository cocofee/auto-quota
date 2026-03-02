# -*- coding: utf-8 -*-
"""追踪所有wrong_tier案例的参数提取情况，找出还有哪些参数类型提取失败"""
import json
import re
import sys
from collections import Counter

# 添加项目路径
sys.path.insert(0, '.')
from src.text_parser import TextParser

parser = TextParser()

with open('tests/cross_province_tests/_latest_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 收集所有wrong_tier案例
wrong_tiers = []
for r in data['results']:
    prov = r['province']
    for d in r['details']:
        if not d['is_match']:
            stored_kw = set((d['stored_names'][0] if d['stored_names'] else '').replace('(', ' ').replace(')', ' ').split())
            algo_kw = set(d['algo_name'].replace('(', ' ').replace(')', ' ').split())
            ignore = {'安装', '制作', '周长', 'mm', 'm2', '以内'}
            stored_kw -= ignore
            algo_kw -= ignore
            if len(stored_kw & algo_kw) > 0:
                wrong_tiers.append({
                    'province': prov[:10],
                    'bill': d['bill_name'],
                    'algo': d['algo_name'],
                    'stored': d['stored_names'][0] if d['stored_names'] else '?',
                    'algo_id': d['algo_id'],
                    'stored_id': d['stored_ids'][0] if d['stored_ids'] else '?',
                })

print(f"=== 参数提取诊断（{len(wrong_tiers)}条wrong_tier） ===\n")

# 对每条wrong_tier，用text_parser分析算法结果和正确答案
param_fail_types = Counter()
param_fail_examples = {}

for e in wrong_tiers:
    algo_params = parser.parse(e['algo'])
    stored_params = parser.parse(e['stored'])
    
    # 比较关键参数
    key_params = ['dn', 'cable_section', 'power_kw', 'weight_t', 'current_a', 'length_mm', 'width_mm', 'perimeter_mm']
    
    diff_params = []
    for p in key_params:
        a_val = algo_params.get(p)
        s_val = stored_params.get(p)
        if a_val is not None or s_val is not None:
            if a_val != s_val:
                diff_params.append(f"{p}: 算={a_val} 正={s_val}")
    
    # 找出名称中有数值但提取不出来的情况
    # 检查算法名称中是否有未提取的数值模式
    algo_nums = re.findall(r'(\d+(?:\.\d+)?)', e['algo'])
    stored_nums = re.findall(r'(\d+(?:\.\d+)?)', e['stored'])
    
    # 找算法名和正确名的差异关键词
    algo_words = set(re.findall(r'[\u4e00-\u9fff]+', e['algo']))
    stored_words = set(re.findall(r'[\u4e00-\u9fff]+', e['stored']))
    diff_words_algo = algo_words - stored_words  # 算法有但正确没有
    diff_words_stored = stored_words - algo_words  # 正确有但算法没有
    
    # 分类失败类型
    fail_type = None
    
    # 有参数差异的
    if diff_params:
        for dp in diff_params:
            pname = dp.split(':')[0]
            if pname == 'dn':
                fail_type = 'DN值不同'
            elif pname == 'cable_section':
                fail_type = '截面值不同'
            elif pname == 'power_kw':
                fail_type = '功率值不同'
            elif pname == 'perimeter_mm':
                fail_type = '周长值不同'
            else:
                fail_type = f'其他参数不同({pname})'
            break
    
    # 无参数差异，看名称差异
    if not fail_type:
        # 检查是否是安装方式差异
        install_pairs = [
            ('明装', '暗装'), ('明配', '暗配'), ('落地', '墙上'), ('落地', '挂墙'),
            ('室内', '室外'), ('室内', '电缆沟'), ('单相', '三相'), ('单联', '双联'),
            ('单控', '双控'), ('离心', '轴流'), ('砌筑', '混凝土'),
            ('碳钢', '合金'), ('中压', '低压'), ('微穿孔', '管式'),
            ('外墙', '内墙'), ('直线', '弧线'), ('硬木', '胶合板'),
            ('角线', '槽线'), ('平面', '艺术造型'), ('圆形', '矩形'),
        ]
        for w1, w2 in install_pairs:
            if (w1 in e['algo'] and w2 in e['stored']) or (w2 in e['algo'] and w1 in e['stored']):
                fail_type = f'子类型差异({w1}/{w2})'
                break
    
    if not fail_type:
        # 看数值差异
        if algo_nums and stored_nums and algo_nums != stored_nums:
            # 找出具体不同的数值
            fail_type = '数值不同(参数未提取)'
        else:
            fail_type = '名称差异(无参数)'
    
    param_fail_types[fail_type] += 1
    if fail_type not in param_fail_examples:
        param_fail_examples[fail_type] = []
    param_fail_examples[fail_type].append({
        **e,
        'diff_params': diff_params,
        'diff_words_algo': diff_words_algo,
        'diff_words_stored': diff_words_stored,
        'algo_params': {k:v for k,v in algo_params.items() if v is not None},
        'stored_params': {k:v for k,v in stored_params.items() if v is not None},
    })

print("=== 按失败类型分布 ===")
for ft, count in param_fail_types.most_common():
    pct = count / len(wrong_tiers) * 100
    print(f"  {ft:30s}: {count:3d}条 ({pct:4.1f}%)")

print()
print("=== 各类型详细案例 ===")
for ft, count in param_fail_types.most_common():
    print(f"\n--- {ft} ({count}条) ---")
    for ex in param_fail_examples[ft][:3]:
        print(f"  {ex['province']:8s} | {ex['bill'][:20]:20s}")
        print(f"    算法: {ex['algo'][:65]}")
        print(f"    正确: {ex['stored'][:65]}")
        if ex['diff_params']:
            print(f"    参数差异: {', '.join(ex['diff_params'])}")
        if ex['algo_params'] or ex['stored_params']:
            print(f"    算法提参: {ex['algo_params']}")
            print(f"    正确提参: {ex['stored_params']}")
        print()
