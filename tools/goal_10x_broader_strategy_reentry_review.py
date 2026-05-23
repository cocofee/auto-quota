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
DEFAULT_STAGE_10_10 = AGENT_STATE / "goal_10x_s2_execution_lane_parking_strategy_return_gate_summary.json"
DEFAULT_STAGE_10_1 = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory_summary.json"
DEFAULT_STAGE_10_0 = AGENT_STATE / "goal_10x_accuracy_strategy_definition_summary.json"
DEFAULT_STAGE_10_6 = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_scope_lock_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_reentry_review"


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


def _by_id(rows: list[dict[str, Any]], key: str = "strategy_id") -> dict[str, dict[str, Any]]:
    return {str(row.get(key)): row for row in rows}


def _reentry_gates(stage_10_10: dict[str, Any], stage_10_6: dict[str, Any]) -> list[dict[str, Any]]:
    metrics_10_10 = stage_10_10.get("metrics", {})
    metrics_10_6 = stage_10_6.get("metrics", {})
    return [
        {
            "gate": "s2_execution_lane_parked",
            "status": "pass" if metrics_10_10.get("s2_execution_lane_parked") is True else "fail",
            "observed": f"s2_execution_lane_parked={metrics_10_10.get('s2_execution_lane_parked')}",
            "decision": "keep_s2_execution_parked",
            "not_allowed": "no dev/OOF experiment without explicit later go",
        },
        {
            "gate": "strategy_return_opened",
            "status": "pass" if metrics_10_10.get("strategy_return_selected") is True else "fail",
            "observed": f"strategy_return_selected={metrics_10_10.get('strategy_return_selected')}",
            "decision": "allow_broader_read_only_review",
            "not_allowed": "no execution-planning churn as default path",
        },
        {
            "gate": "s2_scope_preserved_but_not_executed",
            "status": "pass" if metrics_10_6.get("scope_locked") is True and metrics_10_10.get("execution_performed") is False else "fail",
            "observed": (
                f"scope_locked={metrics_10_6.get('scope_locked')}; "
                f"candidate_matrix_row_count={metrics_10_6.get('candidate_matrix_row_count')}; "
                f"execution_performed={metrics_10_10.get('execution_performed')}"
            ),
            "decision": "preserve_locked_s2_assets_as_reference_only",
            "not_allowed": "no S2 candidate scoring in 10.11",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass" if metrics_10_10.get("heldout_used_for_selection") is False else "fail",
            "observed": f"heldout_used_for_selection={metrics_10_10.get('heldout_used_for_selection')}",
            "decision": "dev_oof_only_for_strategy_selection",
            "not_allowed": "no heldout/hard threshold, strategy, candidate, or feature selection",
        },
        {
            "gate": "implementation_boundary",
            "status": (
                "pass"
                if metrics_10_10.get("implementation_allowed") is False and metrics_10_10.get("training_allowed") is False
                else "fail"
            ),
            "observed": (
                f"implementation_allowed={metrics_10_10.get('implementation_allowed')}; "
                f"training_allowed={metrics_10_10.get('training_allowed')}"
            ),
            "decision": "review_only",
            "not_allowed": "no training, tuning, rule patch, ranking change, feature whitelist edit, gate relaxation, or online integration",
        },
    ]


