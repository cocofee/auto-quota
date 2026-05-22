from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_INPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_numeric_matrix_dry_run"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pretrain_dry_run"
DEFAULT_SPLIT = "quota_selfsup"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pretrain_dry_run_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pretrain_dry_run_summary.md"

FORBIDDEN_FEATURE_TOKENS = [
    "quota_id",
    "quota_name",
    "group_id",
    "source",
    "province",
    "project",
    "sample",
    "expected",
    "query",
    "bill",
]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(float(np.median(values)), 6) if values else None


def _read_group(path: Path) -> list[int]:
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _load_feature_whitelist(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} does not contain training_features")
    return [str(feature) for feature in features]


def _group_slices(groups: list[int]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start = 0
    for size in groups:
        stop = start + size
        result.append((start, stop))
        start = stop
    return result


def _stable_bucket(text: str, seed: int) -> int:
    payload = f"{seed}:{text}".encode("utf-8", errors="ignore")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big") % 10000


def _split_groups(meta: list[dict[str, Any]], validation_rate: float, seed: int) -> tuple[list[int], list[int]]:
    cutoff = int(validation_rate * 10000)
    train_groups: list[int] = []
    valid_groups: list[int] = []
    for idx, item in enumerate(meta):
        group_id = _clean(item.get("group_id")) or str(idx)
        if _stable_bucket(group_id, seed) < cutoff:
            valid_groups.append(idx)
        else:
            train_groups.append(idx)
    if not train_groups or not valid_groups:
        raise ValueError("deterministic group split produced empty train or validation set")
    return train_groups, valid_groups


def _rows_for_groups(group_indices: list[int], slices: list[tuple[int, int]]) -> list[int]:
    rows: list[int] = []
    for group_idx in group_indices:
        start, stop = slices[group_idx]
        rows.extend(range(start, stop))
    return rows


def _groups_for_indices(group_indices: list[int], groups: list[int]) -> list[int]:
    return [groups[idx] for idx in group_indices]


def _meta_for_indices(group_indices: list[int], meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [meta[idx] for idx in group_indices]


def _make_dataset(df: pd.DataFrame, rows: list[int], groups: list[int], features: list[str]) -> tuple[pd.DataFrame, np.ndarray, lgb.Dataset]:
    x = df.iloc[rows][features].astype(np.float32)
    y = df.iloc[rows]["label"].astype(np.int32).to_numpy()
    data = lgb.Dataset(x, label=y, group=groups, feature_name=features, free_raw_data=False)
    return x, y, data


def _first_positive_rank(labels: np.ndarray, scores: np.ndarray) -> int | None:
    order = np.lexsort((np.arange(len(scores)), -scores))
    ranked = labels[order]
    hits = np.flatnonzero(ranked > 0)
    return int(hits[0] + 1) if len(hits) else None


def _dcg(labels: np.ndarray, order: np.ndarray, k: int) -> float:
    score = 0.0
    for rank, idx in enumerate(order[:k], start=1):
        rel = float(labels[idx])
        if rel <= 0:
            continue
        score += (2.0**rel - 1.0) / math.log2(rank + 1.0)
    return score


def _ndcg_at(labels: np.ndarray, scores: np.ndarray, k: int) -> float:
    predicted_order = np.lexsort((np.arange(len(scores)), -scores))
    ideal_order = np.argsort(-labels, kind="stable")
    ideal = _dcg(labels, ideal_order, k)
    if ideal <= 0:
        return 0.0
    return _dcg(labels, predicted_order, k) / ideal


def _evaluate_groups(
    *,
    split: str,
    labels: np.ndarray,
    scores: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    details_path: Path,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    start = 0
    hit1 = 0
    ties = 0
    ranks: list[float] = []
    margins: list[float] = []
    ndcg1: list[float] = []
    ndcg2: list[float] = []
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    by_pair_type: dict[str, Counter[str]] = defaultdict(Counter)

    for idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_scores = scores[start:stop]
        positive_positions = np.flatnonzero(group_labels > 0)
        negative_positions = np.flatnonzero(group_labels <= 0)
        rank = _first_positive_rank(group_labels, group_scores)
        if rank is not None:
            ranks.append(float(rank))
        is_hit1 = rank == 1
        hit1 += int(is_hit1)
        if len(positive_positions) and len(negative_positions):
            pos_score = float(np.max(group_scores[positive_positions]))
            neg_score = float(np.max(group_scores[negative_positions]))
            margin = pos_score - neg_score
            margins.append(margin)
            ties += int(abs(margin) <= 1e-12)
        else:
            pos_score = None
            neg_score = None
            margin = None
        ndcg1.append(_ndcg_at(group_labels, group_scores, 1))
        ndcg2.append(_ndcg_at(group_labels, group_scores, min(2, size)))

        item = dict(meta[idx])
        family = _clean(item.get("family")) or "unknown"
        pair_type = _clean(item.get("pair_type")) or "unknown"
        by_family[family]["groups"] += 1
        by_family[family]["hit1"] += int(is_hit1)
        by_pair_type[pair_type]["groups"] += 1
        by_pair_type[pair_type]["hit1"] += int(is_hit1)
        item.update(
            {
                "split": split,
                "group_index": idx + 1,
                "positive_rank": rank,
                "hit1": is_hit1,
                "positive_score": round(pos_score, 8) if pos_score is not None else None,
                "best_negative_score": round(neg_score, 8) if neg_score is not None else None,
                "positive_margin": round(margin, 8) if margin is not None else None,
            }
        )
        details.append(item)
        start = stop

    _write_jsonl(details_path, details)
    total = len(groups)
    return {
        "split": split,
        "groups": total,
        "rows": int(sum(groups)),
        "hit1": hit1,
        "hit1_rate": _rate(hit1, total),
        "tie_groups": ties,
        "tie_rate": _rate(ties, total),
        "rank_avg": _mean(ranks),
        "rank_median": _median(ranks),
        "positive_margin_avg": _mean(margins),
        "positive_margin_median": _median(margins),
        "positive_margin_min": round(float(min(margins)), 8) if margins else None,
        "positive_margin_max": round(float(max(margins)), 8) if margins else None,
        "ndcg1": _mean(ndcg1),
        "ndcg2": _mean(ndcg2),
        "details_jsonl": str(details_path),
        "by_family": _counter_rates(by_family),
        "by_pair_type": _counter_rates(by_pair_type),
    }


def _counter_rates(counter_map: dict[str, Counter[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, counts in counter_map.items():
        groups = counts.get("groups", 0)
        hit1 = counts.get("hit1", 0)
        rows.append({"key": key, "groups": groups, "hit1": hit1, "hit1_rate": _rate(hit1, groups)})
    rows.sort(key=lambda row: (-int(row["groups"]), row["key"]))
    return rows


def _feature_group(feature: str) -> str:
    if feature.startswith("family_is_"):
        return "family"
    if feature.startswith("contrast_field_is_") or feature.startswith("pair_type_") or feature.startswith("training_mode_"):
        return "pair_metadata"
    if "name" in feature:
        return "candidate_text"
    if "contrast_numeric" in feature or "candidate_contrast" in feature:
        return "param_contrast"
    if "book" in feature or "chapter" in feature or "unit" in feature:
        return "book_chapter_unit"
    if "action" in feature:
        return "action_signal"
    if "material" in feature:
        return "material_signal"
    if "connection" in feature:
        return "connection_signal"
    if "install_method" in feature:
        return "install_method_signal"
    if "param_type" in feature:
        return "param_type_signal"
    if "subtype" in feature:
        return "subtype_signal"
    return "other"


def _write_importance(path: Path, booster: lgb.Booster, features: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gains = booster.feature_importance(importance_type="gain")
    splits = booster.feature_importance(importance_type="split")
    total_gain = float(np.sum(gains)) or 1.0
    rows: list[dict[str, Any]] = []
    group_gain: dict[str, float] = defaultdict(float)
    group_split: dict[str, int] = defaultdict(int)
    for feature, gain, split in zip(features, gains, splits, strict=True):
        group = _feature_group(feature)
        gain_float = float(gain)
        split_int = int(split)
        group_gain[group] += gain_float
        group_split[group] += split_int
        rows.append(
            {
                "feature": feature,
                "feature_group": group,
                "gain": round(gain_float, 8),
                "gain_share": round(gain_float / total_gain, 8),
                "split": split_int,
            }
        )
    rows.sort(key=lambda row: (float(row["gain"]), int(row["split"])), reverse=True)
    _write_csv(path, rows, ["feature", "feature_group", "gain", "gain_share", "split"])
    group_rows = [
        {
            "feature_group": group,
            "gain": round(gain, 8),
            "gain_share": round(gain / total_gain, 8),
            "split": group_split[group],
        }
        for group, gain in group_gain.items()
    ]
    group_rows.sort(key=lambda row: (float(row["gain"]), int(row["split"])), reverse=True)
    return rows, group_rows


def _write_eval_csv(path: Path, train_eval: dict[str, Any], valid_eval: dict[str, Any]) -> None:
    rows = []
    for item in [train_eval, valid_eval]:
        rows.append(
            {
                "split": item["split"],
                "groups": item["groups"],
                "rows": item["rows"],
                "hit1": item["hit1"],
                "hit1_rate": item["hit1_rate"],
                "tie_rate": item["tie_rate"],
                "rank_avg": item["rank_avg"],
                "positive_margin_avg": item["positive_margin_avg"],
                "positive_margin_median": item["positive_margin_median"],
                "ndcg1": item["ndcg1"],
                "ndcg2": item["ndcg2"],
            }
        )
    _write_csv(
        path,
        rows,
        [
            "split",
            "groups",
            "rows",
            "hit1",
            "hit1_rate",
            "tie_rate",
            "rank_avg",
            "positive_margin_avg",
            "positive_margin_median",
            "ndcg1",
            "ndcg2",
        ],
    )


def _write_breakdown_csv(path: Path, train_eval: dict[str, Any], valid_eval: dict[str, Any], key_name: str) -> None:
    rows: list[dict[str, Any]] = []
    for split_eval in [train_eval, valid_eval]:
        for item in split_eval[key_name]:
            rows.append({"split": split_eval["split"], **item})
    _write_csv(path, rows, ["split", "key", "groups", "hit1", "hit1_rate"])


def _forbidden_features(features: list[str]) -> list[str]:
    result: list[str] = []
    for feature in features:
        lower = feature.lower()
        if any(token in lower for token in FORBIDDEN_FEATURE_TOKENS):
            result.append(feature)
    return result


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    train_eval = summary["train_eval"]
    valid_eval = summary["valid_eval"]
    top_importance = [["feature", "group", "gain_share", "split"]]
    for row in summary["top_importance"][:15]:
        top_importance.append([row["feature"], row["feature_group"], row["gain_share"], row["split"]])
    group_importance = [["feature_group", "gain_share", "split"]]
    for row in summary["feature_group_importance"][:12]:
        group_importance.append([row["feature_group"], row["gain_share"], row["split"]])

    lines = [
        "# Goal Self-Supervised Pretrain Dry Run",
        "",
        "Stage 6.0 offline-only experiment. It trains a LightGBM LambdaRank model on the stage 5.9 self-supervised matrix and does not change search ranking or write a production model.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["input_rows", summary["input_rows"]],
                ["features", summary["feature_count"]],
                ["train_groups", summary["train_groups"]],
                ["valid_groups", summary["valid_groups"]],
                ["train_hit1_rate", train_eval["hit1_rate"]],
                ["valid_hit1_rate", valid_eval["hit1_rate"]],
                ["valid_tie_rate", valid_eval["tie_rate"]],
                ["valid_margin_avg", valid_eval["positive_margin_avg"]],
                ["forbidden_feature_count", summary["forbidden_feature_count"]],
                ["top1_gain_share", summary["top1_gain_share"]],
                ["valid_random_like", summary["valid_random_like"]],
                ["pretrain_reuse_allowed", summary["pretrain_reuse_allowed"]],
                ["failure_reasons", ", ".join(summary["failure_reasons"])],
                ["passes_pretrain_dry_run_gate", summary["passes_pretrain_dry_run_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Top Importance",
        "",
        _md_table(top_importance),
        "",
        "## Group Importance",
        "",
        _md_table(group_importance),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["model_path", report["artifacts"]["model_path"]],
                ["importance_csv", report["artifacts"]["importance_csv"]],
                ["feature_group_importance_csv", report["artifacts"]["feature_group_importance_csv"]],
                ["eval_csv", report["artifacts"]["eval_csv"]],
                ["family_breakdown_csv", report["artifacts"]["family_breakdown_csv"]],
                ["pair_type_breakdown_csv", report["artifacts"]["pair_type_breakdown_csv"]],
                ["summary_json", report["artifacts"]["summary_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 6.0 offline-only self-supervised LightGBM pretrain dry-run")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--validation-rate", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260522)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    split = args.split
    matrix_path = input_dir / f"ltr_matrix_{split}.csv"
    group_path = input_dir / f"ltr_group_{split}.txt"
    meta_path = input_dir / f"ltr_group_{split}.jsonl"
    whitelist_path = input_dir / f"ltr_feature_whitelist_{split}.json"

    model_path = output_dir / "goal_quota_self_supervised_pretrain_dry_run.txt"
    importance_path = output_dir / "feature_importance.csv"
    feature_group_importance_path = output_dir / "feature_group_importance.csv"
    eval_path = output_dir / "eval_metrics.csv"
    family_breakdown_path = output_dir / "eval_by_family.csv"
    pair_type_breakdown_path = output_dir / "eval_by_pair_type.csv"
    train_details_path = output_dir / "train_group_predictions.jsonl"
    valid_details_path = output_dir / "valid_group_predictions.jsonl"

    features = _load_feature_whitelist(whitelist_path)
    forbidden = _forbidden_features(features)
    df = pd.read_csv(matrix_path, encoding="utf-8-sig")
    groups = _read_group(group_path)
    meta = _read_jsonl(meta_path)
    missing_features = [feature for feature in features if feature not in df.columns]
    if missing_features:
        raise ValueError(f"matrix missing whitelist features: {missing_features[:10]}")
    if sum(groups) != len(df):
        raise ValueError(f"group sum {sum(groups)} != matrix rows {len(df)}")
    if len(groups) != len(meta):
        raise ValueError(f"group count {len(groups)} != meta rows {len(meta)}")
    if forbidden:
        raise ValueError(f"forbidden diagnostic features in whitelist: {forbidden[:10]}")

    slices = _group_slices(groups)
    train_group_indices, valid_group_indices = _split_groups(meta, args.validation_rate, args.seed)
    train_rows = _rows_for_groups(train_group_indices, slices)
    valid_rows = _rows_for_groups(valid_group_indices, slices)
    train_groups = _groups_for_indices(train_group_indices, groups)
    valid_groups = _groups_for_indices(valid_group_indices, groups)
    train_meta = _meta_for_indices(train_group_indices, meta)
    valid_meta = _meta_for_indices(valid_group_indices, meta)

    _train_x, train_y, train_data = _make_dataset(df, train_rows, train_groups, features)
    _valid_x, valid_y, valid_data = _make_dataset(df, valid_rows, valid_groups, features)

    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 2],
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": args.seed,
        "feature_fraction_seed": args.seed,
        "bagging_seed": args.seed,
        "data_random_seed": args.seed,
        "deterministic": True,
        "force_col_wise": True,
        "verbosity": -1,
    }
    evals_result: dict[str, Any] = {}
    booster = lgb.train(
        params,
        train_data,
        num_boost_round=args.num_boost_round,
        valid_sets=[train_data, valid_data],
        valid_names=["train", "valid"],
        callbacks=[lgb.record_evaluation(evals_result)],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(model_path)

    train_scores = booster.predict(df.iloc[train_rows][features].astype(np.float32), num_iteration=booster.current_iteration())
    valid_scores = booster.predict(df.iloc[valid_rows][features].astype(np.float32), num_iteration=booster.current_iteration())
    train_eval = _evaluate_groups(
        split="train",
        labels=train_y,
        scores=np.asarray(train_scores),
        groups=train_groups,
        meta=train_meta,
        details_path=train_details_path,
    )
    valid_eval = _evaluate_groups(
        split="valid",
        labels=valid_y,
        scores=np.asarray(valid_scores),
        groups=valid_groups,
        meta=valid_meta,
        details_path=valid_details_path,
    )
    importance_rows, feature_group_rows = _write_importance(importance_path, booster, features)
    _write_csv(feature_group_importance_path, feature_group_rows, ["feature_group", "gain", "gain_share", "split"])
    _write_eval_csv(eval_path, train_eval, valid_eval)
    _write_breakdown_csv(family_breakdown_path, train_eval, valid_eval, "by_family")
    _write_breakdown_csv(pair_type_breakdown_path, train_eval, valid_eval, "by_pair_type")

    total_gain_share = sum(float(row["gain_share"]) for row in importance_rows) or 1.0
    top1_gain_share = float(importance_rows[0]["gain_share"]) if importance_rows else 0.0
    top10_gain_share = sum(float(row["gain_share"]) for row in importance_rows[:10])
    hash_gain_share = sum(float(row["gain_share"]) for row in importance_rows if row["feature"].endswith("_hash"))
    stability_gap = abs(float(train_eval["hit1_rate"]) - float(valid_eval["hit1_rate"]))
    valid_random_like = 0.45 <= float(valid_eval["hit1_rate"]) <= 0.55
    passes_gate = (
        valid_eval["groups"] > 0
        and float(valid_eval["hit1_rate"]) >= 0.6
        and stability_gap <= 0.25
        and not forbidden
        and top1_gain_share < 0.5
        and abs(total_gain_share - 1.0) < 0.0001
    )
    failure_reasons: list[str] = []
    if float(valid_eval["hit1_rate"]) < 0.6:
        failure_reasons.append("valid_hit1_rate_below_0_6")
    if valid_random_like:
        failure_reasons.append("valid_hit1_rate_near_random")
    if stability_gap > 0.25:
        failure_reasons.append("train_valid_gap_over_0_25")
    if forbidden:
        failure_reasons.append("forbidden_diagnostic_features_present")
    if top1_gain_share >= 0.5:
        failure_reasons.append("feature_importance_too_concentrated")
    summary = {
        "input_rows": int(len(df)),
        "input_groups": int(len(groups)),
        "feature_count": len(features),
        "train_groups": len(train_groups),
        "valid_groups": len(valid_groups),
        "train_rows": len(train_rows),
        "valid_rows": len(valid_rows),
        "validation_rate": args.validation_rate,
        "seed": args.seed,
        "num_boost_round": args.num_boost_round,
        "current_iteration": int(booster.current_iteration()),
        "forbidden_feature_count": len(forbidden),
        "forbidden_features": forbidden,
        "train_eval": train_eval,
        "valid_eval": valid_eval,
        "train_valid_hit1_gap": round(stability_gap, 6),
        "top1_gain_share": round(top1_gain_share, 8),
        "top10_gain_share": round(top10_gain_share, 8),
        "hash_feature_gain_share": round(hash_gain_share, 8),
        "valid_random_like": valid_random_like,
        "pretrain_reuse_allowed": passes_gate,
        "failure_reasons": failure_reasons,
        "top_importance": importance_rows[:30],
        "feature_group_importance": feature_group_rows,
        "evals_result_tail": {
            dataset: {metric: values[-5:] for metric, values in metrics.items()}
            for dataset, metrics in evals_result.items()
        },
        "passes_pretrain_dry_run_gate": passes_gate,
        "interpretation_notes": [
            "This is an offline self-supervised pair discrimination test, not an OSS heldout accuracy test.",
            "The model is saved under reports/agent_state only and is not a production search model.",
            "High validation hit1 means the numeric pair features can recover the frozen pair labels; it does not prove online rerank gain.",
        ],
        "recommended_next_stage": (
            "Stage 6.1 self-supervised label direction audit; inspect whether positive/negative pair labels are arbitrary before any further training."
            if not passes_gate
            else "Stage 6.1 pretrain residual/importance audit; inspect validation misses and feature importance before any model reuse."
        ),
    }
    report = {
        "stage": "Goal LTR v1 / stage 6.0 quota self-supervised pretrain dry-run",
        "offline_only": True,
        "no_search_integration": True,
        "no_production_model_write": True,
        "input_dir": str(input_dir),
        "summary": summary,
        "artifacts": {
            "output_dir": str(output_dir),
            "model_path": str(model_path),
            "importance_csv": str(importance_path),
            "feature_group_importance_csv": str(feature_group_importance_path),
            "eval_csv": str(eval_path),
            "family_breakdown_csv": str(family_breakdown_path),
            "pair_type_breakdown_csv": str(pair_type_breakdown_path),
            "train_details_jsonl": str(train_details_path),
            "valid_details_jsonl": str(valid_details_path),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "input_rows": summary["input_rows"],
                    "feature_count": summary["feature_count"],
                    "train_groups": summary["train_groups"],
                    "valid_groups": summary["valid_groups"],
                    "train_hit1_rate": train_eval["hit1_rate"],
                    "valid_hit1_rate": valid_eval["hit1_rate"],
                    "train_valid_hit1_gap": summary["train_valid_hit1_gap"],
                    "top1_gain_share": summary["top1_gain_share"],
                    "hash_feature_gain_share": summary["hash_feature_gain_share"],
                    "passes_pretrain_dry_run_gate": summary["passes_pretrain_dry_run_gate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
