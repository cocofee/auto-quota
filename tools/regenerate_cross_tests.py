# -*- coding: utf-8 -*-
"""
跨省benchmark试卷生成器（v2 — 全国覆盖+覆盖矩阵）

功能：
1. 自动扫描经验库中所有有权威层数据的省份
2. 按质量标准过滤+均匀抽样生成试卷（每省50题）
3. 输出覆盖矩阵报告（哪些省有试卷、哪些数据不足）

用法：
    python tools/regenerate_cross_tests.py                # 重建已有试卷（向后兼容）
    python tools/regenerate_cross_tests.py --all           # 全量扫描，新省份也出题
    python tools/regenerate_cross_tests.py --province 广东  # 只重建指定省份
    python tools/regenerate_cross_tests.py --all --matrix   # 只输出覆盖矩阵，不重建试卷
"""

import sys
import os
import json
import re
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

# 输出目录
TEST_DIR = PROJECT_ROOT / "tests" / "cross_province_tests"

# 定额编号前缀→专业册号映射
QUOTA_PREFIX_TO_BOOK = {
    # 标准格式：C4-1-10 或 4-1-10
    '1': 'C1', '2': 'C2', '3': 'C3', '4': 'C4', '5': 'C5',
    '6': 'C6', '7': 'C7', '8': 'C8', '9': 'C9', '10': 'C10',
    '11': 'C11', '12': 'C12', '13': 'C13',
    # CG格式（重庆）：CG0001 → C=C3, D=C4, G=C7, I=C9, J=C10 等
    'CA': 'C1', 'CB': 'C2', 'CC': 'C3', 'CD': 'C4', 'CE': 'C5',
    'CF': 'C6', 'CG': 'C7', 'CH': 'C8', 'CI': 'C9', 'CJ': 'C10',
    'CK': 'C11', 'CL': 'C12',
}


def infer_specialty(quota_ids):
    """从定额编号推断专业册号"""
    for qid in quota_ids:
        qid = qid.strip()
        # CG格式（重庆）：CG0001
        if len(qid) >= 2 and qid[0] == 'C' and qid[1].isalpha():
            prefix = qid[:2]
            if prefix in QUOTA_PREFIX_TO_BOOK:
                return QUOTA_PREFIX_TO_BOOK[prefix]

        # 标准格式：10-1-183 或 C10-1-183
        m = re.match(r'C?(\d+)-', qid)
        if m:
            num = m.group(1)
            if num in QUOTA_PREFIX_TO_BOOK:
                return QUOTA_PREFIX_TO_BOOK[num]

        # 8位数字格式（福建等）：30402076 → 前1-2位判断
        if re.match(r'^\d{7,8}$', qid):
            # 暂不处理，返回空
            pass

    return ''


