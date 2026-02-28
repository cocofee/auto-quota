# -*- coding: utf-8 -*-
"""
经验库审计脚本 — 用经验卡片当"考卷"，给算法找弱点

思路：
  经验卡片 = 用户确认的正确答案（标准答案）
  算法匹配 = 系统的"考试成绩"
  不一致 = 算法答错了 → 分析为什么错 → 找出改进方向

用法：
  python tools/audit_experience.py --province "重庆" --limit 50   # 先跑小样本
  python tools/audit_experience.py --province "北京"               # 跑一个省
  python tools/audit_experience.py                                 # 全量跑（慢）

输出：
  output/temp/audit/audit_<省份>.json  — 每条详细结果
  output/temp/audit/diagnosis_<省份>.json — 根因分析和改进建议
"""

import sys
import os
import json
import time
import sqlite3
from datetime import datetime
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")


def load_cards_by_province():
    """加载所有经验卡片，按省份分组"""
    from src.experience_db import ExperienceDB
    db = ExperienceDB()
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute('''
        SELECT id, bill_text, bill_name, quota_ids, quota_names,
               province, layer, source, confirm_count, specialty
        FROM experiences ORDER BY province, id
    ''').fetchall()

    by_province = {}
    for r in rows:
        province = r['province']
        try:
            quota_ids = json.loads(r['quota_ids']) if r['quota_ids'] else []
            quota_names = json.loads(r['quota_names']) if r['quota_names'] else []
        except json.JSONDecodeError:
            quota_ids, quota_names = [], []

        card = {
            'id': r['id'],
            'bill_text': r['bill_text'] or '',
            'bill_name': r['bill_name'] or '',
            'quota_ids': quota_ids,
            'quota_names': quota_names,
            'province': province,
            'layer': r['layer'] or 'candidate',
            'source': r['source'] or '',
            'specialty': r['specialty'] or '',
        }
        by_province.setdefault(province, []).append(card)

    conn.close()
    return by_province


def card_to_bill_item(card, seq):
    """把经验卡片转成bill_item格式"""
    bill_name = card['bill_name']
    bill_text = card['bill_text']

    if not bill_name:
        first_line = bill_text.split('\n')[0].strip()
        for prefix in ('名称:', '名称：'):
            if prefix in first_line:
                bill_name = first_line.split(prefix)[-1].strip().split()[0]
                break
        else:
            parts = first_line.split()
            bill_name = parts[0] if parts else first_line[:20]

    return {
        'name': bill_name,
        'description': bill_text,
        'unit': '',
        'quantity': 1,
        'seq': seq,
        'specialty': card.get('specialty', ''),
    }


def diagnose_mismatch(card, algo_result, parser, province):
    """
    诊断一条不一致的根因。
    返回根因类别和说明。
    """
    from src.query_builder import build_quota_query
    from src.specialty_classifier import classify as classify_specialty

    bill_name = card['bill_name'] or card['bill_text'].split()[0][:20]
    bill_text = card['bill_text']
    stored_names = card['quota_names']
    stored_first = stored_names[0] if stored_names else ''

    # 算法结果
    quotas = algo_result.get('quotas', [])
    algo_name = quotas[0].get('name', '') if quotas else ''
    algo_id = quotas[0].get('quota_id', '') if quotas else ''
    confidence = algo_result.get('confidence', 0)
    match_source = algo_result.get('match_source', '')

    # 获取搜索词和分类
    params = parser.parse(bill_text)
    try:
        cls = classify_specialty(bill_name, bill_text, province=province)
        primary_book = cls.get('primary', '') or ''
    except Exception:
        primary_book = ''

    try:
        search_query = build_quota_query(parser, bill_name, bill_text,
                                         specialty=primary_book, bill_params=params)
    except Exception:
        search_query = bill_name

    # --- 诊断逻辑 ---

    # 1. 算法没找到任何结果
    if not quotas:
        return 'no_result', '算法未找到任何候选定额', search_query, primary_book

    # 2. 搜索词和正确定额名没有关键词重叠 → 同义词缺失
    stored_keywords = set(stored_first.replace('(', ' ').replace(')', ' ').split())
    search_keywords = set(search_query.split())
    overlap = stored_keywords & search_keywords
    # 去掉太泛的词
    overlap -= {'安装', '制作', '周长', 'mm', 'm2', '以内', '≤'}

    if len(overlap) == 0 and stored_first:
        return 'synonym_gap', f'搜索词"{search_query}"和正确定额"{stored_first}"无关键词重叠', search_query, primary_book

    # 3. 专业分类错误：算法搜的册和正确定额不在同一册
    stored_id = card['quota_ids'][0] if card['quota_ids'] else ''
    if stored_id and algo_id:
        # 提取册号前缀（如CG→C7, C10-→C10）
        def get_book_prefix(qid):
            """从定额编号推断册号"""
            # 重庆格式：CA=C1, CB=C2... CG=C7
            if len(qid) >= 2 and qid[0] == 'C' and qid[1].isalpha():
                letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                              'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                              'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
                return letter_map.get(qid[1], '')
            # 北京格式：C7-2-131
            import re
            m = re.match(r'(C\d+)-', qid)
            if m:
                return m.group(1)
            # 其他格式：7-3-197 → C7
            m = re.match(r'(\d+)-', qid)
            if m:
                return f'C{m.group(1)}'
            return ''

        stored_book = get_book_prefix(stored_id)
        algo_book = get_book_prefix(algo_id)

        if stored_book and algo_book and stored_book != algo_book:
            return 'wrong_book', f'算法搜了{algo_book}册，正确在{stored_book}册', search_query, primary_book

    # 4. 同一册但找错子目 → 搜索词不够精确或参数提取问题
    # 检查是否同族（定额名有部分重叠）
    algo_keywords = set(algo_name.replace('(', ' ').replace(')', ' ').split())
    algo_keywords -= {'安装', '制作', '周长', 'mm', 'm2', '以内', '≤'}
    stored_keywords_clean = stored_keywords - {'安装', '制作', '周长', 'mm', 'm2', '以内', '≤'}

    family_overlap = algo_keywords & stored_keywords_clean
    if len(family_overlap) > 0:
        # 同族不同档或不同子类型
        return 'wrong_tier', f'同族但选错：算法"{algo_name[:25]}" vs 正确"{stored_first[:25]}"', search_query, primary_book

    # 5. 完全不同的定额
    return 'wrong_family', f'完全不同：算法"{algo_name[:25]}" vs 正确"{stored_first[:25]}"', search_query, primary_book


