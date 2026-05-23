from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_STAGE_9_31_SUMMARY = AGENT_STATE / "goal_recall_missing_no_eligible_closure_9x_summary.json"
DEFAULT_STAGE_9_31_BACKLOG = AGENT_STATE / "goal_recall_missing_no_eligible_closure_9x_backlog_items.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_taxonomy_data_quality_backlog_handoff_9x"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _handoff_items(backlog_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    route_map = {
        "source_provenance": {
            "handoff_owner_lane": "data_provenance",
            "route_boundary": "generated_source_boundary_not_learning",
            "learning_lane_disposition": "exclude_from_rank_recall_learning",
            "acceptance_check": "generated repair-decision rows are documented and cannot become direct training or rule evidence",
        },
        "query_family_empty": {
            "handoff_owner_lane": "taxonomy_label_coverage",
            "route_boundary": "query_taxonomy_empty_not_learning",
            "learning_lane_disposition": "taxonomy_backlog_before_learning",
            "acceptance_check": "empty query_family rows are labeled or explicitly classified as taxonomy-empty before any learning review",
        },
        "top1_family_coverage": {
            "handoff_owner_lane": "taxonomy_label_coverage",
            "route_boundary": "top1_family_coverage_gap_not_recall_rule",
            "learning_lane_disposition": "coverage_backlog_before_learning",
            "acceptance_check": "top1_family coverage is reviewed for same-domain pipe/valve/lamp/weak-current cases",
        },
        "label_or_taxonomy_mixture": {
            "handoff_owner_lane": "taxonomy_label_quality",
            "route_boundary": "overbroad_or_mixed_label_not_rank_rule",
            "learning_lane_disposition": "label_backlog_before_learning",
            "acceptance_check": "overbroad valve labels are separated from water meter/sanitary/instrument/civil mixtures",
        },
    }
    rows: list[dict[str, Any]] = []
    for row in backlog_rows:
        area = _clean(row.get("backlog_area"))
        if area not in route_map:
            continue
        meta = route_map[area]
        rows.append(
            {
                "backlog_area": area,
                "count": _to_int(row.get("count")),
                "priority": _clean(row.get("priority")),
                "handoff_owner_lane": meta["handoff_owner_lane"],
                "route_boundary": meta["route_boundary"],
                "learning_lane_disposition": meta["learning_lane_disposition"],
                "evidence": _clean(row.get("evidence")),
                "recommended_review": _clean(row.get("recommended_review")),
                "acceptance_check": meta["acceptance_check"],
            }
        )
    return rows


def _evidence_slices(backlog_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in backlog_rows:
        if _clean(row.get("backlog_area")) != "learnability_slice_no_eligible":
            continue
        evidence = _clean(row.get("evidence"))
        if "query_family_empty" in evidence:
            route = "query_family_empty"
        elif "top1_family_empty" in evidence or "top1_family_coverage_gap" in evidence:
            route = "top1_family_coverage"
        elif "valve_label_or_taxonomy_mixture" in evidence:
            route = "label_or_taxonomy_mixture"
        else:
            route = "source_provenance"
        if "blocked_source_provenance" in evidence:
            route = "source_provenance"
        rows.append(
            {
                "source_backlog_area": "learnability_slice_no_eligible",
                "mapped_backlog_area": route,
                "count": _to_int(row.get("count")),
                "evidence_slice": evidence,
                "non_learning_reason": _clean(row.get("recommended_review")),
                "learning_lane_disposition": "evidence_only_do_not_train_or_patch",
            }
        )
    return rows


def _route_boundaries(metrics: dict[str, Any], handoff_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "boundary": "generated_source_provenance",
            "status": "blocked_from_learning",
            "evidence_count": metrics.get("source_provenance_class_counts", {}).get("generated_global_repair_decision_table", 0),
            "decision": "do_not_train_or_patch_from_global_repair_decision_table",
            "next_action": "document_data_provenance_boundary",
        },
        {
            "boundary": "taxonomy_empty_or_coverage_gap",
            "status": "taxonomy_data_quality_backlog",
            "evidence_count": sum(row["count"] for row in handoff_rows if row["backlog_area"] in {"query_family_empty", "top1_family_coverage"}),
            "decision": "resolve_label_coverage_before_any_learning_claim",
            "next_action": "handoff_to_taxonomy_label_coverage_review",
        },
        {
            "boundary": "label_or_taxonomy_mixture",
            "status": "taxonomy_data_quality_backlog",
            "evidence_count": sum(row["count"] for row in handoff_rows if row["backlog_area"] == "label_or_taxonomy_mixture"),
            "decision": "do_not_convert_overbroad_labels_into_rank_rules",
            "next_action": "handoff_to_label_quality_review",
        },
        {
            "boundary": "rank_recall_learning_lane",
            "status": "closed_for_current_9x_mining",
            "evidence_count": int(metrics.get("learnable_slice_count") or 0),
            "decision": "no_learning_slice_after_9_31",
            "next_action": "close_9x_mining_or_define_new_10x_strategy_after_backlog_handoff",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    handoff_rows = report["handoff_items"]
    route_rows = report["route_boundary_decisions"]
    evidence_rows = report["evidence_slices"]
    lines = [
        "# Stage 9.32 Taxonomy/Data-quality Backlog Handoff",
        "",
        "Read-only handoff package for residual evidence from stage 9.31. These items are explicitly outside the rank/recall learning lane until data provenance and taxonomy/label coverage are resolved.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["handoff_item_count", metrics.get("handoff_item_count")],
                ["evidence_slice_count", metrics.get("evidence_slice_count")],
                ["total_priority_backlog_rows", metrics.get("total_priority_backlog_rows")],
                ["source_provenance_rows", metrics.get("source_provenance_rows")],
                ["query_family_empty_rows", metrics.get("query_family_empty_rows")],
                ["top1_family_coverage_rows", metrics.get("top1_family_coverage_rows")],
                ["label_or_taxonomy_mixture_rows", metrics.get("label_or_taxonomy_mixture_rows")],
                ["learnable_slice_count", metrics.get("learnable_slice_count")],
            ]
        ),
        "",
        "## Handoff Items",
        "",
        _md_table(
            [["area", "count", "priority", "owner lane", "learning disposition", "acceptance check"]]
            + [
                [
                    row["backlog_area"],
                    row["count"],
                    row["priority"],
                    row["handoff_owner_lane"],
                    row["learning_lane_disposition"],
                    row["acceptance_check"],
                ]
                for row in handoff_rows
            ]
        ),
        "",
        "## Route Boundary Decisions",
        "",
        _md_table(
            [["boundary", "status", "evidence_count", "decision", "next_action"]]
            + [
                [
                    row["boundary"],
                    row["status"],
                    row["evidence_count"],
                    row["decision"],
                    row["next_action"],
                ]
                for row in route_rows
            ]
        ),
        "",
        "## Evidence Slices",
        "",
        _md_table(
            [["mapped_area", "count", "non_learning_reason", "slice"]]
            + [
                [
                    row["mapped_backlog_area"],
                    row["count"],
                    row["non_learning_reason"],
                    row["evidence_slice"],
                ]
                for row in evidence_rows
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
    parser = argparse.ArgumentParser(description="Stage 9.32 taxonomy/data-quality backlog handoff")
    parser.add_argument("--stage-9-31-summary", default=str(DEFAULT_STAGE_9_31_SUMMARY))
    parser.add_argument("--stage-9-31-backlog", default=str(DEFAULT_STAGE_9_31_BACKLOG))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_9_31 = _read_json(Path(args.stage_9_31_summary))
    backlog_rows = _read_csv(Path(args.stage_9_31_backlog))
    handoff_rows = _handoff_items(backlog_rows)
    evidence_rows = _evidence_slices(backlog_rows)
    metrics_9_31 = stage_9_31.get("metrics", {})
    route_rows = _route_boundaries(metrics_9_31, handoff_rows)
    by_area = {row["backlog_area"]: row["count"] for row in handoff_rows}
    metrics = {
        "handoff_item_count": len(handoff_rows),
        "evidence_slice_count": len(evidence_rows),
        "total_priority_backlog_rows": sum(row["count"] for row in handoff_rows),
        "source_provenance_rows": by_area.get("source_provenance", 0),
        "query_family_empty_rows": by_area.get("query_family_empty", 0),
        "top1_family_coverage_rows": by_area.get("top1_family_coverage", 0),
        "label_or_taxonomy_mixture_rows": by_area.get("label_or_taxonomy_mixture", 0),
        "learnable_slice_count": metrics_9_31.get("learnable_slice_count", 0),
        "eligible_for_learning_after_9_30_counts": metrics_9_31.get("eligible_for_learning_after_9_30_counts", {}),
        "source_provenance_class_counts": metrics_9_31.get("source_provenance_class_counts", {}),
        "coverage_gap_class_counts": metrics_9_31.get("coverage_gap_class_counts", {}),
    }

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "handoff_items_csv": str(output_prefix.with_name(output_prefix.name + "_handoff_items.csv")),
        "route_boundary_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_route_boundary_decisions.csv")),
        "evidence_slices_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_slices.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.32 taxonomy/data-quality backlog handoff",
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
            "stage_9_31_summary": str(Path(args.stage_9_31_summary)),
            "stage_9_31_backlog": str(Path(args.stage_9_31_backlog)),
        },
        "metrics": metrics,
        "handoff_items": handoff_rows,
        "route_boundary_decisions": route_rows,
        "evidence_slices": evidence_rows,
        "decision": (
            "Move the residual 9.31 evidence into a taxonomy/data-quality backlog and keep it outside rank/recall learning. "
            "The P0 source-provenance and query_family_empty items must be resolved as data-quality work before any future learning claim; "
            "top1_family coverage and label-mixture items remain taxonomy/label backlog, not ranking rules."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.32 only packages backlog evidence from 9.31. It does not train, tune, patch rules, change ranking, modify GoalSearcher, "
            "use heldout for selection, connect online, relax gates, or convert taxonomy/data-quality issues into rank/recall learning evidence."
        ),
        "next_stage": {
            "stage": "9.33 9.x mining closure and next-strategy gate review",
            "goal": (
                "Read-only close the 9.x wrong-rank and recall-missing mining routes after taxonomy/data-quality handoff, "
                "then decide whether the next work should be a new 10.x accuracy strategy definition."
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

    _write_csv(
        Path(artifacts["handoff_items_csv"]),
        handoff_rows,
        [
            "backlog_area",
            "count",
            "priority",
            "handoff_owner_lane",
            "route_boundary",
            "learning_lane_disposition",
            "evidence",
            "recommended_review",
            "acceptance_check",
        ],
    )
    _write_csv(
        Path(artifacts["route_boundary_decisions_csv"]),
        route_rows,
        ["boundary", "status", "evidence_count", "decision", "next_action"],
    )
    _write_csv(
        Path(artifacts["evidence_slices_csv"]),
        evidence_rows,
        [
            "source_backlog_area",
            "mapped_backlog_area",
            "count",
            "evidence_slice",
            "non_learning_reason",
            "learning_lane_disposition",
        ],
    )
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": {
                    "handoff_item_count": metrics["handoff_item_count"],
                    "evidence_slice_count": metrics["evidence_slice_count"],
                    "total_priority_backlog_rows": metrics["total_priority_backlog_rows"],
                    "learnable_slice_count": metrics["learnable_slice_count"],
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
