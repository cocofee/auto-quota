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
DEFAULT_STAGE_10_21 = AGENT_STATE / "goal_10x_taxonomy_data_quality_prerequisite_contract_summary.json"
DEFAULT_STAGE_10_20 = AGENT_STATE / "goal_10x_broader_strategy_reentry_after_s3_parking_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_taxonomy_data_quality_contract_closure_reentry_gate"


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


def _closure_gates(stage_10_21: dict[str, Any], stage_10_20: dict[str, Any]) -> list[dict[str, Any]]:
    metrics_10_21 = stage_10_21.get("metrics", {})
    metrics_10_20 = stage_10_20.get("metrics", {})
    owner_contracts = stage_10_21.get("owner_contracts", [])
    acceptance_checks = stage_10_21.get("acceptance_checks", [])
    reentry_criteria = stage_10_21.get("reentry_criteria", [])
    learning_boundaries = stage_10_21.get("learning_boundaries", [])
    return [
        {
            "gate": "contract_defined",
            "status": "pass" if metrics_10_21.get("contract_defined") is True else "fail",
            "observed": f"contract_defined={metrics_10_21.get('contract_defined')}",
            "decision": "use_10_21_contract_as_closure_input",
            "not_allowed": "no re-opening strategy selection without new evidence",
        },
        {
            "gate": "contract_coverage_complete",
            "status": "pass" if len(owner_contracts) == 4 and len(acceptance_checks) >= 5 and len(reentry_criteria) == 4 else "fail",
            "observed": (
                f"owner_contract_count={len(owner_contracts)}; acceptance_check_count={len(acceptance_checks)}; "
                f"reentry_criteria_count={len(reentry_criteria)}; learning_boundary_count={len(learning_boundaries)}"
            ),
            "decision": "contract_covers_all_known_backlog_areas",
            "not_allowed": "no ad hoc backlog-to-learning shortcut",
        },
        {
            "gate": "learning_reentry_stays_closed_now",
            "status": "pass" if metrics_10_21.get("learning_reentry_allowed_now") is False else "fail",
            "observed": f"learning_reentry_allowed_now={metrics_10_21.get('learning_reentry_allowed_now')}",
            "decision": "park_learning_reentry",
            "not_allowed": "no training, no tuning, no rule patch, no ranking or GoalSearcher change",
        },
        {
            "gate": "execution_lanes_parked",
            "status": "pass"
            if metrics_10_20.get("s2_execution_lane_parked") is True
            and metrics_10_20.get("s3_execution_lane_parked") is True
            and metrics_10_20.get("whatif_execution_allowed") is False
            else "fail",
            "observed": (
                f"s2_execution_lane_parked={metrics_10_20.get('s2_execution_lane_parked')}; "
                f"s3_execution_lane_parked={metrics_10_20.get('s3_execution_lane_parked')}; "
                f"whatif_execution_allowed={metrics_10_20.get('whatif_execution_allowed')}"
            ),
            "decision": "keep_execution_lanes_outside_10_22",
            "not_allowed": "no S2/S3 execution, what-if, threshold change, or implementation",
        },
        {
            "gate": "backlog_route_can_open_as_non_learning",
            "status": "pass" if metrics_10_21.get("total_priority_backlog_rows", 0) > 0 else "fail",
            "observed": f"total_priority_backlog_rows={metrics_10_21.get('total_priority_backlog_rows', 0)}",
            "decision": "open_data_quality_route_reference_only",
            "not_allowed": "no Top1 gain claim or learning evidence claim from backlog rows",
        },
        {
            "gate": "future_reentry_is_conditioned_not_admitted",
            "status": "pass" if all(row.get("evidence_allowed_after_pass") for row in reentry_criteria) else "fail",
            "observed": f"future_reentry_criteria_defined={len(reentry_criteria)}",
            "decision": "future_reentry_requires_accepted_clean_evidence",
            "not_allowed": "no heldout/hard selection and no using backlog fixes as gain proof",
        },
    ]


