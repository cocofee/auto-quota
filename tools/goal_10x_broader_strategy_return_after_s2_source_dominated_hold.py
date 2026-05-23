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
DEFAULT_S2_HOLD = AGENT_STATE / "goal_10x_s2_source_dominated_candidate_hold_strategy_return_gate_summary.json"
DEFAULT_REENTRY_10_11 = AGENT_STATE / "goal_10x_broader_strategy_reentry_review_summary.json"
DEFAULT_REENTRY_10_20 = AGENT_STATE / "goal_10x_broader_strategy_reentry_after_s3_parking_summary.json"
DEFAULT_DQ_HANDOFF = AGENT_STATE / "goal_10x_taxonomy_data_quality_backlog_route_handoff_reentry_parking_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_broader_strategy_return_after_s2_source_dominated_hold"


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


def _reentry_gates(s2_hold: dict[str, Any], dq_handoff: dict[str, Any]) -> list[dict[str, Any]]:
    s2 = s2_hold.get("metrics", {})
    dq = dq_handoff.get("metrics", {})
    return [
        {
            "gate": "s2_hold_confirmed",
            "status": "pass" if s2.get("s2_candidate_held") is True else "fail",
            "observed": f"s2_candidate_held={s2.get('s2_candidate_held')}; freeze={s2.get('s2_candidate_frozen_for_general_validation')}",
            "decision": "keep_s2_as_diagnostic_only",
            "not_allowed": "no S2 validation or implementation from held candidate",
        },
        {
            "gate": "independent_evidence_missing",
            "status": "pass" if s2.get("non_generated_positive_net") == 0 else "fail",
            "observed": f"non_generated_positive_net={s2.get('non_generated_positive_net')}; non_generated_sources={s2.get('non_generated_positive_source_count')}",
            "decision": "require_evidence_wait_lane",
            "not_allowed": "no general Top1 claim from generated-source dominated S2 gain",
        },
        {
            "gate": "data_quality_route_handed_off",
            "status": "pass" if dq.get("data_quality_backlog_route_handed_off") is True else "fail",
            "observed": f"data_quality_backlog_route_handed_off={dq.get('data_quality_backlog_route_handed_off')}",
            "decision": "use_existing_dq_route_as_prerequisite_context",
            "not_allowed": "do not convert DQ backlog rows into learning labels",
        },
        {
            "gate": "learning_reentry_still_parked",
            "status": "pass" if dq.get("learning_reentry_parked") is True else "fail",
            "observed": f"learning_reentry_parked={dq.get('learning_reentry_parked')}",
            "decision": "stay_read_only",
            "not_allowed": "no training, tuning, rule patch, ranking change, or GoalSearcher change",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass",
            "observed": "heldout_used_for_selection=false; hard_used_for_selection=false",
            "decision": "validation_splits_remain_closed",
            "not_allowed": "no heldout/hard selection or validation from this stage",
        },
    ]


