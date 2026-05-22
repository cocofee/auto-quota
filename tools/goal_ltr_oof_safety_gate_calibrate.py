from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

import goal_ltr_safety_gate_calibrate as gate_calibrate
import goal_ltr_safety_gate_eval as gate_eval

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_FULL_MODEL_OUTPUT = PROJECT_ROOT / "data" / "goal_search" / "goal_ltr_oof_full_dev_v1.txt"
DEFAULT_GATE_OUTPUT = PROJECT_ROOT / "data" / "goal_search" / "ltr_safety_gate_oof_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_oof_safety_gate_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_oof_safety_gate_summary.md"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_oof_safety_gate_variants.csv"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"


def _train_params(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 5, 10],
        "learning_rate": args.learning_rate,
        "num_leaves": args.num_leaves,
        "min_data_in_leaf": args.min_data_in_leaf,
        "feature_fraction": 0.90,
        "bagging_fraction": 0.90,
        "bagging_freq": 1,
        "label_gain": [0, 1],
        "verbosity": -1,
        "seed": args.seed,
        "feature_fraction_seed": args.seed,
        "bagging_seed": args.seed,
        "data_random_seed": args.seed,
        "num_threads": 0,
    }


def _group_slices(groups: list[int]) -> list[tuple[int, int]]:
    slices: list[tuple[int, int]] = []
    start = 0
    for size in groups:
        stop = start + size
        slices.append((start, stop))
        start = stop
    return slices


def _group_row_indices(slices: list[tuple[int, int]], group_ids: list[int]) -> np.ndarray:
    chunks = [np.arange(slices[group_id][0], slices[group_id][1], dtype=np.int64) for group_id in group_ids]
    return np.concatenate(chunks) if chunks else np.array([], dtype=np.int64)


def _assign_folds(group_count: int, folds: int, seed: int) -> list[int]:
    if folds < 2:
        raise ValueError("folds must be >= 2")
    if folds > group_count:
        raise ValueError(f"folds {folds} > group_count {group_count}")
    rng = np.random.default_rng(seed)
    shuffled = np.arange(group_count)
    rng.shuffle(shuffled)
    fold_ids = [-1] * group_count
    for fold_idx, fold_groups in enumerate(np.array_split(shuffled, folds)):
        for group_id in fold_groups.tolist():
            fold_ids[int(group_id)] = fold_idx
    if any(fold_id < 0 for fold_id in fold_ids):
        raise ValueError("fold assignment failed")
    return fold_ids


def _positive_group_count(labels: np.ndarray, slices: list[tuple[int, int]], group_ids: list[int]) -> int:
    return sum(int(np.any(labels[start:stop] > 0)) for start, stop in (slices[group_id] for group_id in group_ids))


def _train_booster(
    *,
    x,
    y: np.ndarray,
    row_indices: np.ndarray,
    train_groups: list[int],
    features: list[str],
    params: dict[str, Any],
    num_boost_round: int,
) -> lgb.Booster:
    data = lgb.Dataset(
        x.iloc[row_indices],
        label=y[row_indices],
        group=train_groups,
        feature_name=features,
        free_raw_data=False,
    )
    return lgb.train(
        params,
        data,
        num_boost_round=num_boost_round,
        valid_sets=[data],
        valid_names=["train"],
        callbacks=[lgb.log_evaluation(period=0)],
    )