def _route_decisions(stage_10_21: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_21.get("metrics", {})
    return [
        {
            "decision_area": "10x_strategy_loop",
            "decision": "CLOSE_CURRENT_10X_SELECTION_LOOP",
            "basis": (
                "10.20 selected the taxonomy/data-quality prerequisite lane and 10.21 defined complete owner, acceptance, "
                "and re-entry contracts. No further 10.x strategy branching is needed until accepted backlog evidence exists."
            ),
            "allowed_next": "read-only backlog route handoff and future re-entry parking review",
            "not_allowed": "no new learning lane, no execution lane, no gate relaxation",
        },
        {
            "decision_area": "data_quality_backlog_route",
            "decision": "OPEN_NON_LEARNING_BACKLOG_ROUTE",
            "basis": (
                f"total_priority_backlog_rows={metrics.get('total_priority_backlog_rows', 0)} remain owned by data-quality/taxonomy lanes "
                "with explicit acceptance checks and route boundaries."
            ),
            "allowed_next": "package backlog route as evidence/ownership work only",
            "not_allowed": "do not convert backlog route into ranking, recall, or training work",
        },
        {
            "decision_area": "learning_reentry_now",
            "decision": "KEEP_CLOSED",
            "basis": (
                "10.21 defined re-entry criteria but did not satisfy them. Source provenance, query_family_empty, top1 family coverage, "
                "and mixed taxonomy labels remain blocked until accepted cleanup artifacts exist."
            ),
            "allowed_next": "future read-only re-entry check after acceptance artifacts are independently produced",
            "not_allowed": "no training, tuning, ranking change, recall patch, or GoalSearcher edit",
        },
        {
            "decision_area": "next_stage",
            "decision": "DEFINE_10_23_BACKLOG_ROUTE_HANDOFF_AND_REENTRY_PARKING",
            "basis": "10.22 resolves the closure question by closing the strategy loop and parking learning re-entry behind backlog acceptance evidence.",
            "allowed_next": "stage 10.23 read-only backlog route handoff and learning re-entry parking review",
            "not_allowed": "no implementation or online integration",
        },
    ]


def _future_reentry_conditions(stage_10_21: dict[str, Any]) -> list[dict[str, Any]]:
    acceptance_checks = {row["backlog_area"]: row for row in stage_10_21.get("acceptance_checks", []) if row.get("backlog_area") != "all_backlog_areas"}
    rows: list[dict[str, Any]] = []
    for row in stage_10_21.get("reentry_criteria", []):
        backlog_area = row["backlog_area"]
        acceptance = acceptance_checks.get(backlog_area, {})
        rows.append(
            {
                "backlog_area": backlog_area,
                "eligible_future_lane": row["eligible_future_lane"],
                "required_acceptance_check": acceptance.get("pass_condition", ""),
                "required_before_reentry": row["required_before_reentry"],
                "evidence_allowed_after_pass": row["evidence_allowed_after_pass"],
                "still_forbidden_after_pass": row["still_forbidden_after_pass"],
                "current_status": "blocked_pending_cleanup_artifacts",
            }
        )
    rows.append(
        {
            "backlog_area": "all_backlog_areas",
            "eligible_future_lane": "future_read_only_reentry_review_only",
            "required_acceptance_check": "backlog rows are not counted as Top1 gain, training labels, recall rules, ranking features, or safety-gate thresholds",
            "required_before_reentry": "independent non-generated evidence must be inventoried in a later stage",
            "evidence_allowed_after_pass": "review-only evidence inventory",
            "still_forbidden_after_pass": "heldout/hard selection, direct training, threshold tuning, rule patch, ranking change, GoalSearcher change",
            "current_status": "global_non_learning_boundary_active",
        }
    )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_whatif_or_execution_lane",
            "reason": "10.22 is a read-only closure/re-entry gate; S2/S3 remain parked.",
            "allowed_after": "explicit later user go in a separate execution stage",
        },
        {
            "blocked_action": "train_tune_or_change_thresholds",
            "reason": "learning re-entry stays closed and no accepted cleanup artifacts exist.",
            "allowed_after": "future explicitly opened execution stage after accepted re-entry evidence",
        },
        {
            "blocked_action": "patch_rules_change_ranking_or_edit_goal_searcher",
            "reason": "backlog route is non-learning and non-implementation only.",
            "allowed_after": "future separately reviewed implementation lane, if ever opened",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "10.22 does not authorize ranking feature changes.",
            "allowed_after": "separate feature proposal plus leakage review",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "validation splits remain selection-forbidden.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "count_backlog_rows_as_learning_evidence",
            "reason": "the route opens as data-quality backlog only, not as gain evidence.",
            "allowed_after": "never directly; only independent cleaned evidence may be reviewed later",
        },
        {
            "blocked_action": "connect_online_or_enable_switches",
            "reason": "10.22 is read-only analysis only.",
            "allowed_after": "separate later readiness/integration stage",
        },
    ]


