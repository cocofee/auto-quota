# -*- coding: utf-8 -*-
"""
从经验库生成benchmark试卷。

从权威层全量出题，候选层按比例抽样，生成固定JSON试卷。
试卷生成后不再变动，保证每次跑分用同一套题，结果可对比。

用法：
  python tools/generate_test_papers.py                    # 生成试卷（默认权威层全量+候选层抽样200）
  python tools/generate_test_papers.py --authority-only    # 只用权威层（最可靠）
  python tools/generate_test_papers.py --max-per-province 500  # 每省最多500条
  python tools/generate_test_papers.py --dry-run           # 只看统计，不写文件

输出：
  tests/benchmark_papers/ 目录下按省份生成JSON试卷
"""

import sys
import os
import json
import sqlite3
import hashlib
import random
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'db', 'common', 'experience.db')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'tests', 'benchmark_papers')

# 每省最少出题量（低于此数的省份跳过）
MIN_ITEMS = 20


def load_from_experience_db(authority_only=False, min_text_len=20):
    """从经验库加载出题数据

    参数:
        authority_only: 只用权威层（质量最高）
        min_text_len: bill_text最短长度（过滤垃圾数据）

    返回:
        {province: [item, ...]} 字典
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 基础质量过滤：名称非空、有答案、文本够长
    layer_filter = "AND layer = 'authority'" if authority_only else ""
    c.execute(f'''
        SELECT id, bill_text, bill_name, quota_ids, quota_names,
               province, layer, specialty, source
        FROM experiences
        WHERE bill_name IS NOT NULL AND bill_name != ''
          AND quota_ids IS NOT NULL AND quota_ids != '' AND quota_ids != '[]'
          AND LENGTH(bill_text) >= ?
          {layer_filter}
        ORDER BY province, id
    ''', (min_text_len,))

    # 按省份分组
    province_items = {}
    for row in c.fetchall():
        prov = row['province']
        if not prov:
            continue

        # 解析quota_ids（可能是JSON数组或逗号分隔）
        raw_ids = row['quota_ids']
        try:
            quota_ids = json.loads(raw_ids)
        except (json.JSONDecodeError, TypeError):
            quota_ids = [s.strip() for s in raw_ids.split(',') if s.strip()]

        # 解析quota_names
        raw_names = row['quota_names'] or '[]'
        try:
            quota_names = json.loads(raw_names)
        except (json.JSONDecodeError, TypeError):
            quota_names = [s.strip() for s in raw_names.split(',') if s.strip()]

        if not quota_ids:
            continue

        item = {
            'id': row['id'],
            'bill_text': row['bill_text'],
            'bill_name': row['bill_name'],
            'quota_ids': quota_ids,
            'quota_names': quota_names,
            'specialty': row['specialty'] or '',
            'layer': row['layer'],
            'source': row['source'] or '',
        }

        if prov not in province_items:
            province_items[prov] = []
        province_items[prov].append(item)

    conn.close()
    return province_items


def deduplicate_items(items):
    """去重：同一个bill_name只保留一条（优先权威层）"""
    seen = {}
    for item in items:
        key = item['bill_name'].strip()
        if key in seen:
            # 权威层优先
            if item['layer'] == 'authority' and seen[key]['layer'] != 'authority':
                seen[key] = item
        else:
            seen[key] = item
    return list(seen.values())


def sample_items(items, max_count, seed=42):
    """从items中抽样，权威层全量保留，候选层按比例抽样"""
    authority = [it for it in items if it['layer'] == 'authority']
    candidate = [it for it in items if it['layer'] != 'authority']

    # 权威层全量保留
    result = list(authority)

    # 如果权威层已经超过max_count，随机抽样权威层
    if len(result) > max_count:
        rng = random.Random(seed)
        result = rng.sample(result, max_count)
        return result

    # 候选层补充到max_count
    remaining = max_count - len(result)
    if remaining > 0 and candidate:
        rng = random.Random(seed)
        sample_size = min(remaining, len(candidate))
        result.extend(rng.sample(candidate, sample_size))

    return result


def generate_paper(province, items):
    """生成一个省份的试卷JSON"""
    # 统计
    authority_count = sum(1 for it in items if it['layer'] == 'authority')
    candidate_count = len(items) - authority_count

    # 生成内容hash（用于检测试卷是否需要更新）
    content_str = json.dumps([it['id'] for it in items], sort_keys=True)
    content_hash = hashlib.md5(content_str.encode()).hexdigest()[:8]

    # 清理item，去掉生成时的辅助字段
    clean_items = []
    for it in items:
        clean_items.append({
            'id': it['id'],
            'bill_text': it['bill_text'],
            'bill_name': it['bill_name'],
            'quota_ids': it['quota_ids'],
            'quota_names': it['quota_names'],
            'specialty': it['specialty'],
            'layer': it['layer'],
        })

    paper = {
        'province': province,
        'total_items': len(clean_items),
        'authority_count': authority_count,
        'candidate_count': candidate_count,
        'content_hash': content_hash,
        'created': datetime.now().strftime('%Y-%m-%d'),
        'items': clean_items,
    }
    return paper


def main():
    import argparse
    ap = argparse.ArgumentParser(description='从经验库生成benchmark试卷')
    ap.add_argument('--authority-only', action='store_true',
                    help='只用权威层数据（最可靠，约8500条）')
    ap.add_argument('--max-per-province', type=int, default=0,
                    help='每省最多出题数（0=不限制）')
    ap.add_argument('--min-text-len', type=int, default=20,
                    help='bill_text最短长度（默认20字符）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只看统计，不写文件')
    args = ap.parse_args()

    print(f"从经验库生成benchmark试卷")
    print(f"  数据库: {DB_PATH}")
    print(f"  模式: {'仅权威层' if args.authority_only else '权威层全量+候选层补充'}")
    if args.max_per_province:
        print(f"  每省上限: {args.max_per_province}")
    print()

    # 加载数据
    province_items = load_from_experience_db(
        authority_only=args.authority_only,
        min_text_len=args.min_text_len,
    )

    # 处理每个省份
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total_items = 0
    total_provinces = 0

    for prov in sorted(province_items.keys()):
        items = province_items[prov]

        # 去重
        items = deduplicate_items(items)

        # 过滤太少的省份
        if len(items) < MIN_ITEMS:
            continue

        # 抽样（如果设了上限）
        if args.max_per_province > 0:
            items = sample_items(items, args.max_per_province)

        # 统计
        auth_cnt = sum(1 for it in items if it['layer'] == 'authority')
        cand_cnt = len(items) - auth_cnt
        prov_short = prov.split('(')[0].split('（')[0]
        print(f"  {prov_short}: {len(items)}条 (权威{auth_cnt} + 候选{cand_cnt})")

        total_items += len(items)
        total_provinces += 1

        if args.dry_run:
            continue

        # 生成试卷并写文件
        paper = generate_paper(prov, items)
        # 文件名用省份全名（含年份），和原来的格式一致
        fname = prov + '.json'
        fpath = os.path.join(OUTPUT_DIR, fname)
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(paper, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"共 {total_provinces} 个省份，{total_items} 条题目")
    if not args.dry_run:
        print(f"试卷已保存到 {OUTPUT_DIR}/")
    else:
        print(f"（dry-run模式，未写文件）")


if __name__ == '__main__':
    main()
