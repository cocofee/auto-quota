# -*- coding: utf-8 -*-
"""
LTR训练数据生成器

跑 benchmark 2174条试卷，每条清单产生约20个候选，
提取21维特征 + 分级标注(0/1/2)，输出CSV供LightGBM训练。

v1: 16维（原始语义+参数聚合特征）
v2: 21维（+5个参数距离特征，解决57%的"选错档位"问题）

用法:
    python tools/ltr_prepare_data.py                  # 全量生成
    python tools/ltr_prepare_data.py --province 北京   # 只跑一个省（调试）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, ".")

# 21维特征列名（v2: 原16维 + 5个参数距离特征）
FEATURE_COLUMNS = [
    "bm25_score",          # 1. BM25文本匹配分
    "vector_score",        # 2. 向量语义相似度
    "hybrid_score",        # 3. 混合搜索分
    "rerank_score",        # 4. 交叉编码器精排分
    "param_score",         # 5. 参数匹配得分(0~1)
    "param_match",         # 6. 参数是否匹配(0/1)
    "param_tier_0",        # 7. one-hot: 硬失败
    "param_tier_1",        # 8. one-hot: 部分匹配
    "param_tier_2",        # 9. one-hot: 精确匹配
    "name_bonus",          # 10. 品类核心词匹配度
    "candidates_count",    # 11. 有效候选数量
    "bm25_rank_score",     # 12. 1-归一化排名（越大越好）
    "vector_rank_score",   # 13. 1-归一化排名（越大越好）
    "name_edit_dist",      # 14. 清单名称vs候选名称编辑距离（归一化）
    "score_gap_to_top1",   # 15. 与top1的composite分差
    "dual_recall",         # 16. 是否同时被BM25和向量召回
    # v2新增：参数距离特征（让模型学会"参数精确匹配比语义相似更重要"）
    "param_main_exact",    # 17. 主参数(DN/截面等)是否精确匹配(0/1)
    "param_main_rel_dist", # 18. 主参数相对距离(0=精确, 1=最远)
    "param_main_direction",# 19. 向上取(+1)/向下取(-1)/精确(0)
    "param_material_match",# 20. 材质匹配度(1.0精确/0.7兼容/0.0冲突/-1无信息)
    "param_n_checks",      # 21. 参数检查项数(越多越可信)
]

# CSV列：query_id + province + 特征 + label
CSV_COLUMNS = ["query_id", "province"] + FEATURE_COLUMNS + ["label"]


def load_benchmark_papers(province_filter: str = None) -> dict:
    """加载JSON试卷"""
    papers_dir = Path("tests/benchmark_papers")
    papers = {}
    for f in sorted(papers_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
        prov = data.get("province", f.stem)
        if province_filter and province_filter not in prov:
            continue
        papers[prov] = data
    return papers


def compute_name_edit_dist(bill_name: str, candidate_name: str) -> float:
    """清单名称与候选名称的归一化编辑距离（0=完全不同, 1=完全相同）"""
    if not bill_name or not candidate_name:
        return 0.0
    return SequenceMatcher(None, bill_name, candidate_name).ratio()


def extract_features(candidate: dict, bill_name: str,
                     n_candidates: int, top1_composite: float,
                     bm25_ranks: dict, vector_ranks: dict,
                     bm25_ids: set, vector_ids: set) -> dict:
    """从单个候选提取21维特征"""
    qid = str(candidate.get("quota_id", ""))
    tier = candidate.get("param_tier", 1)

    # 当前候选的composite分（和param_validator排序公式一致）
    ps = candidate.get("param_score", 0)
    nb = candidate.get("name_bonus", 0)
    rr = candidate.get("rerank_score", candidate.get("hybrid_score", 0))
    composite = ps * 0.55 + nb * 0.30 + rr * 0.15

    # v2新增：从candidate["_ltr_param"]读取参数距离特征
    ltr_param = candidate.get("_ltr_param", {})

    return {
        "bm25_score": candidate.get("bm25_score") or 0,
        "vector_score": candidate.get("vector_score") or 0,
        "hybrid_score": candidate.get("hybrid_score") or 0,
        "rerank_score": candidate.get("rerank_score") or 0,
        "param_score": ps,
        "param_match": 1 if candidate.get("param_match", True) else 0,
        "param_tier_0": 1 if tier == 0 else 0,
        "param_tier_1": 1 if tier == 1 else 0,
        "param_tier_2": 1 if tier == 2 else 0,
        "name_bonus": nb,
        "candidates_count": n_candidates,
        "bm25_rank_score": 1.0 - bm25_ranks.get(qid, 1.0),
        "vector_rank_score": 1.0 - vector_ranks.get(qid, 1.0),
        "name_edit_dist": compute_name_edit_dist(bill_name, candidate.get("name", "")),
        "score_gap_to_top1": top1_composite - composite,
        "dual_recall": 1 if (qid in bm25_ids and qid in vector_ids) else 0,
        # v2新增：参数距离特征
        "param_main_exact": ltr_param.get("param_main_exact", 0),
        "param_main_rel_dist": ltr_param.get("param_main_rel_dist", 1.0),
        "param_main_direction": ltr_param.get("param_main_direction", 0),
        "param_material_match": ltr_param.get("param_material_match", -1.0),
        "param_n_checks": ltr_param.get("param_n_checks", 0),
    }


def label_candidate(candidate: dict, correct_ids: list[str],
                    correct_names: list[str]) -> int:
    """
    标注候选的相关性等级:
      2 = quota_id在标准答案中（完全正确）
      1 = 名称高度相似(>0.8)但ID不同（可接受替代）
      0 = 其余
    """
    qid = str(candidate.get("quota_id", "")).strip()
    if qid and qid in correct_ids:
        return 2

    # 检查名称相似度
    cand_name = candidate.get("name", "")
    for cn in correct_names:
        if cn and cand_name:
            sim = SequenceMatcher(None, cn, cand_name).ratio()
            if sim > 0.8:
                return 1

    return 0


def build_rank_maps(candidates: list[dict]) -> tuple[dict, dict, set, set]:
    """构建BM25和向量搜索的归一化排名映射 + 召回集合"""
    # 按bm25_score排序获取排名
    bm25_sorted = sorted(candidates,
                         key=lambda x: x.get("bm25_score") or 0, reverse=True)
    vector_sorted = sorted(candidates,
                           key=lambda x: x.get("vector_score") or 0, reverse=True)

    n = max(len(candidates), 1)
    bm25_ranks = {}
    vector_ranks = {}
    bm25_ids = set()
    vector_ids = set()

    for i, c in enumerate(bm25_sorted):
        qid = str(c.get("quota_id", ""))
        bm25_ranks[qid] = i / n  # 归一化到0~1
        if (c.get("bm25_score") or 0) > 0:
            bm25_ids.add(qid)

    for i, c in enumerate(vector_sorted):
        qid = str(c.get("quota_id", ""))
        vector_ranks[qid] = i / n
        if (c.get("vector_score") or 0) > 0:
            vector_ids.add(qid)

    return bm25_ranks, vector_ranks, bm25_ids, vector_ids


def process_province(province: str, items: list[dict],
                     writer, query_counter: list[int]) -> dict:
    """处理一个省份的所有试题，写入CSV"""
    from src.match_engine import init_search_components
    from src.match_core import cascade_search
    from src.text_parser import parser as text_parser_inst
    from src.specialty_classifier import classify

    # 初始化搜索引擎
    searcher, validator = init_search_components(resolved_province=province)
    from src.reranker import Reranker
    reranker = Reranker()

    stats = {"total": 0, "has_positive": 0, "candidates": 0, "skipped": 0}

    for item in items:
        bill_name = item["bill_name"]
        bill_text = item["bill_text"]
        correct_ids = [str(x).strip() for x in item.get("quota_ids", []) if x]
        correct_names = item.get("quota_names", [])

        if not bill_name or not correct_ids:
            stats["skipped"] += 1
            continue

        # 构建查询（和match_pipeline._build_item_context一致）
        full_query = f"{bill_name} {bill_text}".strip()
        search_query = text_parser_inst.build_quota_query(
            bill_name, bill_text,
            specialty=item.get("specialty", ""))

        # 分类
        try:
            classification = classify(bill_name, bill_text,
                                      specialty=item.get("specialty", ""))
        except Exception:
            classification = {"specialty": item.get("specialty", ""),
                              "search_books": []}

        # 级联搜索
        try:
            candidates = cascade_search(searcher, search_query, classification)
        except Exception as e:
            print(f"  搜索跳过 {bill_name}: {e}")
            stats["skipped"] += 1
            continue

        if not candidates:
            stats["skipped"] += 1
            continue

        # 去重（和_prepare_candidates一致）
        seen_ids = {}
        for c in candidates:
            qid = c.get("quota_id", "")
            if not qid:
                seen_ids[f"_no_id_{len(seen_ids)}"] = c
                continue
            dedup_key = (qid, c.get("_source_province", ""))
            existing = seen_ids.get(dedup_key)
            if existing is None or c.get("hybrid_score", 0) > existing.get("hybrid_score", 0):
                seen_ids[dedup_key] = c
        candidates = list(seen_ids.values())
        candidates.sort(key=lambda x: x.get("hybrid_score", 0), reverse=True)

        # Reranker重排
        if len(candidates) > 1:
            candidates = reranker.rerank(search_query, candidates)

        # 参数验证（会添加param_score/name_bonus/param_tier等）
        candidates = validator.validate_candidates(
            full_query, candidates, supplement_query=search_query)

        if not candidates:
            stats["skipped"] += 1
            continue

        # 构建排名映射
        bm25_ranks, vector_ranks, bm25_ids, vector_ids = build_rank_maps(candidates)

        # 计算top1的composite分（用于score_gap_to_top1）
        top1 = candidates[0]
        top1_composite = (
            top1.get("param_score", 0) * 0.55
            + top1.get("name_bonus", 0) * 0.30
            + top1.get("rerank_score", top1.get("hybrid_score", 0)) * 0.15
        )

        n_candidates = len(candidates)
        query_id = query_counter[0]

        # 标注每个候选
        has_positive = False
        rows = []
        for c in candidates:
            label = label_candidate(c, correct_ids, correct_names)
            if label >= 1:
                has_positive = True
            features = extract_features(
                c, bill_name, n_candidates, top1_composite,
                bm25_ranks, vector_ranks, bm25_ids, vector_ids)
            row = [query_id, province]
            row.extend(features[col] for col in FEATURE_COLUMNS)
            row.append(label)
            rows.append(row)

        # 无正样本的query跳过（Codex建议）
        if not has_positive:
            stats["skipped"] += 1
            continue

        # 写入CSV
        for row in rows:
            writer.writerow(row)

        query_counter[0] += 1
        stats["total"] += 1
        stats["has_positive"] += 1
        stats["candidates"] += n_candidates

    return stats


def main():
    parser = argparse.ArgumentParser(description="LTR训练数据生成器")
    parser.add_argument("--province", type=str, default=None,
                        help="只处理指定省份（调试用）")
    parser.add_argument("--output", type=str, default="data/ltr_training_data.csv",
                        help="输出CSV路径")
    args = parser.parse_args()

    print("=" * 60)
    print("LTR训练数据生成器")
    print("=" * 60)

    # 加载试卷
    papers = load_benchmark_papers(args.province)
    if not papers:
        print("未找到试卷！请检查 tests/benchmark_papers/ 目录")
        return

    total_items = sum(len(p["items"]) for p in papers.values())
    print(f"加载 {len(papers)} 个省份，共 {total_items} 条试题")

    # 打开CSV写入
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    query_counter = [0]  # 用列表模拟可变整数

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)

        for prov, paper in papers.items():
            items = paper["items"]
            print(f"\n处理 {prov}（{len(items)}条）...")
            prov_start = time.time()
            stats = process_province(prov, items, writer, query_counter)
            elapsed = time.time() - prov_start
            print(f"  完成: {stats['total']}条有效, "
                  f"{stats['skipped']}条跳过, "
                  f"{stats['candidates']}个候选, "
                  f"耗时{elapsed:.1f}秒")

    total_elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"生成完毕: {output_path}")
    print(f"总query数: {query_counter[0]}")
    print(f"总耗时: {total_elapsed:.1f}秒")
    print(f"{'=' * 60}")

    # 打印label分布
    import pandas as pd
    df = pd.read_csv(output_path)
    print(f"\n数据统计:")
    print(f"  总行数: {len(df)}")
    print(f"  总query数: {df['query_id'].nunique()}")
    print(f"  label分布:")
    for label, count in df["label"].value_counts().sort_index().items():
        pct = count / len(df) * 100
        print(f"    label={label}: {count} ({pct:.1f}%)")
    print(f"  省份分布:")
    for prov, count in df["province"].value_counts().items():
        queries = df[df["province"] == prov]["query_id"].nunique()
        print(f"    {prov}: {count}行, {queries}个query")


if __name__ == "__main__":
    main()
