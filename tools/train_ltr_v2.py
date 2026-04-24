from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "ltr_r2_silver_training_data.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "ltr" / "model_v2"
DEFAULT_MODEL_PATH = DEFAULT_OUTPUT_DIR / "ltr_v2_model.txt"
DEFAULT_FEATURES_PATH = DEFAULT_OUTPUT_DIR / "ltr_v2_features.json"
DEFAULT_SUMMARY_PATH = DEFAULT_OUTPUT_DIR / "train_summary.json"
DEFAULT_PROTECT_GROUP_WEIGHTS = {
    "electrical_box_guard": 1.2,
    "socket_guard": 1.5,
    "lighting_guard": 1.25,
    "sleeve_guard": 1.25,
    "switch_guard": 1.25,
    "air_outlet_guard": 1.0,
    "equipotential_guard": 4.0,
    "beijing_water_guard": 1.0,
}

META_COLUMNS = {
    "query_id",
    "province",
    "bill_id",
    "bill_name",
    "correct_quota_id",
    "predicted_quota_id",
    "candidate_quota_id",
    "candidate_name",
    "label",
    "sample_source",
    "sample_class",
    "protect_group",
    "risk_reason",
    "training_role",
}


def _sorted_counter(mapping: dict[str, int]) -> dict[str, int]:
    return dict(sorted(mapping.items(), key=lambda item: (-item[1], item[0])))


def infer_feature_names(df: pd.DataFrame) -> list[str]:
    feature_names: list[str] = []
    for column in df.columns:
        if column in META_COLUMNS:
            continue
        if pd.api.types.is_numeric_dtype(df[column]):
            feature_names.append(str(column))
    return feature_names