def _metrics(
    closure_gates: list[dict[str, Any]],
    route_decisions: list[dict[str, Any]],
    reentry_conditions: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_21: dict[str, Any],
) -> dict[str, Any]:
    metrics_10_21 = stage_10_21.get("metrics", {})
    gate_pass_count = sum(1 for row in closure_gates if row["status"] == "pass")
    return {
        "closure_gate_count": len(closure_gates),
        "closure_gate_pass_count": gate_pass_count,
        "closure_gate_fail_count": len(closure_gates) - gate_pass_count,
        "route_decision_count": len(route_decisions),
        "reentry_condition_count": len(reentry_conditions),
        "blocked_action_count": len(blocked_actions),
        "owner_contract_count": metrics_10_21.get("owner_contract_count", 0),
        "acceptance_check_count": metrics_10_21.get("acceptance_check_count", 0),
        "reentry_criteria_count": metrics_10_21.get("reentry_criteria_count", 0),
        "total_priority_backlog_rows": metrics_10_21.get("total_priority_backlog_rows", 0),
        "source_provenance_rows": metrics_10_21.get("source_provenance_rows", 0),
        "query_family_empty_rows": metrics_10_21.get("query_family_empty_rows", 0),
        "top1_family_coverage_rows": metrics_10_21.get("top1_family_coverage_rows", 0),
        "label_or_taxonomy_mixture_rows": metrics_10_21.get("label_or_taxonomy_mixture_rows", 0),
        "strategy_loop_closed_for_now": True,
        "data_quality_backlog_route_opened": True,
        "learning_reentry_allowed_now": False,
        "learning_reentry_parked_pending_acceptance": True,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.22 Taxonomy/Data-quality Contract Closure And Learning Re-entry Gate",
        "",
        "Read-only closure/re-entry review for the 10.21 taxonomy/data-quality prerequisite contract.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closure_gate_pass_count", metrics["closure_gate_pass_count"]],
                ["route_decision_count", metrics["route_decision_count"]],
                ["reentry_condition_count", metrics["reentry_condition_count"]],
                ["total_priority_backlog_rows", metrics["total_priority_backlog_rows"]],
                ["strategy_loop_closed_for_now", metrics["strategy_loop_closed_for_now"]],
                ["data_quality_backlog_route_opened", metrics["data_quality_backlog_route_opened"]],
                ["learning_reentry_allowed_now", metrics["learning_reentry_allowed_now"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Route Decisions",
        "",
        _md_table(
            [["decision_area", "decision", "allowed_next", "not_allowed"]]
            + [[row["decision_area"], row["decision"], row["allowed_next"], row["not_allowed"]] for row in report["route_decisions"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.22 taxonomy/data-quality contract closure and learning re-entry gate")
    parser.add_argument("--stage-10-21", default=str(DEFAULT_STAGE_10_21))
    parser.add_argument("--stage-10-20", default=str(DEFAULT_STAGE_10_20))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_21 = _read_json(Path(args.stage_10_21))
    stage_10_20 = _read_json(Path(args.stage_10_20))

    closure_gates = _closure_gates(stage_10_21, stage_10_20)
    route_decisions = _route_decisions(stage_10_21)
    reentry_conditions = _future_reentry_conditions(stage_10_21)
    blocked_actions = _blocked_actions()
    metrics = _metrics(closure_gates, route_decisions, reentry_conditions, blocked_actions, stage_10_21)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_gates_csv": str(output_prefix.with_name(output_prefix.name + "_closure_gates.csv")),
        "route_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_route_decisions.csv")),
        "future_reentry_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_future_reentry_conditions.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.22 taxonomy/data-quality contract closure and learning re-entry gate",
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
            "stage_10_21_contract": str(Path(args.stage_10_21)),
            "stage_10_20_reentry": str(Path(args.stage_10_20)),
        },
        "metrics": metrics,
        "closure_gates": closure_gates,
        "route_decisions": route_decisions,
        "future_learning_reentry_conditions": reentry_conditions,
        "blocked_actions": blocked_actions,
        "decision": (
            "Close the current 10.x strategy-selection loop using the 10.21 taxonomy/data-quality contract, and open a data-quality backlog route as a "
            "non-learning handoff only. Learning re-entry remains closed now: the contract is sufficient to park future re-entry behind accepted cleanup artifacts, "
            "but it does not authorize training, tuning, rule patches, ranking changes, GoalSearcher edits, heldout-based selection, or converting backlog rows "
            "into gain evidence."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.22 is a read-only closure/re-entry gate. It closes the current 10.x strategy loop and opens only a non-learning data-quality backlog route. "
            "It does not run what-if, train, tune, change thresholds, patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard "
            "for selection, relax gates, convert backlog rows into learning evidence, enable switches, or connect online."
        ),
        "next_stage": {
            "stage": "10.23 taxonomy/data-quality backlog route handoff and learning re-entry parking review",
            "goal": (
                "Read-only package the opened data-quality backlog route, confirm learning re-entry stays parked pending accepted cleanup artifacts, and keep the "
                "future re-entry path explicit without authorizing execution."
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

    _write_csv(Path(artifacts["closure_gates_csv"]), closure_gates, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(Path(artifacts["route_decisions_csv"]), route_decisions, ["decision_area", "decision", "basis", "allowed_next", "not_allowed"])
    _write_csv(
        Path(artifacts["future_reentry_conditions_csv"]),
        reentry_conditions,
        [
            "backlog_area",
            "eligible_future_lane",
            "required_acceptance_check",
            "required_before_reentry",
            "evidence_allowed_after_pass",
            "still_forbidden_after_pass",
            "current_status",
        ],
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