def run_audit(province, cards, limit=None):
    """对一个省份运行算法审计"""
    from src.match_engine import init_search_components, match_search_only
    from src.text_parser import TextParser

    if limit:
        cards = cards[:limit]
    total = len(cards)

    print(f"  初始化搜索引擎...")
    searcher, validator = init_search_components(resolved_province=province)
    parser = TextParser()

    # 构建bill_items
    bill_items = []
    card_map = {}
    for i, card in enumerate(cards):
        item = card_to_bill_item(card, seq=i+1)
        bill_items.append(item)
        card_map[i+1] = card

    print(f"  匹配 {total} 条...")
    start = time.time()
    results = match_search_only(
        bill_items, searcher, validator,
        experience_db=None, province=province)
    elapsed = time.time() - start
    print(f"  完成，耗时 {elapsed:.0f}秒 ({elapsed/max(total,1):.1f}秒/条)")

    # 逐条对比 + 诊断
    audit_items = []
    match_count = 0
    mismatch_count = 0
    diagnosis_counter = Counter()

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
            match_count += 1
            diagnosis = ('correct', '', '', '')
        else:
            mismatch_count += 1
            diagnosis = diagnose_mismatch(card, result, parser, province)

        cause, detail, search_query, book = diagnosis
        if not is_match:
            diagnosis_counter[cause] += 1

        audit_items.append({
            'card_id': card['id'],
            'bill_name': card['bill_name'] or card['bill_text'][:30],
            'stored_ids': stored_ids,
            'stored_names': card['quota_names'][:2],
            'algo_id': algo_id,
            'algo_name': algo_name,
            'confidence': confidence,
            'is_match': is_match,
            'cause': cause,
            'detail': detail,
            'search_query': search_query if not is_match else '',
            'book': book if not is_match else '',
            'layer': card['layer'],
        })

    return {
        'province': province,
        'total': len(audit_items),
        'match': match_count,
        'mismatch': mismatch_count,
        'rate': match_count / max(len(audit_items), 1) * 100,
        'diagnosis': dict(diagnosis_counter),
        'items': audit_items,
        'elapsed': elapsed,
    }