def _lane_status(s2_hold: dict[str, Any], reentry_10_11: dict[str, Any], reentry_10_20: dict[str, Any], dq_handoff: dict[str, Any]) -> list[dict[str, Any]]:
    s2 = s2_hold.get("metrics", {})
    dq = dq_handoff.get("metrics", {})
    return [
        {
            "lane_id": "S2_ranking_objective_feature_execution",
            "status_after_return": "held_diagnostic_only",
            "evidence": f"selected_path={s2.get('selected_path')}; generated_positive_net_share={s2.get('generated_positive_net_share')}",
            "blocker_or_boundary": "needs independent non-generated positive support and renewed source robustness pass",
            "decision": "do_not_resume",
            "next_if_selected": "not selected; no validation or implementation",
            "implementation_allowed": "no",
        },
        {
            "lane_id": "S3_safety_gate_calibration_execution",
            "status_after_return": "parked_reference_only",
            "evidence": f"10.20 selected_next_strategy_id={reentry_10_20.get('metrics', {}).get('selected_next_strategy_id')}; S3 was parked before DQ route",
            "blocker_or_boundary": "requires explicit S3 execution go; no threshold change or what-if execution here",
            "decision": "preserve_not_select",
            "next_if_selected": "separate explicit S3 dev/OOF what-if stage",
            "implementation_allowed": "no",
        },
        {
            "lane_id": "S1_recall_route_learning",
            "status_after_return": "blocked_pending_independent_evidence",
            "evidence": "prior 9.x/10.x closure found recall-missing evidence dominated by taxonomy/provenance gaps",
            "blocker_or_boundary": "needs accepted DQ artifacts and non-generated recall traces before learning review",
            "decision": "defer_not_select",
            "next_if_selected": "future independent recall evidence inventory only",
            "implementation_allowed": "no",
        },
        {
            "lane_id": "S4_taxonomy_data_quality_backlog",
            "status_after_return": "open_non_learning_prerequisite",
            "evidence": f"total_priority_backlog_rows={dq.get('total_priority_backlog_rows')}; learning_reentry_parked={dq.get('learning_reentry_parked')}",
            "blocker_or_boundary": "owner artifacts and acceptance checks still pending",
            "decision": "carry_forward_as_context",
            "next_if_selected": "continue evidence-wait checkpoint; no learning use",
            "implementation_allowed": "no",
        },
        {
            "lane_id": "S5_independent_non_generated_evidence_wait",
            "status_after_return": "selected_next_non_execution_lane",
            "evidence": "S2 source gate requires non-generated positive support; DQ route defines accepted cleanup prerequisites",
            "blocker_or_boundary": "must remain checkpoint/review only until evidence artifacts exist",
            "decision": "select_next_lane",
            "next_if_selected": "10.24 taxonomy/data-quality backlog parked checkpoint and S2 independent evidence-wait review",
            "implementation_allowed": "no",
        },
    ]


def _selection_decisions() -> list[dict[str, Any]]:
    return [
        {
            "decision_area": "next_non_execution_lane",
            "decision": "SELECT_S5_INDEPENDENT_NON_GENERATED_EVIDENCE_WAIT_CHECKPOINT",
            "selected_lane_id": "S5_independent_non_generated_evidence_wait",
            "basis": "S2 has dev/OOF signal but no independent non-generated support; DQ route is open but learning re-entry is parked.",
            "allowed_next": "read-only evidence-wait checkpoint tying S2 source requirements to DQ acceptance artifacts",
            "not_allowed": "no experiment execution, heldout/hard validation, training, ranking change, or general Top1 claim",
        },
        {
            "decision_area": "s2_resume",
            "decision": "DO_NOT_RESUME_S2",
            "selected_lane_id": "S2_ranking_objective_feature_execution",
            "basis": "generated_positive_net_share=1.0 and non_generated_positive_net=0",
            "allowed_next": "resume only after independent non-generated support exists and a renewed source gate passes",
            "not_allowed": "no heldout/hard validation from source-dominated evidence",
        },
        {
            "decision_area": "dq_backlog",
            "decision": "KEEP_AS_NON_LEARNING_CONTEXT",
            "selected_lane_id": "S4_taxonomy_data_quality_backlog",
            "basis": "10.23 handed off backlog route but kept learning re-entry parked",
            "allowed_next": "track pending acceptance artifacts as evidence-wait prerequisites",
            "not_allowed": "do not count backlog rows as training labels, recall rules, ranking features, or Top1 gain",
        },
        {
            "decision_area": "execution_lanes",
            "decision": "KEEP_S2_S3_EXECUTION_CLOSED",
            "selected_lane_id": "S2_S3_execution_lanes",
            "basis": "current stage is strategy return only and no explicit new execution go is present",
            "allowed_next": "separate explicit go stage only",
            "not_allowed": "no implicit dev/OOF, what-if, tuning, threshold, or implementation work",
        },
    ]


