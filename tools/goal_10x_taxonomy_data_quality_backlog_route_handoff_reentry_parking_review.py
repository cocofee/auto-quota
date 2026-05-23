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
DEFAULT_STAGE_10_22 = AGENT_STATE / "goal_10x_taxonomy_data_quality_contract_closure_reentry_gate_summary.json"
DEFAULT_STAGE_10_21 = AGENT_STATE / "goal_10x_taxonomy_data_quality_prerequisite_contract_summary.json"
DEFAULT_STAGE_9_32 = AGENT_STATE / "goal_taxonomy_data_quality_backlog_handoff_9x_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_taxonomy_data_quality_backlog_route_handoff_reentry_parking_review"


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


def _backlog_route_handoff(stage_10_21: dict[str, Any], stage_9_32: dict[str, Any]) -> list[dict[str, Any]]:
    backlog_rows = {row["backlog_area"]: row for row in stage_9_32.get("handoff_items", [])}
    rows: list[dict[str, Any]] = []
    for row in stage_10_21.get("owner_contracts", []):
        backlog_area = row["backlog_area"]
        prior = backlog_rows.get(backlog_area, {})
        rows.append(
            {
                "backlog_area": backlog_area,
                "priority": row["priority"],
                "owner_lane": row["owner_lane"],
                "row_count": row["row_count"],
                "route_boundary": row["route_boundary"],
                "required_evidence_output": row["required_evidence_output"],
                "acceptance_check": row["acceptance_check"],
                "reentry_gate": row["reentry_gate"],
                "learning_boundary": row["learning_boundary"],
                "recommended_review": prior.get("recommended_review", ""),
                "handoff_status": "open_non_learning_backlog_route",
            }
        )
    return rows