def load_authority_records(province):
    """从经验库加载权威层记录"""
    import sqlite3
    db_path = PROJECT_ROOT / "db" / "common" / "experience.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, bill_text, bill_name, quota_ids, quota_names, specialty
        FROM experiences
        WHERE province = ? AND layer = 'authority'
        ORDER BY id
    """, (province,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def quality_filter(records):
    """质量过滤：确保题目信息充足"""
    good = []
    for r in records:
        # 1. bill_name非空
        if not r.get('bill_name') or not r['bill_name'].strip():
            continue

        # 2. bill_text长度>=30
        if not r.get('bill_text') or len(r['bill_text'].strip()) < 30:
            continue

        # 3. quota_ids非空
        quota_ids = r.get('quota_ids', '')
        if isinstance(quota_ids, str):
            try:
                quota_ids = json.loads(quota_ids)
            except:
                quota_ids = [x.strip() for x in quota_ids.split(',') if x.strip()]
        if not quota_ids:
            continue

        # 4. quota_names非空
        quota_names = r.get('quota_names', '')
        if isinstance(quota_names, str):
            try:
                quota_names = json.loads(quota_names)
            except:
                quota_names = [x.strip() for x in quota_names.split(',') if x.strip()]
        if not quota_names:
            continue

        # 5. 自动推断specialty
        specialty = r.get('specialty', '') or ''
        if not specialty:
            specialty = infer_specialty(quota_ids)

        good.append({
            'id': r['id'],
            'bill_text': r['bill_text'].strip(),
            'bill_name': r['bill_name'].strip(),
            'quota_ids': quota_ids,
            'quota_names': quota_names,
            'specialty': specialty,
            'status': 'auto',  # auto=自动生成，confirmed=人工确认过
        })

    return good


def uniform_sample(records, n=50, seed=42):
    """均匀抽样：固定种子，可复现"""
    import random
    rng = random.Random(seed)

    if len(records) <= n:
        return records

    # 按specialty分组，每组均匀抽
    from collections import defaultdict
    by_spec = defaultdict(list)
    for r in records:
        by_spec[r['specialty'] or 'unknown'].append(r)

    # 按组大小分配名额
    total = len(records)
    sampled = []
    remaining_slots = n

    groups = sorted(by_spec.items(), key=lambda x: -len(x[1]))
    for spec, group in groups:
        # 按比例分配，至少1条
        quota = max(1, round(len(group) / total * n))
        quota = min(quota, remaining_slots, len(group))
        if quota <= 0:
            continue
        picked = rng.sample(group, quota)
        sampled.extend(picked)
        remaining_slots -= len(picked)
        if remaining_slots <= 0:
            break

    # 如果还有剩余名额，从所有剩余中随机补
    if remaining_slots > 0:
        used_ids = {r['id'] for r in sampled}
        rest = [r for r in records if r['id'] not in used_ids]
        if rest:
            extra = rng.sample(rest, min(remaining_slots, len(rest)))
            sampled.extend(extra)

    return sampled[:n]


def generate_province_test(province, sample_size=50):
    """生成单个省份的试卷"""
    records = load_authority_records(province)
    if not records:
        return None, f"无权威层数据"

    filtered = quality_filter(records)
    if not filtered:
        return None, f"过滤后无有效数据（原{len(records)}条）"

    sampled = uniform_sample(filtered, n=sample_size, seed=42)

    today = datetime.now().strftime("%Y-%m-%d")
    test_data = {
        "province": province,
        "total_authority": len(records),
        "after_quality_filter": len(filtered),
        "sample_size": len(sampled),
        "sample_method": "quality_filtered_uniform",
        "quality_criteria": "bill_name非空, bill_text>=30字, quota_ids非空, auto_specialty",
        "created": today,
        "items": sampled,
    }

    return test_data, None


def scan_all_provinces(min_authority=20):
    """扫描经验库，返回所有省份的权威层数据统计

    返回：[(省份名, 权威层数量), ...]，按数量降序
    """
    import sqlite3
    db_path = PROJECT_ROOT / "db" / "common" / "experience.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # 查所有省份（不设阈值，覆盖矩阵需要看全貌）
    cur.execute("""
        SELECT province, COUNT(*) as cnt
        FROM experiences
        WHERE layer = 'authority'
        GROUP BY province
        ORDER BY cnt DESC
    """)
    all_provinces = cur.fetchall()
    conn.close()
    return all_provinces


def generate_coverage_matrix(all_provinces, min_authority=20):
    """生成覆盖矩阵报告

    显示哪些省有试卷、哪些数据不足、每个省多少题
    """
    today = datetime.now().strftime("%Y-%m-%d")
    ok_count = sum(1 for _, cnt in all_provinces if cnt >= min_authority)
    insufficient = sum(1 for _, cnt in all_provinces if cnt < min_authority)

    # 检查已有试卷
    existing_tests = {}
    for f in TEST_DIR.glob("*.json"):
        if f.name.startswith('_'):
            continue
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                existing_tests[data.get('province', '')] = data.get('sample_size', 0)
        except:
            pass

    provinces_detail = []
    for prov, cnt in all_provinces:
        test_count = existing_tests.get(prov, 0)
        if cnt >= min_authority:
            status = "ok"
        else:
            status = "insufficient"
        provinces_detail.append({
            "name": prov,
            "authority_count": cnt,
            "test_count": test_count,
            "status": status,
        })

    matrix = {
        "generated": today,
        "min_authority_threshold": min_authority,
        "summary": {
            "total_provinces": len(all_provinces),
            "with_tests": ok_count,
            "insufficient_data": insufficient,
            "existing_test_files": len(existing_tests),
        },
        "provinces": provinces_detail,
    }
    return matrix


def print_coverage_matrix(matrix):
    """打印覆盖矩阵到控制台"""
    summary = matrix['summary']
    print(f"\n{'='*70}")
    print(f"覆盖矩阵 — {matrix['generated']}")
    print(f"{'='*70}")
    print(f"  可出题省份(≥{matrix['min_authority_threshold']}条): {summary['with_tests']}个")
    print(f"  数据不足省份:  {summary['insufficient_data']}个")
    print(f"  已有试卷文件:  {summary['existing_test_files']}个")
    print()
    print(f"  {'省份':40s} | {'权威层':>6s} | {'试卷':>4s} | 状态")
    print(f"  {'-'*40}-+{'-'*8}-+{'-'*6}-+{'-'*10}")
    for p in matrix['provinces']:
        marker = '✅' if p['status'] == 'ok' else '⚠️'
        test_str = str(p['test_count']) if p['test_count'] > 0 else '-'
        name = p['name'][:38]
        print(f"  {marker} {name:38s} | {p['authority_count']:6d} | {test_str:>4s} | {p['status']}")
    print(f"{'='*70}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='跨省benchmark试卷生成器')
    parser.add_argument('--all', action='store_true',
                        help='全量扫描所有省份（默认只重建已有试卷）')
    parser.add_argument('--province', type=str,
                        help='只重建指定省份的试卷（模糊匹配）')
    parser.add_argument('--matrix', action='store_true',
                        help='只输出覆盖矩阵，不重建试卷')
    parser.add_argument('--min-authority', type=int, default=20,
                        help='权威层最少条数阈值（默认20）')
    args = parser.parse_args()

    min_auth = args.min_authority

    # 扫描所有省份的权威层数据
    all_provinces = scan_all_provinces(min_auth)

    # 覆盖矩阵（先生成，任何模式都输出）
    matrix = generate_coverage_matrix(all_provinces, min_auth)
    print_coverage_matrix(matrix)

    # 保存矩阵JSON
    matrix_dir = PROJECT_ROOT / "output" / "temp"
    matrix_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = matrix_dir / "coverage_matrix.json"
    with open(matrix_path, 'w', encoding='utf-8') as f:
        json.dump(matrix, f, ensure_ascii=False, indent=2)
    print(f"\n覆盖矩阵已保存: {matrix_path}")

    # 如果只看矩阵，到此结束
    if args.matrix:
        return

    # 确定要生成试卷的省份列表
    eligible = [(p, c) for p, c in all_provinces if c >= min_auth]

    if args.province:
        # 模糊匹配指定省份
        keyword = args.province
        matched = [(p, c) for p, c in eligible if keyword in p]
        if not matched:
            print(f"\n未找到匹配'{keyword}'的省份（权威层≥{min_auth}条）")
            print(f"可选: {', '.join(p[:15] for p,_ in eligible)}")
            return
        provinces_to_gen = matched
        print(f"\n匹配到{len(matched)}个省份:")
    elif args.all:
        # 全量模式：所有有足够数据的省份
        provinces_to_gen = eligible
        print(f"\n全量模式: 将为{len(eligible)}个省份生成试卷")
    else:
        # 默认模式：只重建已有试卷文件对应的省份
        existing_provinces = set()
        for f in TEST_DIR.glob("*.json"):
            if f.name.startswith('_'):
                continue
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                    existing_provinces.add(data.get('province', ''))
            except:
                pass
        provinces_to_gen = [(p, c) for p, c in eligible if p in existing_provinces]
        print(f"\n默认模式: 重建{len(provinces_to_gen)}个已有试卷")
        if len(provinces_to_gen) < len(eligible):
            new_count = len(eligible) - len(provinces_to_gen)
            print(f"  提示: 还有{new_count}个省份可出题，用 --all 生成全量试卷")

    if not provinces_to_gen:
        print("无试卷需要生成")
        return

    # 备份旧试卷
    import shutil
    backup_dir = TEST_DIR / "_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if args.all or not args.province:
        # 全量或默认模式：备份并删除所有旧文件
        old_files = [f for f in TEST_DIR.glob("*.json") if not f.name.startswith('_')]
        for f in old_files:
            shutil.copy2(f, backup_dir / f.name)
            f.unlink()
        print(f"旧试卷已备份并删除（{len(old_files)}个）")

    # 生成新试卷
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    total_generated = 0

    for prov, cnt in provinces_to_gen:
        test_data, err = generate_province_test(prov)
        if err:
            print(f"  ❌ {prov[:30]}: {err}")
            continue

        # 文件名用省份前缀（截断到合理长度，去掉版本号括号）
        fname = prov.split('(')[0].split('（')[0].strip()
        # 文件名太长截断
        if len(fname) > 25:
            fname = fname[:25]
        fname = fname + ".json"
        fpath = TEST_DIR / fname

        # 如果是单省模式，先备份旧文件
        if args.province and fpath.exists():
            shutil.copy2(fpath, backup_dir / fpath.name)

        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(test_data, f, ensure_ascii=False, indent=2)

        orig = test_data['total_authority']
        filtered = test_data['after_quality_filter']
        sampled = test_data['sample_size']
        filter_rate = filtered * 100 // orig if orig > 0 else 0
        print(f"  ✅ {prov[:30]}: {orig}条→过滤{filtered}条({filter_rate}%)→抽样{sampled}题")
        total_generated += 1

    print(f"\n生成完成: {total_generated}个省份试卷")
    print(f"试卷目录: {TEST_DIR}")
    print(f"\n下一步: python tools/run_national_benchmark.py --save  # 跑全国基线")


if __name__ == "__main__":
    main()
