# -*- coding: utf-8 -*-
"""专门分析"数值不同但参数未提取"的33条案例，找出哪些参数格式需要增强"""
import json
import re
import sys
from collections import Counter

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
                })

# 找出"数值不同但参数未提取"的案例
unextracted = []
for e in wrong_tiers:
    algo_p = parser.parse(e['algo'])
    stored_p = parser.parse(e['stored'])
    
    # 获取所有非None参数
    algo_vals = {k: v for k, v in algo_p.items() if v is not None}
    stored_vals = {k: v for k, v in stored_p.items() if v is not None}
    
    # 检查是否有任何参数差异
    all_keys = set(algo_vals.keys()) | set(stored_vals.keys())
    has_param_diff = False
    for k in all_keys:
        if algo_vals.get(k) != stored_vals.get(k):
            has_param_diff = True
            break
    
    if not has_param_diff:
        # 没有结构化参数差异，但名称里可能有不同的数值
        algo_nums = set(re.findall(r'\d+(?:\.\d+)?', e['algo']))
        stored_nums = set(re.findall(r'\d+(?:\.\d+)?', e['stored']))
        if algo_nums != stored_nums:
            # 找出具体哪些数值不同
            diff_nums_algo = algo_nums - stored_nums
            diff_nums_stored = stored_nums - algo_nums
            
            # 提取名称中的参数模式（括号内的说明 + 数值）
            algo_param_patterns = re.findall(r'([\u4e00-\u9fff]+\([^)]*\)\s*[≤≥<>]?\s*\d+(?:\.\d+)?)', e['algo'])
            stored_param_patterns = re.findall(r'([\u4e00-\u9fff]+\([^)]*\)\s*[≤≥<>]?\s*\d+(?:\.\d+)?)', e['stored'])
            
            # 也找无括号的参数模式
            algo_param2 = re.findall(r'([\u4e00-\u9fff]+[≤≥<>]\s*\d+(?:\.\d+)?)', e['algo'])
            stored_param2 = re.findall(r'([\u4e00-\u9fff]+[≤≥<>]\s*\d+(?:\.\d+)?)', e['stored'])
            
            unextracted.append({
                **e,
                'algo_params': algo_vals,
                'stored_params': stored_vals,
                'diff_nums_algo': diff_nums_algo,
                'diff_nums_stored': diff_nums_stored,
                'algo_patterns': algo_param_patterns + algo_param2,
                'stored_patterns': stored_param_patterns + stored_param2,
            })

print(f"=== 数值不同但结构化参数无差异的案例: {len(unextracted)}条 ===\n")

# 统计未提取的参数模式
pattern_counter = Counter()
for e in unextracted:
    for p in e['algo_patterns'] + e['stored_patterns']:
        # 提取参数类型名（括号前的中文）
        m = re.match(r'([\u4e00-\u9fff]+)', p)
        if m:
            pattern_counter[m.group(1)] += 1

print("=== 未提取参数的类型分布 ===")
for p, c in pattern_counter.most_common(20):
    print(f"  {p}: {c}次")
print()

# 打印所有案例
for i, e in enumerate(unextracted):
    print(f"[{i+1}] {e['province']:8s} | {e['bill'][:22]}")
    print(f"    算法: {e['algo'][:75]}")
    print(f"    正确: {e['stored'][:75]}")
    print(f"    算法提参: {e['algo_params']}")
    print(f"    正确提参: {e['stored_params']}")
    if e['algo_patterns']:
        print(f"    算法名参数模式: {e['algo_patterns']}")
    if e['stored_patterns']:
        print(f"    正确名参数模式: {e['stored_patterns']}")
    print(f"    数值差异: 算法多={e['diff_nums_algo']} 正确多={e['diff_nums_stored']}")
    print()