def split_queries(df: pd.DataFrame, *, holdout_ratio: float, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    query_ids = list(df["query_id"].drop_duplicates())
    rng = np.random.default_rng(seed)
    rng.shuffle(query_ids)
    holdout_size = max(1, int(round(len(query_ids) * holdout_ratio))) if query_ids else 0
    holdout_ids = set(query_ids[:holdout_size])
    train_df = df[~df["query_id"].isin(holdout_ids)].copy()
    holdout_df = df[df["query_id"].isin(holdout_ids)].copy()
    return train_df, holdout_df


def eval_hit_at_1(df: pd.DataFrame, score_col: str) -> float:
    if df.empty:
        return 0.0
    top1 = (
        df.sort_values(["query_id", score_col], ascending=[True, False])
        .groupby("query_id")
        .head(1)
    )
    return float((top1["label"] >= 1).mean())


def summarize_sample_sources(df: pd.DataFrame) -> dict[str, int]:
    if "sample_source" not in df.columns or df.empty:
        return {}
    counts = df["sample_source"].fillna("").astype(str).value_counts().to_dict()
    return _sorted_counter({str(key): int(value) for key, value in counts.items() if str(key)})


def build_sample_weights(
    df: pd.DataFrame,
    *,
    default_weight: float = 1.0,
    r2_weight: float = 1.0,
    safety_weight: float = 1.2,
    protect_group_weights: dict[str, float] | None = None,
) -> np.ndarray:
    if "sample_source" not in df.columns:
        return np.full(len(df), float(default_weight), dtype=float)
    source_weights = {
        "benchmark_r2_silver": float(r2_weight),
        "benchmark_safety_correct": float(safety_weight),
        "manual_targeted_safety_seed": float(safety_weight),
    }
    weights = []
    protect_groups = df["protect_group"].fillna("").astype(str) if "protect_group" in df.columns else None
    for index, value in enumerate(df["sample_source"].fillna("").astype(str)):
        weight = float(source_weights.get(value, default_weight))
        if (
            protect_group_weights
            and value in {"benchmark_safety_correct", "manual_targeted_safety_seed"}
            and protect_groups is not None
        ):
            protect_group = protect_groups.iloc[index]
            weight *= float(protect_group_weights.get(protect_group, 1.0))
        weights.append(weight)
    return np.asarray(weights, dtype=float)


def _pick_baseline_score_column(df: pd.DataFrame) -> str | None:
    if "manual_structured_score" in df.columns:
        return "manual_structured_score"
    if "rerank_score" in df.columns:
        return "rerank_score"
    return None


def _baseline_hit_from_predicted(df: pd.DataFrame) -> float:
    if df.empty or "predicted_quota_id" not in df.columns or "candidate_quota_id" not in df.columns:
        return 0.0
    baseline_rows = df[df["candidate_quota_id"].astype(str) == df["predicted_quota_id"].astype(str)]
    if baseline_rows.empty:
        return 0.0
    return float((baseline_rows["label"] >= 1).mean())


def _string_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _first_string_value(df: pd.DataFrame, column: str) -> str:
    if column not in df.columns:
        return ""
    for value in df[column].tolist():
        text = _string_value(value).strip()
        if text:
            return text
    return ""


def _top_row_by_score(df: pd.DataFrame, score_col: str) -> pd.Series | None:
    if df.empty or score_col not in df.columns:
        return None
    sorted_df = df.sort_values(score_col, ascending=False)
    if sorted_df.empty:
        return None
    return sorted_df.iloc[0]


def _build_do_not_break_violations(
    merged: pd.DataFrame,
    *,
    score_col: str,
    protect_group_col: str,
    failure_target_col: str,
    training_role_col: str,
) -> list[dict]:
    violations: list[dict] = []
    if merged.empty:
        return violations

    for query_id, query_df in merged.groupby("query_id", dropna=False):
        baseline_top1_id = _first_string_value(query_df, "predicted_quota_id")
        correct_quota_id = _first_string_value(query_df, "correct_quota_id")
        if not correct_quota_id and "candidate_quota_id" in query_df.columns:
            positive_rows = query_df[query_df["label"] >= 1]
            correct_quota_id = _first_string_value(positive_rows, "candidate_quota_id")

        baseline_hit = False
        if baseline_top1_id and "candidate_quota_id" in query_df.columns:
            baseline_rows = query_df[
                query_df["candidate_quota_id"].astype(str) == baseline_top1_id
            ]
            baseline_hit = bool(not baseline_rows.empty and (baseline_rows["label"] >= 1).any())

        model_top_row = _top_row_by_score(query_df, score_col)
        if model_top_row is None:
            continue
        model_top1_id = _string_value(model_top_row.get("candidate_quota_id", "")).strip()
        model_hit = bool(model_top_row.get("label", 0) >= 1)

        if not baseline_hit or model_hit:
            continue

        model_score = model_top_row.get(score_col)
        try:
            model_top1_score = round(float(model_score), 6)
        except Exception:
            model_top1_score = None

        training_roles = sorted({
            _string_value(value).strip()
            for value in query_df[training_role_col].dropna().tolist()
            if _string_value(value).strip()
        }) if training_role_col in query_df.columns else []
        is_watch_only = bool(training_roles) and all(value == "eval_only" for value in training_roles)

        violations.append({
            "query_id": query_id,
            "protect_group": _first_string_value(query_df, protect_group_col) or "unknown",
            "failure_target": _first_string_value(query_df, failure_target_col),
            "training_role": ",".join(training_roles),
            "is_watch_only": is_watch_only,
            "correct_quota_id": correct_quota_id,
            "baseline_top1_id": baseline_top1_id,
            "model_top1_id": model_top1_id,
            "model_top1_score": model_top1_score,
            "baseline_hit": baseline_hit,
            "model_hit": model_hit,
            "candidate_count": int(query_df["candidate_quota_id"].nunique()) if "candidate_quota_id" in query_df.columns else int(len(query_df)),
        })

    violations.sort(key=lambda row: (
        bool(row.get("is_watch_only")),
        str(row.get("protect_group", "")),
        str(row.get("query_id", "")),
    ))
    return violations


def evaluate_do_not_break(
    df_scored: pd.DataFrame,
    do_not_break_records: list[dict],
    *,
    score_col: str = "ltr_score",
) -> dict:
    result = {
        "total": 0,
        "baseline_hit_at_1": 0.0,
        "hit_at_1": 0.0,
        "hit_at_1_delta": 0.0,
        "regression_guard_failed": False,
        "groups": {},
        "violations": [],
    }
    if df_scored.empty or not do_not_break_records:
        return result

    do_not_break_df = pd.DataFrame(do_not_break_records)
    if do_not_break_df.empty or "query_id" not in do_not_break_df.columns:
        return result

    query_ids = set(do_not_break_df["query_id"].tolist())
    subset = df_scored[df_scored["query_id"].isin(query_ids)].copy()
    if subset.empty:
        return result

    merged = subset.merge(
        do_not_break_df[["query_id", "protect_group", "failure_target", "training_role"]],
        on="query_id",
        how="left",
        suffixes=("", "_eval"),
    )
    protect_group_col = "protect_group" if "protect_group" in merged.columns else "protect_group_eval"
    failure_target_col = "failure_target" if "failure_target" in merged.columns else "failure_target_eval"
    training_role_col = "training_role_eval" if "training_role_eval" in merged.columns else "training_role"
    baseline_hit = _baseline_hit_from_predicted(merged)
    model_hit = eval_hit_at_1(merged, score_col)

    groups: dict[str, dict] = {}
    watch_only_groups: list[str] = []
    violations = _build_do_not_break_violations(
        merged,
        score_col=score_col,
        protect_group_col=protect_group_col,
        failure_target_col=failure_target_col,
        training_role_col=training_role_col,
    )
    for protect_group, group_df in merged.groupby(protect_group_col, dropna=False):
        label = str(protect_group or "unknown")
        group_baseline = _baseline_hit_from_predicted(group_df)
        group_model = eval_hit_at_1(group_df, score_col)
        training_roles = sorted({str(value) for value in group_df[training_role_col].dropna().tolist() if str(value)})
        is_watch_only = bool(training_roles) and all(value == "eval_only" for value in training_roles)
        if is_watch_only:
            watch_only_groups.append(label)
        groups[label] = {
            "queries": int(group_df["query_id"].nunique()),
            "baseline_hit_at_1": round(float(group_baseline), 4),
            "hit_at_1": round(float(group_model), 4),
            "hit_at_1_delta": round(float(group_model - group_baseline), 4),
            "failure_targets": sorted({str(value) for value in group_df[failure_target_col].dropna().tolist() if str(value)}),
            "training_roles": training_roles,
            "is_watch_only": is_watch_only,
            "violations": [row for row in violations if row.get("protect_group") == label],
        }

    result["total"] = int(merged["query_id"].nunique())
    result["baseline_hit_at_1"] = round(float(baseline_hit), 4)
    result["hit_at_1"] = round(float(model_hit), 4)
    result["hit_at_1_delta"] = round(float(model_hit - baseline_hit), 4)
    result["groups"] = dict(sorted(groups.items(), key=lambda item: item[0]))
    result["watch_only_groups"] = sorted(watch_only_groups)
    result["violations"] = violations
    result["regression_guard_failed"] = any(
        float(group.get("hit_at_1_delta", 0.0)) < -0.02
        and not bool(group.get("is_watch_only"))
        for group in result["groups"].values()
    )
    return result


def train_lambdarank(
    df_train: pd.DataFrame,
    *,
    feature_names: list[str],
    df_eval: pd.DataFrame | None = None,
    train_weights: np.ndarray | None = None,
    num_boost_round: int = 300,
    learning_rate: float = 0.05,
    num_leaves: int = 31,
    min_data_in_leaf: int = 10,
    lambda_l1: float = 0.1,
    lambda_l2: float = 1.0,
):
    import lightgbm as lgb

    train_matrix = df_train[feature_names].values
    train_labels = df_train["label"].values
    train_groups = df_train.groupby("query_id").size().values

    train_data = lgb.Dataset(
        train_matrix,
        label=train_labels,
        group=train_groups,
        weight=train_weights,
        feature_name=feature_names,
    )

    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 3],
        "num_leaves": int(num_leaves),
        "min_data_in_leaf": int(min_data_in_leaf),
        "learning_rate": float(learning_rate),
        "lambda_l1": float(lambda_l1),
        "lambda_l2": float(lambda_l2),
        "seed": 42,
        "verbose": -1,
    }

    valid_sets = [train_data]
    valid_names = ["train"]
    callbacks = [lgb.log_evaluation(period=50)]

    if df_eval is not None and not df_eval.empty:
        eval_matrix = df_eval[feature_names].values
        eval_labels = df_eval["label"].values
        eval_groups = df_eval.groupby("query_id").size().values
        eval_data = lgb.Dataset(
            eval_matrix,
            label=eval_labels,
            group=eval_groups,
            feature_name=feature_names,
            reference=train_data,
        )
        valid_sets.append(eval_data)
        valid_names.append("holdout")
        callbacks.append(lgb.early_stopping(stopping_rounds=30, verbose=True))

    model = lgb.train(
        params,
        train_data,
        num_boost_round=int(num_boost_round),
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=callbacks,
    )
    return model


