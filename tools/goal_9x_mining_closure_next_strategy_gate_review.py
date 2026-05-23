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
DEFAULT_WRONG_RANK_CLOSURE = AGENT_STATE / "goal_no_eligible_wrong_rank_closure_9x_summary.json"
DEFAULT_RECALL_CLOSURE = AGENT_STATE / "goal_recall_missing_no_eligible_closure_9x_summary.json"
DEFAULT_BACKLOG_HANDOFF = AGENT_STATE / "goal_taxonomy_data_quality_backlog_handoff_9x_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_9x_mining_closure_next_strategy_gate_9x"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _lane_decisions(wrong_rank: dict[str, Any], recall: dict[str, Any], backlog: dict[str, Any]) -> list[dict[str, Any]]:
    wrong_metrics = wrong_rank.get("metrics", {}).get("wrong_rank_lane", {})
    recall_metrics = recall.get("metrics", {})
    backlog_metrics = backlog.get("metrics", {})
    return [
        {
            "lane": "wrong_rank_bucket_mining",
            "input_stage": "9.27",
            "primary_metric": "eligible_candidates",
            "primary_value": wrong_metrics.get("eligible_candidates", 0),
            "supporting_evidence": (
                f"remaining_dev_wrong_rank_rows={wrong_metrics.get('remaining_dev_wrong_rank_rows', 0)}; "
                f"candidate_groups={wrong_metrics.get('candidate_groups', 0)}; "
                f"support_below_20_candidates={wrong_metrics.get('support_below_20_candidates', 0)}; "
                f"source_below_2_candidates={wrong_metrics.get('source_below_2_candidates', 0)}"
            ),
            "closure_decision": "closed",
            "reason": "no eligible remaining high-support wrong-rank bucket under the original support/province/source gate",
        },
        {
            "lane": "recall_missing_bucket_mining",
            "input_stage": "9.31",
            "primary_metric": "learnable_slice_count",
            "primary_value": recall_metrics.get("learnable_slice_count", 0),
            "supporting_evidence": (
                f"target_rows={recall_metrics.get('target_rows', 0)}; "
                f"learnability_slice_rows={recall_metrics.get('learnability_slice_rows', 0)}; "
                f"eligible_counts={recall_metrics.get('eligible_for_learning_after_9_30_counts', {})}; "
                f"blocked_source_provenance={recall_metrics.get('learnability_status_counts', {}).get('blocked_source_provenance', 0)}"
            ),
            "closure_decision": "closed",
            "reason": "no eligible learning slice after source-provenance and taxonomy coverage review",
        },
        {
            "lane": "taxonomy_data_quality_residuals",
            "input_stage": "9.32",
            "primary_metric": "handoff_item_count",
            "primary_value": backlog_metrics.get("handoff_item_count", 0),
            "supporting_evidence": (
                f"total_priority_backlog_rows={backlog_metrics.get('total_priority_backlog_rows', 0)}; "
                f"source_provenance={backlog_metrics.get('source_provenance_rows', 0)}; "
                f"query_family_empty={backlog_metrics.get('query_family_empty_rows', 0)}; "
                f"top1_family_coverage={backlog_metrics.get('top1_family_coverage_rows', 0)}; "
                f"label_or_taxonomy_mixture={backlog_metrics.get('label_or_taxonomy_mixture_rows', 0)}"
            ),
            "closure_decision": "not_learning_lane",
            "reason": "residuals are data-quality/taxonomy backlog and must not be converted into rank/recall learning evidence",
        },
    ]


def _next_strategy_options() -> list[dict[str, Any]]:
    return [
        {
            "option": "continue_9x_bucket_mining",
            "admissible": "no",
            "reason": "both wrong-rank and recall-missing mining lanes are closed with zero eligible learning buckets",
            "required_before_action": "new evidence source or new route definition; not a gate relaxation",
            "recommendation": "stop",
        },
        {
            "option": "relax_support_source_or_province_gate",
            "admissible": "no",
            "reason": "9.27 showed relaxation mainly admits low-support fragments or single-source artifacts",
            "required_before_action": "explicit policy review outside 9.x mining; heldout still cannot be used for threshold selection",
            "recommendation": "do_not_use_as_next_step",
        },
        {
            "option": "learn_from_taxonomy_data_quality_backlog",
            "admissible": "no",
            "reason": "9.32 classifies residuals as provenance/taxonomy/label coverage work, not learning evidence",
            "required_before_action": "resolve provenance and taxonomy coverage, then re-open with independent evidence if needed",
            "recommendation": "keep_outside_learning_lane",
        },
        {
            "option": "define_10x_accuracy_strategy",
            "admissible": "yes",
            "reason": "9.x mining has exhausted current bucket routes; the next useful work is a broader strategy definition",
            "required_before_action": "read-only strategy framing: candidate levers, evidence requirements, split policy, and loss audit plan",
            "recommendation": "next_stage",
        },
    ]


