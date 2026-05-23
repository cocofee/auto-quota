from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

import goal_eval_query_anchored_ltr_safety_gate_whatif as whatif
from goal_calibrate_query_anchored_ltr_safety_gate import _md_table, _write_csv, _write_json, _variant_by_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_TRIAL_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial_summary.json"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial" / "goal_query_anchored_ltr_dev_trial.txt"
DEFAULT_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_query_anchored_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.md"
DEFAULT_FROZEN_CONFIG = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_frozen_candidate.json"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _leak_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            _clean(row.get("source_file")),
            _clean(row.get("project_name")),
            _clean(row.get("sample_id")),
        ]
    )


def _group_row_ranges(groups: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for size in groups:
        stop = start + size
        ranges.append((start, stop))
        start = stop
    return ranges


def _flatten_group_rows(group_indices: list[int], ranges: list[tuple[int, int]]) -> list[int]:
    rows: list[int] = []
    for group_idx in group_indices:
        start, stop = ranges[group_idx]
        rows.extend(range(start, stop))
    return rows


def _make_folds(meta: list[dict[str, Any]], groups: list[int], folds: int) -> tuple[list[list[int]], list[dict[str, Any]]]:
    grouped: dict[str, list[int]] = {}
    for idx, row in enumerate(meta):
        grouped.setdefault(_leak_key(row), []).append(idx)

    fold_groups: list[list[int]] = [[] for _ in range(folds)]
    fold_row_counts = [0 for _ in range(folds)]
    keys = sorted(grouped, key=_stable_digest)
    for key in keys:
        target = min(range(folds), key=lambda fold: (fold_row_counts[fold], len(fold_groups[fold]), fold))
        indices = grouped[key]
        fold_groups[target].extend(indices)
        fold_row_counts[target] += sum(groups[idx] for idx in indices)

    for fold in range(folds):
        fold_groups[fold].sort()

    assignment_rows: list[dict[str, Any]] = []
    for fold, group_indices in enumerate(fold_groups, start=1):
        for group_idx in group_indices:
            row = meta[group_idx]
            assignment_rows.append(
                {
                    "fold": fold,
                    "group_index": group_idx + 1,
                    "group_id": _clean(row.get("group_id")),
                    "leak_key": _leak_key(row),
                    "sample_id": _clean(row.get("sample_id")),
                    "source_file": _clean(row.get("source_file")),
                    "project_name": _clean(row.get("project_name")),
                    "province": _clean(row.get("province")),
                    "query_family": _clean(row.get("query_family")) or "<empty>",
                    "rows": groups[group_idx],
                    "positive_count": row.get("positive_count"),
                    "positive_rank": row.get("positive_rank"),
                }
            )
    return fold_groups, assignment_rows


def _train_oof_models(
    *,
    x,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    features: list[str],
    fold_groups: list[list[int]],
    params: dict[str, Any],
    num_boost_round: int,
    output_dir: Path,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    ranges = _group_row_ranges(groups)
    all_group_ids = list(range(len(groups)))
    oof_preds = np.full(len(labels), np.nan, dtype=np.float64)
    fold_rows: list[dict[str, Any]] = []
    model_dir = output_dir / "fold_models"
    model_dir.mkdir(parents=True, exist_ok=True)

    for fold_idx, validation_group_ids in enumerate(fold_groups, start=1):
        validation_set = set(validation_group_ids)
        train_group_ids = [idx for idx in all_group_ids if idx not in validation_set]
        train_rows = _flatten_group_rows(train_group_ids, ranges)
        validation_rows = _flatten_group_rows(validation_group_ids, ranges)
        train_group_sizes = [groups[idx] for idx in train_group_ids]

        fold_params = dict(params)
        fold_seed = int(params.get("seed") or 20260522) + fold_idx
        for key in ("seed", "feature_fraction_seed", "bagging_seed", "data_random_seed"):
            fold_params[key] = fold_seed

        train_data = lgb.Dataset(
            x.iloc[train_rows],
            label=labels[train_rows],
            group=train_group_sizes,
            feature_name=features,
            free_raw_data=False,
        )
        booster = lgb.train(
            fold_params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[train_data],
            valid_names=[f"fold_{fold_idx}_train"],
            callbacks=[lgb.log_evaluation(period=0)],
        )
        fold_pred = booster.predict(x.iloc[validation_rows], num_iteration=booster.current_iteration())
        oof_preds[validation_rows] = fold_pred
        model_path = model_dir / f"goal_query_anchored_ltr_oof_fold_{fold_idx}.txt"
        booster.save_model(str(model_path))
        fold_rows.append(
            {
                "fold": fold_idx,
                "train_groups": len(train_group_ids),
                "calibration_groups": len(validation_group_ids),
                "train_rows": len(train_rows),
                "calibration_rows": len(validation_rows),
                "model_path": str(model_path),
                "seed": fold_seed,
                "source_file_count": len({_clean(meta[idx].get("source_file")) for idx in validation_group_ids}),
            }
        )

    if np.isnan(oof_preds).any():
        missing = int(np.isnan(oof_preds).sum())
        raise ValueError(f"OOF prediction has {missing} missing rows")
    return oof_preds, fold_rows


def _write_oof_predictions(
    path: Path,
    *,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    preds: np.ndarray,
    fold_groups: list[list[int]],
) -> None:
    group_to_fold: dict[int, int] = {}
    for fold, group_indices in enumerate(fold_groups, start=1):
        for group_idx in group_indices:
            group_to_fold[group_idx] = fold

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "fold",
        "group_index",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query_family",
        "candidate_rank",
        "quota_id",
        "label",
        "oof_ltr_score",
    ]
    ranges = _group_row_ranges(groups)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for group_idx, (start, stop) in enumerate(ranges):
            group_meta = meta[group_idx]
            for row_idx in range(start, stop):
                feature_row = feature_rows[row_idx]
                writer.writerow(
                    {
                        "fold": group_to_fold[group_idx],
                        "group_index": group_idx + 1,
                        "group_id": _clean(group_meta.get("group_id")),
                        "sample_id": _clean(group_meta.get("sample_id")),
                        "source_file": _clean(group_meta.get("source_file")),
                        "province": _clean(group_meta.get("province")),
                        "query_family": _clean(group_meta.get("query_family")) or "<empty>",
                        "candidate_rank": feature_row.get("candidate_rank"),
                        "quota_id": _clean(feature_row.get("quota_id")),
                        "label": int(labels[row_idx]),
                        "oof_ltr_score": round(float(preds[row_idx]), 8),
                    }
                )


def _select_gate_from_oof(
    *,
    calibration_rows: list[dict[str, Any]],
    variants: list[dict[str, Any]],
    allowed_modes: set[str],
    min_net_retention: float,
    min_loss_reduction: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = next(row for row in calibration_rows if row["variant"] == "raw_ltr")
    raw_net = int(raw["raw_ltr_hit1_net"])
    raw_loss = int(raw["raw_ltr_hit1_loss"])
    required_min_net = math.ceil(max(0, raw_net) * min_net_retention)
    required_max_loss = max(0, raw_loss - min_loss_reduction)
    rows_by_variant = {row["variant"]: row for row in calibration_rows}

    candidates: list[dict[str, Any]] = []
    for variant in variants:
        if variant["name"] in {"baseline_only", "raw_ltr"} or variant["mode"] not in allowed_modes:
            continue
        row = dict(rows_by_variant[variant["name"]])
        row["selection_variant_mode"] = variant["mode"]
        row["selection_loss_reduction"] = raw_loss - int(row["gated_hit1_loss"])
        row["selection_net_retention"] = round(int(row["gated_hit1_net"]) / raw_net, 6) if raw_net > 0 else 0.0
        row["selection_meets_net_retention"] = int(row["gated_hit1_net"]) >= required_min_net
        row["selection_meets_loss_reduction"] = int(row["gated_hit1_loss"]) <= required_max_loss
        candidates.append(row)

    strict_pool = [
        row
        for row in candidates
        if row["selection_meets_net_retention"]
        and row["selection_meets_loss_reduction"]
        and int(row["gated_hit1_net"]) > 0
    ]
    relaxed_pool = [
        row
        for row in candidates
        if row["selection_meets_net_retention"]
        and int(row["gated_hit1_loss"]) <= raw_loss
        and int(row["gated_hit1_net"]) > 0
    ]
    positive_pool = [row for row in candidates if int(row["gated_hit1_net"]) > 0]
    if strict_pool:
        pool = strict_pool
        pool_name = "strict_loss_reduction_and_net_retention"
    elif relaxed_pool:
        pool = relaxed_pool
        pool_name = "relaxed_no_loss_increase_and_net_retention"
    elif positive_pool:
        pool = positive_pool
        pool_name = "fallback_positive_net"
    else:
        pool = candidates
        pool_name = "fallback_any_candidate"

    selected_row = max(
        pool,
        key=lambda row: (
            int(row["selection_loss_reduction"]),
            int(row["gated_hit1_net"]),
            float(row["gated_hit1_rate_eligible"]),
            -int(row["blocked_raw_hit1_gain"]),
            -float(row["gated_override_rate"]),
        ),
    )
    selected_variant = _variant_by_name(variants, selected_row["variant"])
    selection = {
        "selected_gate": selected_variant,
        "selected_metrics": selected_row,
        "raw_oof_metrics": raw,
        "selection_rule": {
            "allowed_modes": sorted(allowed_modes),
            "min_net_retention": min_net_retention,
            "required_min_net": required_min_net,
            "min_loss_reduction": min_loss_reduction,
            "required_max_loss": required_max_loss,
            "excluded_variants": ["baseline_only", "raw_ltr"],
            "sort_order": "loss_reduction, gated_hit1_net, gated_hit1_rate, lower_blocked_gain, lower_override_rate",
        },
        "candidate_count": len(candidates),
        "strict_candidate_count": len(strict_pool),
        "relaxed_candidate_count": len(relaxed_pool),
        "selection_pool": pool_name,
    }
    return selected_variant, selection


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    selected = report["selection"]["selected_gate"]
    selected_metrics = report["selection"]["selected_metrics"]
    raw_oof = report["selection"]["raw_oof_metrics"]
    eval_rows = report["eval_summaries"]
    lines = [
        "# Goal Query-Anchored LTR OOF Safety Gate Calibration",
        "",
        "Stage 7.1 creates dev out-of-fold predictions for safety-gate calibration, freezes one candidate, then evaluates heldout/hard once. Fold models are calibration-only; no search integration and no rerank switch.",
        "",
        "## OOF Calibration",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["folds", report["folds"]],
                ["selected_gate", selected["name"]],
                ["mode", selected["mode"]],
                ["margin", selected.get("margin")],
                ["raw_oof_top1_all", raw_oof["gated_hit1_rate_eligible"]],
                ["raw_oof_gain_loss", f'{raw_oof["gated_hit1_gain"]}/{raw_oof["gated_hit1_loss"]}'],
                ["selected_oof_top1_all", selected_metrics["gated_hit1_rate_eligible"]],
                ["selected_oof_gain_loss", f'{selected_metrics["gated_hit1_gain"]}/{selected_metrics["gated_hit1_loss"]}'],
                ["loss_reduction", selected_metrics["selection_loss_reduction"]],
                ["selection_pool", report["selection"]["selection_pool"]],
                ["production_ready", report["production_ready"]],
            ]
        ),
        "",
        "## Frozen Eval",
        "",
        _md_table(
            [
                ["split", "variant", "top1_all", "net", "gain", "loss", "saved_loss", "blocked_gain", "top5_all", "override_rate"],
                *[
                    [
                        row["split"],
                        row["variant"],
                        row["gated_hit1_rate_eligible"],
                        row["gated_hit1_net"],
                        row["gated_hit1_gain"],
                        row["gated_hit1_loss"],
                        row["prevented_raw_hit1_loss"],
                        row["blocked_raw_hit1_gain"],
                        row["gated_hit5_rate_eligible"],
                        row["gated_override_rate"],
                    ]
                    for row in eval_rows
                ],
            ]
        ),
        "",
        "## Notes",
        "",
        "- Heldout/hard were not used for selecting the gate.",
        "- OOF predictions are from fold models that did not train on the validated dev group.",
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.1 dev OOF safety-gate calibration for query-anchored LTR")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--trial-summary", default=str(DEFAULT_TRIAL_SUMMARY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--num-boost-round", type=int, default=180)
    parser.add_argument("--margins", nargs="+", type=float, default=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--allowed-modes", default="strict_strong,strong_or_margin,guarded_margin")
    parser.add_argument("--min-net-retention", type=float, default=0.80)
    parser.add_argument("--min-loss-reduction", type=int, default=1)
    parser.add_argument("--eval-splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--detail-mode", choices=["events", "all"], default="events")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--frozen-config", default=str(DEFAULT_FROZEN_CONFIG))
    args = parser.parse_args()

    if args.folds < 2:
        raise ValueError("--folds must be >= 2")

    started = time.perf_counter()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir)
    features = whatif._load_features(Path(args.feature_whitelist))
    trial_summary = _read_json(Path(args.trial_summary))
    trial_eval_by_split = {item.get("split"): item for item in trial_summary.get("evaluations", [])}
    params = dict(trial_summary.get("params") or {})
    if not params:
        raise ValueError(f"{args.trial_summary} missing params")

    x, labels, groups, meta, feature_rows = whatif._load_split(data_dir, "dev", features)
    fold_groups, fold_assignment_rows = _make_folds(meta, groups, args.folds)
    oof_preds, fold_model_rows = _train_oof_models(
        x=x,
        labels=labels,
        groups=groups,
        meta=meta,
        features=features,
        fold_groups=fold_groups,
        params=params,
        num_boost_round=args.num_boost_round,
        output_dir=output_dir,
    )

    fold_assignment_csv = output_dir / "dev_oof_fold_assignments.csv"
    _write_csv(
        fold_assignment_csv,
        fold_assignment_rows,
        [
            "fold",
            "group_index",
            "group_id",
            "leak_key",
            "sample_id",
            "source_file",
            "project_name",
            "province",
            "query_family",
            "rows",
            "positive_count",
            "positive_rank",
        ],
    )
    fold_models_csv = output_dir / "dev_oof_fold_models.csv"
    _write_csv(
        fold_models_csv,
        fold_model_rows,
        ["fold", "train_groups", "calibration_groups", "train_rows", "calibration_rows", "model_path", "seed", "source_file_count"],
    )
    oof_predictions_csv = output_dir / "dev_oof_predictions.csv"
    _write_oof_predictions(
        oof_predictions_csv,
        labels=labels,
        groups=groups,
        meta=meta,
        feature_rows=feature_rows,
        preds=oof_preds,
        fold_groups=fold_groups,
    )

    variants = whatif._make_variants(args.margins)
    allowed_modes = {_clean(item) for item in args.allowed_modes.split(",") if _clean(item)}
    dev_eval = trial_eval_by_split.get("dev", {})
    eligible_rows = int(dev_eval.get("eligible_anchor_rows") or len(groups))
    recall_gap_groups = int(dev_eval.get("recall_gap_groups") or max(0, eligible_rows - len(groups)))
    oof_details_jsonl = output_dir / "dev_oof_safety_gate_details.jsonl"
    with oof_details_jsonl.open("w", encoding="utf-8") as handle:
        calibration_summaries, _bucket_rows = whatif._evaluate_split(
            split="dev_oof",
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
            preds=oof_preds,
            variants=variants,
            eligible_rows=eligible_rows,
            recall_gap_groups=recall_gap_groups,
            detail_mode=args.detail_mode,
            detail_handle=handle,
        )
    selected_variant, selection = _select_gate_from_oof(
        calibration_rows=calibration_summaries,
        variants=variants,
        allowed_modes=allowed_modes,
        min_net_retention=args.min_net_retention,
        min_loss_reduction=args.min_loss_reduction,
    )

    eval_details_jsonl = output_dir / "frozen_eval_details.jsonl"
    full_booster = lgb.Booster(model_file=str(Path(args.model)))
    eval_variants = [_variant_by_name(variants, "baseline_only"), _variant_by_name(variants, "raw_ltr"), selected_variant]
    eval_summaries: list[dict[str, Any]] = []
    with eval_details_jsonl.open("w", encoding="utf-8") as handle:
        for split in args.eval_splits:
            split_x, split_labels, split_groups, split_meta, split_feature_rows = whatif._load_split(data_dir, split, features)
            split_preds = full_booster.predict(split_x, num_iteration=full_booster.current_iteration())
            split_eval = trial_eval_by_split.get(split, {})
            split_eligible_rows = int(split_eval.get("eligible_anchor_rows") or len(split_groups))
            split_recall_gap_groups = int(split_eval.get("recall_gap_groups") or max(0, split_eligible_rows - len(split_groups)))
            summaries, _split_bucket_rows = whatif._evaluate_split(
                split=split,
                labels=split_labels,
                groups=split_groups,
                meta=split_meta,
                feature_rows=split_feature_rows,
                preds=split_preds,
                variants=eval_variants,
                eligible_rows=split_eligible_rows,
                recall_gap_groups=split_recall_gap_groups,
                detail_mode=args.detail_mode,
                detail_handle=handle,
            )
            eval_summaries.extend(summaries)

    selected_metrics = selection["selected_metrics"]
    raw_oof = selection["raw_oof_metrics"]
    warnings: list[str] = []
    if int(raw_oof["raw_ltr_hit1_loss"]) <= 0:
        warnings.append("oof_raw_ltr_has_zero_loss")
    if selection["selection_pool"] != "strict_loss_reduction_and_net_retention":
        warnings.append("selected_from_relaxed_or_fallback_pool")
    if int(selected_metrics["selection_loss_reduction"]) < args.min_loss_reduction:
        warnings.append("selected_gate_did_not_meet_min_loss_reduction")
    production_ready = not warnings

    frozen_config = {
        "stage": "Goal LTR v1 / stage 7.1 OOF frozen safety gate candidate",
        "eval_only": True,
        "oof_calibration_training_only": True,
        "no_search_integration": True,
        "selected_on": "dev_oof",
        "folds": args.folds,
        "selected_gate": selected_variant,
        "selection_rule": selection["selection_rule"],
        "warnings": warnings,
        "production_ready": production_ready,
        "notes": [
            "Fold models are for calibration only.",
            "Heldout/hard were used only after selecting the gate on dev OOF.",
            "Do not wire this into GoalSearcher by default.",
        ],
    }
    _write_json(Path(args.frozen_config), frozen_config)

    calibration_variants_csv = output_dir / "dev_oof_safety_gate_variants.csv"
    _write_csv(calibration_variants_csv, calibration_summaries, whatif._variant_fields())
    eval_variants_csv = output_dir / "frozen_eval_variants.csv"
    _write_csv(eval_variants_csv, eval_summaries, whatif._variant_fields())

    report = {
        "stage": "Goal LTR v1 / stage 7.1 OOF safety gate calibration",
        "eval_only": True,
        "oof_calibration_training_only": True,
        "no_search_integration": True,
        "data_dir": str(data_dir),
        "full_model_path": str(Path(args.model)),
        "feature_whitelist": str(Path(args.feature_whitelist)),
        "trial_summary": str(Path(args.trial_summary)),
        "folds": args.folds,
        "num_boost_round": args.num_boost_round,
        "allowed_modes": sorted(allowed_modes),
        "selection": selection,
        "warnings": warnings,
        "production_ready": production_ready,
        "fold_models": fold_model_rows,
        "calibration_summaries": calibration_summaries,
        "eval_summaries": eval_summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "recommended_next_stage": "Stage 7.2: audit OOF-selected gate residual loss/blocked gain before any eval-only switch integration.",
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "frozen_config": str(Path(args.frozen_config)),
            "fold_assignments_csv": str(fold_assignment_csv),
            "fold_models_csv": str(fold_models_csv),
            "oof_predictions_csv": str(oof_predictions_csv),
            "oof_calibration_variants_csv": str(calibration_variants_csv),
            "oof_details_jsonl": str(oof_details_jsonl),
            "frozen_eval_variants_csv": str(eval_variants_csv),
            "frozen_eval_details_jsonl": str(eval_details_jsonl),
        },
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": report["eval_only"],
                    "oof_calibration_training_only": report["oof_calibration_training_only"],
                    "no_search_integration": report["no_search_integration"],
                    "selected_gate": selected_variant,
                    "warnings": warnings,
                    "production_ready": production_ready,
                    "elapsed_sec": report["elapsed_sec"],
                    "recommended_next_stage": report["recommended_next_stage"],
                },
                "selection": selection,
                "eval_summaries": eval_summaries,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
