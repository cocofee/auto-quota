# -*- coding: utf-8 -*-
"""
同义词跨省覆盖率审计工具（v2 — 直接调用搜索函数，比subprocess快10倍+）

功能：对 engineering_synonyms.json 中的每条同义词，
在所有省份的BM25索引中搜索其目标词，统计覆盖率。
覆盖率低的同义词目标需要替换为全国通用词。

用法：
    python tools/audit_synonym_coverage.py              # 审计所有同义词
    python tools/audit_synonym_coverage.py --fix         # 审计并自动修复低覆盖率的
    python tools/audit_synonym_coverage.py --keyword 桥架  # 只看含"桥架"的同义词
"""
import json
import sys
import os
import re
from collections import defaultdict

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.quota_search import search_quota_db

# 安装类定额库（24个省/市）
INSTALL_PROVINCES = {
    "北京": "北京市建设工程施工消耗量标准(2024)",
    "上海": "上海市安装工程预算定额(2016)",
    "宁夏": "宁夏安装工程计价定额(2019)",
    "广东": "广东省通用安装工程综合定额(2018)",
    "广西": "广西安装工程消耗量定额(2023)",
    "江苏": "江苏省安装工程计价定额(2014)",
    "江西": "江西省通用安装工程消耗量定额及统一基价表(2017)",
    "河南": "河南省通用安装工程预算定额(2016)",
    "浙江": "浙江省通用安装工程预算定额(2018)",
    "湖北": "湖北省通用安装工程消耗量定额及全费用基价表(2024)",
    "湖南": "湖南省安装工程消耗量标准(2020)",
    "四川": "四川省2020序列定额",
    "山东": "山东省安装工程消耗量定额(2025)",
    "西藏": "西藏自治区通用安装工程预算定额(2016)",
    "辽宁": "辽宁省通用安装工程定额(2024)",
    "重庆": "重庆市通用安装工程计价定额(2018)",
    "陕西": "陕西省通用安装工程基价表(2025)",
    "黑龙江": "黑龙江省通用安装工程消耗量定额(2019)",
    "云南": "云南省通用安装工程计价标准(2020)",
    "内蒙古": "内蒙古通用安装工程预算定额(2017)",
    "甘肃": "甘肃省安装工程预算定额(2013)",
    "深圳": "深圳市安装工程消耗量标准(2025)",
    "福建": "福建省通用安装工程预算定额(2017)",
}

# 快速筛选用的代表省（地域分散、定额体系差异大）
QUICK_PROVINCES = ["北京", "广东", "四川", "山东", "浙江", "辽宁", "云南", "福建"]


def search_province_direct(keyword, province_full):
    """直接调用搜索函数，返回结果列表"""
    try:
        # search_quota_db 接受关键词列表
        keywords = keyword.split()
        results = search_quota_db(keywords, province=province_full, limit=5)
        return results  # [(quota_id, name, unit), ...]
    except Exception as e:
        return []


def check_coverage(target_word, provinces=None):
    """检查目标词在多少个省能搜到相关结果"""
    if provinces is None:
        provinces = INSTALL_PROVINCES
    hit_provinces = []
    miss_provinces = []
    for short, full in provinces.items():
        results = search_province_direct(target_word, full)
        if results:
            hit_provinces.append(short)
        else:
            miss_provinces.append(short)
    return hit_provinces, miss_provinces


def generate_candidates(current_target):
    """对一个低覆盖率目标词，生成可能更好的候选词列表"""
    candidates = []

    # 策略1：去掉"安装""制作""敷设"等动词后缀
    for suffix in ['安装', '制作', '敷设', '布线', '穿线', '穿放', '制作安装']:
        if current_target.endswith(suffix):
            core = current_target[:-len(suffix)].strip()
            if core and len(core) >= 2:
                candidates.append(('去后缀', core))
            break

    # 策略2：如果目标词有多个空格分隔的部分，取前面的核心部分
    parts = current_target.split()
    if len(parts) >= 2:
        # 只取第一个词（通常是核心名词）
        candidates.append(('取核心', parts[0]))
        # 取前两个词
        if len(parts) >= 3:
            candidates.append(('取前两词', ' '.join(parts[:2])))

    # 策略3：去掉"式"前面的类型修饰词（如 "跷板式暗开关" → "暗开关"）
    m = re.match(r'^.+式(.+)$', current_target)
    if m and len(m.group(1)) >= 2:
        candidates.append(('去类型', m.group(1)))

    return candidates


def find_better_target(bill_term, current_target, current_coverage, min_coverage):
    """对覆盖率低的同义词，尝试找更好的目标词"""
    best_target = None
    best_coverage = current_coverage

    # 先用快速省份筛选候选
    quick_provs = {k: v for k, v in INSTALL_PROVINCES.items() if k in QUICK_PROVINCES}

    # 生成候选
    candidates = generate_candidates(current_target)
    # 额外加上清单词本身（有时清单词搜索效果更好）
    if bill_term != current_target:
        candidates.append(('用清单词', bill_term))

    for strategy, candidate in candidates:
        if candidate == current_target:
            continue
        # 快速筛选
        quick_hits, _ = check_coverage(candidate, quick_provs)
        if len(quick_hits) < len(quick_provs) // 3:
            # 快速筛选都通不过，跳过
            continue
        # 全量验证
        hits, _ = check_coverage(candidate)
        if len(hits) > best_coverage:
            best_target = candidate
            best_coverage = len(hits)

    if best_target and best_coverage >= min_coverage:
        return best_target, best_coverage
    return None, current_coverage


