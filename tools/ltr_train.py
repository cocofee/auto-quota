# -*- coding: utf-8 -*-
"""
LTR模型训练器（坐标上升 + LightGBM LambdaRank）

读取 ltr_prepare_data.py 生成的训练数据，训练排序模型，
输出3方对比表（手工公式 vs 坐标上升 vs LambdaRank）。

用法:
    python tools/ltr_train.py                    # 训练+交叉验证
    python tools/ltr_train.py --no-cv            # 跳过交叉验证，直接全量训练
    python tools/ltr_train.py --input data/ltr_training_data.csv  # 指定输入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

# 特征列（和 ltr_prepare_data.py 一致）
# v2: 21维（原16维+5参数距离）
# v3: 23维（+book_match, token_overlap）
FEATURE_COLUMNS_V2 = [
    "bm25_score", "vector_score", "hybrid_score", "rerank_score",
    "param_score", "param_match",
    "param_tier_0", "param_tier_1", "param_tier_2",
    "name_bonus", "candidates_count",
    "bm25_rank_score", "vector_rank_score",
    "name_edit_dist", "score_gap_to_top1", "dual_recall",
    "param_main_exact", "param_main_rel_dist", "param_main_direction",
    "param_material_match", "param_n_checks",
]
# v3新增特征
FEATURE_COLUMNS_V3_NEW = [
    "book_match",       # 22. 册号匹配度(1=主专业/0.5=借用/0=不匹配)
    "token_overlap",    # 23. 同义词归一化后词级Jaccard重叠度
]
# v4新增特征：同族排名+幅度（Codex 5.4审核确认）
FEATURE_COLUMNS_V4_NEW = [
    "param_tier_rank",       # 24. 同tier内参数距离排名(0=最近, 1=最远)
    "family_size",           # 25. log1p(同tier候选数)/log1p(20)
    "param_score_rank",      # 26. 同tier内param_score排名(0=最高, 1=最低)
    "rerank_within_tier",    # 27. 同tier内rerank_score排名(0=最高, 1=最低)
    "dist_to_tier_best",     # 28. 与同tier最优param_score的差距[0,1]
]
FEATURE_COLUMNS = FEATURE_COLUMNS_V2 + FEATURE_COLUMNS_V3_NEW + FEATURE_COLUMNS_V4_NEW


def eval_hit_at_1(df: pd.DataFrame, score_col: str) -> float:
    """计算Hit@1：top1候选的label>=1的比例"""
    top1 = (df.sort_values(["query_id", score_col], ascending=[True, False])
              .groupby("query_id").head(1))
    return (top1["label"] >= 1).mean()


def eval_hit_at_1_strict(df: pd.DataFrame, score_col: str) -> float:
    """严格Hit@1：top1候选的label==2的比例"""
    top1 = (df.sort_values(["query_id", score_col], ascending=[True, False])
              .groupby("query_id").head(1))
    return (top1["label"] == 2).mean()


def eval_mrr(df: pd.DataFrame, score_col: str) -> float:
    """计算MRR（Mean Reciprocal Rank）"""
    ranked = (df.sort_values(["query_id", score_col], ascending=[True, False])
                .copy())
    ranked["rank"] = ranked.groupby("query_id").cumcount() + 1
    # 每个query找第一个label>=1的rank
    positive = ranked[ranked["label"] >= 1]
    first_hit = positive.groupby("query_id")["rank"].min()
    # 没有命中的query贡献0
    all_queries = df["query_id"].unique()
    rr = pd.Series(0.0, index=all_queries)
    rr.loc[first_hit.index] = 1.0 / first_hit
    return rr.mean()


def compute_handcraft_score(df: pd.DataFrame) -> pd.Series:
    """用现有手工公式计算排序分（和param_validator一致）"""
    # 有参数分支：ps*0.55 + nb*0.30 + rr*0.15
    # 无参数分支：ps*0.20 + nb*0.55 + rr*0.25
    # 这里统一用有参数分支（简化，因为训练数据包含两种情况）
    ps = df["param_score"]
    nb = df["name_bonus"]
    rr = df["rerank_score"]
    # 加param_tier作为第一排序键（tier*10保证硬分层）
    tier = df["param_tier_0"] * 0 + df["param_tier_1"] * 1 + df["param_tier_2"] * 2
    return tier * 10 + ps * 0.55 + nb * 0.30 + rr * 0.15


# ============================================================
# 坐标上升：学习线性最优权重
# ============================================================

def coordinate_ascent(df: pd.DataFrame, feats: list[str],
                      iters: int = 30) -> tuple[np.ndarray, float]:
    """
    坐标上升法：每次调一个特征的权重，直接优化Hit@1。

    返回: (最优权重向量, 最优Hit@1)
    """
    n = len(feats)
    w = np.ones(n) / n
    X = df[feats].to_numpy()

    def _eval(weights):
        s = X @ weights
        df_tmp = df[["query_id", "label"]].copy()
        df_tmp["_score"] = s
        top1 = (df_tmp.sort_values(["query_id", "_score"], ascending=[True, False])
                      .groupby("query_id").head(1))
        return (top1["label"] >= 1).mean()

    best = _eval(w)
    print(f"  坐标上升初始Hit@1: {best:.4f}")

    for iteration in range(iters):
        improved = False
        for j in range(n):
            cur_best_w, cur_best = w.copy(), best
            for delta in np.linspace(-0.3, 0.3, 13):
                cand = w.copy()
                cand[j] = max(0.0, cand[j] + delta)
                total = cand.sum()
                if total <= 0:
                    continue
                cand = cand / total
                v = _eval(cand)
                if v > cur_best:
                    cur_best, cur_best_w = v, cand
            if cur_best > best:
                w, best, improved = cur_best_w, cur_best, True
        if not improved:
            print(f"  坐标上升在第{iteration+1}轮收敛")
            break

    return w, best


# ============================================================
# LightGBM LambdaRank
# ============================================================

def train_lambdarank(df_train: pd.DataFrame, df_val: pd.DataFrame = None,
                     feature_cols: list[str] = None,
                     extra_params: dict = None,
                     num_boost_round: int = 200,
                     ) -> "lightgbm.Booster":
    """训练LightGBM LambdaRank模型

    Args:
        feature_cols: 特征列名列表（默认用全局FEATURE_COLUMNS，自动过滤CSV中不存在的列）
        extra_params: 额外LightGBM参数（覆盖默认值，如label_gain/objective等）
        num_boost_round: 最大迭代次数
    """
    import lightgbm as lgb

    if feature_cols is None:
        # 自动检测CSV中存在的特征列（兼容v2/v3训练数据）
        available = [c for c in FEATURE_COLUMNS if c in df_train.columns]
        feature_cols = available

    X_train = df_train[feature_cols].values
    y_train = df_train["label"].values
    groups_train = df_train.groupby("query_id").size().values

    train_data = lgb.Dataset(X_train, label=y_train, group=groups_train,
                             feature_name=feature_cols)

    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 3],
        "num_leaves": 15,           # 保守，防过拟合（Codex建议）
        "min_data_in_leaf": 10,     # 保守（Codex建议）
        "learning_rate": 0.05,
        "seed": 42,                 # 固定seed，结果可复现
        "verbose": -1,
    }
    # 外部参数覆盖（用于label_gain/objective/超参网格搜索）
    if extra_params:
        params.update(extra_params)

    callbacks = [lgb.log_evaluation(period=50)]
    valid_sets = [train_data]
    valid_names = ["train"]

    if df_val is not None and len(df_val) > 0:
        X_val = df_val[feature_cols].values
        y_val = df_val["label"].values
        groups_val = df_val.groupby("query_id").size().values
        if len(groups_val) > 0:
            val_data = lgb.Dataset(X_val, label=y_val, group=groups_val,
                                  feature_name=feature_cols, reference=train_data)
            valid_sets.append(val_data)
            valid_names.append("valid")
            callbacks.append(lgb.early_stopping(stopping_rounds=20, verbose=True))

    model = lgb.train(
        params,
        train_data,
        num_boost_round=num_boost_round,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks,
    )

    return model


def print_feature_importance(model, top_n: int = 16):
    """打印特征重要度"""
    importance = model.feature_importance(importance_type="gain")
    feature_names = model.feature_name()
    pairs = sorted(zip(feature_names, importance), key=lambda x: x[1], reverse=True)
    print("\n特征重要度（gain）:")
    for name, imp in pairs[:top_n]:
        bar = "█" * int(imp / max(importance) * 30)
        print(f"  {name:<25s} {imp:>8.1f}  {bar}")


# ============================================================
# 省级交叉验证（LOPO: Leave-One-Province-Out）
# ============================================================

def run_lopo_cv(df: pd.DataFrame, extra_params: dict = None,
                num_boost_round: int = 200):
    """按省份做留一法交叉验证，对比3种方法

    Args:
        extra_params: 额外LightGBM参数（覆盖默认值）
        num_boost_round: 最大迭代次数
    """
    # 自动检测CSV中存在的特征列（兼容v2/v3训练数据）
    feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    provinces = df["province"].unique()
    print(f"\n{'='*60}")
    print(f"省级交叉验证（LOPO, {len(provinces)}省, {len(feature_cols)}维特征）")
    if extra_params:
        print(f"额外参数: {extra_params}")
    print(f"{'='*60}")

    results = []

    for prov in provinces:
        df_val = df[df["province"] == prov].copy()
        df_train = df[df["province"] != prov].copy()

        n_val_queries = df_val["query_id"].nunique()
        if n_val_queries < 5:
            print(f"\n跳过 {prov}（只有{n_val_queries}条query）")
            continue

        print(f"\n验证省: {prov}（{n_val_queries}条query, "
              f"训练{df_train['query_id'].nunique()}条query）")

        # 1. 手工公式
        df_val["handcraft_score"] = compute_handcraft_score(df_val)
        hit1_hand = eval_hit_at_1(df_val, "handcraft_score")
        hit1_hand_strict = eval_hit_at_1_strict(df_val, "handcraft_score")

        # 2. 坐标上升
        w, _ = coordinate_ascent(df_train, feature_cols)
        X_val = df_val[feature_cols].to_numpy()
        df_val["coord_score"] = X_val @ w
        hit1_coord = eval_hit_at_1(df_val, "coord_score")
        hit1_coord_strict = eval_hit_at_1_strict(df_val, "coord_score")

        # 3. LambdaRank
        model = train_lambdarank(df_train, df_val,
                                 feature_cols=feature_cols,
                                 extra_params=extra_params,
                                 num_boost_round=num_boost_round)
        df_val["ltr_score"] = model.predict(df_val[feature_cols].values)
        hit1_ltr = eval_hit_at_1(df_val, "ltr_score")
        hit1_ltr_strict = eval_hit_at_1_strict(df_val, "ltr_score")

        results.append({
            "province": prov[:15],
            "n_queries": n_val_queries,
            "hand_hit1": hit1_hand,
            "hand_strict": hit1_hand_strict,
            "coord_hit1": hit1_coord,
            "coord_strict": hit1_coord_strict,
            "ltr_hit1": hit1_ltr,
            "ltr_strict": hit1_ltr_strict,
        })

        print(f"  手工公式  Hit@1={hit1_hand:.3f}  严格={hit1_hand_strict:.3f}")
        print(f"  坐标上升  Hit@1={hit1_coord:.3f}  严格={hit1_coord_strict:.3f}")
        print(f"  LambdaRank Hit@1={hit1_ltr:.3f}  严格={hit1_ltr_strict:.3f}")

    # 汇总表
    if results:
        print(f"\n{'='*60}")
        print("3方对比汇总")
        print(f"{'='*60}")
        print(f"{'省份':<18s} {'N':>4s}  {'手工':>6s} {'坐标↑':>6s} {'LTR':>6s}  "
              f"{'手工严':>6s} {'坐标严':>6s} {'LTR严':>6s}")
        print("-" * 76)
        for r in results:
            print(f"{r['province']:<18s} {r['n_queries']:>4d}  "
                  f"{r['hand_hit1']:>6.3f} {r['coord_hit1']:>6.3f} {r['ltr_hit1']:>6.3f}  "
                  f"{r['hand_strict']:>6.3f} {r['coord_strict']:>6.3f} {r['ltr_strict']:>6.3f}")

        # 加权平均
        total_n = sum(r["n_queries"] for r in results)
        avg_hand = sum(r["hand_hit1"] * r["n_queries"] for r in results) / total_n
        avg_coord = sum(r["coord_hit1"] * r["n_queries"] for r in results) / total_n
        avg_ltr = sum(r["ltr_hit1"] * r["n_queries"] for r in results) / total_n
        avg_hand_s = sum(r["hand_strict"] * r["n_queries"] for r in results) / total_n
        avg_coord_s = sum(r["coord_strict"] * r["n_queries"] for r in results) / total_n
        avg_ltr_s = sum(r["ltr_strict"] * r["n_queries"] for r in results) / total_n
        print("-" * 76)
        print(f"{'加权平均':<18s} {total_n:>4d}  "
              f"{avg_hand:>6.3f} {avg_coord:>6.3f} {avg_ltr:>6.3f}  "
              f"{avg_hand_s:>6.3f} {avg_coord_s:>6.3f} {avg_ltr_s:>6.3f}")

        # 最差省份
        worst_hand = min(r["hand_hit1"] for r in results)
        worst_ltr = min(r["ltr_hit1"] for r in results)
        print(f"\n最差省份: 手工={worst_hand:.3f}  LTR={worst_ltr:.3f}")

    return results


# ============================================================
# 全量训练 + 保存模型
# ============================================================

def train_final_model(df: pd.DataFrame, output_path: str = "data/ltr_model.txt",
                      extra_params: dict = None, num_boost_round: int = 200):
    """全量训练最终模型并保存"""
    # 自动检测CSV中存在的特征列
    feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    print(f"\n{'='*60}")
    print(f"全量训练最终模型（{len(feature_cols)}维特征）")
    if extra_params:
        print(f"额外参数: {extra_params}")
    print(f"{'='*60}")

    # 全量训练（不留验证集）
    model = train_lambdarank(df, feature_cols=feature_cols,
                             extra_params=extra_params,
                             num_boost_round=num_boost_round)

    # 保存模型
    model.save_model(output_path)
    print(f"模型已保存: {output_path}")

    # 特征重要度
    print_feature_importance(model)

    # 全量评估
    df["ltr_score"] = model.predict(df[feature_cols].values)
    df["handcraft_score"] = compute_handcraft_score(df)

    hit1_hand = eval_hit_at_1(df, "handcraft_score")
    hit1_ltr = eval_hit_at_1(df, "ltr_score")
    mrr_hand = eval_mrr(df, "handcraft_score")
    mrr_ltr = eval_mrr(df, "ltr_score")

    print(f"\n全量评估（训练集，参考值）:")
    print(f"  手工公式  Hit@1={hit1_hand:.4f}  MRR={mrr_hand:.4f}")
    print(f"  LambdaRank Hit@1={hit1_ltr:.4f}  MRR={mrr_ltr:.4f}")

    # 坐标上升权重
    print(f"\n坐标上升最优权重:")
    w, best = coordinate_ascent(df, feature_cols)
    for feat, weight in zip(feature_cols, w):
        if weight > 0.01:
            print(f"  {feat:<25s} {weight:.4f}")
    print(f"  Hit@1 = {best:.4f}")

    return model


def main():
    parser = argparse.ArgumentParser(description="LTR模型训练器")
    parser.add_argument("--input", type=str, default="data/ltr_training_data.csv",
                        help="训练数据CSV路径")
    parser.add_argument("--output", type=str, default="data/ltr_model.txt",
                        help="模型输出路径")
    parser.add_argument("--no-cv", action="store_true",
                        help="跳过交叉验证，直接全量训练")
    # 第1步实验：label_gain网格搜索
    parser.add_argument("--label-gain", type=str, default=None,
                        help="label_gain参数，如 '0,1,5'（逗号分隔）")
    # 第1步实验：目标函数切换
    parser.add_argument("--objective", type=str, default=None,
                        choices=["lambdarank", "rank_xendcg"],
                        help="目标函数（默认lambdarank，可选rank_xendcg）")
    # 第3步实验：超参优化
    parser.add_argument("--num-leaves", type=int, default=None,
                        help="叶子数（默认15，可调31/63）")
    parser.add_argument("--num-round", type=int, default=200,
                        help="最大迭代次数（默认200）")
    parser.add_argument("--lambda-l1", type=float, default=None,
                        help="L1正则化系数")
    parser.add_argument("--lambda-l2", type=float, default=None,
                        help="L2正则化系数")
    args = parser.parse_args()

    # 构建extra_params（用户指定的参数覆盖默认值）
    extra_params = {}
    if args.label_gain:
        gains = [float(x) for x in args.label_gain.split(",")]
        extra_params["label_gain"] = gains
    if args.objective:
        extra_params["objective"] = args.objective
    if args.num_leaves:
        extra_params["num_leaves"] = args.num_leaves
    if args.lambda_l1 is not None:
        extra_params["lambda_l1"] = args.lambda_l1
    if args.lambda_l2 is not None:
        extra_params["lambda_l2"] = args.lambda_l2
    extra_params = extra_params or None  # 没有额外参数时传None

    # 读取训练数据
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"训练数据不存在: {input_path}")
        print("请先运行: python tools/ltr_prepare_data.py")
        return

    df = pd.read_csv(input_path)
    print(f"加载训练数据: {len(df)}行, {df['query_id'].nunique()}个query, "
          f"{df['province'].nunique()}个省份")

    # 交叉验证
    if not args.no_cv:
        run_lopo_cv(df, extra_params=extra_params,
                    num_boost_round=args.num_round)

    # 全量训练
    train_final_model(df, args.output, extra_params=extra_params,
                      num_boost_round=args.num_round)

    print(f"\n{'='*60}")
    print("训练完毕！")
    print(f"模型文件: {args.output}")
    print(f"下一步: 运行 python -m pytest tests/ -q 然后 python tools/run_benchmark.py --compare")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