def _acceptance_boundary_register(stage_10_21: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_21.get("acceptance_checks", []):
        rows.append(
            {
                "check_id": row["check_id"],
                "backlog_area": row["backlog_area"],
                "owner_lane": row["owner_lane"],
                "minimum_artifact": row["minimum_artifact"],
                "pass_condition": row["pass_condition"],
                "fail_action": row["fail_action"],
                "review_split_policy": row["review_split_policy"],
                "current_status": "pending_artifact_and_manual_acceptance",
            }
        )
    return rows


def _learning_reentry_parking_decisions(stage_10_22: dict[str, Any], stage_10_21: dict[str, Any]) -> list[dict[str, Any]]:
    route_decisions = {row["decision_area"]: row for row in stage_10_22.get("route_decisions", [])}
    rows = [
        {
            "parking_area": "learning_reentry_now",
            "status": "parked",
            "evidence": f"learning_reentry_allowed_now={stage_10_22['metrics'].get('learning_reentry_allowed_now')}",
            "decision": route_decisions.get("learning_reentry_now", {}).get("decision", "KEEP_CLOSED"),
            "resume_condition": "all relevant acceptance artifacts exist and a later read-only re-entry review confirms independent non-generated evidence",
            "not_allowed": "no training, no tuning, no rule patch, no ranking change, no GoalSearcher change",
        },
        {
            "parking_area": "data_quality_backlog_route",
            "status": "open_reference_only",
            "evidence": f"data_quality_backlog_route_opened={stage_10_22['metrics'].get('data_quality_backlog_route_opened')}",
            "decision": route_decisions.get("data_quality_backlog_route", {}).get("decision", "OPEN_NON_LEARNING_BACKLOG_ROUTE"),
            "resume_condition": "owner lanes produce artifacts and acceptance checks pass",
            "not_allowed": "do not convert backlog route directly into gain evidence",
        },
        {
            "parking_area": "10x_strategy_loop",
            "status": "closed_for_new_branching",
            "evidence": f"strategy_loop_closed_for_now={stage_10_22['metrics'].get('strategy_loop_closed_for_now')}",
            "decision": route_decisions.get("10x_strategy_loop", {}).get("decision", "CLOSE_CURRENT_10X_SELECTION_LOOP"),
            "resume_condition": "new accepted evidence justifies a later explicit strategy re-entry review",
            "not_allowed": "no reopening of S1/S2/S3 or generic 10.x changes by default",
        },
        {
            "parking_area": "global_non_learning_boundary",
            "status": "active",
            "evidence": "ACCEPT_NO_DIRECT_LEARNING remains in force for all backlog areas",
            "decision": "KEEP_BACKLOG_OUTSIDE_LEARNING_LANE",
            "resume_condition": "never directly; only later independent evidence review can reopen",
            "not_allowed": "no Top1 gain claims, no training labels, no recall rules, no ranking features from backlog rows",
        },
    ]
    return rows


def _future_reentry_triggers(stage_10_22: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_22.get("future_learning_reentry_conditions", []):
        rows.append(
            {
                "backlog_area": row["backlog_area"],
                "eligible_future_lane": row["eligible_future_lane"],
                "required_acceptance_check": row["required_acceptance_check"],
                "required_before_reentry": row["required_before_reentry"],
                "evidence_allowed_after_pass": row["evidence_allowed_after_pass"],
                "still_forbidden_after_pass": row["still_forbidden_after_pass"],
                "trigger_status": "not_ready_pending_artifacts" if row["backlog_area"] != "all_backlog_areas" else "global_boundary_always_required",
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "reenter_learning_now",
            "reason": "10.23 only hands off the backlog route and confirms re-entry remains parked.",
            "allowed_after": "future read-only re-entry review after accepted cleanup artifacts exist",
        },
        {
            "blocked_action": "train_tune_or_run_whatif",
            "reason": "backlog route handoff is non-learning and non-execution only.",
            "allowed_after": "separate explicitly opened execution or training stage",
        },
        {
            "blocked_action": "patch_rules_change_ranking_or_edit_goal_searcher",
            "reason": "10.23 does not authorize implementation changes.",
            "allowed_after": "separate future implementation review, if ever opened",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "feature whitelist changes remain outside the data-quality handoff route.",
            "allowed_after": "separate feature proposal with leakage review",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "validation splits remain selection-forbidden.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "count_backlog_rows_as_learning_evidence",
            "reason": "backlog rows remain ownership/reference work, not gain evidence.",
            "allowed_after": "never directly; only later independent cleaned evidence may be reviewed",
        },
        {
            "blocked_action": "connect_online_enable_switches_or_relax_gates",
            "reason": "10.23 is a read-only handoff/parking review.",
            "allowed_after": "separate readiness or policy stage",
        },
    ]


def _metrics(
    route_handoff: list[dict[str, Any]],
    acceptance_boundaries: list[dict[str, Any]],
    parking_decisions: list[dict[str, Any]],
    future_triggers: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_22: dict[str, Any],
) -> dict[str, Any]:
    owner_lanes = sorted({row["owner_lane"] for row in route_handoff})
    return {
        "backlog_route_handoff_count": len(route_handoff),
        "owner_lane_count": len(owner_lanes),
        "acceptance_boundary_count": len(acceptance_boundaries),
        "parking_decision_count": len(parking_decisions),
        "future_reentry_trigger_count": len(future_triggers),
        "blocked_action_count": len(blocked_actions),
        "total_priority_backlog_rows": stage_10_22["metrics"].get("total_priority_backlog_rows", 0),
        "source_provenance_rows": stage_10_22["metrics"].get("source_provenance_rows", 0),
        "query_family_empty_rows": stage_10_22["metrics"].get("query_family_empty_rows", 0),
        "top1_family_coverage_rows": stage_10_22["metrics"].get("top1_family_coverage_rows", 0),
        "label_or_taxonomy_mixture_rows": stage_10_22["metrics"].get("label_or_taxonomy_mixture_rows", 0),
        "data_quality_backlog_route_handed_off": True,
        "learning_reentry_parked": True,
        "strategy_loop_closed_for_now": True,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.23 Taxonomy/Data-quality Backlog Route Handoff And Learning Re-entry Parking Review",
        "",
        "Read-only handoff of the opened taxonomy/data-quality backlog route. Learning re-entry remains parked pending accepted cleanup artifacts.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["backlog_route_handoff_count", metrics["backlog_route_handoff_count"]],
                ["owner_lane_count", metrics["owner_lane_count"]],
                ["acceptance_boundary_count", metrics["acceptance_boundary_count"]],
                ["parking_decision_count", metrics["parking_decision_count"]],
                ["future_reentry_trigger_count", metrics["future_reentry_trigger_count"]],
                ["total_priority_backlog_rows", metrics["total_priority_backlog_rows"]],
                ["data_quality_backlog_route_handed_off", metrics["data_quality_backlog_route_handed_off"]],
                ["learning_reentry_parked", metrics["learning_reentry_parked"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Backlog Route Handoff",
        "",
        _md_table(
            [["backlog_area", "priority", "owner_lane", "row_count", "handoff_status", "reentry_gate"]]
            + [
                [row["backlog_area"], row["priority"], row["owner_lane"], row["row_count"], row["handoff_status"], row["reentry_gate"]]
                for row in report["backlog_route_handoff"]
            ]
        ),
        "",
        "## Parking Decisions",
        "",
        _md_table(
            [["parking_area", "status", "decision", "resume_condition"]]
            + [
                [row["parking_area"], row["status"], row["decision"], row["resume_condition"]]
                for row in report["learning_reentry_parking_decisions"]
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
    parser = argparse.ArgumentParser(description="Stage 10.23 taxonomy/data-quality backlog route handoff and learning re-entry parking review")
    parser.add_argument("--stage-10-22", default=str(DEFAULT_STAGE_10_22))
    parser.add_argument("--stage-10-21", default=str(DEFAULT_STAGE_10_21))
    parser.add_argument("--stage-9-32", default=str(DEFAULT_STAGE_9_32))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_22 = _read_json(Path(args.stage_10_22))
    stage_10_21 = _read_json(Path(args.stage_10_21))
    stage_9_32 = _read_json(Path(args.stage_9_32))

    route_handoff = _backlog_route_handoff(stage_10_21, stage_9_32)
    acceptance_boundaries = _acceptance_boundary_register(stage_10_21)
    parking_decisions = _learning_reentry_parking_decisions(stage_10_22, stage_10_21)
    future_triggers = _future_reentry_triggers(stage_10_22)
    blocked_actions = _blocked_actions()
    metrics = _metrics(route_handoff, acceptance_boundaries, parking_decisions, future_triggers, blocked_actions, stage_10_22)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "backlog_route_handoff_csv": str(output_prefix.with_name(output_prefix.name + "_backlog_route_handoff.csv")),
        "acceptance_boundary_register_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_boundary_register.csv")),
        "learning_reentry_parking_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_learning_reentry_parking_decisions.csv")),
        "future_reentry_triggers_csv": str(output_prefix.with_name(output_prefix.name + "_future_reentry_triggers.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.23 taxonomy/data-quality backlog route handoff and learning re-entry parking review",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_whatif_execution": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_22_closure_gate": str(Path(args.stage_10_22)),
            "stage_10_21_contract": str(Path(args.stage_10_21)),
            "stage_9_32_backlog_handoff": str(Path(args.stage_9_32)),
        },
        "metrics": metrics,
        "backlog_route_handoff": route_handoff,
        "acceptance_boundary_register": acceptance_boundaries,
        "learning_reentry_parking_decisions": parking_decisions,
        "future_reentry_triggers": future_triggers,
        "blocked_actions": blocked_actions,
        "decision": (
            "Hand off the opened taxonomy/data-quality backlog route to its owner lanes with the 10.21 acceptance and re-entry boundaries intact, "
            "and keep learning re-entry parked. This stage records who owns each backlog area, what artifact is required before any future re-entry review, "
            "and preserves the non-learning boundary so backlog rows remain outside training, tuning, ranking, recall-rule, and GoalSearcher changes."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.23 is a read-only backlog-route handoff and parking review. It does not run what-if, train, tune, change thresholds, patch rules, "
            "change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, convert backlog rows into learning evidence, "
            "enable switches, or connect online."
        ),
        "next_stage": {
            "stage": "10.24 taxonomy/data-quality backlog parked checkpoint and evidence-wait review",
            "goal": (
                "Read-only confirm the backlog route remains parked outside learning until accepted cleanup artifacts exist, and record the evidence-wait checkpoints "
                "that must be satisfied before any future re-entry review can even be considered."
            ),
            "prohibited": [
                "what-if execution",
                "training",
                "tuning",
                "threshold changes",
                "rule patches",
                "GoalSearcher changes",
                "ranking changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
                "counting backlog rows as learning evidence",
            ],
        },
    }

    _write_csv(
        Path(artifacts["backlog_route_handoff_csv"]),
        route_handoff,
        ["backlog_area", "priority", "owner_lane", "row_count", "route_boundary", "required_evidence_output", "acceptance_check", "reentry_gate", "learning_boundary", "recommended_review", "handoff_status"],
    )
    _write_csv(
        Path(artifacts["acceptance_boundary_register_csv"]),
        acceptance_boundaries,
        ["check_id", "backlog_area", "owner_lane", "minimum_artifact", "pass_condition", "fail_action", "review_split_policy", "current_status"],
    )
    _write_csv(
        Path(artifacts["learning_reentry_parking_decisions_csv"]),
        parking_decisions,
        ["parking_area", "status", "evidence", "decision", "resume_condition", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["future_reentry_triggers_csv"]),
        future_triggers,
        ["backlog_area", "eligible_future_lane", "required_acceptance_check", "required_before_reentry", "evidence_allowed_after_pass", "still_forbidden_after_pass", "trigger_status"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": metrics,
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
