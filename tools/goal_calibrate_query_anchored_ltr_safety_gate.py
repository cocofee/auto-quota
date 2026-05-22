from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb

import goal_eval_query_anchored_ltr_safety_gate_whatif as whatif

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_TRIAL_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial_summary.json"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial" / "goal_query_anchored_ltr_dev_trial.txt"
DEFAULT_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_query_anchored_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_calibration_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_calibration_summary.md"
DEFAULT_CALIBRATION_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_calibration_variants.csv"
DEFAULT_EVAL_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_calibration_eval_variants.csv"
DEFAULT_DETAILS_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_calibration_details.jsonl"
DEFAULT_FROZEN_CONFIG = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_frozen_candidate.json"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(value) for value in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _variant_by_name(variants: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for variant in variants:
        if variant["name"] == name:
            return variant
    raise ValueError(f"variant not found: {name}")


def _row_by_variant(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    for row in rows:
        if row.get("variant") == variant:
            return row
    raise ValueError(f"variant row not found: {variant}")


def _evaluate_variants(
    *,
    split: str,
    variants: list[dict[str, Any]],
    data_dir: Path,
    features: list[str],
    booster: lgb.Booster,
    trial_eval_by_split: dict[str, dict[str, Any]],
    detail_mode: str,
    detail_handle,
) -> list[dict[str, Any]]:
    x, labels, groups, meta, feature_rows = whatif._load_split(data_dir, split, features)
    preds = booster.predict(x, num_iteration=booster.current_iteration())
    split_summary = trial_eval_by_split.get(split, {})
    eligible_rows = int(split_summary.get("eligible_anchor_rows") or len(groups))
    recall_gap_groups = int(split_summary.get("recall_gap_groups") or max(0, eligible_rows - len(groups)))
    summaries, _bucket_source_rows = whatif._evaluate_split(
        split=split,
        labels=labels,
        groups=groups,
        meta=meta,
        feature_rows=feature_rows,
        preds=preds,
        variants=variants,
        eligible_rows=eligible_rows,
        recall_gap_groups=recall_gap_groups,
        detail_mode=detail_mode,
        detail_handle=detail_handle,
    )
    return summaries


def _select_gate(
    *,
    calibration_rows: list[dict[str, Any]],
    variants: list[dict[str, Any]],
    allowed_modes: set[str],
    min_net_retention: float,
    max_loss_increase: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = _row_by_variant(calibration_rows, "raw_ltr")
    raw_net = int(raw["raw_ltr_hit1_net"])
    raw_loss = int(raw["raw_ltr_hit1_loss"])
    min_net = math.ceil(max(0, raw_net) * min_net_retention)
    rows_by_variant = {row["variant"]: row for row in calibration_rows}
    candidates: list[dict[str, Any]] = []

    for variant in variants:
        if variant["name"] in {"baseline_only", "raw_ltr"}:
            continue
        if variant["mode"] not in allowed_modes:
            continue
        row = rows_by_variant.get(variant["name"])
        if not row:
            continue
        row = dict(row)
        row["selection_meets_net_retention"] = int(row["gated_hit1_net"]) >= min_net
        row["selection_meets_loss_limit"] = int(row["gated_hit1_loss"]) <= raw_loss + max_loss_increase
        row["selection_variant_mode"] = variant["mode"]
        candidates.append(row)

    eligible = [
        row
        for row in candidates
        if row["selection_meets_net_retention"]
        and row["selection_meets_loss_limit"]
        and int(row["gated_hit1_net"]) > 0
    ]
    selection_pool = eligible or [row for row in candidates if int(row["gated_hit1_net"]) > 0] or candidates
    if not selection_pool:
        raise ValueError("no safety gate candidates available")

    selected_row = max(
        selection_pool,
        key=lambda row: (
            int(row["selection_meets_loss_limit"]),
            int(row["selection_meets_net_retention"]),
            int(row["gated_hit1_net"]),
            float(row["gated_hit1_rate_eligible"]),
            -int(row["gated_hit1_loss"]),
            -float(row["gated_override_rate"]),
        ),
    )
    selected_variant = _variant_by_name(variants, selected_row["variant"])
    selection = {
        "selected_gate": selected_variant,
        "selected_metrics": selected_row,
        "raw_calibration_metrics": raw,
        "selection_rule": {
            "allowed_modes": sorted(allowed_modes),
            "min_net_retention": min_net_retention,
            "required_min_net": min_net,
            "max_loss_increase_vs_raw": max_loss_increase,
            "excluded_variants": ["baseline_only", "raw_ltr"],
            "sort_order": "loss_limit, net_retention, gated_hit1_net, gated_hit1_rate, lower_loss, lower_override_rate",
        },
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "selected_from_fallback_pool": not bool(eligible),
    }
    return selected_variant, selection


def _calibration_warnings(
    *,
    calibration_split: str,
    trial_summary: dict[str, Any],
    raw_calibration_metrics: dict[str, Any],
    min_observed_losses: int,
) -> list[str]:
    warnings: list[str] = []
    train_split = _clean(trial_summary.get("train_split"))
    if train_split and calibration_split == train_split:
        warnings.append("calibration_split_is_model_training_split")
    raw_loss = int(raw_calibration_metrics.get("raw_ltr_hit1_loss") or 0)
    if raw_loss < min_observed_losses:
        warnings.append("insufficient_raw_ltr_losses_on_calibration_for_loss_saving_estimate")
    if raw_loss == 0:
        warnings.append("dev_trained_model_has_zero_calibration_loss_threshold_is_diagnostic")
    return warnings


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    selected = report["selection"]["selected_gate"]
    selected_metrics = report["selection"]["selected_metrics"]
    eval_rows = report["eval_summaries"]
    lines = [
        "# Goal Query-Anchored LTR Safety Gate Calibration",
        "",
        "Stage 7.0 calibrates a frozen safety-gate candidate using dev/calibration only, then evaluates that frozen candidate on heldout/hard once. No model training, no search integration, no rerank switch.",
        "",
        "## Frozen Candidate",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_gate", selected["name"]],
                ["mode", selected["mode"]],
                ["margin", selected.get("margin")],
                ["calibration_split", report["calibration_split"]],
                ["calibration_top1_all", selected_metrics["gated_hit1_rate_eligible"]],
                ["calibration_net", selected_metrics["gated_hit1_net"]],
                ["calibration_gain_loss", f'{selected_metrics["gated_hit1_gain"]}/{selected_metrics["gated_hit1_loss"]}'],
                ["warnings", "; ".join(report["calibration_warnings"]) or "<none>"],
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
        "- Heldout/hard rows above were not used for selecting the gate.",
        "- The current calibration split is also the model training split, so the frozen candidate is diagnostic rather than production-ready.",
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.0 calibrate query-anchored LTR safety gate on dev/calibration only")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--trial-summary", default=str(DEFAULT_TRIAL_SUMMARY))
    parser.add_argument("--calibration-split", default="dev")
    parser.add_argument("--eval-splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--margins", nargs="+", type=float, default=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--allowed-modes", default="strict_strong,strong_or_margin,guarded_margin")
    parser.add_argument("--min-net-retention", type=float, default=0.60)
    parser.add_argument("--max-loss-increase", type=int, default=0)
    parser.add_argument("--min-observed-losses", type=int, default=5)
    parser.add_argument("--detail-mode", choices=["events", "all"], default="events")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--calibration-csv", default=str(DEFAULT_CALIBRATION_CSV))
    parser.add_argument("--eval-csv", default=str(DEFAULT_EVAL_CSV))
    parser.add_argument("--details-jsonl", default=str(DEFAULT_DETAILS_JSONL))
    parser.add_argument("--frozen-config", default=str(DEFAULT_FROZEN_CONFIG))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    features = whatif._load_features(Path(args.feature_whitelist))
    trial_summary = _read_json(Path(args.trial_summary))
    trial_eval_by_split = {item.get("split"): item for item in trial_summary.get("evaluations", [])}
    booster = lgb.Booster(model_file=str(Path(args.model)))
    all_variants = whatif._make_variants(args.margins)
    allowed_modes = {_clean(item) for item in args.allowed_modes.split(",") if _clean(item)}

    details_path = Path(args.details_jsonl)
    details_path.parent.mkdir(parents=True, exist_ok=True)
    with details_path.open("w", encoding="utf-8") as detail_handle:
        calibration_rows = _evaluate_variants(
            split=args.calibration_split,
            variants=all_variants,
            data_dir=data_dir,
            features=features,
            booster=booster,
            trial_eval_by_split=trial_eval_by_split,
            detail_mode=args.detail_mode,
            detail_handle=detail_handle,
        )
        selected_variant, selection = _select_gate(
            calibration_rows=calibration_rows,
            variants=all_variants,
            allowed_modes=allowed_modes,
            min_net_retention=args.min_net_retention,
            max_loss_increase=args.max_loss_increase,
        )
        calibration_warnings = _calibration_warnings(
            calibration_split=args.calibration_split,
            trial_summary=trial_summary,
            raw_calibration_metrics=selection["raw_calibration_metrics"],
            min_observed_losses=args.min_observed_losses,
        )
        eval_variants = [
            _variant_by_name(all_variants, "baseline_only"),
            _variant_by_name(all_variants, "raw_ltr"),
            selected_variant,
        ]
        eval_summaries: list[dict[str, Any]] = []
        for split in args.eval_splits:
            eval_summaries.extend(
                _evaluate_variants(
                    split=split,
                    variants=eval_variants,
                    data_dir=data_dir,
                    features=features,
                    booster=booster,
                    trial_eval_by_split=trial_eval_by_split,
                    detail_mode=args.detail_mode,
                    detail_handle=detail_handle,
                )
            )

    frozen_config = {
        "stage": "Goal LTR v1 / stage 7.0 frozen safety gate candidate",
        "eval_only": True,
        "no_training": True,
        "no_search_integration": True,
        "selected_on": args.calibration_split,
        "selected_gate": selected_variant,
        "selection_rule": selection["selection_rule"],
        "calibration_warnings": calibration_warnings,
        "production_ready": False if calibration_warnings else True,
        "notes": [
            "Do not wire this into GoalSearcher by default.",
            "Heldout/hard were used only after the gate was selected.",
        ],
    }
    _write_json(Path(args.frozen_config), frozen_config)

    report = {
        "stage": "Goal LTR v1 / stage 7.0 safety gate calibration",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "data_dir": str(data_dir),
        "model_path": str(Path(args.model)),
        "feature_whitelist": str(Path(args.feature_whitelist)),
        "trial_summary": str(Path(args.trial_summary)),
        "calibration_split": args.calibration_split,
        "eval_splits": args.eval_splits,
        "allowed_modes": sorted(allowed_modes),
        "selection": selection,
        "calibration_warnings": calibration_warnings,
        "production_ready": frozen_config["production_ready"],
        "calibration_summaries": calibration_rows,
        "eval_summaries": eval_summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "recommended_next_stage": "Stage 7.1: create a real OOF/calibration prediction set from dev before production gating, because current dev calibration has zero raw LTR losses.",
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "calibration_variants_csv": str(Path(args.calibration_csv)),
            "eval_variants_csv": str(Path(args.eval_csv)),
            "frozen_config": str(Path(args.frozen_config)),
            "details_jsonl": str(details_path),
        },
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.calibration_csv), calibration_rows, whatif._variant_fields())
    _write_csv(Path(args.eval_csv), eval_summaries, whatif._variant_fields())

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": report["eval_only"],
                    "no_training": report["no_training"],
                    "no_search_integration": report["no_search_integration"],
                    "selected_gate": selected_variant,
                    "calibration_warnings": calibration_warnings,
                    "production_ready": report["production_ready"],
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
