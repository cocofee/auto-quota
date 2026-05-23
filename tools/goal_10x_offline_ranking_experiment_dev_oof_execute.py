from __future__ import annotations

import argparse
import csv
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_SCOPE_LOCK = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_scope_lock_summary.json"
DEFAULT_FROZEN_PLAN = AGENT_STATE / "goal_10x_offline_ranking_experiment_plan_definition_summary.json"
DEFAULT_MATRIX_SUMMARY = AGENT_STATE / "goal_query_anchored_ranking_matrix_dry_run_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof"

FORBIDDEN_FEATURE_KEYS = {
    "sample_id",
    "source_file",
    "expected_id",
    "expected_ids",
    "expected_quota_id",
    "expected_quota_ids",
    "positive_id",
    "correct_quota_id",
    "quota_id",
    "province",
    "project_name",
    "group_id",
    "candidate_id",
    "stored_ids",
}

FEATURE_FAMILIES = {
    "FT_EXCLUDE_BASE_RETRIEVAL_SCORE": [
        "base_rank",
        "current_score",
        "confidence",
        "bm25_score",
        "national_cluster_bonus",
        "token_overlap",
    ],
    "FT_EXCLUDE_BOOK_AND_CHAPTER_ALIGNMENT": [
        "book_requested",
        "book_match",
        "book_conflict",
        "chapter_book_match",
    ],
    "FT_EXCLUDE_TAXONOMY_FAMILY_AND_ACTION": [
        "query_family_present",
        "candidate_family_present",
        "family_match",
        "family_conflict",
        "action_match",
        "material_match",
        "connection_match",
        "install_method_match",
    ],
    "FT_EXCLUDE_FIELD_NUMERIC_DOMAIN_SCORES": [
        "field_score",
        "numeric_score",
        "domain_rule_score",
        "domain_label_overlap_count",
        "domain_conflict_count",
    ],
    "FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES": [
        "param_exact_count",
        "param_tier_up_count",
        "param_conflict_count",
        "dn_exact",
        "dn_gap_ratio",
        "cable_section_exact",
        "cable_section_gap_ratio",
        "thickness_exact",
        "thickness_gap_ratio",
        "width_height_exact",
        "width_height_gap_ratio",
    ],
    "FT_EXCLUDE_CONFLICT_REASON_FLAGS": [
        "has_domain_conflict",
        "has_family_conflict_reason",
        "has_book_conflict_reason",
        "has_unit_conflict_reason",
        "has_param_conflict_reason",
        "has_national_reason",
        "reason_count",
    ],
}

SAFE_CORE_FEATURES = (
    FEATURE_FAMILIES["FT_EXCLUDE_BASE_RETRIEVAL_SCORE"]
    + FEATURE_FAMILIES["FT_EXCLUDE_BOOK_AND_CHAPTER_ALIGNMENT"]
    + FEATURE_FAMILIES["FT_EXCLUDE_CONFLICT_REASON_FLAGS"]
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_group(path: Path) -> list[int]:
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _load_training_features(path: Path) -> list[str]:
    payload = _read_json(path)
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} missing training_features")
    return [str(feature).strip() for feature in features if str(feature).strip()]


def _load_dev_matrix(data_dir: Path, all_features: list[str]) -> tuple[pd.DataFrame, np.ndarray, list[int], list[dict[str, Any]], list[dict[str, Any]]]:
    matrix_path = data_dir / "ltr_matrix_dev.csv"
    group_path = data_dir / "ltr_group_dev.txt"
    meta_path = data_dir / "ltr_group_dev.jsonl"
    feature_path = data_dir / "ltr_features_dev.jsonl"
    df = pd.read_csv(matrix_path, encoding="utf-8-sig")
    missing = [feature for feature in all_features if feature not in df.columns]
    if missing:
        raise ValueError(f"{matrix_path} missing features: {missing[:20]}")
    labels = df["label"].astype(np.int8).to_numpy()
    groups = _read_group(group_path)
    meta = _read_jsonl(meta_path)
    feature_rows = _read_jsonl(feature_path)
    if sum(groups) != len(df):
        raise ValueError(f"group sum {sum(groups)} != matrix rows {len(df)}")
    if len(groups) != len(meta):
        raise ValueError(f"group count {len(groups)} != meta rows {len(meta)}")
    if len(feature_rows) != len(df):
        raise ValueError(f"feature row count {len(feature_rows)} != matrix rows {len(df)}")
    return df, labels, groups, meta, feature_rows


