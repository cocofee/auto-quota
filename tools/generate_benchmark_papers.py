#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从经验库自动生成benchmark试卷

数据来源：
  - 权威层（人工确认的答案）
  - 候选层-OSS导入（编号对应关系就是答案）

用法：
  python tools/generate_benchmark_papers.py --preview       # 预览各省能出多少题
  python tools/generate_benchmark_papers.py                  # 生成所有新试卷
  python tools/generate_benchmark_papers.py --province 福建  # 只出某省
  python tools/generate_benchmark_papers.py --max-per-paper 200  # 每卷最多200题
  python tools/generate_benchmark_papers.py --include-existing   # 也重新生成已有试卷的省份
"""

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPERS_DIR = os.path.join(ROOT, "tests", "benchmark_papers")
DB_PATH = os.path.join(ROOT, "db", "common", "experience.db")


def get_existing_papers():
    """获取已有试卷的省份列表和已有的bill_text集合"""
    existing_provinces = set()
    existing_texts = defaultdict(set)  # province -> set(bill_text)

    for f in os.listdir(PAPERS_DIR):
        if not f.endswith(".json") or f.startswith("_"):
            continue
        province = f.replace(".json", "")
        existing_provinces.add(province)

        filepath = os.path.join(PAPERS_DIR, f)
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("items", data) if isinstance(data, dict) else data
        for item in items:
            if isinstance(item, dict):
                existing_texts[province].add(item.get("bill_text", ""))

    return existing_provinces, existing_texts


def infer_specialty(quota_ids):
    """从定额编号推断专业册号"""
    if not quota_ids:
        return ""
    # 取第一个编号
    qid = quota_ids[0] if isinstance(quota_ids, list) else quota_ids
    # 匹配 C数字 开头的模式，如 C4-1-10, C10-3-5
    m = re.match(r"(C\d+)-", str(qid))
    if m:
        return m.group(1)
    return ""


def parse_quota_field(value):
    """解析quota_ids/quota_names字段（可能是JSON字符串或已经是列表）"""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
        return [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [str(value)]


def quality_filter(record):
    """质量过滤：返回True表示通过"""
    bill_text = record.get("bill_text", "")
    if not bill_text or len(bill_text.strip()) < 10:
        return False

    quota_ids = parse_quota_field(record.get("quota_ids"))
    quota_names = parse_quota_field(record.get("quota_names"))

    if not quota_ids:
        return False

    # 检查编号格式：非空且长度≥2即可
    # 各省编号格式不同：C4-1-10（含-）、30403100（纯数字）、CL1592（CX开头）
    has_valid_id = any(len(str(qid).strip()) >= 2 for qid in quota_ids)
    if not has_valid_id:
        return False

    # quota_ids和quota_names长度一致
    if quota_names and len(quota_ids) != len(quota_names):
        return False

    return True


def load_candidates(province_filter=None, include_existing=False):
    """
    从经验库加载可出题的数据

    返回: {province: [record, ...]}
    """
    existing_provinces, existing_texts = get_existing_papers()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 查询：权威层 + OSS候选层
    sql = """
        SELECT id, bill_text, bill_name, quota_ids, quota_names,
               province, specialty, layer, source, confidence
        FROM experiences
        WHERE (layer = 'authority' OR (layer = 'candidate' AND source = 'oss_import'))
          AND bill_text IS NOT NULL
          AND bill_text != ''
          AND quota_ids IS NOT NULL
          AND quota_ids != ''
          AND quota_ids != '[]'
    """

    if province_filter:
        sql += f" AND province LIKE '%{province_filter}%'"

    cur.execute(sql)

    # 按省份分组，去重
    by_province = defaultdict(list)
    seen_texts = defaultdict(set)  # province -> set(bill_text)，用于去重

    for row in cur.fetchall():
        record = dict(row)
        province = record["province"]
        bill_text = record["bill_text"].strip()

        # 跳过已有试卷的省份（除非指定--include-existing）
        if not include_existing and province in existing_provinces:
            continue

        # 质量过滤
        if not quality_filter(record):
            continue

        # 去重（同省同bill_text只留一条）
        if bill_text in seen_texts[province]:
            continue
        seen_texts[province].add(bill_text)

        # 解析字段
        record["quota_ids"] = parse_quota_field(record["quota_ids"])
        record["quota_names"] = parse_quota_field(record["quota_names"])

        # specialty补全
        if not record.get("specialty"):
            record["specialty"] = infer_specialty(record["quota_ids"])

        by_province[province].append(record)

    conn.close()
    return by_province


def stratified_sample(records, max_count):
    """
    按专业分层抽样

    确保每个专业都有代表，然后按比例填满剩余名额
    """
    if len(records) <= max_count:
        return records

    # 按specialty分组
    by_spec = defaultdict(list)
    for r in records:
        spec = r.get("specialty", "") or "未知"
        by_spec[spec].append(r)

    result = []

    # 第一轮：每个专业至少抽min(5, 该专业总数)条
    min_per_spec = 5
    remaining_quota = max_count

    for spec, items in by_spec.items():
        n = min(min_per_spec, len(items))
        sampled = random.sample(items, n)
        result.extend(sampled)
        remaining_quota -= n

    if remaining_quota <= 0:
        return random.sample(result, max_count)

    # 第二轮：按比例分配剩余名额
    already_ids = {r["id"] for r in result}
    remaining_pool = [r for r in records if r["id"] not in already_ids]

    if remaining_pool:
        n = min(remaining_quota, len(remaining_pool))
        result.extend(random.sample(remaining_pool, n))

    return result


def generate_paper(province, records, max_count):
    """生成一份试卷JSON"""
    # 分层抽样
    sampled = stratified_sample(records, max_count)

    # 随机打乱顺序
    random.shuffle(sampled)

    # 统计
    auth_count = sum(1 for r in sampled if r["layer"] == "authority")
    cand_count = len(sampled) - auth_count

    # 构建试卷格式
    items = []
    for i, r in enumerate(sampled):
        item = {
            "id": r["id"],
            "bill_text": r["bill_text"].strip(),
            "bill_name": (r.get("bill_name") or "").strip(),
            "quota_ids": r["quota_ids"],
            "quota_names": r["quota_names"],
            "specialty": r.get("specialty", ""),
            "layer": r["layer"]
        }
        items.append(item)

    # 内容哈希（用于检测试卷是否有变化）
    content_str = json.dumps([it["bill_text"] for it in items], ensure_ascii=False)
    content_hash = hashlib.md5(content_str.encode()).hexdigest()[:8]

    paper = {
        "province": province,
        "total_items": len(items),
        "authority_count": auth_count,
        "candidate_count": cand_count,
        "content_hash": content_hash,
        "created": str(date.today()),
        "items": items
    }

    return paper


def preview(by_province, max_per_paper, min_per_paper):
    """预览模式：显示各省能出多少题"""
    print("=" * 70)
    print("Benchmark试卷生成预览")
    print("=" * 70)
    print()

    total_new = 0
    provinces_to_generate = []

    # 按数据量排序
    for province in sorted(by_province.keys(), key=lambda p: -len(by_province[p])):
        records = by_province[province]
        n = len(records)

        # 专业分布
        specs = defaultdict(int)
        layers = defaultdict(int)
        for r in records:
            spec = r.get("specialty", "") or "未知"
            specs[spec] += 1
            layers[r["layer"]] += 1

        will_generate = n >= min_per_paper
        status = "✅ 可出卷" if will_generate else f"❌ 不足{min_per_paper}题"
        actual = min(n, max_per_paper)

        print(f"  {province}")
        print(f"    可用: {n}条 → 出题: {actual}条 {status}")
        print(f"    来源: 权威{layers.get('authority', 0)} + 候选{layers.get('candidate', 0)}")
        spec_str = ", ".join(f"{k}:{v}" for k, v in sorted(specs.items(), key=lambda x: -x[1])[:6])
        print(f"    专业: {spec_str}")
        print()

        if will_generate:
            total_new += actual
            provinces_to_generate.append(province)

    print(f"合计: {len(provinces_to_generate)}份新试卷, 约{total_new}题")

    # 对比现有
    existing_provinces, _ = get_existing_papers()
    print(f"现有试卷: {len(existing_provinces)}份")
    print(f"扩充后: {len(existing_provinces) + len(provinces_to_generate)}份")


def generate_all(by_province, max_per_paper, min_per_paper):
    """生成所有试卷"""
    os.makedirs(PAPERS_DIR, exist_ok=True)

    generated = 0
    total_items = 0

    for province in sorted(by_province.keys(), key=lambda p: -len(by_province[p])):
        records = by_province[province]

        if len(records) < min_per_paper:
            print(f"  跳过 {province}: 只有{len(records)}条，不足{min_per_paper}题")
            continue

        paper = generate_paper(province, records, max_per_paper)

        # 保存
        filepath = os.path.join(PAPERS_DIR, f"{province}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(paper, f, ensure_ascii=False, indent=2)

        generated += 1
        total_items += paper["total_items"]
        print(f"  ✅ {province}: {paper['total_items']}题 "
              f"(权威{paper['authority_count']} + 候选{paper['candidate_count']})")

    print()
    print(f"生成完成: {generated}份试卷, 共{total_items}题")
    print(f"保存到: {PAPERS_DIR}")


def main():
    parser = argparse.ArgumentParser(description="从经验库生成benchmark试卷")
    parser.add_argument("--preview", action="store_true", help="预览模式，只显示统计不生成")
    parser.add_argument("--province", help="只处理包含该关键词的省份")
    parser.add_argument("--max-per-paper", type=int, default=200, help="每份试卷最多题数（默认200）")
    parser.add_argument("--min-per-paper", type=int, default=30, help="最少题数才生成试卷（默认30）")
    parser.add_argument("--include-existing", action="store_true", help="也重新生成已有试卷的省份")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认42，保证可复现）")
    args = parser.parse_args()

    random.seed(args.seed)

    print(f"加载经验库数据...")
    by_province = load_candidates(
        province_filter=args.province,
        include_existing=args.include_existing
    )

    if not by_province:
        print("没有找到可出题的数据。")
        if not args.include_existing:
            print("提示: 已有试卷的省份被跳过了，加 --include-existing 可以重新生成")
        return

    if args.preview:
        preview(by_province, args.max_per_paper, args.min_per_paper)
    else:
        generate_all(by_province, args.max_per_paper, args.min_per_paper)


if __name__ == "__main__":
    main()