def _entry_checklist() -> list[dict[str, Any]]:
    return [
        {
            "check": "strategy_scope",
            "required": "define candidate 10.x levers before implementation",
            "status": "required_next",
            "notes": "Examples: recall route expansion, feature/LTR strategy, safety-gate recalibration, taxonomy-informed data cleanup; no implementation in 9.33.",
        },
        {
            "check": "evidence_policy",
            "required": "dev/OOF for strategy selection; heldout only for final validation",
            "status": "required_next",
            "notes": "Keeps the same anti-leakage boundary used throughout 9.x.",
        },
        {
            "check": "loss_audit",
            "required": "every future gain claim must carry loss buckets and regression checks",
            "status": "required_next",
            "notes": "Needed before any future LTR, recall, or safety-gate change.",
        },
        {
            "check": "data_quality_boundary",
            "required": "taxonomy/data-quality backlog cannot be used as direct rank/recall learning evidence",
            "status": "already_set_by_9_32",
            "notes": "P0 provenance and query_family_empty items stay outside the learning lane.",
        },
    ]


def _metrics(wrong_rank: dict[str, Any], recall: dict[str, Any], backlog: dict[str, Any]) -> dict[str, Any]:
    wrong_lane = wrong_rank.get("metrics", {}).get("wrong_rank_lane", {})
    recall_metrics = recall.get("metrics", {})
    backlog_metrics = backlog.get("metrics", {})
    return {
        "wrong_rank_eligible_candidates": wrong_lane.get("eligible_candidates", 0),
        "wrong_rank_remaining_rows": wrong_lane.get("remaining_dev_wrong_rank_rows", 0),
        "wrong_rank_candidate_groups": wrong_lane.get("candidate_groups", 0),
        "wrong_rank_gate_relax_low_support_groups": len(
            wrong_rank.get("metrics", {}).get("gate_policy_review", {}).get("lower_support_to_10_keep_diversity", [])
        ),
        "wrong_rank_gate_relax_single_source_groups": len(
            wrong_rank.get("metrics", {}).get("gate_policy_review", {}).get("lower_source_to_1_keep_support", [])
        ),
        "recall_learnable_slice_count": recall_metrics.get("learnable_slice_count", 0),
        "recall_learnability_slice_rows": recall_metrics.get("learnability_slice_rows", 0),
        "recall_target_rows": recall_metrics.get("target_rows", 0),
        "recall_blocked_source_provenance": recall_metrics.get("learnability_status_counts", {}).get("blocked_source_provenance", 0),
        "taxonomy_backlog_handoff_items": backlog_metrics.get("handoff_item_count", 0),
        "taxonomy_backlog_priority_rows": backlog_metrics.get("total_priority_backlog_rows", 0),
        "all_9x_mining_lanes_closed": (
            int(wrong_lane.get("eligible_candidates") or 0) == 0
            and int(recall_metrics.get("learnable_slice_count") or 0) == 0
            and int(backlog_metrics.get("handoff_item_count") or 0) > 0
        ),
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 9.33 9.x Mining Closure and Next-strategy Gate Review",
        "",
        "Read-only closure review combining the wrong-rank, recall-missing, and taxonomy/data-quality handoff conclusions from stages 9.27, 9.31, and 9.32.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["wrong_rank_eligible_candidates", metrics.get("wrong_rank_eligible_candidates")],
                ["wrong_rank_remaining_rows", metrics.get("wrong_rank_remaining_rows")],
                ["wrong_rank_candidate_groups", metrics.get("wrong_rank_candidate_groups")],
                ["recall_learnable_slice_count", metrics.get("recall_learnable_slice_count")],
                ["recall_learnability_slice_rows", metrics.get("recall_learnability_slice_rows")],
                ["recall_blocked_source_provenance", metrics.get("recall_blocked_source_provenance")],
                ["taxonomy_backlog_handoff_items", metrics.get("taxonomy_backlog_handoff_items")],
                ["taxonomy_backlog_priority_rows", metrics.get("taxonomy_backlog_priority_rows")],
                ["all_9x_mining_lanes_closed", metrics.get("all_9x_mining_lanes_closed")],
            ]
        ),
        "",
        "## Lane Closure Decisions",
        "",
        _md_table(
            [["lane", "input_stage", "primary_metric", "primary_value", "closure_decision", "reason"]]
            + [
                [
                    row["lane"],
                    row["input_stage"],
                    row["primary_metric"],
                    row["primary_value"],
                    row["closure_decision"],
                    row["reason"],
                ]
                for row in report["lane_closure_decisions"]
            ]
        ),
        "",
        "## Next Strategy Options",
        "",
        _md_table(
            [["option", "admissible", "recommendation", "reason"]]
            + [
                [
                    row["option"],
                    row["admissible"],
                    row["recommendation"],
                    row["reason"],
                ]
                for row in report["next_strategy_options"]
            ]
        ),
        "",
        "## 10.x Entry Checklist",
        "",
        _md_table(
            [["check", "required", "status", "notes"]]
            + [
                [
                    row["check"],
                    row["required"],
                    row["status"],
                    row["notes"],
                ]
                for row in report["strategy_entry_checklist"]
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
    parser = argparse.ArgumentParser(description="Stage 9.33 9.x mining closure and next-strategy gate review")
    parser.add_argument("--wrong-rank-closure", default=str(DEFAULT_WRONG_RANK_CLOSURE))
    parser.add_argument("--recall-closure", default=str(DEFAULT_RECALL_CLOSURE))
    parser.add_argument("--backlog-handoff", default=str(DEFAULT_BACKLOG_HANDOFF))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    wrong_rank = _read_json(Path(args.wrong_rank_closure))
    recall = _read_json(Path(args.recall_closure))
    backlog = _read_json(Path(args.backlog_handoff))
    metrics = _metrics(wrong_rank, recall, backlog)
    lane_rows = _lane_decisions(wrong_rank, recall, backlog)
    option_rows = _next_strategy_options()
    checklist_rows = _entry_checklist()

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "lane_closure_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_lane_closure_decisions.csv")),
        "next_strategy_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_strategy_options.csv")),
        "strategy_entry_checklist_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_entry_checklist.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.33 9.x mining closure and next-strategy gate review",
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
            "stage_9_27_wrong_rank_closure": str(Path(args.wrong_rank_closure)),
            "stage_9_31_recall_closure": str(Path(args.recall_closure)),
            "stage_9_32_backlog_handoff": str(Path(args.backlog_handoff)),
        },
        "metrics": metrics,
        "lane_closure_decisions": lane_rows,
        "next_strategy_options": option_rows,
        "strategy_entry_checklist": checklist_rows,
        "decision": (
            "Stop 9.x bucket mining under the current evidence and gate policy. The wrong-rank lane has zero eligible remaining buckets, "
            "the recall-missing lane has zero learnable slices, and residual taxonomy/data-quality issues have been handed off outside the learning lane. "
            "The next admissible step is a read-only 10.x accuracy strategy definition, not more bucket mining, gate relaxation, training, tuning, or rules."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.33 only closes the 9.x mining route and selects the next read-only planning lane. It does not train, tune, patch rules, "
            "change ranking, modify GoalSearcher, use heldout for selection, connect online, relax gates, or convert taxonomy/data-quality backlog into learning evidence."
        ),
        "next_stage": {
            "stage": "10.0 accuracy strategy definition",
            "goal": (
                "Read-only define the next accuracy strategy after 9.x mining closure: candidate levers, evidence requirements, "
                "split policy, loss-audit plan, and acceptance criteria before any implementation."
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
        Path(artifacts["lane_closure_decisions_csv"]),
        lane_rows,
        ["lane", "input_stage", "primary_metric", "primary_value", "supporting_evidence", "closure_decision", "reason"],
    )
    _write_csv(
        Path(artifacts["next_strategy_options_csv"]),
        option_rows,
        ["option", "admissible", "reason", "required_before_action", "recommendation"],
    )
    _write_csv(
        Path(artifacts["strategy_entry_checklist_csv"]),
        checklist_rows,
        ["check", "required", "status", "notes"],
    )
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": {
                    "wrong_rank_eligible_candidates": metrics["wrong_rank_eligible_candidates"],
                    "recall_learnable_slice_count": metrics["recall_learnable_slice_count"],
                    "taxonomy_backlog_handoff_items": metrics["taxonomy_backlog_handoff_items"],
                    "all_9x_mining_lanes_closed": metrics["all_9x_mining_lanes_closed"],
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