def _candidate_features(toggle_id: str, all_features: list[str]) -> list[str]:
    if toggle_id == "FT_ALL_CURRENT_WHITELIST":
        return list(all_features)
    if toggle_id == "FT_SAFE_CORE_ONLY":
        return [feature for feature in all_features if feature in SAFE_CORE_FEATURES]
    excluded = set(FEATURE_FAMILIES.get(toggle_id, []))
    if not excluded:
        raise ValueError(f"unknown feature toggle: {toggle_id}")
    return [feature for feature in all_features if feature not in excluded]


def _objective_params(objective_variant: str, seed: int) -> dict[str, Any]:
    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 5, 10],
        "learning_rate": 0.04,
        "num_leaves": 31,
        "min_data_in_leaf": 25,
        "feature_fraction": 0.90,
        "bagging_fraction": 0.90,
        "bagging_freq": 1,
        "label_gain": [0, 1],
        "verbosity": -1,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
        "num_threads": 0,
    }
    if objective_variant == "OBJ_B_loss_budgeted_top1_net":
        params["min_data_in_leaf"] = 35
        params["lambda_l2"] = 1.0
    elif objective_variant == "OBJ_C_recall_separated_top80_present":
        params["num_leaves"] = 27
    elif objective_variant == "OBJ_D_fallback_preserving_override":
        params["min_data_in_leaf"] = 40
        params["lambda_l2"] = 2.0
    return params


def _group_weights(objective_variant: str, labels: np.ndarray, groups: list[int]) -> np.ndarray | None:
    weights = np.ones(len(labels), dtype=np.float32)
    start = 0
    for size in groups:
        stop = start + size
        group_labels = labels[start:stop]
        baseline_hit = bool(len(group_labels) and group_labels[0] > 0)
        if objective_variant == "OBJ_B_loss_budgeted_top1_net" and not baseline_hit:
            weights[start:stop] = 1.25
        elif objective_variant == "OBJ_D_fallback_preserving_override" and baseline_hit:
            weights[start:stop] = 1.45
        start = stop
    return weights if objective_variant in {"OBJ_B_loss_budgeted_top1_net", "OBJ_D_fallback_preserving_override"} else None


def _fold_assignments(group_count: int, folds: int) -> list[list[int]]:
    return [list(range(fold, group_count, folds)) for fold in range(folds)]


def _row_offsets(groups: list[int]) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    for size in groups:
        offsets.append(cursor)
        cursor += size
    return offsets


def _take_groups(df: pd.DataFrame, labels: np.ndarray, groups: list[int], group_indices: list[int]) -> tuple[pd.DataFrame, np.ndarray, list[int], np.ndarray]:
    offsets = _row_offsets(groups)
    row_indices: list[int] = []
    out_groups: list[int] = []
    for group_idx in group_indices:
        start = offsets[group_idx]
        size = groups[group_idx]
        row_indices.extend(range(start, start + size))
        out_groups.append(size)
    idx = np.array(row_indices, dtype=np.int64)
    return df.iloc[idx], labels[idx], out_groups, idx


def _rank_bucket(rank: int | None) -> str:
    if rank is None:
        return "missing_positive"
    if rank == 1:
        return "rank_1"
    if 2 <= rank <= 5:
        return "rank_2_5"
    if 6 <= rank <= 10:
        return "rank_6_10"
    if 11 <= rank <= 20:
        return "rank_11_20"
    if 21 <= rank <= 40:
        return "rank_21_40"
    return "rank_41_80"


def _first_positive_rank(group_labels: np.ndarray, order: np.ndarray) -> int | None:
    hits = np.flatnonzero(group_labels[order] > 0)
    return int(hits[0] + 1) if len(hits) else None


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text or "<empty>"