def _lane_status(
    stage_10_0: dict[str, Any],
    stage_10_1: dict[str, Any],
    stage_10_10: dict[str, Any],
    stage_10_6: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = _by_id(stage_10_0.get("strategy_candidates", []))
    evidence = _by_id(stage_10_1.get("evidence_inventory", []))
    scores = _by_id(stage_10_1.get("scoring_matrix", []))
    metrics_10_6 = stage_10_6.get("metrics", {})
    return [
        {
            "strategy_id": "S2_ranking_objective_and_feature_strategy",
            "candidate_lever": candidates["S2_ranking_objective_and_feature_strategy"]["candidate_lever"],
            "status_after_reentry": "parked_reference_only",
            "evidence": (
                f"10.10 selected_path={stage_10_10.get('metrics', {}).get('selected_path')}; "
                f"10.6 scope_locked={metrics_10_6.get('scope_locked')}; "
                f"candidate_matrix_rows={metrics_10_6.get('candidate_matrix_row_count')}"
            ),
            "blocker_or_boundary": "execution requires explicit later user go; 10.11 cannot score locked candidates",
            "decision": "preserve_not_select",
            "next_if_selected": "separate explicit S2 dev/OOF-only execution stage, not 10.11",
            "implementation_allowed": "no",
        },
        {
            "strategy_id": "S3_safety_gate_calibration_v2_plan",
            "candidate_lever": candidates["S3_safety_gate_calibration_v2_plan"]["candidate_lever"],
            "status_after_reentry": "selected_next_non_execution_lane",
            "evidence": (
                f"{evidence['S3_safety_gate_calibration_v2_plan'].get('dev_signal')}; "
                f"{evidence['S3_safety_gate_calibration_v2_plan'].get('oof_signal')}; "
                f"10.1_score={scores['S3_safety_gate_calibration_v2_plan'].get('score')}"
            ),
            "blocker_or_boundary": "now allowed as read-only policy/loss-budget review because S2 loss framing exists, but no threshold change is allowed",
            "decision": "select_next_lane",
            "next_if_selected": "10.12 safety gate calibration v2 policy/loss-budget review",
            "implementation_allowed": "no",
        },
        {
            "strategy_id": "S1_recall_route_evidence_inventory",
            "candidate_lever": candidates["S1_recall_route_evidence_inventory"]["candidate_lever"],
            "status_after_reentry": "deferred_inventory_only",
            "evidence": (
                f"{evidence['S1_recall_route_evidence_inventory'].get('dev_signal')}; "
                f"{evidence['S1_recall_route_evidence_inventory'].get('blocking_evidence')}; "
                f"10.1_score={scores['S1_recall_route_evidence_inventory'].get('score')}"
            ),
            "blocker_or_boundary": "still needs independent non-generated recall traces and taxonomy/provenance separation before learning selection",
            "decision": "defer_not_select",
            "next_if_selected": "future independent recall evidence inventory only",
            "implementation_allowed": "no",
        },
        {
            "strategy_id": "S4_taxonomy_data_quality_prerequisite_track",
            "candidate_lever": candidates["S4_taxonomy_data_quality_prerequisite_track"]["candidate_lever"],
            "status_after_reentry": "parallel_prerequisite_not_learning",
            "evidence": (
                f"{evidence['S4_taxonomy_data_quality_prerequisite_track'].get('dev_signal')}; "
                f"{evidence['S4_taxonomy_data_quality_prerequisite_track'].get('blocking_evidence')}; "
                f"10.1_score={scores['S4_taxonomy_data_quality_prerequisite_track'].get('score')}"
            ),
            "blocker_or_boundary": "data-quality backlog may unblock future evidence but is not ranking/recall learning evidence",
            "decision": "keep_parallel_not_select",
            "next_if_selected": "parallel taxonomy/data-quality backlog acceptance contract, outside learning lane",
            "implementation_allowed": "no",
        },
    ]


def _selection_decisions(lane_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [row for row in lane_rows if row["decision"] == "select_next_lane"]
    rows = [
        {
            "decision_area": "next_non_execution_lane",
            "decision": "SELECT_S3_SAFETY_GATE_CALIBRATION_V2_READ_ONLY_REVIEW",
            "selected_strategy_id": selected[0]["strategy_id"] if selected else "",
            "basis": "S3 has concrete OOF rescue/loss evidence and its prior blocker was loss-budget framing, which S2 planning has now supplied.",
            "allowed_next": "define a read-only safety gate calibration v2 policy/loss-budget review",
            "not_allowed": "no threshold change, tuning, rule patch, gate relaxation, ranking change, GoalSearcher change, or execution",
        },
        {
            "decision_area": "s2_execution",
            "decision": "KEEP_PARKED",
            "selected_strategy_id": "S2_ranking_objective_and_feature_strategy",
            "basis": "10.10 parked S2 execution and 10.8 recorded no explicit go.",
            "allowed_next": "resume only after explicit user go in a separate execution stage",
            "not_allowed": "no implicit execution from broader strategy review",
        },
        {
            "decision_area": "recall_lane",
            "decision": "DEFER_S1_UNTIL_INDEPENDENT_RECALL_EVIDENCE",
            "selected_strategy_id": "S1_recall_route_evidence_inventory",
            "basis": "S1 remains blocked by source provenance and taxonomy-empty evidence from the 9.x closure.",
            "allowed_next": "future inventory may define independent non-generated recall traces",
            "not_allowed": "no recall rule patch or generated-repair learning",
        },
        {
            "decision_area": "taxonomy_backlog",
            "decision": "KEEP_S4_PARALLEL_PREREQUISITE",
            "selected_strategy_id": "S4_taxonomy_data_quality_prerequisite_track",
            "basis": "S4 is high-evidence data-quality work, but not an accuracy learning lane.",
            "allowed_next": "parallel backlog ownership/acceptance contract outside rank/recall learning",
            "not_allowed": "do not count backlog rows as Top1 gain or training evidence",
        },
    ]
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s2_dev_oof_experiment",
            "reason": "S2 execution remains parked; 10.11 only selects a non-execution strategy lane.",
            "allowed_after": "explicit user go plus separate dev/OOF-only execution stage",
        },
        {
            "blocked_action": "change_safety_gate_threshold",
            "reason": "S3 is selected only for read-only policy/loss-budget review.",
            "allowed_after": "future OOF calibration plan and explicit implementation approval, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_model",
            "reason": "broader strategy re-entry is not an execution or tuning stage.",
            "allowed_after": "separate explicitly opened execution stage with frozen scope",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "10.11 does not propose feature changes.",
            "allowed_after": "separate feature proposal plus leakage preflight",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "convert_taxonomy_backlog_to_learning_evidence",
            "reason": "S4 remains a parallel prerequisite outside ranking/recall learning.",
            "allowed_after": "only after ownership, acceptance checks, and re-entry criteria are met in a later data-quality route",
        },
    ]


