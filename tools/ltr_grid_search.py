# -*- coding: utf-8 -*-
"""
LTR超参网格搜索实验（v4扩展版）

两阶段搜索：
  1. 单维度扫描：固定其他参数，分别搜每个维度最优
  2. 组合网格：用阶段1的Top值做小范围组合

用法:
    python tools/ltr_grid_search.py                # 两阶段完整搜索
    python tools/ltr_grid_search.py --quick        # 只跑阶段1（快速）
    python tools/ltr_grid_search.py --legacy       # 只跑旧版label_gain实验
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from tools.ltr_train import (
    FEATURE_COLUMNS,
    compute_handcraft_score,
    eval_hit_at_1,
    eval_hit_at_1_strict,
    eval_mrr,
    train_lambdarank,
)

# 旧版实验配置（label_gain和objective组合）
LEGACY_EXPERIMENTS = [
    {"name": "基线(无label_gain)", "params": {}},
    {"name": "label_gain=[0,1,3]", "params": {"label_gain": [0, 1, 3]}},
    {"name": "label_gain=[0,1,5]", "params": {"label_gain": [0, 1, 5]}},
    {"name": "label_gain=[0,1,8]", "params": {"label_gain": [0, 1, 8]}},
    {"name": "label_gain=[0,0.5,5]", "params": {"label_gain": [0, 0.5, 5]}},
    {"name": "rank_xendcg", "params": {"objective": "rank_xendcg"}},
    {"name": "xendcg+gain=[0,1,5]", "params": {"objective": "rank_xendcg", "label_gain": [0, 1, 5]}},
]

# 阶段1：单维度扫描网格（固定其他参数为当前最优）
# 当前最优基线: num_leaves=31, lr=0.05, L1=0.1, L2=1.0, rounds=300
BASE_PARAMS = {
    "num_leaves": 31,
    "learning_rate": 0.05,
    "lambda_l1": 0.1,
    "lambda_l2": 1.0,
}
BASE_ROUNDS = 300

SINGLE_DIM_GRID = {
    "num_leaves": [15, 31, 47, 63],
    "learning_rate": [0.01, 0.03, 0.05, 0.1],
    "lambda_l1": [0.0, 0.05, 0.1, 0.3, 0.5],
    "lambda_l2": [0.0, 0.5, 1.0, 3.0, 5.0],
    "min_data_in_leaf": [5, 10, 15, 20],
    "num_boost_round": [200, 300, 400, 500],
}


def run_lopo_experiment(df: pd.DataFrame, extra_params: dict,
                        feature_cols: list[str],
                        num_boost_round: int = 200) -> dict:
    """跑一轮LOPO-CV，返回汇总指标"""
    provinces = df["province"].unique()
    results = []

    for prov in provinces:
        df_val = df[df["province"] == prov].copy()
        df_train = df[df["province"] != prov].copy()

        n_val = df_val["query_id"].nunique()
        if n_val < 5:
            continue

        # 手工公式基线
        df_val["handcraft_score"] = compute_handcraft_score(df_val)
        hit1_hand = eval_hit_at_1(df_val, "handcraft_score")

        # LambdaRank
        model = train_lambdarank(df_train, df_val,
                                 feature_cols=feature_cols,
                                 extra_params=extra_params or None,
                                 num_boost_round=num_boost_round)
        df_val["ltr_score"] = model.predict(df_val[feature_cols].values)
        hit1_ltr = eval_hit_at_1(df_val, "ltr_score")
        hit1_strict = eval_hit_at_1_strict(df_val, "ltr_score")
        mrr_ltr = eval_mrr(df_val, "ltr_score")

        results.append({
            "province": prov[:15],
            "n_queries": n_val,
            "hand_hit1": hit1_hand,
            "ltr_hit1": hit1_ltr,
            "ltr_strict": hit1_strict,
            "ltr_mrr": mrr_ltr,
        })

    if not results:
        return {"hit1": 0, "strict": 0, "mrr": 0, "worst": 0, "details": []}

    # 加权平均
    total_n = sum(r["n_queries"] for r in results)
    avg_hit1 = sum(r["ltr_hit1"] * r["n_queries"] for r in results) / total_n
    avg_strict = sum(r["ltr_strict"] * r["n_queries"] for r in results) / total_n
    avg_mrr = sum(r["ltr_mrr"] * r["n_queries"] for r in results) / total_n
    avg_hand = sum(r["hand_hit1"] * r["n_queries"] for r in results) / total_n
    worst = min(r["ltr_hit1"] for r in results)

    return {
        "hit1": avg_hit1,
        "strict": avg_strict,
        "mrr": avg_mrr,
        "hand": avg_hand,
        "worst": worst,
        "details": results,
    }


def print_results_table(all_results: list[dict]):
    """打印实验结果汇总表"""
    print(f"\n{'='*70}")
    print("网格搜索汇总")
    print(f"{'='*70}")
    print(f"{'配置':<30s} {'Hit@1':>7s} {'严格':>7s} {'MRR':>7s} {'最差省':>7s} {'耗时':>5s}")
    print("-" * 70)

    all_results.sort(key=lambda x: x["hit1"], reverse=True)
    best = all_results[0] if all_results else None

    for r in all_results:
        marker = " ★" if r is best else ""
        print(f"{r['name']:<30s} {r['hit1']:>7.4f} {r['strict']:>7.4f} "
              f"{r['mrr']:>7.4f} {r['worst']:>7.4f} {r['elapsed']:>4.0f}s{marker}")

    if best:
        print(f"\n最优配置: {best['name']}（Hit@1={best['hit1']:.4f}）")
        if "hand" in best and best["hand"] > 0:
            print(f"手工公式基线: Hit@1={best['hand']:.4f}")
            print(f"LTR提升: +{(best['hit1'] - best['hand'])*100:.1f}%")

    return best


def run_single_dim_search(df: pd.DataFrame, feature_cols: list[str]) -> dict:
    """阶段1：单维度扫描，固定其他参数分别搜最优"""
    print(f"\n{'#'*70}")
    print("阶段1：单维度扫描")
    print(f"基线参数: {BASE_PARAMS}, rounds={BASE_ROUNDS}")
    print(f"{'#'*70}")

    # 每个维度的最优值
    best_per_dim = {}

    for dim_name, values in SINGLE_DIM_GRID.items():
        print(f"\n{'='*60}")
        print(f"扫描维度: {dim_name}")
        print(f"{'='*60}")

        dim_results = []
        for val in values:
            # 构建参数
            params = dict(BASE_PARAMS)
            rounds = BASE_ROUNDS

            if dim_name == "num_boost_round":
                rounds = val
                exp_name = f"rounds={val}"
            else:
                params[dim_name] = val
                exp_name = f"{dim_name}={val}"

            start = time.time()
            result = run_lopo_experiment(df, params, feature_cols,
                                         num_boost_round=rounds)
            elapsed = time.time() - start

            result["name"] = exp_name
            result["elapsed"] = elapsed
            result["value"] = val
            dim_results.append(result)

            print(f"  {exp_name:<25s} Hit@1={result['hit1']:.4f}  "
                  f"严格={result['strict']:.4f}  最差={result['worst']:.4f}  "
                  f"{elapsed:.0f}s")

        # 记录本维度最优
        dim_results.sort(key=lambda x: x["hit1"], reverse=True)
        best_val = dim_results[0]["value"]
        best_per_dim[dim_name] = best_val
        print(f"  → {dim_name}最优: {best_val}（Hit@1={dim_results[0]['hit1']:.4f}）")

    print(f"\n{'='*60}")
    print("阶段1汇总：各维度最优值")
    print(f"{'='*60}")
    for dim, val in best_per_dim.items():
        print(f"  {dim}: {val}")

    return best_per_dim


def run_combo_search(df: pd.DataFrame, feature_cols: list[str],
                     best_per_dim: dict) -> list[dict]:
    """阶段2：用阶段1最优值做小范围组合网格"""
    print(f"\n{'#'*70}")
    print("阶段2：组合网格搜索")
    print(f"{'#'*70}")

    # 在每个维度的最优值附近取±1档
    combo_grid = {}
    for dim_name, values in SINGLE_DIM_GRID.items():
        best_val = best_per_dim.get(dim_name, values[len(values)//2])
        if best_val in values:
            idx = values.index(best_val)
            # 取当前最优和相邻值
            nearby = set()
            for offset in [-1, 0, 1]:
                ni = idx + offset
                if 0 <= ni < len(values):
                    nearby.add(values[ni])
            combo_grid[dim_name] = sorted(nearby)
        else:
            combo_grid[dim_name] = [best_val]

    # 只组合关键维度（num_leaves × learning_rate × lambda_l1 × lambda_l2）
    # min_data_in_leaf和num_boost_round用阶段1最优值固定
    key_dims = ["num_leaves", "learning_rate", "lambda_l1", "lambda_l2"]
    fixed_min_data = best_per_dim.get("min_data_in_leaf", 10)
    fixed_rounds = best_per_dim.get("num_boost_round", BASE_ROUNDS)

    # 生成组合
    from itertools import product
    combos = list(product(*[combo_grid.get(d, [BASE_PARAMS.get(d, 0)]) for d in key_dims]))
    print(f"组合数量: {len(combos)}（{' × '.join(str(len(combo_grid.get(d, [1]))) for d in key_dims)}）")
    print(f"固定: min_data_in_leaf={fixed_min_data}, rounds={fixed_rounds}")

    all_results = []
    for i, combo in enumerate(combos):
        params = dict(zip(key_dims, combo))
        params["min_data_in_leaf"] = fixed_min_data

        exp_name = "/".join(f"{k[0]}={v}" for k, v in zip(key_dims, combo))

        start = time.time()
        result = run_lopo_experiment(df, params, feature_cols,
                                     num_boost_round=fixed_rounds)
        elapsed = time.time() - start

        result["name"] = exp_name
        result["elapsed"] = elapsed
        result["params"] = params
        result["rounds"] = fixed_rounds
        all_results.append(result)

        print(f"  [{i+1}/{len(combos)}] {exp_name:<35s} "
              f"Hit@1={result['hit1']:.4f}  严格={result['strict']:.4f}  "
              f"{elapsed:.0f}s")

    return all_results


def main():
    parser = argparse.ArgumentParser(description="LTR超参网格搜索")
    parser.add_argument("--quick", action="store_true",
                        help="只跑阶段1单维度扫描（快速）")
    parser.add_argument("--legacy", action="store_true",
                        help="只跑旧版label_gain实验")
    args = parser.parse_args()

    # 读取训练数据
    df = pd.read_csv("data/ltr_training_data.csv")
    feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    print(f"训练数据: {len(df)}行, {df['query_id'].nunique()}个query, "
          f"{df['province'].nunique()}个省份, {len(feature_cols)}维特征")
    print(f"label分布: {dict(df['label'].value_counts().sort_index())}")

    if args.legacy:
        # 旧版实验
        all_results = []
        for exp in LEGACY_EXPERIMENTS:
            name = exp["name"]
            params = exp["params"]
            print(f"\n{'='*60}")
            print(f"实验: {name}")
            print(f"{'='*60}")

            start = time.time()
            result = run_lopo_experiment(df, params, feature_cols)
            elapsed = time.time() - start
            result["name"] = name
            result["elapsed"] = elapsed
            all_results.append(result)

            print(f"  加权Hit@1={result['hit1']:.4f}  严格={result['strict']:.4f}")

        print_results_table(all_results)
        return

    # 阶段1：单维度扫描
    best_per_dim = run_single_dim_search(df, feature_cols)

    if args.quick:
        print("\n[--quick模式] 跳过阶段2组合搜索")
        return

    # 阶段2：组合网格
    combo_results = run_combo_search(df, feature_cols, best_per_dim)
    best = print_results_table(combo_results)

    if best and "params" in best:
        print(f"\n{'='*60}")
        print("推荐训练命令:")
        p = best["params"]
        rounds = best.get("rounds", BASE_ROUNDS)
        cmd = (f"python tools/ltr_train.py --no-cv "
               f"--num-leaves {p.get('num_leaves', 31)} "
               f"--num-round {rounds} "
               f"--lambda-l1 {p.get('lambda_l1', 0.1)} "
               f"--lambda-l2 {p.get('lambda_l2', 1.0)}")
        print(f"  {cmd}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
