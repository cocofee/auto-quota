# -*- coding: utf-8 -*-
"""
跨省算法benchmark — 用固定试卷测试纯搜索算法在各省的准确率

思路：
  从经验库权威层导出的固定试卷（tests/cross_province_tests/*.json）
  每条都有"标准答案"（人工确认的定额编号）
  用纯搜索模式匹配，对比答案，计算准确率
  支持基线对比，跟踪算法改善

用法：
  python tools/run_cross_benchmark.py              # 跑全部省份
  python tools/run_cross_benchmark.py --save        # 跑完保存为基线
  python tools/run_cross_benchmark.py --province 广东  # 只跑一个省

输出指标：
  命中率 = 算法找到的定额编号在标准答案列表中
  同义词缺口率 = 因同义词缺失导致的错误占比
  选错档位率 = 找对了家族但选错规格/档位的错误占比
"""

import sys
import os
import json
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")


# 试卷目录和基线文件
TEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'tests', 'cross_province_tests')
BASELINE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'tests', 'cross_province_baseline.json')


def load_test_sets(province_filter=None):
    """加载固定试卷"""
    test_sets = {}
    for fname in sorted(os.listdir(TEST_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        fpath = os.path.join(TEST_DIR, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        prov = data.get('province')
        if not prov:
            continue
        if province_filter and province_filter not in prov:
            continue

        test_sets[prov] = data
    return test_sets


def run_province_test(province, items, use_experience=False):
    """对一个省份运行测试，返回结果"""
    from src.match_engine import init_search_components, match_search_only
    from src.text_parser import TextParser

    # 初始化搜索引擎
    searcher, validator = init_search_components(resolved_province=province)
    parser = TextParser()

    # 初始化经验库（如果启用）
    exp_db = None
    if use_experience:
        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB(province)

    # 构建bill_items（和audit_experience.py一致）
    bill_items = []
    card_map = {}
    for i, item in enumerate(items):
        bill_name = item['bill_name']
        bill_text = item['bill_text']

        if not bill_name:
            first_line = bill_text.split('\n')[0].strip()
            for prefix in ('名称:', '名称：'):
                if prefix in first_line:
                    bill_name = first_line.split(prefix)[-1].strip().split()[0]
                    break
            else:
                parts = first_line.split()
                bill_name = parts[0] if parts else first_line[:20]

        bill_items.append({
            'name': bill_name,
            'description': bill_text,
            'unit': '',
            'quantity': 1,
            'seq': i + 1,
            'specialty': item.get('specialty', ''),
        })
        card_map[i + 1] = item

    # 运行搜索匹配
    start = time.time()
    results = match_search_only(
        bill_items, searcher, validator,
        experience_db=exp_db, province=province)
    elapsed = time.time() - start

    # 逐条对比
    correct = 0
    wrong = 0
    diagnosis = Counter()  # 错误根因统计

    details = []
    for result in results:
        bi = result.get('bill_item', {})
        seq = bi.get('seq', 0)
        card = card_map.get(seq)
        if not card:
            continue

        source = result.get('match_source', '')
        if source == 'skip_measure':
            continue

        quotas = result.get('quotas', [])
        algo_id = quotas[0]['quota_id'] if quotas else ''
        algo_name = quotas[0].get('name', '') if quotas else ''
        confidence = result.get('confidence', 0)
        stored_ids = card['quota_ids']

        is_match = algo_id in stored_ids if (algo_id and stored_ids) else False

        if is_match:
            correct += 1
        else:
            wrong += 1
            # 简化根因诊断
            cause = _diagnose_cause(card, algo_id, algo_name, quotas, parser, province)
            diagnosis[cause] += 1

        details.append({
            'bill_name': card['bill_name'][:30],
            'is_match': is_match,
            'algo_id': algo_id,
            'algo_name': algo_name[:30],
            'stored_ids': stored_ids[:2],
            'stored_names': card['quota_names'][:1],
            'confidence': confidence,
        })

    total = correct + wrong
    rate = correct / max(total, 1) * 100

    return {
        'province': province,
        'total': total,
        'correct': correct,
        'wrong': wrong,
        'rate': round(rate, 1),
        'diagnosis': dict(diagnosis),
        'elapsed': round(elapsed, 1),
        'details': details,
    }


def _diagnose_cause(card, algo_id, algo_name, quotas, parser, province):
    """简化版根因诊断（和audit_experience.py一致的逻辑）"""
    import re

    if not quotas:
        return 'no_result'

    # 检查搜索词和正确定额名是否有关键词重叠
    stored_first = card['quota_names'][0] if card['quota_names'] else ''
    stored_keywords = set(stored_first.replace('(', ' ').replace(')', ' ').split())
    algo_keywords = set(algo_name.replace('(', ' ').replace(')', ' ').split())
    ignore = {'安装', '制作', '周长', 'mm', 'm2', '以内', '≤'}
    stored_keywords -= ignore
    algo_keywords -= ignore

    # 检查专业册是否一致
    def get_book(qid):
        if len(qid) >= 2 and qid[0] == 'C' and qid[1].isalpha():
            letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                          'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                          'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
            return letter_map.get(qid[1], '')
        m = re.match(r'(C\d+)-', qid)
        if m:
            return m.group(1)
        m = re.match(r'(\d+)-', qid)
        if m:
            return f'C{m.group(1)}'
        return ''

    stored_id = card['quota_ids'][0] if card['quota_ids'] else ''
    if stored_id and algo_id:
        if get_book(stored_id) and get_book(algo_id) and get_book(stored_id) != get_book(algo_id):
            return 'wrong_book'

    # 同族判断
    family_overlap = stored_keywords & algo_keywords
    if len(family_overlap) > 0:
        return 'wrong_tier'

    return 'synonym_gap'


def load_baseline():
    """加载基线"""
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_baseline(results):
    """保存基线"""
    baseline = {
        'created': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'provinces': {}
    }
    for r in results:
        baseline['provinces'][r['province']] = {
            'total': r['total'],
            'rate': r['rate'],
            'diagnosis': r['diagnosis'],
        }
    with open(BASELINE_FILE, 'w', encoding='utf-8') as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    print(f"\n基线已保存到 {BASELINE_FILE}")


def print_summary(results, baseline=None, use_experience=False):
    """打印汇总表"""
    print(f"\n{'='*80}")
    mode_label = "含经验库" if use_experience else "纯搜索模式，不含经验库"
    print(f"跨省算法Benchmark（{mode_label}）")
    print(f"{'='*80}")

    # 表头
    header = f"{'省份':<16} {'题数':>4} {'命中率':>8}"
    if baseline:
        header += f" {'基线':>8} {'变化':>8}"
    header += f" {'同义词缺口':>10} {'选错档位':>10} {'搜偏':>6} {'耗时':>6}"
    print(header)
    print('-' * 80)

    total_correct = 0
    total_items = 0

    for r in results:
        prov_short = r['province'].split('(')[0].split('（')[0][:14]
        diag = r['diagnosis']
        syn_gap = diag.get('synonym_gap', 0)
        wrong_tier = diag.get('wrong_tier', 0)
        wrong_book = diag.get('wrong_book', 0)
        wrong_family = diag.get('wrong_family', 0)
        no_result = diag.get('no_result', 0)

        line = f"{prov_short:<16} {r['total']:>4} {r['rate']:>7.1f}%"

        if baseline and r['province'] in baseline.get('provinces', {}):
            base_rate = baseline['provinces'][r['province']]['rate']
            delta = r['rate'] - base_rate
            sign = '+' if delta > 0 else ''
            line += f" {base_rate:>7.1f}% {sign}{delta:>6.1f}%"
        elif baseline:
            line += f" {'新':>8} {'':>8}"

        line += f" {syn_gap:>10} {wrong_tier:>10} {wrong_book + wrong_family:>6} {r['elapsed']:>5.0f}s"
        print(line)

        total_correct += r['correct']
        total_items += r['total']

    # 汇总行
    overall_rate = total_correct / max(total_items, 1) * 100
    print('-' * 80)
    print(f"{'总计':<16} {total_items:>4} {overall_rate:>7.1f}%")
    print(f"{'='*80}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description='跨省算法benchmark — 固定试卷，跟踪改善')
    ap.add_argument('--province', help='只跑指定省份（模糊匹配）')
    ap.add_argument('--save', action='store_true', help='保存当前结果为基线')
    ap.add_argument('--detail', action='store_true', help='打印每题详情（调试用）')
    ap.add_argument('--with-experience', action='store_true', help='启用经验库（测试经验库对准确率的提升）')
    args = ap.parse_args()

    # 加载试卷
    test_sets = load_test_sets(province_filter=args.province)
    if not test_sets:
        print(f"未找到试卷。请先在 {TEST_DIR}/ 下放置试卷JSON文件。")
        return

    print(f"跨省Benchmark: {len(test_sets)}个省份")

    # 加载基线
    baseline = load_baseline()
    if baseline:
        print(f"基线: {baseline['created']}")

    # 逐省运行
    all_results = []
    for province, data in test_sets.items():
        items = data['items']
        prov_short = province.split('(')[0][:14]
        print(f"\n  测试 {prov_short}（{len(items)}题）...")

        result = run_province_test(province, items, use_experience=args.with_experience)
        all_results.append(result)

        print(f"  → 命中 {result['correct']}/{result['total']} = {result['rate']:.1f}% ({result['elapsed']:.0f}s)")

        # 打印详情
        if args.detail:
            for d in result['details']:
                mark = '✓' if d['is_match'] else '✗'
                name = d['bill_name'][:20]
                algo = d['algo_name'][:20]
                stored = d['stored_names'][0][:20] if d['stored_names'] else '?'
                print(f"    {mark} {name} → {algo} (正确:{stored})")

    # 汇总
    print_summary(all_results, baseline, use_experience=args.with_experience)

    # 保存基线
    if args.save:
        save_baseline(all_results)

    # 保存详细结果（每次都存，方便追踪）
    result_file = os.path.join(TEST_DIR, '_latest_result.json')
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump({
            'run_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'results': all_results,
        }, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