def _build_oof_predictions(
    *,
    x,
    y: np.ndarray,
    groups: list[int],
    features: list[str],
    folds: int,
    params: dict[str, Any],
    num_boost_round: int,
    seed: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    slices = _group_slices(groups)
    fold_ids = _assign_folds(len(groups), folds, seed)
    preds = np.zeros(len(y), dtype=np.float64)
    fold_summaries: list[dict[str, Any]] = []

    for fold_idx in range(folds):
        train_group_ids = [idx for idx, assigned in enumerate(fold_ids) if assigned != fold_idx]
        valid_group_ids = [idx for idx, assigned in enumerate(fold_ids) if assigned == fold_idx]
        train_rows = _group_row_indices(slices, train_group_ids)
        valid_rows = _group_row_indices(slices, valid_group_ids)
        train_groups = [groups[idx] for idx in train_group_ids]

        started = time.perf_counter()
        booster = _train_booster(
            x=x,
            y=y,
            row_indices=train_rows,
            train_groups=train_groups,
            features=features,
            params=params,
            num_boost_round=num_boost_round,
        )
        preds[valid_rows] = booster.predict(x.iloc[valid_rows], num_iteration=booster.current_iteration())
        fold_summaries.append(
            {
                "fold": fold_idx + 1,
                "train_groups": len(train_group_ids),
                "valid_groups": len(valid_group_ids),
                "train_rows": int(len(train_rows)),
                "valid_rows": int(len(valid_rows)),
                "valid_positive_groups": _positive_group_count(y, slices, valid_group_ids),
                "elapsed_sec": round(time.perf_counter() - started, 3),
                "current_iteration": booster.current_iteration(),
            }
        )

    return preds, fold_summaries


def _write_variants_csv(path: Path, summaries: list[dict[str, Any]], selected_variant: str) -> None:
    fields = [
        "selected",
        "split",
        "variant",
        "mode",
        "margin",
        "groups",
        "baseline_hit1_rate",
        "raw_ltr_hit1_rate",
        "gated_hit1_rate",
        "gated_hit1_net",
        "gated_hit1_gain",
        "gated_hit1_loss",
        "prevented_raw_hit1_loss",
        "blocked_raw_hit1_gain",
        "gated_hit5_rate",
        "gated_hit5_net",
        "gated_override_rate",
        "gated_ndcg5",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in summaries:
            row = dict(item)
            row["selected"] = item["variant"] == selected_variant
            writer.writerow(row)


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_gate(path: Path, report: dict[str, Any]) -> None:
    payload = {
        "stage": report["stage"],
        "no_search_integration": True,
        "selected_gate": report["selected_gate"],
        "selection_split": report["selection_split"],
        "selection_policy": report["selection_policy"],
        "selection_status": report["selection_status"],
        "selection_note": report["selection_note"],
        "calibration": {
            "raw_oof_hit1_net": report["calibration"]["raw"]["gated_hit1_net"],
            "raw_oof_hit1_loss": report["calibration"]["raw"]["gated_hit1_loss"],
            "oof_net_floor": report["calibration"]["net_floor"],
            "eligible_threshold_candidates": report["calibration"]["eligible_count"],
            "folds": report["folds"],
        },
        "model_path": report["full_model_output"],
        "whitelist": report["whitelist"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    selected_name = report["selected_gate"]["name"]
    rows = [item for item in report["variant_summaries"] if item["variant"] in {"raw_ltr", selected_name}]
    lines = [
        "# Goal LTR OOF Safety Gate Calibration",
        "",
        "Stage 2.4 uses 3-fold out-of-fold predictions on dev to calibrate the safety-gate threshold, then evaluates the frozen gate on heldout/hard with a full-dev model. No search integration.",
        "",
        "## Selected Gate",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_variant", selected_name],
                ["selection_split", report["selection_split"]],
                ["selection_status", report["selection_status"]],
                ["margin", report["selected_gate"].get("margin")],
                ["oof_raw_net", report["calibration"]["raw"]["gated_hit1_net"]],
                ["oof_raw_loss", report["calibration"]["raw"]["gated_hit1_loss"]],
                ["oof_net_floor", report["calibration"]["net_floor"]],
                ["eligible_threshold_candidates", report["calibration"]["eligible_count"]],
            ]
        ),
        "",
        report["selection_note"],
        "",
        "## Fold Summary",
        "",
        _md_table(
            [
                ["fold", "train_groups", "valid_groups", "valid_positive_groups", "elapsed_sec"],
                *[
                    [
                        item["fold"],
                        item["train_groups"],
                        item["valid_groups"],
                        item["valid_positive_groups"],
                        item["elapsed_sec"],
                    ]
                    for item in report["fold_summaries"]
                ],
            ]
        ),
        "",
        "## Frozen Gate Evaluation",
        "",
        _md_table(
            [
                [
                    "split",
                    "variant",
                    "top1_rate",
                    "top1_net",
                    "gain",
                    "loss",
                    "prevented_loss",
                    "blocked_gain",
                    "top5_rate",
                    "top5_net",
                ],
                *[
                    [
                        item["split"],
                        item["variant"],
                        item["gated_hit1_rate"],
                        item["gated_hit1_net"],
                        item["gated_hit1_gain"],
                        item["gated_hit1_loss"],
                        item["prevented_raw_hit1_loss"],
                        item["blocked_raw_hit1_gain"],
                        item["gated_hit5_rate"],
                        item["gated_hit5_net"],
                    ]
                    for item in rows
                ],
            ]
        ),
        "",
        "## Notes",
        "",
        "- Threshold selection used dev_oof only.",
        "- Heldout/hard metrics are offline validation of the frozen gate, not tuning inputs.",
        "- The final model is trained on full dev only for heldout/hard scoring.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate Goal LTR safety gate with dev OOF predictions")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--eval-splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--margins", nargs="+", type=float, default=[0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--net-floor-ratio", type=float, default=0.80)
    parser.add_argument("--min-loss-reduction-ratio", type=float, default=0.50)
    parser.add_argument("--num-boost-round", type=int, default=160)
    parser.add_argument("--learning-rate", type=float, default=0.045)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-data-in-leaf", type=int, default=25)
    parser.add_argument("--seed", type=int, default=20260521)
    parser.add_argument("--full-model-output", default=str(DEFAULT_FULL_MODEL_OUTPUT))
    parser.add_argument("--gate-output", default=str(DEFAULT_GATE_OUTPUT))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    features = gate_eval._load_feature_whitelist(Path(args.whitelist))
    params = _train_params(args)
    variants = gate_eval._make_variants(args.margins)

    dev_x, dev_y, dev_groups, dev_feature_rows = gate_eval._load_split(data_dir, "dev", features)
    oof_preds, fold_summaries = _build_oof_predictions(
        x=dev_x,
        y=dev_y,
        groups=dev_groups,
        features=features,
        folds=args.folds,
        params=params,
        num_boost_round=args.num_boost_round,
        seed=args.seed,
    )
    oof_summaries, _oof_rows = gate_eval._evaluate_split(
        split="dev_oof",
        labels=dev_y,
        groups=dev_groups,
        feature_rows=dev_feature_rows,
        preds=oof_preds,
        variants=variants,
        details_path=Path(args.details_dir) / "goal_ltr_oof_safety_gate_details_dev_oof.jsonl",
    )
    calibration = gate_calibrate._select_variant(
        oof_summaries,
        net_floor_ratio=args.net_floor_ratio,
        min_loss_reduction_ratio=args.min_loss_reduction_ratio,
    )
    selected = calibration["selected"]
    selected_gate = {
        "name": selected["variant"],
        "mode": selected["mode"],
        "margin": selected.get("margin"),
        "rule": "allow LTR Top1 override when strict same family/book/no param conflict, or LTR score margin exceeds threshold",
    }

    all_dev_rows = np.arange(len(dev_y), dtype=np.int64)
    full_started = time.perf_counter()
    full_booster = _train_booster(
        x=dev_x,
        y=dev_y,
        row_indices=all_dev_rows,
        train_groups=dev_groups,
        features=features,
        params=params,
        num_boost_round=args.num_boost_round,
    )
    full_model_output = Path(args.full_model_output)
    full_model_output.parent.mkdir(parents=True, exist_ok=True)
    full_booster.save_model(str(full_model_output))
    full_model_train_sec = round(time.perf_counter() - full_started, 3)

    variant_summaries = list(oof_summaries)
    detail_paths = {"dev_oof": str(Path(args.details_dir) / "goal_ltr_oof_safety_gate_details_dev_oof.jsonl")}
    for split in args.eval_splits:
        x, y, groups, feature_rows = gate_eval._load_split(data_dir, split, features)
        preds = full_booster.predict(x, num_iteration=full_booster.current_iteration())
        details_path = Path(args.details_dir) / f"goal_ltr_oof_safety_gate_details_{split}.jsonl"
        summaries, _rows = gate_eval._evaluate_split(
            split=split,
            labels=y,
            groups=groups,
            feature_rows=feature_rows,
            preds=preds,
            variants=variants,
            details_path=details_path,
        )
        variant_summaries.extend(summaries)
        detail_paths[split] = str(details_path)

    report = {
        "stage": "Goal LTR v1 / stage 2.4 OOF safety gate calibration",
        "no_search_integration": True,
        "selection_split": "dev_oof",
        "eval_splits": args.eval_splits,
        "folds": args.folds,
        "fold_summaries": fold_summaries,
        "selection_policy": {
            "net_floor_ratio": args.net_floor_ratio,
            "min_loss_reduction_ratio": args.min_loss_reduction_ratio,
            "threshold_candidates": "strict_or_margin variants only",
        },
        "selection_status": calibration["selection_status"],
        "selection_note": calibration["selection_note"],
        "selected_gate": selected_gate,
        "calibration": calibration,
        "variant_summaries": variant_summaries,
        "train_params": params,
        "num_boost_round": args.num_boost_round,
        "features": features,
        "whitelist": str(Path(args.whitelist)),
        "full_model_output": str(full_model_output),
        "full_model_train_sec": full_model_train_sec,
        "gate_output": args.gate_output,
        "variants_csv": args.variants_csv,
        "details_jsonl": detail_paths,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    _write_variants_csv(Path(args.variants_csv), variant_summaries, selected["variant"])
    _write_gate(Path(args.gate_output), report)

    selected_rows = [item for item in variant_summaries if item["variant"] in {"raw_ltr", selected["variant"]}]
    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_search_integration": True,
                    "selected_gate": selected_gate,
                    "selection_status": report["selection_status"],
                    "selection_note": report["selection_note"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "fold_summaries": fold_summaries,
                "selected_variant_metrics": selected_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