def analyze_improvement_opportunities(audit_result):
    """分析改进机会：哪类根因最多，改了收益最大"""
    items = audit_result['items']
    mismatches = [i for i in items if not i['is_match']]

    cause_names = {
        'synonym_gap': '同义词缺失（清单名和定额名叫法不同）',
        'wrong_book': '专业分类错误（搜了错的册）',
        'wrong_family': '完全搜偏（找到不相关的定额）',
        'wrong_tier': '同族选错（对的家族，错的档位/子类型）',
        'no_result': '未找到结果',
    }

    # 按根因分组
    by_cause = defaultdict(list)
    for item in mismatches:
        by_cause[item['cause']].append(item)

    opportunities = []
    for cause, cause_items in sorted(by_cause.items(), key=lambda x: -len(x[1])):
        # 找出高频的错误模式（按bill_name分组）
        name_counter = Counter()
        for item in cause_items:
            # 提取核心设备名（去掉规格参数）
            name = item['bill_name']
            # 简化：取前4个字作为设备类型
            core = name[:6] if len(name) > 6 else name
            name_counter[core] += 1

        top_patterns = name_counter.most_common(10)

        opportunities.append({
            'cause': cause,
            'cause_name': cause_names.get(cause, cause),
            'count': len(cause_items),
            'percent': len(cause_items) / max(len(mismatches), 1) * 100,
            'top_patterns': [{'name': n, 'count': c} for n, c in top_patterns],
            'samples': [{
                'bill_name': i['bill_name'][:30],
                'stored': i['stored_names'][0][:30] if i['stored_names'] else '',
                'algo': i['algo_name'][:30],
                'search_query': i['search_query'][:40],
            } for i in cause_items[:5]],
        })

    return opportunities


def print_report(audit_result, opportunities):
    """打印可读报告"""
    r = audit_result
    print(f"\n{'='*60}")
    print(f"算法成绩单: {r['province'][:30]}")
    print(f"{'='*60}")
    print(f"  总题数: {r['total']}")
    print(f"  答对: {r['match']} ({r['rate']:.1f}%)")
    print(f"  答错: {r['mismatch']} ({100-r['rate']:.1f}%)")
    print(f"  耗时: {r['elapsed']:.0f}秒")

    print(f"\n--- 错误根因分析 ---")
    for opp in opportunities:
        print(f"\n  [{opp['cause_name']}] {opp['count']}个 ({opp['percent']:.0f}%)")
        print(f"  高频设备:")
        for p in opp['top_patterns'][:5]:
            print(f"    {p['name']}: {p['count']}次")
        print(f"  典型案例:")
        for s in opp['samples'][:3]:
            print(f"    清单「{s['bill_name']}」")
            print(f"      搜索词: {s['search_query']}")
            print(f"      算法找: {s['algo']}")
            print(f"      正确是: {s['stored']}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description='用经验卡片给算法打分，找弱点')
    ap.add_argument('--province', help='指定省份（模糊匹配）')
    ap.add_argument('--limit', type=int, help='每省最多N条')
    args = ap.parse_args()

    report_dir = 'output/temp/audit'
    os.makedirs(report_dir, exist_ok=True)

    print("加载经验库...")
    by_province = load_cards_by_province()
    total = sum(len(v) for v in by_province.values())
    print(f"共 {total} 张卡片，{len(by_province)} 个省份\n")

    if args.province:
        matched = {k: v for k, v in by_province.items() if args.province in k}
        if not matched:
            print(f"未找到包含'{args.province}'的省份")
            return
        by_province = matched

    all_results = []
    for province, cards in sorted(by_province.items(), key=lambda x: -len(x[1])):
        print(f"\n{'='*60}")
        print(f"省份: {province} ({len(cards)}条)")
        print(f"{'='*60}")

        try:
            result = run_audit(province, cards, limit=args.limit)
            opportunities = analyze_improvement_opportunities(result)
            print_report(result, opportunities)

            # 保存详细结果
            safe_name = province[:15].replace('(', '').replace(')', '')
            # 保存诊断报告（精简版，不含全部items）
            diag = {
                'province': province,
                'total': result['total'],
                'match': result['match'],
                'mismatch': result['mismatch'],
                'rate': round(result['rate'], 1),
                'diagnosis': result['diagnosis'],
                'opportunities': opportunities,
            }
            diag_path = os.path.join(report_dir, f'diagnosis_{safe_name}.json')
            with open(diag_path, 'w', encoding='utf-8') as f:
                json.dump(diag, f, ensure_ascii=False, indent=2)

            # 保存完整结果（含每条详情）
            full_path = os.path.join(report_dir, f'audit_{safe_name}.json')
            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            all_results.append(result)

        except Exception as e:
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()

    # 总汇总
    if len(all_results) > 1:
        total_items = sum(r['total'] for r in all_results)
        total_match = sum(r['match'] for r in all_results)
        total_mismatch = sum(r['mismatch'] for r in all_results)

        # 合并所有省份的根因统计
        merged_diag = Counter()
        for r in all_results:
            for cause, cnt in r['diagnosis'].items():
                merged_diag[cause] += cnt

        print(f"\n{'='*60}")
        print(f"全部省份汇总")
        print(f"{'='*60}")
        print(f"  总题: {total_items} | 答对: {total_match} ({total_match/max(total_items,1)*100:.1f}%) | 答错: {total_mismatch}")
        print(f"\n  根因分布:")
        for cause, cnt in merged_diag.most_common():
            print(f"    {cause}: {cnt}个 ({cnt/max(total_mismatch,1)*100:.0f}%)")


if __name__ == '__main__':
    main()
