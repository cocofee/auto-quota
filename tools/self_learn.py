# -*- coding: utf-8 -*-
"""
自进化脚本 — 从经验卡片中自动挖掘同义词，提升算法

原理：
  经验卡片 = "清单叫法 → 定额叫法" 的对照表
  如果清单名和定额名关键词完全不重叠 → BM25搜不到 → 就是同义词缺口
  从13000+张卡片中自动挖出这些缺口，批量补进同义词表

用法：
  python tools/self_learn.py                # 挖掘并展示建议
  python tools/self_learn.py --apply        # 挖掘并自动写入同义词表
  python tools/self_learn.py --verify       # 挖掘、写入、重新审计验证效果
"""

import sys
import os
import re
import json
import sqlite3
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 第1步：从卡片中提取 (清单核心名, 定额核心名) 对
# ============================================================

# 清洗用的停用词（从名称中去掉的无意义词）
STOP_WORDS = {'安装', '制作', '制作安装', '以内', '以上', '以下',
              '名称', '规格', '型号', '规格型号', '甲供材', '甲供',
              '其他', '未尽事宜', '满足', '要求', '详见', '图纸'}

# 定额名中的参数后缀（去掉数值和单位）
PARAM_SUFFIX_RE = re.compile(
    r'[\(（].*?[\)）]|≤\S+|≥\S+|DN\d+|φ\d+|\d+[mtkg]|\d+mm|'
    r'\d+m2|\d+m3/h|\d+kW|\d+kVA|\d+A'
)


def clean_bill_name(text):
    """从清单文本提取核心设备名"""
    if not text:
        return ''
    # 取第一行
    line = text.split('\n')[0].strip()
    # 去掉"名称:"前缀
    for prefix in ['名称:', '名称：', '1、名称:', '1.名称:']:
        if prefix in line:
            line = line.split(prefix)[-1].strip()
    # 去掉规格参数
    for sep in ['规格', '型号', '甲供', 'FD', 'FDEH', 'FDH', 'BECH', 'DFD',
                'AV', 'AK', 'FK', 'BV', 'SV', 'JM']:
        idx = line.find(sep)
        if idx > 2:  # 保留至少2个字
            line = line[:idx]
    # 去掉尾部的空格、数字、符号
    line = re.sub(r'[\s\d\.\-\+\*×xX/]+$', '', line).strip()
    # 去掉温度前缀如 "70℃" "280℃"
    line = re.sub(r'^\d+℃', '', line).strip()
    return line


def clean_quota_name(name):
    """从定额名提取核心名（去掉参数/档位）"""
    if not name:
        return ''
    # 去掉参数后缀
    cleaned = PARAM_SUFFIX_RE.sub('', name)
    # 去掉尾部的数值和单位
    cleaned = re.sub(r'[\d\.\s≤≥]+$', '', cleaned).strip()
    return cleaned


def extract_search_term(quota_name):
    """从定额名提取BM25搜索用的关键词"""
    if not quota_name:
        return ''
    # 保留核心名词，去掉参数但保留"安装"等动词（BM25需要）
    cleaned = PARAM_SUFFIX_RE.sub('', quota_name)
    cleaned = re.sub(r'[\d\.\s≤≥]+$', '', cleaned).strip()
    # 去掉多余空格
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def has_keyword_overlap(name1, name2):
    """检查两个名称是否有实质性关键词重叠"""
    # 分词（简单按字符分）
    # 用2-gram和3-gram检查重叠
    if not name1 or not name2:
        return False

    # 先检查是否有>=2字的公共子串
    for length in [3, 2]:
        grams1 = set(name1[i:i+length] for i in range(len(name1)-length+1))
        grams2 = set(name2[i:i+length] for i in range(len(name2)-length+1))
        # 去掉太泛的gram
        common = grams1 & grams2 - {'安装', '制作', '周长'}
        if common:
            return True
    return False


# ============================================================
# 第2步：挖掘同义词候选
# ============================================================

