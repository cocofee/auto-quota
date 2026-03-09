# -*- coding: utf-8 -*-
"""
Qwen3-Embedding 微调训练数据生成器

从经验库权威层读取(清单文本, 定额名称)对，
用BM25搜索挖掘hard negative，构造三元组用于embedding微调。

用法:
    python tools/qwen3_prepare_training_data.py                    # 默认参数
    python tools/qwen3_prepare_training_data.py --max-neg 5        # 每条最多5个负样本
    python tools/qwen3_prepare_training_data.py --holdout 广东,宁夏  # 指定hold-out省份
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

import config


# ============================================================
# 1. 从经验库读取权威层数据
# ============================================================

def load_authority_records(db_path: str | Path) -> list[dict]:
    """读取经验库权威层所有记录"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, bill_text, bill_name, quota_ids, quota_names,
               province, specialty, confidence, confirm_count
        FROM experiences
        WHERE layer = 'authority'
          AND bill_text IS NOT NULL AND bill_text != ''
          AND quota_names IS NOT NULL AND quota_names != ''
          AND quota_names != '[]'
        ORDER BY province, id
    """)
    records = []
    for row in cursor.fetchall():
        rec = dict(row)
        # 解析JSON字段
        try:
            rec["quota_ids"] = json.loads(rec["quota_ids"]) if rec["quota_ids"] else []
            rec["quota_names"] = json.loads(rec["quota_names"]) if rec["quota_names"] else []
        except json.JSONDecodeError:
            continue
        # 过滤空定额名
        if not rec["quota_names"] or not any(n.strip() for n in rec["quota_names"]):
            continue
        records.append(rec)
    conn.close()
    return records


# ============================================================
# 2. 用BM25挖掘hard negative
# ============================================================

def mine_hard_negatives(
    records: list[dict],
    max_neg_per_query: int = 5,
    bm25_top_k: int = 20,
) -> list[dict]:
    """
    对每条经验记录用BM25搜索，排除正确答案后取排名靠前的作为hard negative。

    返回三元组列表: [{"query": ..., "positive": ..., "negative": ..., "province": ..., "split": ...}]
    """
    from src.bm25_engine import BM25Engine

    # 按省份分组（每个省份需要初始化自己的BM25引擎）
    by_province = defaultdict(list)
    for rec in records:
        by_province[rec["province"]].append(rec)

    triplets = []
    total = len(records)
    processed = 0
    skipped_no_engine = 0

    for province, prov_records in sorted(by_province.items()):
        print(f"\n处理 {province}（{len(prov_records)} 条）...")

        # 尝试初始化BM25引擎
        try:
            bm25 = BM25Engine(province=province)
            bm25.ensure_index()
            if bm25.bm25 is None:
                print(f"  ⚠️ {province} BM25索引构建失败，跳过")
                skipped_no_engine += len(prov_records)
                processed += len(prov_records)
                continue
        except Exception as e:
            print(f"  ⚠️ {province} 初始化BM25失败: {e}，跳过")
            skipped_no_engine += len(prov_records)
            processed += len(prov_records)
            continue

        prov_triplets = 0
        for rec in prov_records:
            query = rec["bill_text"]
            correct_ids = set(rec["quota_ids"])
            positive_names = [n.strip() for n in rec["quota_names"] if n.strip()]

            if not positive_names:
                processed += 1
                continue

            # BM25搜索
            try:
                results = bm25.search(query, top_k=bm25_top_k)
            except Exception:
                processed += 1
                continue

            # 挖掘hard negative：排名靠前但不是正确答案的
            negatives = []
            for r in results:
                rid = r.get("quota_id", "")
                rname = r.get("name", "").strip()
                if rid not in correct_ids and rname and rname not in positive_names:
                    negatives.append(rname)
                if len(negatives) >= max_neg_per_query:
                    break

            # 构造三元组（每个positive × 每个negative）
            for pos in positive_names[:3]:  # 最多3个positive（避免一条清单对应太多定额时爆炸）
                for neg in negatives[:max_neg_per_query]:
                    triplets.append({
                        "query": query,
                        "positive": pos,
                        "negative": neg,
                        "province": province,
                    })
                    prov_triplets += 1

            processed += 1
            if processed % 5000 == 0:
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                print(f"  进度: {processed}/{total} ({processed/total*100:.1f}%), "
                      f"速度: {speed:.0f}条/秒, 三元组: {len(triplets)}")

        print(f"  {province} 完成: {prov_triplets} 个三元组")

    if skipped_no_engine:
        print(f"\n⚠️ 跳过 {skipped_no_engine} 条（所属省份无BM25索引）")

    return triplets


# ============================================================
# 3. 按省份划分train/val/test
# ============================================================

def split_by_province(
    triplets: list[dict],
    holdout_provinces: list[str],
    val_ratio: float = 0.1,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    按省份划分数据集：
    - holdout_provinces 整省做测试集
    - 剩余省份中随机抽 val_ratio 做验证集
    - 其余做训练集
    """
    import random
    random.seed(42)

    holdout_set = set(holdout_provinces)
    test = [t for t in triplets if t["province"] in holdout_set]
    rest = [t for t in triplets if t["province"] not in holdout_set]

    # 从剩余中按比例抽验证集
    random.shuffle(rest)
    val_size = int(len(rest) * val_ratio)
    val = rest[:val_size]
    train = rest[val_size:]

    return train, val, test


