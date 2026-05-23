from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_STAGE_9_30_SUMMARY = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_summary.json"
DEFAULT_LEARNABILITY_SLICES = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_learnability_slices.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_recall_missing_no_eligible_closure_9x"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _closure_options(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    learnable = int(metrics.get("learnable_slice_count") or 0)
    global_rows = int(metrics.get("source_provenance_class_counts", {}).get("generated_global_repair_decision_table", 0))
    non_global = int(metrics.get("source_provenance_class_counts", {}).get("non_global_eval_trace", 0))
    return [
        {
            "option": "continue_recall_missing_bucket_mining",
            "admissible": "no",
            "supporting_rows": learnable,
            "risk": "no_eligible_learning_slice",
            "recommendation": "stop",
            "rationale": "Stage 9.30 found learnable_slice_count=0 across all audited recall-missing slices.",
        },
        {
            "option": "train_or_patch_from_global_repair_source",
            "admissible": "no",
            "supporting_rows": global_rows,
            "risk": "generated_source_feedback_loop",
            "recommendation": "do_not_use",
            "rationale": "global_repair_decision_table.csv is a generated repair-decision source, not an independent learning source.",
        },
        {
            "option": "move_to_taxonomy_data_quality_backlog",
            "admissible": "yes",
            "supporting_rows": non_global,
            "risk": "not_accuracy_learning_yet",
            "recommendation": "use_as_next_read_only_track",
            "rationale": "Remaining non-global rows are small and point to query/top1 taxonomy coverage or label backlog.",
        },
        {
            "option": "close_9x_mining_and_plan_next_accuracy_strategy",
            "admissible": "yes",
            "supporting_rows": 0,
            "risk": "requires_new_route_definition",
            "recommendation": "use_after_backlog_handoff",
            "rationale": "Both wrong-rank and recall-missing high-support mining routes have no eligible learning bucket under current safeguards.",
        },
    ]