def _metrics(
    gates: list[dict[str, Any]],
    lane_rows: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    pass_count = sum(1 for row in gates if row["status"] == "pass")
    selected = [row for row in lane_rows if row["decision"] == "select_next_lane"]
    return {
        "reentry_gate_count": len(gates),
        "reentry_gate_pass_count": pass_count,
        "reentry_gate_fail_count": len(gates) - pass_count,
        "lane_status_count": len(lane_rows),
        "selection_decision_count": len(selection_rows),
        "blocked_action_count": len(blocked),
        "selected_next_lane_count": len(selected),
        "selected_next_strategy_id": selected[0]["strategy_id"] if selected else "",
        "selected_next_stage": selected[0]["next_if_selected"] if selected else "",
        "s2_execution_lane_parked": True,
        "execution_performed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.11 Broader 10.x Strategy Re-entry Review",
        "",
        "Read-only re-entry into broader 10.x strategy after parking S2 execution. This stage compares deferred levers and selects the next non-execution lane.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["reentry_gate_pass_count", metrics["reentry_gate_pass_count"]],
                ["selected_next_strategy_id", metrics["selected_next_strategy_id"]],
                ["selected_next_stage", metrics["selected_next_stage"]],
                ["s2_execution_lane_parked", metrics["s2_execution_lane_parked"]],
                ["execution_performed", metrics["execution_performed"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Lane Status",
        "",
        _md_table(
            [["strategy_id", "status_after_reentry", "decision", "next_if_selected"]]
            + [[row["strategy_id"], row["status_after_reentry"], row["decision"], row["next_if_selected"]] for row in report["deferred_lane_status"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.11 broader 10.x strategy re-entry review")
    parser.add_argument("--stage-10-10", default=str(DEFAULT_STAGE_10_10))
    parser.add_argument("--stage-10-1", default=str(DEFAULT_STAGE_10_1))
    parser.add_argument("--stage-10-0", default=str(DEFAULT_STAGE_10_0))
    parser.add_argument("--stage-10-6", default=str(DEFAULT_STAGE_10_6))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_10 = _read_json(Path(args.stage_10_10))
    stage_10_1 = _read_json(Path(args.stage_10_1))
    stage_10_0 = _read_json(Path(args.stage_10_0))
    stage_10_6 = _read_json(Path(args.stage_10_6))

    gates = _reentry_gates(stage_10_10, stage_10_6)
    lane_rows = _lane_status(stage_10_0, stage_10_1, stage_10_10, stage_10_6)
    selection_rows = _selection_decisions(lane_rows)
    blocked = _blocked_actions()
    metrics = _metrics(gates, lane_rows, selection_rows, blocked)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "reentry_gates_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_gates.csv")),
        "deferred_lane_status_csv": str(output_prefix.with_name(output_prefix.name + "_deferred_lane_status.csv")),
        "selection_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_selection_decisions.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.11 broader 10.x strategy re-entry review",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_10_parking": str(Path(args.stage_10_10)),
            "stage_10_1_evidence_inventory": str(Path(args.stage_10_1)),
            "stage_10_0_strategy_definition": str(Path(args.stage_10_0)),
            "stage_10_6_s2_scope_lock": str(Path(args.stage_10_6)),
        },
        "metrics": metrics,
        "reentry_gates": gates,
        "deferred_lane_status": lane_rows,
        "selection_decisions": selection_rows,
        "blocked_actions": blocked,
        "decision": (
            "Select S3_safety_gate_calibration_v2_plan as the next non-execution lane for a read-only policy/loss-budget review. "
            "S2 execution remains parked and preserved as reference-only, S1 recall remains deferred until independent non-generated recall evidence exists, "
            "and S4 taxonomy/data-quality remains a parallel prerequisite outside ranking/recall learning."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.11 only re-enters broader strategy review and selects the next read-only lane. It does not execute S2, train, tune, patch rules, "
            "change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, convert taxonomy backlog rows "
            "into learning evidence, or connect online."
        ),
        "next_stage": {
            "stage": "10.12 safety gate calibration v2 policy/loss-budget review",
            "goal": (
                "Read-only review S3 as the next non-execution lane: define safety-gate/compatibility calibration policy, OOF evidence requirements, "
                "loss budget, residual slices, and freeze/validation boundaries without changing thresholds or implementation."
            ),
            "prohibited": [
                "training",
                "tuning",
                "threshold changes",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
            ],
        },
    }

    _write_csv(Path(artifacts["reentry_gates_csv"]), gates, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(
        Path(artifacts["deferred_lane_status_csv"]),
        lane_rows,
        [
            "strategy_id",
            "candidate_lever",
            "status_after_reentry",
            "evidence",
            "blocker_or_boundary",
            "decision",
            "next_if_selected",
            "implementation_allowed",
        ],
    )
    _write_csv(
        Path(artifacts["selection_decisions_csv"]),
        selection_rows,
        ["decision_area", "decision", "selected_strategy_id", "basis", "allowed_next", "not_allowed"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked, ["blocked_action", "reason", "allowed_after"])
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
