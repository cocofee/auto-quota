# -*- coding: utf-8 -*-
"""
重新生成跨省benchmark试卷（P0修复）

问题：原试卷约40%题目信息不足（bill_name为空、bill_text太短、specialty缺失），
导致算法答不出不是算法的错，而是题出得不公平。

修复：
1. bill_name必须非空
2. bill_text长度>=30字（确保有项目特征描述）
3. 从quota_ids前缀自动推断specialty
4. 每省50题，均匀抽样，固定种子可复现
"""

import sys
import os
import json
import re
from pathlib import Path

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

    test_data = {
        "province": province,
        "total_authority": len(records),
        "after_quality_filter": len(filtered),
        "sample_size": len(sampled),
        "sample_method": "quality_filtered_uniform",
        "quality_criteria": "bill_name非空, bill_text>=30字, quota_ids非空, auto_specialty",
        "created": "2026-03-02",
        "items": sampled,
    }

    return test_data, None


def main():
    import config

    # 找出有权威层数据的省份
    import sqlite3
    db_path = PROJECT_ROOT / "db" / "common" / "experience.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        SELECT province, COUNT(*) as cnt
        FROM experiences
        WHERE layer = 'authority'
        GROUP BY province
        HAVING cnt >= 20
        ORDER BY cnt DESC
    """)
    provinces = cur.fetchall()
    conn.close()

    print(f"有权威层数据的省份（>=20条）：{len(provinces)}个")
    for prov, cnt in provinces:
        print(f"  {prov[:35]}: {cnt}条")

    # 备份旧试卷，然后删除（防止新旧文件名不同导致重复加载）
    import shutil
    backup_dir = TEST_DIR / "_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    old_files = [f for f in TEST_DIR.glob("*.json") if not f.name.startswith('_')]
    for f in old_files:
        shutil.copy2(f, backup_dir / f.name)
        f.unlink()  # 删除旧文件
    print(f"\n旧试卷已备份并删除（{len(old_files)}个）")

    # 生成新试卷
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    total_generated = 0

    for prov, cnt in provinces:
        test_data, err = generate_province_test(prov)
        if err:
            print(f"  ❌ {prov[:30]}: {err}")
            continue

        # 文件名用省份前缀（截断到合理长度）
        fname = prov.split('(')[0].split('（')[0][:20] + ".json"
        fpath = TEST_DIR / fname
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
    print(f"\n下一步: python tools/run_cross_benchmark.py --save  # 跑新基线")


if __name__ == "__main__":
    main()
