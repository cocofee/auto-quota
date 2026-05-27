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
DEFAULT_STRATEGY_RETURN = AGENT_STATE / "goal_10x_dq_implementation_parked_broader_strategy_return_gate_summary.json"
DEFAULT_STRATEGY_INVENTORY = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory_summary.json"
DEFAULT_RECALL_KICKOFF = AGENT_STATE / "goal_recall_missing_decomposition_9x_kickoff_summary.json"
DEFAULT_RECALL_REVIEW = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_summary.json"
DEFAULT_LEARNABILITY_SLICES = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_learnability_slices.csv"
DEFAULT_COVERAGE_SUMMARY = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_coverage_summary.csv"
DEFAULT_SOURCE_ACCEPTANCE = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s1_recall_route_evidence_inventory_reentry_review"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
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
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    gate_checks: list[dict[str, Any]],
    route_options: list[dict[str, Any]],
    evidence_requests: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.43 S1 Recall Route Evidence Inventory Re-entry Review",
        "",
        "Read-only review of whether S1 recall work can continue without owner row mappings.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["dev_top80_missing", metrics["dev_top80_missing"]],
                ["dev_top80_recall_rate", metrics["dev_top80_recall_rate"]],
                ["stage_9_30_target_rows", metrics["stage_9_30_target_rows"]],
                ["stage_9_30_generated_rows", metrics["stage_9_30_generated_rows"]],
                ["stage_9_30_non_global_rows", metrics["stage_9_30_non_global_rows"]],
                ["learnable_slice_count", metrics["learnable_slice_count"]],
                ["owner_row_mappings_required_for_s1_review", metrics["owner_row_mappings_required_for_s1_review"]],
                ["s1_learning_reentry_allowed_now", metrics["s1_learning_reentry_allowed_now"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["gate", "status", "evidence", "decision"]]
            + [[row["gate"], row["status"], row["evidence"], row["decision"]] for row in gate_checks]
        ),
        "",
        "## Route Options",
        "",
        _md_table(
            [["route_option", "status", "rationale"]]
            + [[row["route_option"], row["status"], row["rationale"]] for row in route_options]
        ),
        "",
        "## Evidence Requests",
        "",
        _md_table(
            [["request_id", "required_content", "acceptance_check"]]
            + [[row["request_id"], row["required_content"], row["acceptance_check"]] for row in evidence_requests]
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
    path.write_text("\n".join(lines), encoding="utf-8")


def _find_strategy(inventory: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    for row in inventory.get("evidence_inventory", []):
        if row.get("strategy_id") == strategy_id:
            return row
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Review S1 recall route re-entry evidence without owner row mappings")
    parser.add_argument("--strategy-return", default=str(DEFAULT_STRATEGY_RETURN))
    parser.add_argument("--strategy-inventory", default=str(DEFAULT_STRATEGY_INVENTORY))
    parser.add_argument("--recall-kickoff", default=str(DEFAULT_RECALL_KICKOFF))
    parser.add_argument("--recall-review", default=str(DEFAULT_RECALL_REVIEW))
    parser.add_argument("--learnability-slices", default=str(DEFAULT_LEARNABILITY_SLICES))
    parser.add_argument("--coverage-summary", default=str(DEFAULT_COVERAGE_SUMMARY))
    parser.add_argument("--source-acceptance", default=str(DEFAULT_SOURCE_ACCEPTANCE))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    strategy_return = _read_json(Path(args.strategy_return))
    strategy_inventory = _read_json(Path(args.strategy_inventory))
    recall_kickoff = _read_json(Path(args.recall_kickoff))
    recall_review = _read_json(Path(args.recall_review))
    source_acceptance = _read_json(Path(args.source_acceptance))
    learnability_slices = _read_csv(Path(args.learnability_slices))
    coverage_summary = _read_csv(Path(args.coverage_summary))

    s1_inventory = _find_strategy(strategy_inventory, "S1_recall_route_evidence_inventory")
    kickoff_metrics = recall_kickoff["metrics"]
    recall_overview = kickoff_metrics["recall_missing_overview"]
    review_metrics = recall_review["metrics"]
    acceptance_metrics = source_acceptance["metrics"]

    target_rows = int(review_metrics["target_rows"])
    generated_rows = int(review_metrics["source_provenance_class_counts"]["generated_global_repair_decision_table"])
    non_global_rows = int(review_metrics["source_provenance_class_counts"]["non_global_eval_trace"])
    learnable_slice_count = int(review_metrics["learnable_slice_count"])
    generated_rate = round(generated_rows / target_rows, 6) if target_rows else 0.0

    gate_checks = [
        {
            "gate": "owner_row_mapping_dependency",
            "status": "pass_not_required_for_review",
            "evidence": "S1 recall evidence review consumes existing dev/OOF/provenance artifacts; DQ row mappings are only required for DQ implementation.",
            "decision": "S1 review can run without the 64 owner row mappings.",
        },
        {
            "gate": "independent_non_generated_recall_evidence",
            "status": "fail_current_evidence",
            "evidence": f"9.30 target rows={target_rows}; generated_global_repair_decision_table rows={generated_rows}; non_global_eval_trace rows={non_global_rows}.",
            "decision": "Do not treat current recall-missing evidence as transferable recall learning evidence.",
        },
        {
            "gate": "learnable_slice_support",
            "status": "fail_current_evidence",
            "evidence": f"learnable_slice_count={learnable_slice_count}; all learnability_slices eligible_for_learning_after_9_30=no.",
            "decision": "No current internal slice can enter recall training, tuning, or rule implementation.",
        },
        {
            "gate": "taxonomy_vs_recall_separation",
            "status": "fail_for_recall_learning",
            "evidence": "Non-global rows are small and point to top1/query taxonomy coverage or label backlog.",
            "decision": "Route taxonomy-empty and label-mixture evidence to DQ backlog, not recall learning.",
        },
        {
            "gate": "accepted_oss_source_provenance",
            "status": "partial_pass_for_source_scope_only",
            "evidence": f"accepted_human_oss_source_file_count={acceptance_metrics['accepted_human_oss_source_file_count']}; accepted_source_family_count={acceptance_metrics['accepted_source_family_count']}.",
            "decision": "Accepted OSS provenance helps define future evidence scope, but does not itself create positive S1 recall evidence.",
        },
    ]

    route_options = [
        {
            "route_option": "S1_internal_existing_evidence_learning_lane",
            "status": "blocked",
            "rationale": "Existing 9.x recall-missing review found learnable_slice_count=0 and generated/source-dominated recall rows.",
        },
        {
            "route_option": "S1_independent_non_generated_recall_evidence_request",
            "status": "allowed_read_only_next",
            "rationale": "This route does not need owner row mappings, but it does need new accepted OSS/dev/OOF recall evidence before any learning re-entry.",
        },
        {
            "route_option": "S1_DQ_taxonomy_artifact_route",
            "status": "already_routed_to_DQ_backlog",
            "rationale": "query_family_empty/top1_family_empty/label mixture evidence is DQ coverage work, not recall objective evidence.",
        },
        {
            "route_option": "S1_train_or_rule_patch",
            "status": "blocked",
            "rationale": "No independent non-generated positive recall slice exists; implementation would be source-artifact chasing.",
        },
        {
            "route_option": "return_to_broader_10x_strategy",
            "status": "available_after_closure",
            "rationale": "If no new S1 evidence package is available, broader strategy review can choose a lane not blocked by source dominance.",
        },
    ]

    evidence_requests = [
        {
            "request_id": "S1_ACCEPTED_OSS_RECALL_MISSING_ROWS",
            "required_content": "dev/OOF-only top80_missing rows from accepted human OSS sources, with generated/global repair-decision rows excluded.",
            "acceptance_check": "source_family_count >= 2; source_file provenance accepted; no global_repair_decision_table rows counted as positive evidence.",
        },
        {
            "request_id": "S1_TRUE_RECALL_FAILURE_LABEL",
            "required_content": "row-level classification separating true candidate-not-in-top80 recall failure from query_family_empty, top1_family_empty, book-label-empty, and label-mixture cases.",
            "acceptance_check": "true_missing_recall rows remain after DQ/taxonomy-empty exclusions; each row has query_family/top1_family/source/province fields populated where possible.",
        },
        {
            "request_id": "S1_POSITIVE_SUPPORT_BUCKET",
            "required_content": "a recall route bucket with enough support across accepted OSS sources to justify future offline analysis.",
            "acceptance_check": "support >= 20 or explicit lower-support exception with >=2 source families and clear repeated semantic pattern.",
        },
        {
            "request_id": "S1_LOSS_AUDIT_BOUNDARY",
            "required_content": "required future loss slices before any recall change: query_family, source_family, province, book_relation, and taxonomy-empty status.",
            "acceptance_check": "future experiment plan must include loss budget and no heldout/hard selection.",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "start_s1_recall_training_or_tuning",
            "reason": "Current S1 evidence has learnable_slice_count=0 and is dominated by generated/global repair-decision provenance.",
            "allowed_after": "future accepted-OSS recall evidence package passes S1 request checks and an explicit execution gate.",
        },
        {
            "blocked_action": "write_recall_rules_or_change_goal_searcher",
            "reason": "10.43 is a read-only re-entry review and has no implementation authorization.",
            "allowed_after": "separate implementation plan and explicit go after validated offline evidence.",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "10.x selection remains dev/OOF-only; heldout/hard is not a tuning or selection source.",
            "allowed_after": "future validation-only gate after candidate freeze, not for selection.",
        },
        {
            "blocked_action": "convert_dq_taxonomy_rows_to_learning_evidence",
            "reason": "9.30 classified remaining non-global rows as taxonomy coverage or query taxonomy empty, not transferable recall rules.",
            "allowed_after": "accepted DQ artifacts plus separate learning re-entry evidence.",
        },
        {
            "blocked_action": "resume_dq_implementation",
            "reason": "DQ implementation remains parked and still requires explicit go plus owner row mappings.",
            "allowed_after": "explicit implementation go plus complete owner row mapping package.",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "route_options_csv": str(output_prefix.with_name(output_prefix.name + "_route_options.csv")),
        "evidence_requests_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_requests.csv")),
        "coverage_reentry_review_csv": str(output_prefix.with_name(output_prefix.name + "_coverage_reentry_review.csv")),
        "learnability_reentry_review_csv": str(output_prefix.with_name(output_prefix.name + "_learnability_reentry_review.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    coverage_review_rows = [
        {
            "bucket_key": row.get("bucket_key", ""),
            "count": row.get("count", ""),
            "source_count": row.get("source_count", ""),
            "dominant_source": row.get("dominant_source", ""),
            "dominant_source_rate": row.get("dominant_source_rate", ""),
            "top_learnability_status": row.get("top_learnability_status", ""),
            "reentry_decision": "DQ_or_source_review_only_not_S1_learning",
        }
        for row in coverage_summary
    ]
    learnability_review_rows = [
        {
            "bucket_key": row.get("bucket_key", ""),
            "count": row.get("count", ""),
            "source_count": row.get("source_count", ""),
            "dominant_source": row.get("dominant_source", ""),
            "eligible_for_learning_after_9_30": row.get("eligible_for_learning_after_9_30", ""),
            "non_eligible_reason": row.get("non_eligible_reason", ""),
            "reentry_decision": "not_eligible_for_S1_learning_now",
        }
        for row in learnability_slices
    ]

    metrics = {
        "selected_next_strategy_lane_from_10_42": strategy_return["metrics"]["selected_next_strategy_lane"],
        "s1_10_1_readiness": s1_inventory.get("readiness", "unknown"),
        "s1_10_1_blocker_risk": s1_inventory.get("blocker_risk", "unknown"),
        "dev_top80_missing": kickoff_metrics["dev_decomposition"]["dev_top80_missing"],
        "dev_top80_recall_rate": kickoff_metrics["dev_decomposition"]["dev_top80_recall_rate"],
        "dev_top80_missing_rate": kickoff_metrics["dev_decomposition"]["dev_top80_missing_rate"],
        "stage_9_28_dominant_source": recall_overview["dominant_source"],
        "stage_9_28_dominant_source_count": recall_overview["dominant_source_count"],
        "stage_9_28_dominant_source_rate": recall_overview["dominant_source_rate"],
        "stage_9_30_target_rows": target_rows,
        "stage_9_30_generated_rows": generated_rows,
        "stage_9_30_generated_rate": generated_rate,
        "stage_9_30_non_global_rows": non_global_rows,
        "learnable_slice_count": learnable_slice_count,
        "coverage_bucket_count": len(coverage_summary),
        "learnability_slice_rows": len(learnability_slices),
        "owner_row_mappings_required_for_s1_review": False,
        "owner_row_mappings_required_for_s1_learning": False,
        "independent_non_generated_recall_evidence_available_now": False,
        "s1_read_only_evidence_request_route_exists": True,
        "s1_internal_learning_lane_exists_now": False,
        "s1_learning_reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.43 S1 recall-route evidence inventory re-entry review",
        "read_only": True,
        "reentry_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "S1 has a real dev recall gap, and a read-only S1 evidence-request route exists without depending on the 64 owner row mappings. "
            "However, the current internal evidence does not contain a learnable or executable recall lane: 9.30 found learnable_slice_count=0, "
            "the reviewed rows are dominated by generated/global repair-decision provenance, and the remaining non-global rows are taxonomy/DQ coverage issues. "
            "Keep S1 learning re-entry closed until a new accepted-OSS, non-generated recall evidence package is supplied."
        ),
        "anti_drift_conclusion": (
            "10.43 only reviews S1 recall evidence inventory and writes audit artifacts. It does not train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, implement DQ fixes, or convert DQ backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "10.44 S1 independent recall evidence request / broader strategy closure",
            "goal": "Read-only either request an accepted-OSS non-generated recall evidence package for S1, or close S1 and return to broader 10.x strategy if no such package is available.",
            "default": "do_not_execute_or_train; no S1 learning re-entry without new accepted OSS recall evidence",
        },
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["route_options_csv"]), route_options, ["route_option", "status", "rationale"])
    _write_csv(Path(artifacts["evidence_requests_csv"]), evidence_requests, ["request_id", "required_content", "acceptance_check"])
    _write_csv(
        Path(artifacts["coverage_reentry_review_csv"]),
        coverage_review_rows,
        ["bucket_key", "count", "source_count", "dominant_source", "dominant_source_rate", "top_learnability_status", "reentry_decision"],
    )
    _write_csv(
        Path(artifacts["learnability_reentry_review_csv"]),
        learnability_review_rows,
        ["bucket_key", "count", "source_count", "dominant_source", "eligible_for_learning_after_9_30", "non_eligible_reason", "reentry_decision"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_checks, route_options, evidence_requests)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