def _backlog_items(metrics: dict[str, Any], slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage = metrics.get("coverage_gap_class_counts", {})
    learnability = metrics.get("learnability_status_counts", {})
    items = [
        {
            "backlog_area": "source_provenance",
            "count": learnability.get("blocked_source_provenance", 0),
            "priority": "P0",
            "evidence": "generated_global_repair_decision_table rows dominate audited recall-missing slices",
            "recommended_review": "Document provenance boundary and prevent generated repair-decision source from becoming direct learning evidence.",
        },
        {
            "backlog_area": "query_family_empty",
            "count": coverage.get("query_family_empty_blocks_coverage_decision", 0),
            "priority": "P0",
            "evidence": "query_family_empty_blocks_coverage_decision",
            "recommended_review": "Taxonomy coverage review for empty query_family rows before any recall learning.",
        },
        {
            "backlog_area": "top1_family_coverage",
            "count": int(coverage.get("probable_top1_family_coverage_gap", 0)) + int(coverage.get("high_confidence_top1_family_coverage_gap", 0)),
            "priority": "P1",
            "evidence": "probable/high-confidence top1_family coverage gaps",
            "recommended_review": "Review top1_family labeling coverage for same-domain pipe/valve/lamp/weak-current cases.",
        },
        {
            "backlog_area": "label_or_taxonomy_mixture",
            "count": coverage.get("valve_label_or_taxonomy_mixture", 0),
            "priority": "P1",
            "evidence": "valve_label_or_taxonomy_mixture",
            "recommended_review": "Separate overbroad valve labels from water meter/sanitary/instrument/civil mixtures.",
        },
    ]
    # Add the largest concrete slices to make the handoff traceable.
    for row in slices[:6]:
        items.append(
            {
                "backlog_area": "learnability_slice_no_eligible",
                "count": _to_int(row.get("count")),
                "priority": "evidence",
                "evidence": _clean(row.get("bucket_key")),
                "recommended_review": _clean(row.get("non_eligible_reason")),
            }
        )
    return items


def _metrics(stage_9_30: dict[str, Any], slices: list[dict[str, Any]]) -> dict[str, Any]:
    m = dict(stage_9_30.get("metrics", {}))
    eligible_counter = Counter(_clean(row.get("eligible_for_learning_after_9_30")) or "<empty>" for row in slices)
    non_eligible_counter = Counter(_clean(row.get("non_eligible_reason")) or "<empty>" for row in slices)
    m["learnability_slice_rows"] = len(slices)
    m["eligible_for_learning_after_9_30_counts"] = dict(eligible_counter.most_common())
    m["non_eligible_reason_counts"] = dict(non_eligible_counter.most_common())
    m["all_slices_no_eligible"] = eligible_counter.get("yes", 0) == 0 and eligible_counter.get("possible_manual_review", 0) == 0
    return m


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    closure_options: list[dict[str, Any]],
    backlog_items: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 9.31 Recall-missing No-eligible Learning-slice Closure Review",
        "",
        "Read-only closure review of the recall-missing mining route after stage 9.30. This decides whether to continue 9.x bucket mining or move the remaining evidence into a separate taxonomy/data-quality backlog. It does not train, tune, patch rules, change ranking, use heldout for selection, or modify GoalSearcher.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", metrics.get("target_rows")],
                ["learnability_slice_rows", metrics.get("learnability_slice_rows")],
                ["learnable_slice_count", metrics.get("learnable_slice_count")],
                ["all_slices_no_eligible", metrics.get("all_slices_no_eligible")],
                ["blocked_source_provenance", metrics.get("learnability_status_counts", {}).get("blocked_source_provenance", 0)],
                ["taxonomy_coverage_not_recall_learning", metrics.get("learnability_status_counts", {}).get("taxonomy_coverage_not_recall_learning", 0)],
                ["blocked_query_taxonomy_empty", metrics.get("learnability_status_counts", {}).get("blocked_query_taxonomy_empty", 0)],
            ]
        ),
        "",
        "## Closure Options",
        "",
        _md_table(
            [["option", "admissible", "supporting_rows", "risk", "recommendation", "rationale"]]
            + [
                [
                    row["option"],
                    row["admissible"],
                    row["supporting_rows"],
                    row["risk"],
                    row["recommendation"],
                    row["rationale"],
                ]
                for row in closure_options
            ]
        ),
        "",
        "## Backlog Handoff",
        "",
        _md_table(
            [["area", "count", "priority", "evidence", "review"]]
            + [
                [
                    row["backlog_area"],
                    row["count"],
                    row["priority"],
                    row["evidence"],
                    row["recommended_review"],
                ]
                for row in backlog_items
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
    parser = argparse.ArgumentParser(description="Stage 9.31 recall-missing no-eligible closure review")
    parser.add_argument("--stage-9-30-summary", default=str(DEFAULT_STAGE_9_30_SUMMARY))
    parser.add_argument("--learnability-slices", default=str(DEFAULT_LEARNABILITY_SLICES))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_9_30 = _read_json(Path(args.stage_9_30_summary))
    slices = _read_csv(Path(args.learnability_slices))
    metrics = _metrics(stage_9_30, slices)
    closure_options = _closure_options(metrics)
    backlog = _backlog_items(metrics, slices)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_options_csv": str(output_prefix.with_name(output_prefix.name + "_closure_options.csv")),
        "backlog_items_csv": str(output_prefix.with_name(output_prefix.name + "_backlog_items.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.31 recall-missing no-eligible learning-slice closure review",
        "read_only": True,
        "eval_only": True,
        "dev_only_analysis": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifacts": {
            "stage_9_30_summary": str(Path(args.stage_9_30_summary)),
            "stage_9_30_learnability_slices": str(Path(args.learnability_slices)),
        },
        "metrics": metrics,
        "closure_options": closure_options,
        "taxonomy_data_quality_backlog": backlog,
        "decision": (
            "Stop the recall-missing mining route as a learning path for now. Stage 9.30 found no eligible learning slice: "
            "the largest evidence is blocked by generated source provenance, and the remaining non-global evidence is small taxonomy/coverage backlog. "
            "Move the residual evidence into a separate taxonomy/data-quality backlog and do not train, tune, patch rules, or change GoalSearcher from it."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.31 only closes the dev recall-missing mining route using 9.30 learnability slices. It does not train, tune, patch rules, "
            "change ranking, modify GoalSearcher, use heldout for selection, connect online, or relax any gate."
        ),
        "next_stage": {
            "stage": "9.32 taxonomy/data-quality backlog handoff",
            "goal": (
                "Read-only package the source-provenance, query_family_empty, top1_family coverage, and label-mixture findings into a separate "
                "taxonomy/data-quality backlog, outside the rank/recall learning lane."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
            ],
        },
    }

    closure_fields = ["option", "admissible", "supporting_rows", "risk", "recommendation", "rationale"]
    backlog_fields = ["backlog_area", "count", "priority", "evidence", "recommended_review"]
    _write_csv(Path(artifacts["closure_options_csv"]), closure_options, closure_fields)
    _write_csv(Path(artifacts["backlog_items_csv"]), backlog, backlog_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_options, backlog)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": {
                    "target_rows": metrics.get("target_rows"),
                    "learnability_slice_rows": metrics.get("learnability_slice_rows"),
                    "learnable_slice_count": metrics.get("learnable_slice_count"),
                    "all_slices_no_eligible": metrics.get("all_slices_no_eligible"),
                    "eligible_counts": metrics.get("eligible_for_learning_after_9_30_counts"),
                },
                "decision": report["decision"],
                "next_stage": report["next_stage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