def main():
    import argparse
    parser = argparse.ArgumentParser(description='同义词跨省覆盖率审计')
    parser.add_argument('--fix', action='store_true', help='自动修复低覆盖率的同义词')
    parser.add_argument('--keyword', type=str, help='只审计含指定关键词的同义词')
    parser.add_argument('--min-coverage', type=int, default=8, help='覆盖率低于此值的标记为需修复（默认8，即24省的1/3）')
    parser.add_argument('--quick', action='store_true', help='只用8个代表省快速筛选（不做全量）')
    args = parser.parse_args()

    # 读取同义词表
    syn_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'engineering_synonyms.json')
    with open(syn_path, 'r', encoding='utf-8') as f:
        synonyms = json.load(f)

    provinces_to_use = INSTALL_PROVINCES
    if args.quick:
        provinces_to_use = {k: v for k, v in INSTALL_PROVINCES.items() if k in QUICK_PROVINCES}

    total_provinces = len(provinces_to_use)

    # 过滤
    items = [(k, v[0]) for k, v in synonyms.items() if not k.startswith('_')]
    if args.keyword:
        items = [(k, v) for k, v in items if args.keyword in k or args.keyword in v]

    print(f"审计 {len(items)} 条同义词，{total_provinces} 个省份")
    print(f"{'='*80}")

    # 去重：同一个目标词只搜一次
    target_cache = {}  # target → (hits, misses)
    targets_to_check = set(v for _, v in items)

    print(f"去重后有 {len(targets_to_check)} 个唯一目标词需要搜索")
    print()

    for i, target in enumerate(sorted(targets_to_check)):
        hits, misses = check_coverage(target, provinces_to_use)
        target_cache[target] = (hits, misses)
        progress = f"[{i+1}/{len(targets_to_check)}]"
        coverage = len(hits)
        bar = '█' * coverage + '░' * (total_provinces - coverage)
        status = '✅' if coverage >= args.min_coverage else '⚠️'
        print(f"  {progress} {status} {bar} {coverage:2d}/{total_provinces} | {target[:40]}")

    # 生成报告
    print()
    print(f"{'='*80}")
    print("审计报告")
    print(f"{'='*80}")

    low_coverage = []
    good_coverage = []

    for bill_term, target in items:
        hits, misses = target_cache.get(target, ([], list(provinces_to_use.keys())))
        coverage = len(hits)
        if coverage < args.min_coverage:
            low_coverage.append((bill_term, target, coverage, hits, misses))
        else:
            good_coverage.append((bill_term, target, coverage))

    # 按覆盖率排序
    low_coverage.sort(key=lambda x: x[2])

    print(f"\n✅ 覆盖率>={args.min_coverage}的同义词：{len(good_coverage)}条")
    print(f"⚠️  覆盖率<{args.min_coverage}的同义词：{len(low_coverage)}条（需要修复）\n")

    if low_coverage:
        print(f"{'清单词':20s} | {'当前目标':30s} | 覆盖 | 命中省份")
        print(f"{'-'*20}-+-{'-'*30}-+------+{'-'*30}")
        for bill_term, target, coverage, hits, misses in low_coverage:
            hit_str = ','.join(hits) if hits else '无'
            print(f"{bill_term:20s} | {target:30s} | {coverage:2d}/{total_provinces} | {hit_str}")

    # --fix 模式：自动修复
    fixes = []
    if args.fix and low_coverage:
        print(f"\n{'='*80}")
        print("自动修复（寻找更好的目标词）")
        print(f"{'='*80}\n")

        # 按目标词去重（同一个目标词修复一次即可）
        targets_to_fix = {}
        for bill_term, target, coverage, hits, misses in low_coverage:
            if target not in targets_to_fix:
                targets_to_fix[target] = (bill_term, coverage)

        for i, (target, (bill_term, coverage)) in enumerate(sorted(targets_to_fix.items())):
            print(f"  [{i+1}/{len(targets_to_fix)}] {target} (当前{coverage}/{total_provinces})")
            better, better_cov = find_better_target(bill_term, target, coverage, args.min_coverage)
            if better:
                print(f"    → 找到更好的: \"{better}\" ({better_cov}/{total_provinces})")
                fixes.append((target, better, better_cov))
            else:
                print(f"    → 未找到更好的替代")

        # 应用修复
        if fixes:
            print(f"\n应用 {len(fixes)} 条修复...")
            fix_count = 0
            for old_target, new_target, new_cov in fixes:
                for k, v in synonyms.items():
                    if k.startswith('_'):
                        continue
                    if isinstance(v, list) and v[0] == old_target:
                        v[0] = new_target
                        fix_count += 1

            with open(syn_path, 'w', encoding='utf-8') as f:
                json.dump(synonyms, f, ensure_ascii=False, indent=2)
            print(f"已修复 {fix_count} 条同义词目标，保存到 {syn_path}")

    # 保存详细报告到JSON
    report = {
        'total_synonyms': len(items),
        'total_provinces': total_provinces,
        'good_coverage': len(good_coverage),
        'low_coverage_count': len(low_coverage),
        'fixes_applied': len(fixes),
        'low_coverage_details': [
            {
                'bill_term': bt,
                'current_target': tgt,
                'coverage': cov,
                'hit_provinces': hits,
                'miss_provinces': misses
            }
            for bt, tgt, cov, hits, misses in low_coverage
        ],
        'fixes': [
            {'old_target': old, 'new_target': new, 'new_coverage': cov}
            for old, new, cov in fixes
        ],
        'coverage_by_target': {
            tgt: {'coverage': len(hits), 'hit_provinces': hits}
            for tgt, (hits, _) in target_cache.items()
        }
    }

    report_path = os.path.join(os.path.dirname(__file__), '..', 'output', 'temp', 'synonym_coverage_report.json')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存到：{report_path}")


if __name__ == '__main__':
    main()
