from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import lightgbm as lgb  # noqa: E402

from goal_eval_ltr_switch import (  # noqa: E402
    _base_metrics,
    _candidate_label,
    _gate_facts,
    _gated_order,
    _load_feature_whitelist,
    _load_gate_margin,
)
from goal_ltr_safety_gate_whatif_narrow import _narrow_decision  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_ANCHOR_DIR = DEFAULT_DATA_DIR / "anchor_audit"
DEFAULT_MODEL_PATH = DEFAULT_DATA_DIR / "goal_ltr_oof_full_dev_v1.txt"
DEFAULT_WHITELIST_PATH = DEFAULT_DATA_DIR / "ltr_feature_whitelist_v1.json"
DEFAULT_SAFETY_GATE_PATH = DEFAULT_DATA_DIR / "ltr_safety_gate_oof_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_eval_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_eval_summary.md"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _read_group_ids(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _float_feature(row: dict[str, Any], feature: str) -> float:
    try:
        return float(row.get(feature) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _load_filtered_feature_groups(
    *,
    data_dir: Path,
    split: str,
    group_ids: set[str],
    features: list[str],
) -> tuple[list[dict[str, Any]], list[list[float]], list[int], list[int]]:
    feature_path = data_dir / f"ltr_features_{split}.jsonl"
    rows: list[dict[str, Any]] = []
    matrix: list[list[float]] = []
    labels: list[int] = []
    groups: list[int] = []
    current_group = ""
    current_count = 0

    for row in _iter_jsonl(feature_path):
        group_id = _clean(row.get("group_id"))
        if group_id not in group_ids:
            continue
        if current_group and group_id != current_group:
            groups.append(current_count)
            current_count = 0
        current_group = group_id
        current_count += 1
        rows.append(row)
        labels.append(int(float(row.get("label") or 0)))
        matrix.append([_float_feature(row, feature) for feature in features])

    if current_count:
        groups.append(current_count)
    if len(groups) != len(group_ids):
        loaded_ids = {_clean(row.get("group_id")) for row in rows}
        missing = sorted(group_ids - loaded_ids)
        raise ValueError(f"{split} missing feature groups: {missing[:10]}")
    return rows, matrix, labels, groups


def _first_positive_rank(labels: list[int], order: list[int]) -> int | None:
    for rank, idx in enumerate(order, start=1):
        if labels[idx] > 0:
            return rank
    return None


def _evaluate_split(
    *,
    split: str,
    data_dir: Path,
    anchor_dir: Path,
    features: list[str],
    booster: lgb.Booster,
    margin: float,
    details_dir: Path,
) -> dict[str, Any]:
    group_ids = _read_group_ids(anchor_dir / f"{split}_validation_group_ids.txt")
    rows, matrix, labels, groups = _load_filtered_feature_groups(
        data_dir=data_dir,
        split=split,
        group_ids=group_ids,
        features=features,
    )

    started_predict = time.perf_counter()
    preds = [float(item) for item in booster.predict(matrix, num_iteration=booster.current_iteration())]
    predict_sec = time.perf_counter() - started_predict

    details_path = details_dir / f"goal_anchor_clean_eval_details_{split}.jsonl"
    details_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(groups)
    positive_groups = 0
    baseline_hit1 = baseline_hit5 = 0
    raw_hit1 = raw_hit5 = 0
    gated_hit1 = gated_hit5 = 0
    hit1_gain = hit1_loss = hit5_gain = hit5_loss = 0
    raw_hit1_gain = raw_hit1_loss = 0
    blocked_raw_gain = prevented_raw_loss = 0
    raw_override_count = gated_override_count = 0
    gate_reasons: Counter[str] = Counter()
    baseline_ranks: list[float] = []
    gated_ranks: list[float] = []
    start = 0

    variant = {"name": "whatif_A_plus_B_narrow", "mode": "whatif_a_plus_b_narrow", "margin": margin}

    with details_path.open("w", encoding="utf-8") as handle:
        for group_index, size in enumerate(groups, start=1):
            stop = start + size
            group_rows = rows[start:stop]
            group_labels = labels[start:stop]
            group_preds = preds[start:stop]

            baseline_order = list(range(size))
            raw_order = sorted(range(size), key=lambda idx: (-group_preds[idx], idx))
            baseline_idx = 0
            ltr_top_idx = int(raw_order[0])
            baseline_row = group_rows[baseline_idx]
            ltr_top_row = group_rows[ltr_top_idx]
            facts = _gate_facts(
                baseline_row,
                ltr_top_row,
                float(group_preds[baseline_idx]),
                float(group_preds[ltr_top_idx]),
            )
            allow_override, gate_reason = _narrow_decision(
                variant=variant,
                ltr_top_idx=ltr_top_idx,
                facts=facts,
                query=_clean(baseline_row.get("query")),
                baseline_name=_clean(baseline_row.get("quota_name")),
                ltr_name=_clean(ltr_top_row.get("quota_name")),
            )
            gated_order = _gated_order(raw_order, allow_override)

            baseline_metrics = _base_metrics("baseline", group_labels, baseline_order)
            raw_metrics = _base_metrics("raw_ltr", group_labels, raw_order)
            gated_metrics = _base_metrics("gated", group_labels, gated_order)

            has_positive = any(label > 0 for label in group_labels)
            positive_groups += int(has_positive)
            base_h1 = bool(baseline_metrics["baseline_hit1"])
            base_h5 = bool(baseline_metrics["baseline_hit5"])
            raw_h1 = bool(raw_metrics["raw_ltr_hit1"])
            raw_h5 = bool(raw_metrics["raw_ltr_hit5"])
            gated_h1 = bool(gated_metrics["gated_hit1"])
            gated_h5 = bool(gated_metrics["gated_hit5"])

            baseline_hit1 += int(base_h1)
            baseline_hit5 += int(base_h5)
            raw_hit1 += int(raw_h1)
            raw_hit5 += int(raw_h5)
            gated_hit1 += int(gated_h1)
            gated_hit5 += int(gated_h5)
            hit1_gain += int((not base_h1) and gated_h1)
            hit1_loss += int(base_h1 and not gated_h1)
            hit5_gain += int((not base_h5) and gated_h5)
            hit5_loss += int(base_h5 and not gated_h5)
            raw_hit1_gain += int((not base_h1) and raw_h1)
            raw_hit1_loss += int(base_h1 and not raw_h1)
            blocked_raw_gain += int((not base_h1) and raw_h1 and not gated_h1)
            prevented_raw_loss += int(base_h1 and not raw_h1 and gated_h1)
            raw_override_count += int(ltr_top_idx != baseline_idx)
            gated_override_count += int(ltr_top_idx != baseline_idx and bool(allow_override))
            gate_reasons[gate_reason] += 1

            base_rank = baseline_metrics["baseline_positive_rank"]
            gated_rank = gated_metrics["gated_positive_rank"]
            if base_rank is not None:
                baseline_ranks.append(float(base_rank))
            if gated_rank is not None:
                gated_ranks.append(float(gated_rank))

            gated_top_idx = int(gated_order[0])
            gated_top_row = group_rows[gated_top_idx]
            detail = {
                "split": split,
                "group_index": group_index,
                "group_id": _clean(baseline_row.get("group_id")),
                "sample_id": _clean(baseline_row.get("sample_id")),
                "source_file": _clean(baseline_row.get("source_file")),
                "project_name": _clean(baseline_row.get("project_name")),
                "province": _clean(baseline_row.get("province")),
                "query": _clean(baseline_row.get("query")),
                "expected_ids": _clean(baseline_row.get("expected_ids")),
                "has_positive": has_positive,
                "positive_count": int(sum(1 for label in group_labels if label > 0)),
                "baseline_top_id": _clean(baseline_row.get("quota_id")),
                "baseline_top": _candidate_label(baseline_row),
                "raw_ltr_top_id": _clean(ltr_top_row.get("quota_id")),
                "raw_ltr_top": _candidate_label(ltr_top_row),
                "gated_top_id": _clean(gated_top_row.get("quota_id")),
                "gated_top": _candidate_label(gated_top_row),
                "gate_allowed": bool(allow_override),
                "gate_reason": gate_reason,
                "score_margin": round(float(facts["score_margin"]), 8),
                "same_family": bool(facts["same_family"]),
                "same_book": bool(facts["same_book"]),
                "no_param_conflict": bool(facts["no_param_conflict"]),
                **baseline_metrics,
                **raw_metrics,
                **gated_metrics,
                "hit1_delta_vs_baseline": int(gated_h1) - int(base_h1),
                "hit5_delta_vs_baseline": int(gated_h5) - int(base_h5),
            }
            handle.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")
            start = stop

    return {
        "split": split,
        "groups": total,
        "positive_groups": positive_groups,
        "top80_ceiling_rate": _rate(positive_groups, total),
        "top80_missing": total - positive_groups,
        "baseline_hit1": baseline_hit1,
        "baseline_hit1_rate": _rate(baseline_hit1, total),
        "baseline_hit5": baseline_hit5,
        "baseline_hit5_rate": _rate(baseline_hit5, total),
        "raw_ltr_hit1": raw_hit1,
        "raw_ltr_hit1_rate": _rate(raw_hit1, total),
        "raw_ltr_hit5": raw_hit5,
        "raw_ltr_hit5_rate": _rate(raw_hit5, total),
        "raw_ltr_hit1_gain": raw_hit1_gain,
        "raw_ltr_hit1_loss": raw_hit1_loss,
        "raw_ltr_hit1_net": raw_hit1_gain - raw_hit1_loss,
        "gated_hit1": gated_hit1,
        "gated_hit1_rate": _rate(gated_hit1, total),
        "gated_hit5": gated_hit5,
        "gated_hit5_rate": _rate(gated_hit5, total),
        "gated_hit1_gain": hit1_gain,
        "gated_hit1_loss": hit1_loss,
        "gated_hit1_net": hit1_gain - hit1_loss,
        "gated_hit5_gain": hit5_gain,
        "gated_hit5_loss": hit5_loss,
        "gated_hit5_net": hit5_gain - hit5_loss,
        "distance_to_75_top1": round(max(0.0, 0.75 - _rate(gated_hit1, total)), 6),
        "ceiling_margin_over_75": round(_rate(positive_groups, total) - 0.75, 6),
        "raw_override_count": raw_override_count,
        "gated_override_count": gated_override_count,
        "gated_override_rate": _rate(gated_override_count, raw_override_count),
        "blocked_raw_hit1_gain": blocked_raw_gain,
        "prevented_raw_hit1_loss": prevented_raw_loss,
        "baseline_rank_avg": _mean(baseline_ranks),
        "gated_rank_avg": _mean(gated_ranks),
        "gate_reasons": dict(gate_reasons),
        "predict_sec": round(predict_sec, 3),
        "details_jsonl": str(details_path),
    }


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
    summaries = report["splits"]
    lines = [
        "# Goal Anchor-Clean Eval Summary",
        "",
        "Stage 3.8 anchor-clean baseline recompute. It filters to Stage 3.7 validation groups, then recomputes Top80 ceiling, baseline Top1/Top5, raw LTR, and A+B narrow safety-gated LTR. No model tuning and no search change.",
        "",
        "## Inputs",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["model_path", report["model_path"]],
                ["whitelist_path", report["whitelist_path"]],
                ["safety_gate_path", report["safety_gate_path"]],
                ["margin", report["margin"]],
                ["anchor_dir", report["anchor_dir"]],
                ["splits", ", ".join(report["splits_requested"])],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Metrics",
        "",
        _md_table(
            [
                [
                    "split",
                    "groups",
                    "top80_ceiling",
                    "baseline_top1",
                    "gated_top1",
                    "top1_net",
                    "gain",
                    "loss",
                    "gated_top5",
                    "top5_net",
                    "distance_to_75",
                    "ceiling_margin_over_75",
                ],
                *[
                    [
                        item["split"],
                        item["groups"],
                        item["top80_ceiling_rate"],
                        item["baseline_hit1_rate"],
                        item["gated_hit1_rate"],
                        item["gated_hit1_net"],
                        item["gated_hit1_gain"],
                        item["gated_hit1_loss"],
                        item["gated_hit5_rate"],
                        item["gated_hit5_net"],
                        item["distance_to_75_top1"],
                        item["ceiling_margin_over_75"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
        "## Details",
        "",
        _md_table([["split", "details_jsonl"]] + [[item["split"], item["details_jsonl"]] for item in summaries]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute Goal metrics on Stage 3.7 anchor-clean validation groups")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--anchor-dir", default=str(DEFAULT_ANCHOR_DIR))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--model-path", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--whitelist-path", default=str(DEFAULT_WHITELIST_PATH))
    parser.add_argument("--safety-gate-path", default=str(DEFAULT_SAFETY_GATE_PATH))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    features = _load_feature_whitelist(Path(args.whitelist_path))
    margin = _load_gate_margin(Path(args.safety_gate_path))
    booster = lgb.Booster(model_file=str(args.model_path))

    split_summaries: list[dict[str, Any]] = []
    for split in args.splits:
        split_summaries.append(
            _evaluate_split(
                split=split,
                data_dir=Path(args.data_dir),
                anchor_dir=Path(args.anchor_dir),
                features=features,
                booster=booster,
                margin=margin,
                details_dir=Path(args.details_dir),
            )
        )

    report = {
        "stage": "Goal LTR v1 / stage 3.8 anchor-clean baseline recompute",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": args.data_dir,
        "anchor_dir": args.anchor_dir,
        "model_path": args.model_path,
        "whitelist_path": args.whitelist_path,
        "safety_gate_path": args.safety_gate_path,
        "margin": margin,
        "feature_count": len(features),
        "splits_requested": args.splits,
        "splits": split_summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "splits": split_summaries,
                },
                "artifacts": {
                    "report_json": args.report_json,
                    "report_md": args.report_md,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
