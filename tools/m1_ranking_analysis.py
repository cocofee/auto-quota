"""
M1 排序错误深度分析脚本
用法: python tools/m1_ranking_analysis.py
输出: output/temp/m1-ranking-analysis.md
"""
import json, re, sys
from pathlib import Path
from collections import Counter

def main():
    result_path = Path('tests/benchmark_papers/_latest_result.json')
    if not result_path.exists():
        print(f'错误: 找不到 {result_path}，请先跑 python tools/run_benchmark.py')
        sys.exit(1)

    data = json.loads(result_path.read_text(encoding='utf-8'))

    # 收集所有错误
    ranking_errors = []  # oracle在候选（排序问题）
    recall_errors = []   # oracle不在候选（召回问题）

    for r in data['results']:
        prov = r['province']
        for d in r.get('details', []):
            if d.get('is_match'):
                continue
            d['_province'] = prov
            if d.get('oracle_in_candidates'):
                ranking_errors.append(d)
            else:
                recall_errors.append(d)

    total_err = len(ranking_errors) + len(recall_errors)
    lines = []
    lines.append(f'# M1 排序错误全量分析（{len(ranking_errors)}条）\n')
    lines.append(f'总错误{total_err}条，排序错误{len(ranking_errors)}条(67%)，召回错误{len(recall_errors)}条(33%)\n')

    # === Q1 省份分布 ===
    lines.append('\n## Q1: 排序错误省份分布\n')
    lines.append('| 省份 | 排序错 | 总错 | 排序占比 |')
    lines.append('|------|--------|------|----------|')
    prov_rank = Counter(e['_province'] for e in ranking_errors)
    prov_total = Counter(e['_province'] for e in ranking_errors + recall_errors)
    for prov, cnt in prov_rank.most_common():
        tot = prov_total[prov]
        lines.append(f'| {prov[:20]} | {cnt} | {tot} | {cnt/tot*100:.0f}% |')

    # === Q2 置信度分布 ===
    lines.append('\n## Q2: 排序错误置信度分布\n')
    lines.append('| 区间 | 数量 | 占比 |')
    lines.append('|------|------|------|')
    labels = ['0-20%','20-40%','40-60%','60-80%','80-100%']
    buckets = [0]*5
    for e in ranking_errors:
        c = e.get('confidence', 0)
        idx = min(int(c / 20), 4)
        buckets[idx] += 1
    for l, v in zip(labels, buckets):
        lines.append(f'| {l} | {v} | {v/len(ranking_errors)*100:.1f}% |')

    # === Q3 命名差异分类 ===
    lines.append('\n## Q3: 排序错误命名差异分类\n')

    categories = {
        'tier_diff': [],       # 仅参数档位不同
        'install_method': [],  # 明/暗安装方式
        'category_sub': [],    # 品类/子类型不同
        'other': [],           # 其他
    }

    for e in ranking_errors:
        algo = e.get('algo_name', '')
        stored = (e.get('stored_names', [''])[0] if e.get('stored_names') else '')
        if not algo or not stored:
            categories['other'].append(e)
            continue

        # 去掉数字比较基础名称
        algo_base = re.sub(r'[\d.]+', '#', algo)
        stored_base = re.sub(r'[\d.]+', '#', stored)

        # 检查明/暗安装方式差异
        has_install = False
        install_words = ['明装','暗装','明敷','暗敷','明配','暗配','明设','暗设',
                         '明敷设','暗敷设','落地','挂墙','墙上','柱上']
        for pair in [('明装','暗装'),('明敷','暗敷'),('明配','暗配'),
                     ('明敷设','暗敷设'),('落地','挂墙'),('落地','墙上')]:
            if (pair[0] in algo and pair[1] in stored) or (pair[1] in algo and pair[0] in stored):
                has_install = True
                break

        if has_install:
            categories['install_method'].append(e)
        elif algo_base == stored_base:
            categories['tier_diff'].append(e)
        else:
            # 看核心品类词是否不同
            algo_core = re.split(r'[\s　]+', algo)[0]
            stored_core = re.split(r'[\s　]+', stored)[0]
            if algo_core != stored_core and len(algo_core) > 2 and len(stored_core) > 2:
                categories['category_sub'].append(e)
            else:
                categories['other'].append(e)

    cat_labels = {
        'category_sub': '品类/子类型不同',
        'other': '其他差异',
        'tier_diff': '仅参数档位不同',
        'install_method': '安装方式(明/暗)',
    }

    lines.append('| 模式 | 数量 | 占比 |')
    lines.append('|------|------|------|')
    for key in ['category_sub', 'other', 'tier_diff', 'install_method']:
        items = categories[key]
        lines.append(f'| {cat_labels[key]} | {len(items)} | {len(items)/len(ranking_errors)*100:.1f}% |')

    # 每类输出前20条样本
    for key in ['category_sub', 'other', 'tier_diff', 'install_method']:
        items = categories[key]
        if not items:
            continue
        lines.append(f'\n### {cat_labels[key]}（前20条样本）\n')
        lines.append('| # | 省份 | conf | 算法选的 | 正确答案 |')
        lines.append('|---|------|------|---------|---------|')
        for i, e in enumerate(items[:20], 1):
            algo = e.get('algo_name', '?')
            stored = (e.get('stored_names', ['?'])[0] if e.get('stored_names') else '?')
            conf = e.get('confidence', 0)
            prov = e['_province'][:8]
            lines.append(f'| {i} | {prov} | {conf}% | {algo} | {stored} |')

    # 写文件
    out = Path('output/temp/m1-ranking-analysis.md')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines), encoding='utf-8')
    print(f'分析完成，结果写入: {out}')
    print(f'排序错误: {len(ranking_errors)}条')
    print(f'  品类/子类型不同: {len(categories["category_sub"])}条 ({len(categories["category_sub"])/len(ranking_errors)*100:.1f}%)')
    print(f'  其他差异: {len(categories["other"])}条 ({len(categories["other"])/len(ranking_errors)*100:.1f}%)')
    print(f'  仅参数档位不同: {len(categories["tier_diff"])}条 ({len(categories["tier_diff"])/len(ranking_errors)*100:.1f}%)')
    print(f'  安装方式(明/暗): {len(categories["install_method"])}条 ({len(categories["install_method"])/len(ranking_errors)*100:.1f}%)')

if __name__ == '__main__':
    main()
