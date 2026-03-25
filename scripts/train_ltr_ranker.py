from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _group_split(df: pd.DataFrame, valid_ratio: float = 0.2):
    groups = list(dict.fromkeys(df["group_id"].tolist()))
    split = max(1, int(len(groups) * (1.0 - valid_ratio)))
    train_groups = set(groups[:split])
    return df[df["group_id"].isin(train_groups)].copy(), df[~df["group_id"].isin(train_groups)].copy()


def _metrics(df: pd.DataFrame, score_col: str) -> dict[str, float]:
    ranked = df.sort_values(["group_id", score_col], ascending=[True, False]).copy()
    ranked["rank"] = ranked.groupby("group_id").cumcount() + 1
    top1 = ranked.groupby("group_id").head(1)
    in_pool_groups = ranked.groupby("group_id")["oracle_in_candidates"].max()
    in_pool_top1 = top1.merge(in_pool_groups.rename("group_in_pool"), left_on="group_id", right_index=True)
    positive = ranked[ranked["label"] > 0]
    first_positive = positive.groupby("group_id")["rank"].min()
    rr = first_positive.rpow(-1).replace([np.inf, -np.inf], 0.0)
    ndcg1 = float((top1["label"] > 0).mean()) if len(top1) else 0.0
    return {
        "top1_acc": float((top1["label"] > 0).mean()) if len(top1) else 0.0,
        "in_pool_top1_acc": float(
            (in_pool_top1[in_pool_top1["group_in_pool"] > 0]["label"] > 0).mean()
        ) if len(in_pool_top1[in_pool_top1["group_in_pool"] > 0]) else 0.0,
        "mrr": float(rr.mean()) if len(rr) else 0.0,
        "ndcg@1": ndcg1,
    }


def _bucket_metrics(df: pd.DataFrame, score_col: str) -> dict[str, dict[str, float]]:
    bucket_defs = {
        "small_score_gap": df["group_ambiguity_score"] >= 0.85,
        "synonym_gap": df["cause"] == "synonym_gap",
        "wrong_tier": df["cause"] == "wrong_tier",
        "high_confidence": df["confidence"].astype(float) >= 85,
    }
    output = {}
    for name, mask in bucket_defs.items():
        subset = df[mask].copy()
        if subset.empty:
            output[name] = {"before": 0.0, "after": 0.0, "groups": 0}
            continue
        output[name] = {
            "before": _metrics(subset, "manual_structured_score")["top1_acc"],
            "after": _metrics(subset, score_col)["top1_acc"],
            "groups": int(subset["group_id"].nunique()),
        }
    return output


def train(dataset_path: Path, output_dir: Path):
    import lightgbm as lgb

    df = pd.read_csv(dataset_path)
    df = df[df["oracle_in_candidates"] > 0].copy()
    if df.empty:
        raise RuntimeError("no oracle_in_candidates=true groups found")
    for column in df.columns:
        if df[column].dtype == object and column not in {"group_id", "quota_id", "province", "cause", "miss_stage", "final_changed_by"}:
            df[column] = pd.to_numeric(df[column], errors="ignore")
    feature_cols = [
        column for column in df.columns
        if column not in {
            "group_id", "province", "quota_id", "label", "oracle_in_candidates",
            "cause", "miss_stage", "confidence", "final_changed_by",
            "pre_ltr_top1_id", "post_ltr_top1_id", "post_arbiter_top1_id", "post_final_top1_id",
        }
        and pd.api.types.is_numeric_dtype(df[column])
    ]
    train_df, valid_df = _group_split(df)
    train_data = lgb.Dataset(
        train_df[feature_cols].to_numpy(),
        label=train_df["label"].to_numpy(),
        group=train_df.groupby("group_id").size().to_numpy(),
        feature_name=feature_cols,
    )
    valid_sets = [train_data]
    valid_names = ["train"]
    callbacks = [lgb.log_evaluation(period=25)]
    if not valid_df.empty:
        valid_data = lgb.Dataset(
            valid_df[feature_cols].to_numpy(),
            label=valid_df["label"].to_numpy(),
            group=valid_df.groupby("group_id").size().to_numpy(),
            feature_name=feature_cols,
            reference=train_data,
        )
        valid_sets.append(valid_data)
        valid_names.append("valid")
        callbacks.append(lgb.early_stopping(25, verbose=True))
    model = lgb.train(
        {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [1, 3, 5],
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_data_in_leaf": 20,
            "seed": 42,
            "verbose": -1,
        },
        train_data,
        num_boost_round=300,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks,
    )
    score_col = "ltr_score"
    valid_eval = valid_df.copy()
    if not valid_eval.empty:
        valid_eval[score_col] = model.predict(valid_eval[feature_cols].to_numpy())
    else:
        valid_eval = train_df.copy()
        valid_eval[score_col] = model.predict(valid_eval[feature_cols].to_numpy())
    metrics = _metrics(valid_eval, score_col)
    metrics["bucket_stats"] = _bucket_metrics(valid_eval, score_col)
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save_model(str(output_dir / "ltr_v2_model.txt"))
    (output_dir / "ltr_v2_features.json").write_text(
        json.dumps({"feature_names": feature_cols}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    importance = sorted(
        zip(feature_cols, model.feature_importance(importance_type="gain")),
        key=lambda pair: pair[1],
        reverse=True,
    )
    (output_dir / "ltr_v2_feature_importance.json").write_text(
        json.dumps([{ "feature": name, "gain": float(gain)} for name, gain in importance], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "ltr_v2_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Train baseline LightGBM LambdaRank model.")
    parser.add_argument("--input", required=True, help="pair-level csv path")
    parser.add_argument("--output-dir", required=True, help="output directory")
    args = parser.parse_args()
    train(Path(args.input), Path(args.output_dir))


if __name__ == "__main__":
    main()