def _evidence_wait_checkpoints(s2_hold: dict[str, Any], dq_handoff: dict[str, Any]) -> list[dict[str, Any]]:
    s2 = s2_hold.get("metrics", {})
    rows = [
        {
            "checkpoint_id": "S2_NON_GENERATED_POSITIVE_NET",
            "current_value": s2.get("non_generated_positive_net"),
            "required_before_reentry": ">0 and directionally consistent on eligible dev/OOF evidence",
            "owner_or_source": "future independent evidence inventory",
            "status": "not_ready",
        },
        {
            "checkpoint_id": "S2_NON_GENERATED_SOURCE_COUNT",
            "current_value": s2.get("non_generated_positive_source_count"),
            "required_before_reentry": ">=2 independent non-generated sources",
            "owner_or_source": "future independent evidence inventory",
            "status": "not_ready",
        },
        {
            "checkpoint_id": "S2_GENERATED_SOURCE_SHARE",
            "current_value": s2.get("generated_positive_net_share"),
            "required_before_reentry": "<=0.5 or explicitly justified by independent non-generated evidence",
            "owner_or_source": "future source robustness gate",
            "status": "failed_now",
        },
    ]
    for item in dq_handoff.get("future_reentry_triggers", []):
        rows.append(
            {
                "checkpoint_id": f"DQ_{item.get('backlog_area')}",
                "current_value": item.get("trigger_status"),
                "required_before_reentry": item.get("required_before_reentry"),
                "owner_or_source": item.get("eligible_future_lane"),
                "status": item.get("trigger_status"),
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s2_or_s3_execution",
            "reason": "This strategy-return stage only selects an evidence-wait lane.",
            "allowed_after": "separate explicit execution authorization",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation",
            "reason": "S2 failed source robustness and has no independent non-generated positive support.",
            "allowed_after": "future source robustness pass plus explicit validation gate",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "No learnable or validated evidence lane is open.",
            "allowed_after": "future explicitly scoped dev/OOF execution after evidence gates pass",
        },
        {
            "blocked_action": "change_ranking_goal_searcher_or_feature_whitelist",
            "reason": "No candidate is frozen for implementation or validation.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "Current S2 gain is source-dominated by global_repair_decision_table.csv.",
            "allowed_after": "future independent non-generated evidence review",
        },
        {
            "blocked_action": "convert_dq_backlog_to_learning_evidence",
            "reason": "DQ backlog route remains non-learning and acceptance artifacts are pending.",
            "allowed_after": "never directly; only later independent cleaned evidence may be reviewed",
        },
    ]