# ============================================================
# 4. 主函数
# ============================================================

def main():
    global start_time
    start_time = time.time()

    parser = argparse.ArgumentParser(description="Qwen3-Embedding训练数据生成器")
    parser.add_argument("--output", type=str, default="data/qwen3_training_triplets.jsonl",
                        help="输出JSONL路径")
    parser.add_argument("--max-neg", type=int, default=3,
                        help="每条清单最多几个负样本（默认3）")
    parser.add_argument("--holdout", type=str, default="广东安装,宁夏安装",
                        help="hold-out测试省份（逗号分隔）")
    parser.add_argument("--bm25-top-k", type=int, default=20,
                        help="BM25搜索返回数（默认20）")
    args = parser.parse_args()

    # 1. 读取经验库
    db_path = config.get_experience_db_path()
    print(f"经验库路径: {db_path}")
    records = load_authority_records(db_path)
    print(f"权威层记录: {len(records)} 条")

    # 统计省份分布
    province_counts = defaultdict(int)
    for r in records:
        province_counts[r["province"]] += 1
    print(f"省份数: {len(province_counts)}")
    for prov, cnt in sorted(province_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {prov}: {cnt}")

    # 2. 挖掘hard negative
    print(f"\n开始挖掘hard negative（BM25 top-{args.bm25_top_k}，每条最多{args.max_neg}个负样本）...")
    triplets = mine_hard_negatives(records, max_neg_per_query=args.max_neg, bm25_top_k=args.bm25_top_k)
    print(f"\n总计生成 {len(triplets)} 个三元组")

    if not triplets:
        print("❌ 没有生成任何三元组，请检查经验库和BM25索引")
        return

    # 3. 划分数据集
    holdout_list = [p.strip() for p in args.holdout.split(",") if p.strip()]
    train, val, test = split_by_province(triplets, holdout_list)
    print(f"\n数据划分:")
    print(f"  训练集: {len(train)} 条")
    print(f"  验证集: {len(val)} 条")
    print(f"  测试集: {len(test)} 条（hold-out: {holdout_list}）")

    # 4. 写入JSONL（带split标签）
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for t in train:
            t["split"] = "train"
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
        for t in val:
            t["split"] = "val"
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
        for t in test:
            t["split"] = "test"
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    elapsed = time.time() - start_time
    file_size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"\n✅ 训练数据已保存: {output_path}")
    print(f"   文件大小: {file_size_mb:.1f} MB")
    print(f"   总耗时: {elapsed:.1f} 秒")

    # 5. 打印样例
    print(f"\n--- 样例（前3条）---")
    for t in triplets[:3]:
        print(f"  query:    {t['query'][:60]}")
        print(f"  positive: {t['positive'][:60]}")
        print(f"  negative: {t['negative'][:60]}")
        print()


# 全局计时器
start_time = 0

if __name__ == "__main__":
    main()