def _expected_book(feature_rows: list[dict[str, Any]], positive_indices: list[int]) -> str:
    if not positive_indices:
        return "<empty>"
    return _clean(feature_rows[positive_indices[0]].get("quota_book"))


def _score_predictions(
    *,
    candidate_id: str,
    preds: np.ndarray,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    baseline_hit1 = candidate_hit1 = 0
    gain = loss = 0
    baseline_hit5 = candidate_hit5 = 0
    hit5_gain = hit5_loss = 0
    reciprocal_ranks: list[float] = []
    slice_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
    fallback_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = preds[start:stop]
        baseline_order = np.arange(size)
        candidate_order = np.lexsort((np.arange(size), -group_preds))
        baseline_rank = _first_positive_rank(group_labels, baseline_order)
        candidate_rank = _first_positive_rank(group_labels, candidate_order)
        base_h1 = bool(baseline_rank == 1)
        cand_h1 = bool(candidate_rank == 1)
        base_h5 = bool(baseline_rank is not None and baseline_rank <= 5)
        cand_h5 = bool(candidate_rank is not None and candidate_rank <= 5)
        baseline_hit1 += int(base_h1)
        candidate_hit1 += int(cand_h1)
        baseline_hit5 += int(base_h5)
        candidate_hit5 += int(cand_h5)
        gain_flag = (not base_h1) and cand_h1
        loss_flag = base_h1 and not cand_h1
        gain += int(gain_flag)
        loss += int(loss_flag)
        hit5_gain += int((not base_h5) and cand_h5)
        hit5_loss += int(base_h5 and not cand_h5)
        if candidate_rank is not None:
            reciprocal_ranks.append(1.0 / candidate_rank)

        local_top_idx = int(candidate_order[0])
        global_top_idx = start + local_top_idx
        group_rows = feature_rows[start:stop]
        positive_indices = [idx for idx, label in enumerate(group_labels) if label > 0]
        group_meta = meta[group_idx]
        expected_book = _expected_book(group_rows, positive_indices)
        candidate_top_family = _clean(group_rows[local_top_idx].get("candidate_family"))
        candidate_top_book = _clean(group_rows[local_top_idx].get("quota_book"))
        dimensions = {
            "query_family": _clean(group_meta.get("query_family")),
            "top1_family": candidate_top_family,
            "source_file": _clean(group_meta.get("source_file")),
            "province": _clean(group_meta.get("province")),
            "book_and_rank_bucket": f"expected={expected_book};top1={candidate_top_book};{_rank_bucket(baseline_rank)}",
        }
        for dimension, key in dimensions.items():
            item = slice_stats[(dimension, key)]
            item["groups"] += 1
            item["baseline_hit1"] += int(base_h1)
            item["candidate_hit1"] += int(cand_h1)
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)

        row = {
            "candidate_id": candidate_id,
            "group_id": _clean(group_meta.get("group_id")),
            "query": _clean(group_meta.get("query")),
            "query_family": dimensions["query_family"],
            "source_file": dimensions["source_file"],
            "province": dimensions["province"],
            "baseline_hit1": base_h1,
            "candidate_hit1": cand_h1,
            "baseline_positive_rank": baseline_rank,
            "candidate_positive_rank": candidate_rank,
            "candidate_top_quota_id": _clean(feature_rows[global_top_idx].get("quota_id")),
            "candidate_top_family": candidate_top_family,
            "candidate_top_book": candidate_top_book,
            "candidate_top_score": round(float(group_preds[local_top_idx]), 8),
        }
        detail_rows.append(row)
        if gain_flag or loss_flag:
            flips.append({**row, "flip_type": "gain" if gain_flag else "loss"})
        fallback_rows.append(
            {
                "candidate_id": candidate_id,
                "group_id": row["group_id"],
                "baseline_hit1": base_h1,
                "raw_candidate_hit1": cand_h1,
                "candidate_override": local_top_idx != 0,
                "override_outcome": "gain" if gain_flag else "loss" if loss_flag else "neutral",
                "no_gate_relaxation": True,
            }
        )
        start = stop
    total = len(groups)
    slice_rows = [
        {
            "candidate_id": candidate_id,
            "slice_dimension": dimension,
            "slice_key": key,
            **values,
            "baseline_hit1_rate": round(values["baseline_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
            "candidate_hit1_rate": round(values["candidate_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
        }
        for (dimension, key), values in slice_stats.items()
    ]
    metrics = {
        "candidate_id": candidate_id,
        "groups": total,
        "baseline_hit1": baseline_hit1,
        "candidate_hit1": candidate_hit1,
        "baseline_hit1_rate": round(baseline_hit1 / total, 6) if total else 0.0,
        "candidate_hit1_rate": round(candidate_hit1 / total, 6) if total else 0.0,
        "hit1_gain": gain,
        "hit1_loss": loss,
        "hit1_net": gain - loss,
        "baseline_hit5": baseline_hit5,
        "candidate_hit5": candidate_hit5,
        "baseline_hit5_rate": round(baseline_hit5 / total, 6) if total else 0.0,
        "candidate_hit5_rate": round(candidate_hit5 / total, 6) if total else 0.0,
        "hit5_gain": hit5_gain,
        "hit5_loss": hit5_loss,
        "hit5_net": hit5_gain - hit5_loss,
        "candidate_mrr": round(float(np.mean(reciprocal_ranks)), 6) if reciprocal_ranks else 0.0,
    }
    return metrics, slice_rows, fallback_rows, flips


def _train_oof_candidate(
    *,
    candidate: dict[str, Any],
    df: pd.DataFrame,
    labels: np.ndarray,
    groups: list[int],
    features: list[str],
    folds: int,
    num_boost_round: int,
    seed: int,
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    preds = np.zeros(len(labels), dtype=np.float32)
    fold_rows: list[dict[str, Any]] = []
    importance_counter: Counter[str] = Counter()
    group_indices = list(range(len(groups)))
    fold_assignments = _fold_assignments(len(groups), folds)
    params = _objective_params(candidate["objective_variant"], seed)
    for fold_idx, valid_group_indices in enumerate(fold_assignments):
        valid_set = set(valid_group_indices)
        train_group_indices = [idx for idx in group_indices if idx not in valid_set]
        train_df, train_y, train_groups, _ = _take_groups(df, labels, groups, train_group_indices)
        valid_df, valid_y, valid_groups, valid_row_indices = _take_groups(df, labels, groups, valid_group_indices)
        train_weights = _group_weights(candidate["objective_variant"], train_y, train_groups)
        train_data = lgb.Dataset(
            train_df[features].astype(np.float32).to_numpy(),
            label=train_y,
            group=train_groups,
            weight=train_weights,
            feature_name=features,
            free_raw_data=False,
        )
        booster = lgb.train(
            params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[train_data],
            valid_names=["dev_train"],
            callbacks=[],
        )
        preds[valid_row_indices] = booster.predict(valid_df[features].astype(np.float32).to_numpy(), num_iteration=booster.current_iteration())
        for feature, gain in zip(features, booster.feature_importance(importance_type="gain"), strict=True):
            importance_counter[feature] += float(gain)
        fold_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "fold": fold_idx,
                "train_groups": len(train_group_indices),
                "valid_groups": len(valid_group_indices),
                "train_rows": int(sum(train_groups)),
                "valid_rows": int(sum(valid_groups)),
                "num_boost_round": num_boost_round,
                "feature_count": len(features),
            }
        )
    importance_rows = [
        {"candidate_id": candidate["candidate_id"], "feature": feature, "gain_sum": round(gain, 6)}
        for feature, gain in importance_counter.most_common(30)
    ]
    return preds, fold_rows, importance_rows


def _leakage_rows(candidate_matrix: list[dict[str, Any]], all_features: list[str]) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    any_fail = False
    for candidate in candidate_matrix:
        features = _candidate_features(candidate["feature_toggle"], all_features)
        forbidden_present = sorted(set(features) & FORBIDDEN_FEATURE_KEYS)
        status = "fail" if forbidden_present else "pass"
        any_fail = any_fail or bool(forbidden_present)
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "feature_toggle": candidate["feature_toggle"],
                "feature_count": len(features),
                "excluded_feature_count": len(all_features) - len(features),
                "forbidden_feature_present": "|".join(forbidden_present),
                "status": status,
                "decision": "block_execution" if status == "fail" else "allow_dev_oof_training",
            }
        )
    return rows, not any_fail


def _recall_boundary_rows(candidate_matrix: list[dict[str, Any]], groups: list[int], matrix_summary: dict[str, Any]) -> list[dict[str, Any]]:
    dev_summary = next((item for item in matrix_summary.get("splits", []) if item.get("split") == "dev"), {})
    eligible_anchor_rows = int(dev_summary.get("eligible_anchor_rows") or len(groups))
    top80_present_groups = len(groups)
    top80_missing_groups = int(dev_summary.get("recall_gap_groups") or max(0, eligible_anchor_rows - top80_present_groups))
    return [
        {
            "candidate_id": candidate["candidate_id"],
            "split": "dev",
            "top80_present_groups": top80_present_groups,
            "top80_missing_groups": top80_missing_groups,
            "top80_recall_rate": round(top80_present_groups / eligible_anchor_rows, 6) if eligible_anchor_rows else 0.0,
            "ranking_claim_scope": "top80_present_only",
            "recall_missing_claim": "unchanged_not_claimed",
        }
        for candidate in candidate_matrix
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    top_rows = report["top_candidates"][:10]
    lines = [
        "# Stage 10.x S2 Dev/OOF Offline Ranking Experiment Execution",
        "",
        "Explicitly authorized dev/OOF-only execution from locked 10.6 scope. Heldout/hard are not used for selection, and no online GoalSearcher or ranking code is changed.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", metrics["candidate_count"]],
                ["fold_count", metrics["fold_count"]],
                ["leakage_gate_passed", metrics["leakage_gate_passed"]],
                ["best_candidate_id", metrics["best_candidate_id"]],
                ["best_hit1_net", metrics["best_hit1_net"]],
                ["best_hit1_loss", metrics["best_hit1_loss"]],
                ["approval_candidate_count", metrics["approval_candidate_count"]],
                ["heldout_used_for_selection", metrics["heldout_used_for_selection"]],
            ]
        ),
        "",
        "## Top Candidates",
        "",
        _md_table(
            [["rank", "candidate_id", "hit1_net", "hit1_gain", "hit1_loss", "candidate_hit1_rate", "approval_status"]]
            + [
                [
                    idx + 1,
                    row["candidate_id"],
                    row["hit1_net"],
                    row["hit1_gain"],
                    row["hit1_loss"],
                    row["candidate_hit1_rate"],
                    row["approval_status"],
                ]
                for idx, row in enumerate(top_rows)
            ]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute locked S2 offline ranking experiment on dev/OOF only")
    parser.add_argument("--scope-lock-summary", default=str(DEFAULT_SCOPE_LOCK))
    parser.add_argument("--frozen-plan", default=str(DEFAULT_FROZEN_PLAN))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--matrix-summary", default=str(DEFAULT_MATRIX_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dev-oof-only", action="store_true", required=True)
    parser.add_argument("--no-heldout-selection", action="store_true", required=True)
    parser.add_argument("--emit-loss-audit", action="store_true", required=True)
    parser.add_argument("--emit-leakage-report", action="store_true", required=True)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260523)
    args = parser.parse_args()

    started = time.perf_counter()
    scope_lock = _read_json(Path(args.scope_lock_summary))
    frozen_plan = _read_json(Path(args.frozen_plan))
    matrix_summary = _read_json(Path(args.matrix_summary))
    candidate_matrix = list(scope_lock.get("candidate_matrix", []))
    if not candidate_matrix:
        raise ValueError("scope lock summary missing candidate_matrix")
    all_features = _load_training_features(Path(args.data_dir) / "ltr_feature_whitelist_query_anchored_v1.json")
    df, labels, groups, meta, feature_rows = _load_dev_matrix(Path(args.data_dir), all_features)
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_execution_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_execution_summary.md")),
        "candidate_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.csv")),
        "candidate_scorecard_json": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.json")),
        "loss_audit_by_slice_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_by_slice.csv")),
        "leakage_gate_report_json": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.json")),
        "leakage_gate_report_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.csv")),
        "fallback_contract_report_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.csv")),
        "fallback_contract_report_md": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.md")),
        "recall_boundary_report_csv": str(output_prefix.with_name(output_prefix.name + "_recall_boundary_report.csv")),
        "recall_boundary_report_json": str(output_prefix.with_name(output_prefix.name + "_recall_boundary_report.json")),
        "fold_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_fold_manifest.csv")),
        "feature_importance_csv": str(output_prefix.with_name(output_prefix.name + "_feature_importance.csv")),
        "hit1_flips_jsonl": str(output_prefix.with_name(output_prefix.name + "_hit1_flips.jsonl")),
    }

    leakage_rows, leakage_passed = _leakage_rows(candidate_matrix, all_features)
    _write_csv(Path(artifacts["leakage_gate_report_csv"]), leakage_rows, ["candidate_id", "feature_toggle", "feature_count", "excluded_feature_count", "forbidden_feature_present", "status", "decision"])
    _write_json(
        Path(artifacts["leakage_gate_report_json"]),
        {
            "leakage_gate_passed": leakage_passed,
            "forbidden_feature_keys": sorted(FORBIDDEN_FEATURE_KEYS),
            "candidate_count": len(candidate_matrix),
            "rows": leakage_rows,
        },
    )
    if not leakage_passed:
        raise RuntimeError("leakage gate failed; execution stopped before training")

    scorecard_rows: list[dict[str, Any]] = []
    loss_audit_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    flip_rows: list[dict[str, Any]] = []
    fold_manifest_rows: list[dict[str, Any]] = []
    feature_importance_rows: list[dict[str, Any]] = []

    for idx, candidate in enumerate(candidate_matrix, start=1):
        features = _candidate_features(candidate["feature_toggle"], all_features)
        preds, fold_rows, importance_rows = _train_oof_candidate(
            candidate=candidate,
            df=df,
            labels=labels,
            groups=groups,
            features=features,
            folds=args.folds,
            num_boost_round=args.num_boost_round,
            seed=args.seed + idx,
        )
        metrics, candidate_loss_rows, candidate_fallback_rows, candidate_flips = _score_predictions(
            candidate_id=candidate["candidate_id"],
            preds=preds,
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
        )
        approval_status = "pass_dev_oof_candidate" if metrics["hit1_loss"] <= 18 and metrics["hit1_net"] > 48 else "hold_for_review"
        scorecard_rows.append(
            {
                **candidate,
                "feature_count": len(features),
                **metrics,
                "loss_budget_pass": metrics["hit1_loss"] <= 18,
                "net_gain_above_selected_gate": metrics["hit1_net"] > 48,
                "approval_status": approval_status,
                "selection_source": "dev_oof_only",
                "heldout_used_for_selection": False,
            }
        )
        loss_audit_rows.extend(candidate_loss_rows)
        fallback_rows.extend(candidate_fallback_rows)
        flip_rows.extend(candidate_flips)
        fold_manifest_rows.extend(fold_rows)
        feature_importance_rows.extend(importance_rows)

    scorecard_rows.sort(key=lambda row: (int(row["hit1_net"]), -int(row["hit1_loss"]), float(row["candidate_hit1_rate"])), reverse=True)
    for rank, row in enumerate(scorecard_rows, start=1):
        row["scorecard_rank"] = rank
    approved_rows = [row for row in scorecard_rows if row["approval_status"] == "pass_dev_oof_candidate"]
    best_row = scorecard_rows[0]
    recall_rows = _recall_boundary_rows(candidate_matrix, groups, matrix_summary)

    _write_csv(
        Path(artifacts["candidate_scorecard_csv"]),
        scorecard_rows,
        [
            "scorecard_rank",
            "candidate_id",
            "objective_variant",
            "feature_toggle",
            "role",
            "feature_count",
            "groups",
            "baseline_hit1",
            "candidate_hit1",
            "baseline_hit1_rate",
            "candidate_hit1_rate",
            "hit1_gain",
            "hit1_loss",
            "hit1_net",
            "baseline_hit5",
            "candidate_hit5",
            "baseline_hit5_rate",
            "candidate_hit5_rate",
            "hit5_gain",
            "hit5_loss",
            "hit5_net",
            "candidate_mrr",
            "loss_budget_pass",
            "net_gain_above_selected_gate",
            "approval_status",
            "selection_source",
            "heldout_used_for_selection",
        ],
    )
    _write_json(Path(artifacts["candidate_scorecard_json"]), {"candidate_scorecard": scorecard_rows})
    _write_csv(
        Path(artifacts["loss_audit_by_slice_csv"]),
        loss_audit_rows,
        ["candidate_id", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"],
    )
    _write_csv(
        Path(artifacts["fallback_contract_report_csv"]),
        fallback_rows,
        ["candidate_id", "group_id", "baseline_hit1", "raw_candidate_hit1", "candidate_override", "override_outcome", "no_gate_relaxation"],
    )
    _write_jsonl(Path(artifacts["hit1_flips_jsonl"]), flip_rows)
    _write_csv(Path(artifacts["recall_boundary_report_csv"]), recall_rows, ["candidate_id", "split", "top80_present_groups", "top80_missing_groups", "top80_recall_rate", "ranking_claim_scope", "recall_missing_claim"])
    _write_json(Path(artifacts["recall_boundary_report_json"]), {"recall_boundary": recall_rows})
    _write_csv(Path(artifacts["fold_manifest_csv"]), fold_manifest_rows, ["candidate_id", "fold", "train_groups", "valid_groups", "train_rows", "valid_rows", "num_boost_round", "feature_count"])
    _write_csv(Path(artifacts["feature_importance_csv"]), feature_importance_rows, ["candidate_id", "feature", "gain_sum"])
    Path(artifacts["fallback_contract_report_md"]).write_text(
        "\n".join(
            [
                "# S2 Dev/OOF Fallback Contract Report",
                "",
                "No safety gate is relaxed and no online fallback behavior is changed. This report records raw candidate override outcomes against the baseline top1 order.",
                "",
                _md_table(
                    [["metric", "value"], ["fallback_rows", len(fallback_rows)], ["no_gate_relaxation", True], ["heldout_used_for_selection", False]]
                ),
            ]
        ),
        encoding="utf-8",
    )

    metrics = {
        "candidate_count": len(candidate_matrix),
        "fold_count": args.folds,
        "dev_groups": len(groups),
        "dev_rows": int(sum(groups)),
        "leakage_gate_passed": leakage_passed,
        "best_candidate_id": best_row["candidate_id"],
        "best_hit1_net": best_row["hit1_net"],
        "best_hit1_gain": best_row["hit1_gain"],
        "best_hit1_loss": best_row["hit1_loss"],
        "best_candidate_hit1_rate": best_row["candidate_hit1_rate"],
        "approval_candidate_count": len(approved_rows),
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "online_integration": False,
        "goal_searcher_changed": False,
        "feature_whitelist_changed": False,
        "threshold_changed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / S2 dev/OOF-only offline ranking experiment execution",
        "explicit_user_go": True,
        "dev_oof_only": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "offline_training_executed": True,
        "no_online_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "no_threshold_change": True,
        "source_artifacts": {
            "scope_lock_summary": str(Path(args.scope_lock_summary)),
            "frozen_plan": str(Path(args.frozen_plan)),
            "matrix_summary": str(Path(args.matrix_summary)),
            "data_dir": str(Path(args.data_dir)),
        },
        "metrics": metrics,
        "top_candidates": scorecard_rows[:10],
        "approval_candidates": approved_rows,
        "artifacts": artifacts,
        "decision": (
            "Executed the locked 10.6 S2 offline ranking experiment on dev OOF only. Candidate selection evidence is limited to dev OOF scorecard, "
            "loss slices, leakage report, fallback report, and recall-boundary report. Heldout/hard were not used for selection, and no online "
            "ranking or GoalSearcher implementation was changed."
        ),
        "anti_drift_conclusion": (
            "This execution trains offline OOF candidate models only from the locked matrix. It does not use heldout/hard for selection, does not tune "
            "thresholds, does not relax gates, does not edit the feature whitelist, does not patch rules, does not modify GoalSearcher, and does not connect online."
        ),
        "frozen_plan_stage": frozen_plan.get("stage"),
    }
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "best_candidate": best_row}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