def mine_synonyms_from_cards(cards):
    """从经验卡片中挖掘同义词候选"""
    # (清单核心名, 定额搜索词) → 出现次数
    pair_counter = Counter()
    # (清单核心名, 定额搜索词) → 来源省份集合
    pair_provinces = defaultdict(set)
    # 存储详细信息
    pair_details = defaultdict(list)

    for card in cards:
        bill_text = card['bill_text']
        quota_names = card['quota_names']
        if not quota_names:
            continue

        # 提取核心名
        bill_core = clean_bill_name(bill_text)
        if not bill_core or len(bill_core) < 2:
            continue

        # 只看第一个定额（主定额）
        quota_name = quota_names[0]
        quota_core = clean_quota_name(quota_name)
        search_term = extract_search_term(quota_name)

        if not quota_core or len(quota_core) < 2:
            continue

        # 检查是否有关键词重叠
        if has_keyword_overlap(bill_core, quota_core):
            continue  # BM25能搜到，不需要同义词

        # 记录这个同义词候选
        pair = (bill_core, search_term)
        pair_counter[pair] += 1
        pair_provinces[pair].add(card['province'])
        if len(pair_details[pair]) < 3:
            pair_details[pair].append({
                'id': card['id'],
                'bill_text': bill_text[:50],
                'quota_name': quota_name[:40],
                'province': card['province'][:15],
            })

    return pair_counter, pair_provinces, pair_details


def rank_and_filter(pair_counter, pair_provinces, pair_details, min_count=2):
    """排序和过滤同义词候选"""
    # 统计每个搜索词被多少不同清单名映射（太泛的是安装方法不是同义词）
    term_bill_count = Counter()
    for (bill_core, search_term), count in pair_counter.items():
        term_bill_count[search_term] += 1

    # 过滤掉的通用安装方法（不是同义词）
    GENERIC_TERMS = {'法兰阀门安装', '焊接法兰阀', '螺纹阀门', '螺纹阀',
                     '法兰阀安装', '焊接法兰阀安装', '法兰电磁阀',
                     '石膏板', '低压交流异步电动机检查接线及调试'}

    candidates = []
    for (bill_core, search_term), count in pair_counter.most_common():
        if count < min_count:
            continue

        # 过滤1：清单名太长（可能是没清洗干净的描述）
        if len(bill_core) > 12:
            continue

        # 过滤2：搜索词是通用安装方法（被多种不同设备映射）
        core_term = clean_quota_name(search_term)
        if core_term in GENERIC_TERMS:
            continue
        if term_bill_count[search_term] > 5:
            continue  # 被太多不同设备映射的，不是同义词

        # 过滤3：清单名本身太泛（单字或纯数字）
        if len(bill_core) < 2:
            continue

        provinces = pair_provinces[(bill_core, search_term)]
        candidates.append({
            'bill_name': bill_core,
            'search_term': search_term,
            'count': count,
            'province_count': len(provinces),
            'provinces': list(provinces)[:5],
            'samples': pair_details[(bill_core, search_term)],
        })
    return candidates


# ============================================================
# 第3步：生成同义词表条目
# ============================================================

def generate_synonym_entries(candidates, existing_synonyms):
    """生成可直接写入 engineering_synonyms.json 的条目"""
    new_entries = {}
    skipped = []

    for cand in candidates:
        bill_name = cand['bill_name']
        search_term = cand['search_term']

        # 跳过已存在的
        if bill_name in existing_synonyms:
            skipped.append(bill_name)
            continue

        # 搜索词太长截断
        if len(search_term) > 30:
            search_term = search_term[:30]

        new_entries[bill_name] = {
            'search_terms': [search_term],
            'count': cand['count'],
            'province_count': cand['province_count'],
        }

    return new_entries, skipped


