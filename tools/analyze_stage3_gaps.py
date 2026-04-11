#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段三：分析同义词缺口，生成修复方案
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

def analyze_synonym_gaps(jsonl_path, top_n=50):
    """分析同义词缺口"""
    gaps = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                gaps.append(json.loads(line))

    print(f"总计 {len(gaps)} 条同义词缺口\n")

    # 按省份统计
    province_count = Counter(g.get('province', '未知') for g in gaps)
    print("各省分布：")
    for prov, cnt in province_count.most_common():
        print(f"  {prov}: {cnt}条")

    # 提取缺失的关键词对
    synonym_pairs = []
    for gap in gaps:
        bill_name = gap.get('bill_name', '').strip()
        expected_names = gap.get('expected_quota_names', [])
        correct_name = expected_names[0] if expected_names else ''
        if bill_name and correct_name:
            synonym_pairs.append((bill_name, correct_name, gap.get('province', '')))

    print(f"\n\n前{min(top_n, len(synonym_pairs))}个同义词缺口：\n")
    print("=" * 80)

    for i, (bill, correct, prov) in enumerate(synonym_pairs[:top_n], 1):
        print(f"\n{i}. [{prov}]")
        print(f"   清单: {bill}")
        print(f"   定额: {correct}")

    return synonym_pairs

if __name__ == '__main__':
    gaps_file = sys.argv[1] if len(sys.argv) > 1 else 'output/benchmark_assets/20260331_223255/synonym_gaps.jsonl'
    pairs = analyze_synonym_gaps(gaps_file, top_n=50)

    print(f"\n\n共提取 {len(pairs)} 对同义词缺口")
