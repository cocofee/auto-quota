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
DEFAULT_STAGE_10_19 = AGENT_STATE / "goal_10x_s3_execution_lane_parking_strategy_return_gate_summary.json"
DEFAULT_STAGE_10_1 = AGENT_STATE / "goal_10x_accuracy_strategy_evidence_inventory_summary.json"
DEFAULT_STAGE_10_0 = AGENT_STATE / "goal_10x_accuracy_strategy_definition_summary.json"
DEFAULT_STAGE_9_32 = AGENT_STATE / "goal_taxonomy_data_quality_backlog_handoff_9x_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_reentry_after_s3_parking"


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


def _reentry_gates(stage_10_19: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_19.get("metrics", {})
    return [
        {
            "gate": "s3_execution_lane_parked",
            "status": "pass" if metrics.get("s3_execution_lane_parked") is True else "fail",
            "observed": f"s3_execution_lane_parked={metrics.get('s3_execution_lane_parked')}",
            "decision": "keep_s3_execution_parked",
            "not_allowed": "no S3 what-if execution without explicit later go",
        },
        {
            "gate": "strategy_return_opened",
            "status": "pass" if metrics.get("strategy_return_selected") is True else "fail",
            "observed": f"strategy_return_selected={metrics.get('strategy_return_selected')}",
            "decision": "allow_broader_read_only_review",
            "not_allowed": "no execution-planning churn as default path",
        },
        {
            "gate": "execution_boundary",
            "status": "pass" if metrics.get("execution_performed") is False and metrics.get("whatif_execution_allowed") is False else "fail",
            "observed": f"execution_performed={metrics.get('execution_performed')}; whatif_execution_allowed={metrics.get('whatif_execution_allowed')}",
            "decision": "review_only",
            "not_allowed": "no what-if, training, tuning, threshold change, ranking change, or online integration",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass" if metrics.get("heldout_used_for_selection") is False else "fail",
            "observed": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "decision": "dev_oof_only_for_strategy_selection",
            "not_allowed": "no heldout/hard threshold, strategy, candidate, or feature selection",
        },
        {
            "gate": "automation_boundary",
            "status": "pass" if metrics.get("automation_read_only_auto_advance_active") is True else "fail",
            "observed": f"automation_read_only_auto_advance_active={metrics.get('automation_read_only_auto_advance_active')}",
            "decision": "auto_read_only_only",
            "not_allowed": "automation cannot execute what-if or implementation stages",
        },
    ]


def _lane_status(stage_10_0: dict[str, Any], stage_10_1: dict[str, Any], stage_9_32: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = _by_id(stage_10_0.get("strategy_candidates", []))
    evidence = _by_id(stage_10_1.get("evidence_inventory", []))
    scores = _by_id(stage_10_1.get("scoring_matrix", []))
    backlog_metrics = stage_9_32.get("metrics", {})
    return [
        {
            "strategy_id": "S2_ranking_objective_and_feature_strategy",
            "candidate_lever": candidates["S2_ranking_objective_and_feature_strategy"]["candidate_lever"],
            "status_after_reentry": "parked_reference_only",
            "evidence": "S2 execution lane already parked earlier; locked assets preserved as reference only",
            "blocker_or_boundary": "requires explicit later user go; no scoring or training in 10.20",
            "decision": "preserve_not_select",
            "next_if_selected": "separate explicit S2 dev/OOF-only execution stage",
            "implementation_allowed": "no",
        },
        {
            "strategy_id": "S3_safety_gate_calibration_v2_plan",
            "candidate_lever": candidates["S3_safety_gate_calibration_v2_plan"]["candidate_lever"],
            "status_after_reentry": "parked_reference_only",
            "evidence": "10.19 parked S3 execution lane after 10.17 no-go; 10.14-10.19 artifacts preserved",
            "blocker_or_boundary": "requires explicit later S3 execution go; no threshold change or what-if execution in 10.20",
            "decision": "preserve_not_select",
            "next_if_selected": "separate explicit S3 dev/OOF what-if execution stage",
            "implementation_allowed": "no",
        },
        {
            "strategy_id": "S1_recall_route_evidence_inventory",
            "candidate_lever": candidates["S1_recall_route_evidence_inventory"]["candidate_lever"],
            "status_after_reentry": "blocked_pending_prerequisite",
            "evidence": (
                f"{evidence['S1_recall_route_evidence_inventory'].get('dev_signal')}; "
                f"{evidence['S1_recall_route_evidence_inventory'].get('blocking_evidence')}; "
                f"10.1_score={scores['S1_recall_route_evidence_inventory'].get('score')}"
            ),
            "blocker_or_boundary": "needs independent non-generated recall traces and taxonomy/provenance separation before learning selection",
            "decision": "defer_until_prerequisite",
            "next_if_selected": "future independent recall evidence inventory after taxonomy/provenance prerequisites",
            "implementation_allowed": "no",
        },
        {
            "strategy_id": "S4_taxonomy_data_quality_prerequisite_track",
            "candidate_lever": candidates["S4_taxonomy_data_quality_prerequisite_track"]["candidate_lever"],
            "status_after_reentry": "selected_next_non_execution_prerequisite",
            "evidence": (
                f"backlog_handoff_items={backlog_metrics.get('handoff_item_count')}; "
                f"total_priority_backlog_rows={backlog_metrics.get('total_priority_backlog_rows')}; "
                f"source_provenance_rows={backlog_metrics.get('source_provenance_rows')}; "
                f"query_family_empty_rows={backlog_metrics.get('query_family_empty_rows')}; "
                f"10.1_score={scores['S4_taxonomy_data_quality_prerequisite_track'].get('score')}"
            ),
            "blocker_or_boundary": "not a rank/recall learning lane; must define ownership, acceptance checks, and re-entry criteria",
            "decision": "select_next_prerequisite_lane",
            "next_if_selected": "10.21 taxonomy/data-quality prerequisite acceptance and re-entry contract",
            "implementation_allowed": "no",
        },
    ]


def _selection_decisions(lane_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "decision_area": "next_non_execution_lane",
            "decision": "SELECT_S4_TAXONOMY_DATA_QUALITY_PREREQUISITE_CONTRACT",
            "selected_strategy_id": "S4_taxonomy_data_quality_prerequisite_track",
            "basis": "S2/S3 execution lanes are parked; S1 recall remains blocked by taxonomy/provenance; S4 is the prerequisite needed before future learning re-entry.",
            "allowed_next": "read-only define backlog ownership, acceptance checks, and re-entry criteria",
            "not_allowed": "do not count backlog rows as Top1 gain, training evidence, recall rules, or ranking features",
        },
        {
            "decision_area": "s2_execution",
            "decision": "KEEP_PARKED",
            "selected_strategy_id": "S2_ranking_objective_and_feature_strategy",
            "basis": "S2 was parked before S3 re-entry and no explicit execution go exists.",
            "allowed_next": "resume only after explicit user go in a separate execution stage",
            "not_allowed": "no implicit execution from broader strategy review",
        },
        {
            "decision_area": "s3_execution",
            "decision": "KEEP_PARKED",
            "selected_strategy_id": "S3_safety_gate_calibration_v2_plan",
            "basis": "10.19 parked S3 execution after 10.17 no-go.",
            "allowed_next": "resume only after explicit user go in a separate S3 execution stage",
            "not_allowed": "no what-if execution or threshold change",
        },
        {
            "decision_area": "recall_lane",
            "decision": "DEFER_S1_UNTIL_PREREQUISITE_CONTRACT",
            "selected_strategy_id": "S1_recall_route_evidence_inventory",
            "basis": "S1 has a real recall gap, but current evidence is blocked by source provenance, query_family_empty, and taxonomy coverage gaps.",
            "allowed_next": "revisit after S4 acceptance/re-entry criteria are defined",
            "not_allowed": "no recall rule patch or generated-repair learning",
        },
    ]


def _prerequisite_contract_requirements(stage_9_32: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in stage_9_32.get("handoff_items", []):
        rows.append(
            {
                "backlog_area": item.get("backlog_area"),
                "priority": item.get("priority"),
                "owner_lane": item.get("handoff_owner_lane"),
                "row_count": item.get("count"),
                "acceptance_check": item.get("acceptance_check"),
                "learning_boundary": item.get("learning_lane_disposition"),
                "reentry_requirement": "must pass acceptance check before used as future recall/ranking evidence",
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s2_or_s3_execution",
            "reason": "S2 and S3 execution lanes are parked; 10.20 only selects a non-execution prerequisite lane.",
            "allowed_after": "explicit user go plus separate execution stage",
        },
        {
            "blocked_action": "train_or_tune_model",
            "reason": "broader strategy re-entry after parking S3 is not execution or tuning.",
            "allowed_after": "separate explicitly opened execution stage with frozen scope",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "S3 execution and implementation are parked.",
            "allowed_after": "future explicit S3 execution evidence plus implementation review, if ever reached",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "10.20 does not propose feature/ranking changes.",
            "allowed_after": "separate feature/ranking proposal plus leakage preflight",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "convert_taxonomy_backlog_to_learning_evidence",
            "reason": "S4 is selected only to define acceptance and re-entry criteria, not to claim accuracy gain.",
            "allowed_after": "only after ownership, acceptance checks, and re-entry criteria are passed in a later data-quality route",
        },
    ]


def _metrics(
    gates: list[dict[str, Any]],
    lane_rows: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    prerequisite_rows: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    pass_count = sum(1 for row in gates if row["status"] == "pass")
    selected = [row for row in lane_rows if row["decision"] == "select_next_prerequisite_lane"]
    return {
        "reentry_gate_count": len(gates),
        "reentry_gate_pass_count": pass_count,
        "reentry_gate_fail_count": len(gates) - pass_count,
        "lane_status_count": len(lane_rows),
        "selection_decision_count": len(selection_rows),
        "prerequisite_contract_requirement_count": len(prerequisite_rows),
        "blocked_action_count": len(blocked),
        "selected_next_lane_count": len(selected),
        "selected_next_strategy_id": selected[0]["strategy_id"] if selected else "",
        "selected_next_stage": selected[0]["next_if_selected"] if selected else "",
        "s2_execution_lane_parked": True,
        "s3_execution_lane_parked": True,
        "execution_performed": False,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.20 Broader 10.x Strategy Re-entry After Parking S3",
        "",
        "Read-only re-entry into broader 10.x strategy after S3 execution has been parked. This selects the next non-execution prerequisite lane.",
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
                ["s3_execution_lane_parked", metrics["s3_execution_lane_parked"]],
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
    parser = argparse.ArgumentParser(description="Stage 10.20 broader 10.x strategy re-entry after parking S3")
    parser.add_argument("--stage-10-19", default=str(DEFAULT_STAGE_10_19))
    parser.add_argument("--stage-10-1", default=str(DEFAULT_STAGE_10_1))
    parser.add_argument("--stage-10-0", default=str(DEFAULT_STAGE_10_0))
    parser.add_argument("--stage-9-32", default=str(DEFAULT_STAGE_9_32))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_19 = _read_json(Path(args.stage_10_19))
    stage_10_1 = _read_json(Path(args.stage_10_1))
    stage_10_0 = _read_json(Path(args.stage_10_0))
    stage_9_32 = _read_json(Path(args.stage_9_32))

    gates = _reentry_gates(stage_10_19)
    lane_rows = _lane_status(stage_10_0, stage_10_1, stage_9_32)
    selection_rows = _selection_decisions(lane_rows)
    prerequisite_rows = _prerequisite_contract_requirements(stage_9_32)
    blocked = _blocked_actions()
    metrics = _metrics(gates, lane_rows, selection_rows, prerequisite_rows, blocked)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "reentry_gates_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_gates.csv")),
        "deferred_lane_status_csv": str(output_prefix.with_name(output_prefix.name + "_deferred_lane_status.csv")),
        "selection_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_selection_decisions.csv")),
        "prerequisite_contract_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_prerequisite_contract_requirements.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.20 broader 10.x strategy re-entry after parking S3",
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
            "stage_10_19_s3_parking": str(Path(args.stage_10_19)),
            "stage_10_1_evidence_inventory": str(Path(args.stage_10_1)),
            "stage_10_0_strategy_definition": str(Path(args.stage_10_0)),
            "stage_9_32_taxonomy_handoff": str(Path(args.stage_9_32)),
        },
        "metrics": metrics,
        "reentry_gates": gates,
        "deferred_lane_status": lane_rows,
        "selection_decisions": selection_rows,
        "prerequisite_contract_requirements": prerequisite_rows,
        "blocked_actions": blocked,
        "decision": (
            "Select S4_taxonomy_data_quality_prerequisite_track as the next non-execution prerequisite lane. S2 and S3 execution lanes remain parked and "
            "preserved as reference-only; S1 recall remains deferred until taxonomy/provenance prerequisites define acceptance and re-entry criteria. "
            "The selected next step is a read-only taxonomy/data-quality prerequisite acceptance and re-entry contract, not rank/recall learning."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.20 only re-enters broader strategy review and selects the next read-only prerequisite lane. It does not execute S2/S3, run what-if, train, tune, "
            "change thresholds, patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, "
            "convert taxonomy backlog rows into learning evidence, or connect online."
        ),
        "next_stage": {
            "stage": "10.21 taxonomy/data-quality prerequisite acceptance and re-entry contract",
            "goal": (
                "Read-only define ownership, acceptance checks, and re-entry criteria for taxonomy/data-quality backlog items before any future recall/ranking learning use."
            ),
            "prohibited": [
                "what-if execution",
                "training",
                "tuning",
                "threshold changes",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
                "feature whitelist edits",
                "counting backlog rows as learning evidence",
            ],
        },
    }

    _write_csv(Path(artifacts["reentry_gates_csv"]), gates, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(
        Path(artifacts["deferred_lane_status_csv"]),
        lane_rows,
        ["strategy_id", "candidate_lever", "status_after_reentry", "evidence", "blocker_or_boundary", "decision", "next_if_selected", "implementation_allowed"],
    )
    _write_csv(
        Path(artifacts["selection_decisions_csv"]),
        selection_rows,
        ["decision_area", "decision", "selected_strategy_id", "basis", "allowed_next", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["prerequisite_contract_requirements_csv"]),
        prerequisite_rows,
        ["backlog_area", "priority", "owner_lane", "row_count", "acceptance_check", "learning_boundary", "reentry_requirement"],
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
