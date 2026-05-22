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

import goal_ltr_safety_gate_eval as gate_eval

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "data" / "goal_search" / "goal_ltr_v1.txt"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_GATE_OUTPUT = PROJECT_ROOT / "data" / "goal_search" / "ltr_safety_gate_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_calibration_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_calibration_summary.md"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_calibration_variants.csv"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"


def _margin_value(item: dict[str, Any]) -> float:
    margin = item.get("margin")
    return float(margin) if margin is not None else -1.0


def _find_raw(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    for item in summaries:
        if item["variant"] == "raw_ltr":
            return item
    raise ValueError("missing raw_ltr summary")


def _select_variant(
    dev_summaries: list[dict[str, Any]],
    *,
    net_floor_ratio: float,
    min_loss_reduction_ratio: float,
) -> dict[str, Any]:
    raw = _find_raw(dev_summaries)
    raw_net = int(raw["gated_hit1_net"])
    raw_loss = int(raw["gated_hit1_loss"])
    net_floor = math.ceil(raw_net * net_floor_ratio) if raw_net > 0 else raw_net
    threshold_candidates = [
        item for item in dev_summaries if item.get("mode") == "strict_or_margin" and int(item["gated_hit1_net"]) >= net_floor
    ]

    if not threshold_candidates:
        fallback = max(
            [item for item in dev_summaries if item.get("mode") == "strict_or_margin"],
            key=lambda item: (int(item["gated_hit1_net"]), -int(item["gated_hit1_loss"]), _margin_value(item)),
        )
        return {
            "selected": fallback,
            "raw": raw,
            "net_floor": net_floor,
            "eligible_count": 0,
            "selection_status": "no_threshold_candidate_met_net_floor",
            "selection_note": "No threshold candidate met the dev net floor, so the best dev net candidate is reported but should not be promoted.",
        }

    if raw_loss > 0:
        min_prevented = max(1, math.ceil(raw_loss * min_loss_reduction_ratio))
        loss_safe = [item for item in threshold_candidates if int(item["prevented_raw_hit1_loss"]) >= min_prevented]
        pool = loss_safe or threshold_candidates
        selected = max(
            pool,
            key=lambda item: (
                int(item["prevented_raw_hit1_loss"]),
                -int(item["gated_hit1_loss"]),
                int(item["gated_hit1_net"]),
                int(item["gated_hit5_net"]),
                _margin_value(item),
            ),
        )
        status = "selected_by_dev_loss_reduction" if loss_safe else "selected_without_meeting_loss_reduction_floor"
        note = (
            "Selected on dev by loss reduction first, then lower residual loss, then net gain."
            if loss_safe
            else "No candidate met the dev loss reduction floor; selected the best eligible threshold by the same ranking."
        )
    else:
        selected = max(
            threshold_candidates,
            key=lambda item: (
                _margin_value(item),
                -int(item["blocked_raw_hit1_gain"]),
                int(item["gated_hit1_net"]),
                int(item["gated_hit5_net"]),
            ),
        )
        status = "dev_raw_has_no_loss_loss_reduction_uninformative"
        note = (
            "Raw LTR has zero Top1 loss on dev, so dev cannot calibrate loss reduction. "
            "Selected the highest margin that still satisfies the dev net floor."
        )

    return {
        "selected": selected,
        "raw": raw,
        "net_floor": net_floor,
        "eligible_count": len(threshold_candidates),
        "selection_status": status,
        "selection_note": note,
    }


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    selected_name = report["selected_gate"]["name"]
    selected_rows = [
        item for item in report["variant_summaries"] if item["variant"] in {"raw_ltr", selected_name}
    ]
    lines = [
        "# Goal LTR Safety Gate Calibration",
        "",
        "Stage 2.3 calibrates the safety-gate threshold on dev only, then reports the frozen gate on heldout/hard. No search integration.",
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
                ["dev_raw_net", report["calibration"]["raw"]["gated_hit1_net"]],
                ["dev_raw_loss", report["calibration"]["raw"]["gated_hit1_loss"]],
                ["dev_net_floor", report["calibration"]["net_floor"]],
                ["eligible_threshold_candidates", report["calibration"]["eligible_count"]],
            ]
        ),
        "",
        report["selection_note"],
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
                    for item in selected_rows
                ],
            ]
        ),
        "",
        "## Notes",
        "",
        "- The threshold was selected without looking at heldout/hard metrics.",
        "- Because dev is also the LTR training split, loss calibration may be optimistic; the heldout/hard rows are the real offline check.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


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
            "raw_dev_hit1_net": report["calibration"]["raw"]["gated_hit1_net"],
            "raw_dev_hit1_loss": report["calibration"]["raw"]["gated_hit1_loss"],
            "dev_net_floor": report["calibration"]["net_floor"],
            "eligible_threshold_candidates": report["calibration"]["eligible_count"],
        },
        "model_path": report["model_path"],
        "whitelist": report["whitelist"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate Goal LTR v1 safety gate threshold on dev only")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--selection-split", default="dev")
    parser.add_argument("--eval-splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--margins", nargs="+", type=float, default=[0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--net-floor-ratio", type=float, default=0.80)
    parser.add_argument("--min-loss-reduction-ratio", type=float, default=0.50)
    parser.add_argument("--gate-output", default=str(DEFAULT_GATE_OUTPUT))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    features = gate_eval._load_feature_whitelist(Path(args.whitelist))
    booster = lgb.Booster(model_file=str(Path(args.model)))
    variants = gate_eval._make_variants(args.margins)

    splits = [args.selection_split, *[split for split in args.eval_splits if split != args.selection_split]]
    variant_summaries: list[dict[str, Any]] = []
    detail_paths: dict[str, str] = {}

    for split in splits:
        x, y, group, feature_rows = gate_eval._load_split(data_dir, split, features)
        preds = booster.predict(x, num_iteration=booster.current_iteration())
        details_path = Path(args.details_dir) / f"goal_ltr_safety_gate_calibration_details_{split}.jsonl"
        summaries, _variant_rows = gate_eval._evaluate_split(
            split=split,
            labels=y,
            groups=group,
            feature_rows=feature_rows,
            preds=preds,
            variants=variants,
            details_path=details_path,
        )
        variant_summaries.extend(summaries)
        detail_paths[split] = str(details_path)

    dev_summaries = [item for item in variant_summaries if item["split"] == args.selection_split]
    calibration = _select_variant(
        dev_summaries,
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

    report = {
        "stage": "Goal LTR v1 / stage 2.3 safety gate threshold calibration",
        "no_search_integration": True,
        "model_path": str(Path(args.model)),
        "whitelist": str(Path(args.whitelist)),
        "features": features,
        "selection_split": args.selection_split,
        "eval_splits": args.eval_splits,
        "selection_policy": {
            "net_floor_ratio": args.net_floor_ratio,
            "min_loss_reduction_ratio": args.min_loss_reduction_ratio,
            "raw_loss_zero_tie_break": "choose highest margin among dev candidates meeting the net floor",
            "threshold_candidates": "strict_or_margin variants only",
        },
        "selection_status": calibration["selection_status"],
        "selection_note": calibration["selection_note"],
        "selected_gate": selected_gate,
        "calibration": calibration,
        "variant_summaries": variant_summaries,
        "details_jsonl": detail_paths,
        "gate_output": args.gate_output,
        "variants_csv": args.variants_csv,
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
                "selected_variant_metrics": selected_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