def _metrics(
    gates: list[dict[str, Any]],
    lanes: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    s2_hold: dict[str, Any],
    dq_handoff: dict[str, Any],
) -> dict[str, Any]:
    s2 = s2_hold.get("metrics", {})
    dq = dq_handoff.get("metrics", {})
    selected = [row for row in lanes if row["decision"] == "select_next_lane"]
    gate_pass = sum(1 for row in gates if row["status"] == "pass")
    not_ready = sum(1 for row in checkpoints if str(row["status"]).startswith("not_ready") or row["status"] == "failed_now")
    return {
        "reentry_gate_count": len(gates),
        "reentry_gate_pass_count": gate_pass,
        "reentry_gate_fail_count": len(gates) - gate_pass,
        "lane_status_count": len(lanes),
        "selection_decision_count": len(decisions),
        "evidence_wait_checkpoint_count": len(checkpoints),
        "not_ready_checkpoint_count": not_ready,
        "blocked_action_count": len(blocked),
        "selected_next_lane_count": len(selected),
        "selected_next_lane_id": selected[0]["lane_id"] if selected else "",
        "selected_next_stage": selected[0]["next_if_selected"] if selected else "",
        "s2_candidate_held": s2.get("s2_candidate_held"),
        "s2_validation_allowed_now": s2.get("validation_allowed_now"),
        "generated_positive_net_share": s2.get("generated_positive_net_share"),
        "non_generated_positive_net": s2.get("non_generated_positive_net"),
        "non_generated_positive_source_count": s2.get("non_generated_positive_source_count"),
        "dq_backlog_route_handed_off": dq.get("data_quality_backlog_route_handed_off"),
        "learning_reentry_parked": dq.get("learning_reentry_parked"),
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "training_allowed": False,
        "implementation_allowed": False,
        "whatif_execution_allowed": False,
        "threshold_change_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Broader 10.x Strategy Return After S2 Source-dominated Hold",
        "",
        "Read-only strategy return after S2 was held for generated-source dominance. This stage selects the next non-execution lane.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_next_lane_id", metrics["selected_next_lane_id"]],
                ["selected_next_stage", metrics["selected_next_stage"]],
                ["generated_positive_net_share", metrics["generated_positive_net_share"]],
                ["non_generated_positive_net", metrics["non_generated_positive_net"]],
                ["not_ready_checkpoint_count", metrics["not_ready_checkpoint_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Lane Status",
        "",
        _md_table(
            [["lane_id", "status_after_return", "decision", "next_if_selected"]]
            + [[row["lane_id"], row["status_after_return"], row["decision"], row["next_if_selected"]] for row in report["lane_status"]]
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
    parser = argparse.ArgumentParser(description="Broader 10.x strategy return after S2 source-dominated hold")
    parser.add_argument("--s2-hold", default=str(DEFAULT_S2_HOLD))
    parser.add_argument("--reentry-10-11", default=str(DEFAULT_REENTRY_10_11))
    parser.add_argument("--reentry-10-20", default=str(DEFAULT_REENTRY_10_20))
    parser.add_argument("--dq-handoff", default=str(DEFAULT_DQ_HANDOFF))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s2_hold = _read_json(Path(args.s2_hold))
    reentry_10_11 = _read_json(Path(args.reentry_10_11))
    reentry_10_20 = _read_json(Path(args.reentry_10_20))
    dq_handoff = _read_json(Path(args.dq_handoff))

    gates = _reentry_gates(s2_hold, dq_handoff)
    lanes = _lane_status(s2_hold, reentry_10_11, reentry_10_20, dq_handoff)
    decisions = _selection_decisions()
    checkpoints = _evidence_wait_checkpoints(s2_hold, dq_handoff)
    blocked = _blocked_actions()
    metrics = _metrics(gates, lanes, decisions, checkpoints, blocked, s2_hold, dq_handoff)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "reentry_gates_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_gates.csv")),
        "lane_status_csv": str(output_prefix.with_name(output_prefix.name + "_lane_status.csv")),
        "selection_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_selection_decisions.csv")),
        "evidence_wait_checkpoints_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_wait_checkpoints.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / broader 10.x strategy return after S2 source-dominated hold",
        "read_only": True,
        "dev_oof_only_review": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "no_heldout_hard_validation": True,
        "no_whatif_execution": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "s2_hold": str(Path(args.s2_hold)),
            "reentry_10_11": str(Path(args.reentry_10_11)),
            "reentry_10_20": str(Path(args.reentry_10_20)),
            "dq_handoff": str(Path(args.dq_handoff)),
        },
        "metrics": metrics,
        "reentry_gates": gates,
        "lane_status": lanes,
        "selection_decisions": decisions,
        "evidence_wait_checkpoints": checkpoints,
        "blocked_actions": blocked,
        "decision": (
            "Select S5_independent_non_generated_evidence_wait as the next non-execution lane, materialized as 10.24 taxonomy/data-quality backlog parked "
            "checkpoint and S2 independent evidence-wait review. S2 remains held, S3 remains parked, DQ backlog remains non-learning context, and no heldout/hard "
            "validation or algorithm implementation is opened."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "This stage only selects the next read-only strategy lane after S2 source-dominated hold. It does not run S2/S3 execution, train, tune, expand candidates, "
            "run heldout/hard validation or selection, change thresholds, patch rules, modify ranking or GoalSearcher, edit feature whitelist, relax gates, claim "
            "general Top1 gain, convert DQ backlog rows into learning evidence, or connect online."
        ),
        "next_stage": {
            "stage": "10.24 taxonomy/data-quality backlog parked checkpoint and S2 independent evidence-wait review",
            "goal": (
                "Read-only confirm learning remains parked while recording the exact independent non-generated evidence and DQ acceptance checkpoints required before "
                "any future S1/S2 learning re-entry can be considered."
            ),
            "prohibited": [
                "training",
                "candidate expansion",
                "what-if execution",
                "heldout/hard validation",
                "heldout/hard selection",
                "threshold changes",
                "rule patches",
                "ranking implementation",
                "GoalSearcher changes",
                "feature whitelist edits",
                "online integration",
                "claiming general Top1 gain from S2",
                "counting DQ backlog rows as learning evidence",
            ],
        },
    }

    _write_csv(Path(artifacts["reentry_gates_csv"]), gates, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(
        Path(artifacts["lane_status_csv"]),
        lanes,
        ["lane_id", "status_after_return", "evidence", "blocker_or_boundary", "decision", "next_if_selected", "implementation_allowed"],
    )
    _write_csv(
        Path(artifacts["selection_decisions_csv"]),
        decisions,
        ["decision_area", "decision", "selected_lane_id", "basis", "allowed_next", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["evidence_wait_checkpoints_csv"]),
        checkpoints,
        ["checkpoint_id", "current_value", "required_before_reentry", "owner_or_source", "status"],
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
