# -*- coding: utf-8 -*-
"""
Qwen3-Embedding 评测脚本：对比 BGE vs 微调后的 Qwen3

对 benchmark 试卷中每条清单，分别用两个模型做向量搜索，
比较 Recall@10（正确定额出现在前10名的比例）。

用法:
    python tools/qwen3_eval.py                          # 全部省份
    python tools/qwen3_eval.py --province 广东           # 只跑一个省
    python tools/qwen3_eval.py --top-k 20               # 看Recall@20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, ".")


def load_benchmark_papers(province_filter: str = None) -> list[dict]:
    """加载benchmark试卷"""
    papers_dir = Path("tests/benchmark_papers")
    all_items = []

    for f in sorted(papers_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        with open(f, "r", encoding="utf-8") as fp:
            paper = json.load(fp)

        prov = paper.get("province", f.stem)
        if province_filter and province_filter not in prov:
            continue

        for item in paper.get("items", []):
            item["province"] = prov
            all_items.append(item)

    return all_items


def load_model(model_path: str):
    """加载SentenceTransformer模型"""
    from sentence_transformers import SentenceTransformer

    print(f"  加载模型: {model_path}")
    model = SentenceTransformer(model_path)
    # 测试维度
    test_emb = model.encode(["测试"], normalize_embeddings=True)
    print(f"  维度: {len(test_emb[0])}, 设备: {model.device}")
    return model


def encode_batch(model, texts: list[str], is_bge: bool, batch_size: int = 64) -> np.ndarray:
    """批量编码文本

    BGE模型查询端需要加前缀，Qwen3不需要（用prompt_name方式，
    但sentence-transformers的encode不直接支持prompt_name，
    实际测试发现Qwen3不加前缀也能工作）
    """
    if is_bge:
        # BGE查询端加前缀
        prefix = "为这个句子生成表示以用于检索中文文档: "
        texts = [prefix + t for t in texts]

    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return embeddings


def build_quota_index(model, province: str, is_bge: bool) -> tuple:
    """为指定省份构建定额向量索引（内存版，不用ChromaDB）

    直接从SQLite定额库读取所有定额，用模型编码后存内存。
    返回: (quota_names列表, quota_ids列表, embeddings矩阵)
    """
    import sqlite3
    import config

    db_path = config.get_quota_db_path(province)
    if not Path(db_path).exists():
        print(f"    ⚠️ 定额库不存在: {db_path}")
        return [], [], None

    # 从SQLite读取定额
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, search_text FROM quotas WHERE search_text IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return [], [], None

    # 提取定额名称和ID
    names = [row["search_text"] for row in rows]
    ids = [str(row["id"]) for row in rows]

    # 批量编码（定额端不加前缀，BGE和Qwen3都一样）
    print(f"    编码 {len(names)} 条定额...")
    embeddings = model.encode(
        names,
        batch_size=128,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    return names, ids, embeddings


def search_topk(query_emb: np.ndarray, index_embs: np.ndarray, top_k: int = 10) -> list[int]:
    """向量搜索：返回top_k最相似的索引（余弦相似度，已归一化所以用点积）"""
    # query_emb: (dim,)  index_embs: (n, dim)
    scores = index_embs @ query_emb  # 点积 = 余弦相似度（已归一化）
    top_indices = np.argsort(scores)[::-1][:top_k]
    return top_indices.tolist()


def evaluate_model(model, items: list[dict], province_indices: dict,
                   is_bge: bool, top_k: int = 10) -> dict:
    """评估一个模型在benchmark上的Recall@K

    Args:
        model: SentenceTransformer模型
        items: benchmark试卷条目
        province_indices: {省份: (names, ids, embeddings)} 预建索引
        is_bge: 是否是BGE模型（决定查询前缀）
        top_k: Recall@K的K值

    Returns:
        {province: {total, hit, recall}, "overall": {...}}
    """
    results_by_province = {}

    for item in items:
        prov = item["province"]
        if prov not in province_indices:
            continue

        names, ids, index_embs = province_indices[prov]
        if index_embs is None or len(names) == 0:
            continue

        # 编码查询
        query_text = item.get("bill_text", item.get("bill_name", ""))
        query_emb = encode_batch(model, [query_text], is_bge=is_bge)[0]

        # 搜索top_k
        top_indices = search_topk(query_emb, index_embs, top_k)
        top_names = [names[i] for i in top_indices]

        # 检查正确答案是否在top_k中
        correct_names = item.get("quota_names", [])
        hit = any(
            any(cn in tn or tn in cn for tn in top_names)
            for cn in correct_names
        )

        if prov not in results_by_province:
            results_by_province[prov] = {"total": 0, "hit": 0}
        results_by_province[prov]["total"] += 1
        results_by_province[prov]["hit"] += 1 if hit else 0

    # 计算各省和总体Recall
    for prov, stats in results_by_province.items():
        stats["recall"] = stats["hit"] / stats["total"] if stats["total"] > 0 else 0

    total_items = sum(s["total"] for s in results_by_province.values())
    total_hits = sum(s["hit"] for s in results_by_province.values())
    results_by_province["overall"] = {
        "total": total_items,
        "hit": total_hits,
        "recall": total_hits / total_items if total_items > 0 else 0,
    }

    return results_by_province


def main():
    parser = argparse.ArgumentParser(description="BGE vs Qwen3 向量搜索评测")
    parser.add_argument("--province", type=str, default=None,
                        help="只评测指定省份（模糊匹配）")
    parser.add_argument("--qwen3-model", type=str, default="models/qwen3-embedding-quota",
                        help="微调后的Qwen3模型路径")
    parser.add_argument("--bge-model", type=str, default="BAAI/bge-large-zh-v1.5",
                        help="BGE模型路径")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Recall@K的K值（默认10）")
    parser.add_argument("--skip-bge", action="store_true",
                        help="跳过BGE评测（只跑Qwen3）")
    args = parser.parse_args()

    # 1. 加载benchmark数据
    print("=" * 60)
    print("BGE vs Qwen3-Embedding 向量搜索评测")
    print("=" * 60)

    items = load_benchmark_papers(args.province)
    print(f"\n试卷: {len(items)} 条")

    if not items:
        print("没有找到试卷数据")
        return

    # 按省份分组
    provinces = sorted(set(item["province"] for item in items))
    print(f"省份: {len(provinces)} 个")

    # 2. 加载Qwen3模型 + 建索引
    print(f"\n{'='*60}")
    print("加载 Qwen3 微调模型")
    print("=" * 60)
    qwen3_model = load_model(args.qwen3_model)

    print("\n为每个省份建立定额向量索引（Qwen3）...")
    qwen3_indices = {}
    for prov in provinces:
        print(f"  [{prov}]")
        names, ids, embs = build_quota_index(qwen3_model, prov, is_bge=False)
        qwen3_indices[prov] = (names, ids, embs)
        print(f"    → {len(names)} 条定额")

    # 3. 评测Qwen3
    print(f"\n评测 Qwen3 Recall@{args.top_k}...")
    t0 = time.time()
    qwen3_results = evaluate_model(qwen3_model, items, qwen3_indices,
                                    is_bge=False, top_k=args.top_k)
    qwen3_time = time.time() - t0

    # 释放Qwen3显存
    del qwen3_model, qwen3_indices
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # 4. 加载BGE模型 + 建索引 + 评测
    bge_results = None
    if not args.skip_bge:
        print(f"\n{'='*60}")
        print("加载 BGE 基线模型")
        print("=" * 60)
        bge_model = load_model(args.bge_model)

        print("\n为每个省份建立定额向量索引（BGE）...")
        bge_indices = {}
        for prov in provinces:
            print(f"  [{prov}]")
            names, ids, embs = build_quota_index(bge_model, prov, is_bge=False)
            bge_indices[prov] = (names, ids, embs)
            print(f"    → {len(names)} 条定额")

        print(f"\n评测 BGE Recall@{args.top_k}...")
        t0 = time.time()
        bge_results = evaluate_model(bge_model, items, bge_indices,
                                      is_bge=True, top_k=args.top_k)
        bge_time = time.time() - t0

        del bge_model, bge_indices
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # 5. 输出对比结果
    print(f"\n{'='*60}")
    print(f"评测结果 Recall@{args.top_k}")
    print("=" * 60)

    # 表头
    if bge_results:
        print(f"\n{'省份':<40} {'BGE':>8} {'Qwen3':>8} {'差值':>8}")
        print("-" * 66)
    else:
        print(f"\n{'省份':<40} {'Qwen3':>8}")
        print("-" * 50)

    # 按省份输出
    for prov in provinces:
        qr = qwen3_results.get(prov, {})
        q_recall = qr.get("recall", 0)
        q_total = qr.get("total", 0)
        q_hit = qr.get("hit", 0)

        if bge_results:
            br = bge_results.get(prov, {})
            b_recall = br.get("recall", 0)
            diff = q_recall - b_recall
            sign = "+" if diff > 0 else ""
            # 标记提升/退化
            marker = " UP" if diff > 0.02 else (" DOWN" if diff < -0.02 else "")
            print(f"{prov:<40} {b_recall:>7.1%} {q_recall:>7.1%} {sign}{diff:>7.1%}{marker}")
        else:
            print(f"{prov:<40} {q_recall:>7.1%}  ({q_hit}/{q_total})")

    # 总体
    print("-" * 66 if bge_results else "-" * 50)
    qo = qwen3_results["overall"]
    if bge_results:
        bo = bge_results["overall"]
        diff = qo["recall"] - bo["recall"]
        sign = "+" if diff > 0 else ""
        print(f"{'总体':<40} {bo['recall']:>7.1%} {qo['recall']:>7.1%} {sign}{diff:>7.1%}")
        print(f"\nBGE:   {bo['hit']}/{bo['total']} 命中")
        print(f"Qwen3: {qo['hit']}/{qo['total']} 命中")
    else:
        print(f"{'总体':<40} {qo['recall']:>7.1%}  ({qo['hit']}/{qo['total']})")

    # 结论
    print(f"\n{'='*60}")
    if bge_results:
        diff = qo["recall"] - bo["recall"]
        if diff > 0.05:
            print(f"[OK] Qwen3 微调效果显著！Recall@{args.top_k} 提升 {diff:.1%}")
            print("   建议：替换搜索管线，上线使用")
        elif diff > 0:
            print(f"[WARN] Qwen3 略有提升 (+{diff:.1%})，但幅度不大")
            print("   建议：考虑增加训练数据或epochs后重训")
        elif diff > -0.02:
            print(f"[WARN] Qwen3 与BGE基本持平 ({diff:+.1%})")
            print("   建议：调参重训（增加epochs/学习率）")
        else:
            print(f"[FAIL] Qwen3 不如BGE ({diff:+.1%})")
            print("   建议：检查训练数据质量，或尝试4B模型")
    else:
        print(f"Qwen3 Recall@{args.top_k}: {qo['recall']:.1%}")
    print("=" * 60)


if __name__ == "__main__":
    main()