def apply_to_synonym_file(new_entries, synonym_path):
    """把新同义词写入文件"""
    with open(synonym_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    added = 0
    for bill_name, info in new_entries.items():
        if bill_name not in data and bill_name not in ('_说明', '_specialty_scope'):
            data[bill_name] = info['search_terms']
            added += 1

    with open(synonym_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return added


# ============================================================
# 主流程
# ============================================================

def main():
    import argparse
    ap = argparse.ArgumentParser(description='从经验卡片自动挖掘同义词')
    ap.add_argument('--apply', action='store_true', help='自动写入同义词表')
    ap.add_argument('--verify', action='store_true', help='写入后重新审计验证')
    ap.add_argument('--min-count', type=int, default=2, help='最小出现次数（默认2）')
    args = ap.parse_args()

    # 加载卡片
    print("加载经验库...")
    from src.experience_db import ExperienceDB
    db = ExperienceDB()
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT id, bill_text, bill_name, quota_ids, quota_names, province, layer, source
        FROM experiences
        WHERE layer = 'authority' OR source IN ('user_confirmed', 'user_correction')
        ORDER BY province
    ''').fetchall()
    conn.close()

    cards = []
    for r in rows:
        try:
            quota_names = json.loads(r['quota_names']) if r['quota_names'] else []
        except json.JSONDecodeError:
            quota_names = []
        cards.append({
            'id': r['id'],
            'bill_text': r['bill_text'] or '',
            'bill_name': r['bill_name'] or '',
            'quota_names': quota_names,
            'province': r['province'] or '',
            'layer': r['layer'] or '',
            'source': r['source'] or '',
        })

    print(f"共 {len(cards)} 张权威卡片")

    # 挖掘
    print("\n挖掘同义词候选...")
    pair_counter, pair_provinces, pair_details = mine_synonyms_from_cards(cards)

    # 过滤
    candidates = rank_and_filter(pair_counter, pair_provinces, pair_details,
                                 min_count=args.min_count)
    print(f"发现 {len(candidates)} 个同义词候选 (最小出现{args.min_count}次)")

    # 加载现有同义词表
    synonym_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                'data', 'engineering_synonyms.json')
    with open(synonym_path, 'r', encoding='utf-8') as f:
        existing = json.load(f)

    # 生成新条目
    new_entries, skipped = generate_synonym_entries(candidates, existing)
    print(f"新增候选: {len(new_entries)} 个（已存在跳过: {len(skipped)}）")

    # 展示Top结果
    print(f"\n{'='*70}")
    print(f"Top 同义词候选（清单叫法 → 定额搜索词）")
    print(f"{'='*70}")

    sorted_entries = sorted(new_entries.items(), key=lambda x: -x[1]['count'])
    cumulative = 0
    for i, (bill_name, info) in enumerate(sorted_entries[:30]):
        cumulative += info['count']
        cross = f"跨{info['province_count']}省" if info['province_count'] > 1 else "单省"
        print(f"  {i+1:2d}. [{info['count']:3d}次 {cross}] "
              f"\"{bill_name}\" → \"{info['search_terms'][0]}\"")

    print(f"\n  Top 30 累计覆盖 {cumulative} 条卡片的同义词缺口")

    # 保存候选到文件
    report_dir = 'output/temp/audit'
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, 'synonym_candidates.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({
            'total_cards': len(cards),
            'total_candidates': len(candidates),
            'new_entries': len(new_entries),
            'candidates': [{
                'bill_name': c['bill_name'],
                'search_term': c['search_term'],
                'count': c['count'],
                'province_count': c['province_count'],
            } for c in candidates],
        }, f, ensure_ascii=False, indent=2)
    print(f"\n候选列表: {report_path}")

    # 写入
    if args.apply or args.verify:
        print(f"\n写入同义词表...")
        added = apply_to_synonym_file(new_entries, synonym_path)
        print(f"已新增 {added} 条同义词")

        # 验证
        if args.verify:
            print(f"\n重新审计验证效果...")
            os.system(f'python tools/audit_experience.py --province "重庆" --limit 100')


if __name__ == '__main__':
    main()