def build_summary(
    *,
    input_path: Path,
    feature_names: list[str],
    source_counts: dict[str, int],
    train_source_counts: dict[str, int],
    holdout_source_counts: dict[str, int],
    sample_weight_map: dict[str, float],
    train_df: pd.DataFrame,
    holdout_df: pd.DataFrame,
    baseline_hit_at_1: float,
    holdout_hit_at_1: float,
    do_not_break: dict,
    output_model_path: Path,
    output_features_path: Path,
) -> dict:
    return {
        "input_path": str(input_path),
        "feature_count": len(feature_names),
        "sample_source_counts": source_counts,
        "train_sample_source_counts": train_source_counts,
        "holdout_sample_source_counts": holdout_source_counts,
        "sample_weight_map": sample_weight_map,
        "train_queries": int(train_df["query_id"].nunique()),
        "train_rows": int(len(train_df)),
        "holdout_queries": int(holdout_df["query_id"].nunique()),
        "holdout_rows": int(len(holdout_df)),
        "holdout_baseline_hit_at_1": round(float(baseline_hit_at_1), 4),
        "holdout_hit_at_1": round(float(holdout_hit_at_1), 4),
        "holdout_hit_at_1_delta": round(float(holdout_hit_at_1 - baseline_hit_at_1), 4),
        "do_not_break": do_not_break,
        "output_model_path": str(output_model_path),
        "output_features_path": str(output_features_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train LTR v2 model from silver R2 dataset.")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT), help="Silver training csv path")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--holdout-ratio", type=float, default=0.2, help="Query-level holdout ratio")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for query split")
    parser.add_argument("--num-round", type=int, default=300, help="LightGBM boosting rounds")
    parser.add_argument("--learning-rate", type=float, default=0.05, help="LightGBM learning rate")
    parser.add_argument("--num-leaves", type=int, default=31, help="LightGBM num_leaves")
    parser.add_argument("--min-data-in-leaf", type=int, default=10, help="LightGBM min_data_in_leaf")
    parser.add_argument("--lambda-l1", type=float, default=0.1, help="LightGBM lambda_l1")
    parser.add_argument("--lambda-l2", type=float, default=1.0, help="LightGBM lambda_l2")
    parser.add_argument("--sample-weight-r2", type=float, default=1.0, help="Weight for benchmark_r2_silver rows")
    parser.add_argument("--sample-weight-safety", type=float, default=1.2, help="Weight for benchmark_safety_correct rows")
    parser.add_argument("--protect-weight-electrical-box", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["electrical_box_guard"], help="Additional multiplier for electrical_box_guard")
    parser.add_argument("--protect-weight-socket", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["socket_guard"], help="Additional multiplier for socket_guard")
    parser.add_argument("--protect-weight-lighting", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["lighting_guard"], help="Additional multiplier for lighting_guard")
    parser.add_argument("--protect-weight-sleeve", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["sleeve_guard"], help="Additional multiplier for sleeve_guard")
    parser.add_argument("--protect-weight-switch", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["switch_guard"], help="Additional multiplier for switch_guard")
    parser.add_argument("--protect-weight-air-outlet", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["air_outlet_guard"], help="Additional multiplier for air_outlet_guard")
    parser.add_argument("--protect-weight-equipotential", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["equipotential_guard"], help="Additional multiplier for equipotential_guard")
    parser.add_argument("--protect-weight-beijing-water", type=float, default=DEFAULT_PROTECT_GROUP_WEIGHTS["beijing_water_guard"], help="Additional multiplier for beijing_water_guard")
    parser.add_argument("--do-not-break-eval", type=str, default="", help="Optional do-not-break evaluation JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model_path = output_dir / DEFAULT_MODEL_PATH.name
    features_path = output_dir / DEFAULT_FEATURES_PATH.name
    summary_path = output_dir / DEFAULT_SUMMARY_PATH.name

    df = pd.read_csv(input_path, low_memory=False)
    feature_names = infer_feature_names(df)
    source_counts = summarize_sample_sources(df)
    trainable_df = df.copy()
    if "training_role" in trainable_df.columns:
        trainable_df = trainable_df[trainable_df["training_role"].fillna("train_and_eval").astype(str) != "eval_only"].copy()
    train_df, holdout_df = split_queries(
        trainable_df,
        holdout_ratio=max(0.0, min(0.9, float(args.holdout_ratio))),
        seed=int(args.seed),
    )

    if train_df.empty:
        raise ValueError("No training queries available after split.")

    protect_group_weight_map = {
        "electrical_box_guard": float(args.protect_weight_electrical_box),
        "socket_guard": float(args.protect_weight_socket),
        "lighting_guard": float(args.protect_weight_lighting),
        "sleeve_guard": float(args.protect_weight_sleeve),
        "switch_guard": float(args.protect_weight_switch),
        "air_outlet_guard": float(args.protect_weight_air_outlet),
        "equipotential_guard": float(args.protect_weight_equipotential),
        "beijing_water_guard": float(args.protect_weight_beijing_water),
    }
    train_weights = build_sample_weights(
        train_df,
        r2_weight=float(args.sample_weight_r2),
        safety_weight=float(args.sample_weight_safety),
        protect_group_weights=protect_group_weight_map,
    )
    model = train_lambdarank(
        train_df,
        feature_names=feature_names,
        df_eval=holdout_df,
        train_weights=train_weights,
        num_boost_round=int(args.num_round),
        learning_rate=float(args.learning_rate),
        num_leaves=int(args.num_leaves),
        min_data_in_leaf=int(args.min_data_in_leaf),
        lambda_l1=float(args.lambda_l1),
        lambda_l2=float(args.lambda_l2),
    )

    model.save_model(str(model_path))
    features_path.write_text(
        json.dumps({"feature_names": feature_names}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    baseline_hit_at_1 = 0.0
    holdout_hit_at_1 = 0.0
    baseline_score_col = None
    if not holdout_df.empty:
        holdout_scored = holdout_df.copy()
        baseline_score_col = _pick_baseline_score_column(holdout_scored)
        if baseline_score_col:
            baseline_hit_at_1 = eval_hit_at_1(holdout_scored, baseline_score_col)
        holdout_scored["ltr_score"] = model.predict(holdout_scored[feature_names].values)
        holdout_hit_at_1 = eval_hit_at_1(holdout_scored, "ltr_score")

    df_scored = df.copy()
    df_scored["ltr_score"] = model.predict(df_scored[feature_names].values)
    do_not_break_summary = {}
    if args.do_not_break_eval:
        do_not_break_path = Path(args.do_not_break_eval)
        if do_not_break_path.exists():
            do_not_break_records = json.loads(do_not_break_path.read_text(encoding="utf-8"))
            do_not_break_summary = evaluate_do_not_break(df_scored, list(do_not_break_records), score_col="ltr_score")
        else:
            do_not_break_summary = {
                "total": 0,
                "baseline_hit_at_1": 0.0,
                "hit_at_1": 0.0,
                "hit_at_1_delta": 0.0,
                "regression_guard_failed": True,
                "groups": {},
                "error": f"missing file: {do_not_break_path}",
            }

    sample_weight_map = {
        "benchmark_r2_silver": float(args.sample_weight_r2),
        "benchmark_safety_correct": float(args.sample_weight_safety),
        "manual_targeted_safety_seed": float(args.sample_weight_safety),
    }
    sample_weight_map["protect_group_multipliers"] = protect_group_weight_map
    summary = build_summary(
        input_path=input_path,
        feature_names=feature_names,
        source_counts=source_counts,
        train_source_counts=summarize_sample_sources(train_df),
        holdout_source_counts=summarize_sample_sources(holdout_df),
        sample_weight_map=sample_weight_map,
        train_df=train_df,
        holdout_df=holdout_df,
        baseline_hit_at_1=baseline_hit_at_1,
        holdout_hit_at_1=holdout_hit_at_1,
        do_not_break=do_not_break_summary,
        output_model_path=model_path,
        output_features_path=features_path,
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[LTR_V2_TRAIN] input: {input_path}")
    print(f"[LTR_V2_TRAIN] train_queries: {summary['train_queries']} | holdout_queries: {summary['holdout_queries']}")
    print(f"[LTR_V2_TRAIN] feature_count: {summary['feature_count']}")
    if summary["sample_source_counts"]:
        print(f"[LTR_V2_TRAIN] sample_source_counts: {summary['sample_source_counts']}")
    print(f"[LTR_V2_TRAIN] holdout_baseline_hit_at_1: {summary['holdout_baseline_hit_at_1']:.4f}")
    print(f"[LTR_V2_TRAIN] holdout_hit_at_1: {summary['holdout_hit_at_1']:.4f}")
    print(f"[LTR_V2_TRAIN] holdout_hit_at_1_delta: {summary['holdout_hit_at_1_delta']:.4f}")
    if do_not_break_summary:
        print(f"[LTR_V2_TRAIN] do_not_break_hit_at_1: {do_not_break_summary.get('hit_at_1', 0.0):.4f}")
        print(f"[LTR_V2_TRAIN] do_not_break_delta: {do_not_break_summary.get('hit_at_1_delta', 0.0):.4f}")
        print(f"[LTR_V2_TRAIN] do_not_break_guard_failed: {bool(do_not_break_summary.get('regression_guard_failed', False))}")
    print(f"[LTR_V2_TRAIN] model: {model_path}")
    print(f"[LTR_V2_TRAIN] features: {features_path}")
    print(f"[LTR_V2_TRAIN] summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
