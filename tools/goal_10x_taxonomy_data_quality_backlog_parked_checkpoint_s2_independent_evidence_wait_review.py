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
DEFAULT_STRATEGY_RETURN = AGENT_STATE / "goal_10x_broader_strategy_return_after_s2_source_dominated_hold_summary.json"
DEFAULT_S2_HOLD = AGENT_STATE / "goal_10x_s2_source_dominated_candidate_hold_strategy_return_gate_summary.json"
DEFAULT_DQ_HANDOFF = AGENT_STATE / "goal_10x_taxonomy_data_quality_backlog_route_handoff_reentry_parking_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_taxonomy_data_quality_backlog_parked_checkpoint_s2_independent_evidence_wait_review"


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


def _parking_decisions(strategy_return: dict[str, Any], s2_hold: dict[str, Any], dq_handoff: dict[str, Any]) -> list[dict[str, Any]]:
    strategy_metrics = strategy_return.get("metrics", {})
    s2_metrics = s2_hold.get("metrics", {})
    dq_metrics = dq_handoff.get("metrics", {})
    return [
        {
            "parking_area": "learning_reentry",
            "status": "parked",
            "evidence": f"learning_reentry_parked={dq_metrics.get('learning_reentry_parked')}; selected_lane={strategy_metrics.get('selected_next_lane_id')}",
            "decision": "KEEP_LEARNING_REENTRY_PARKED",
            "resume_condition": "future read-only re-entry review after S2 independent evidence and DQ acceptance checkpoints pass",
            "not_allowed": "no training, tuning, rule patch, ranking change, GoalSearcher change, or feature whitelist edit",
        },
        {
            "parking_area": "s2_candidate",
            "status": "held_diagnostic_only",
            "evidence": f"s2_candidate_held={s2_metrics.get('s2_candidate_held')}; validation_allowed_now={s2_metrics.get('validation_allowed_now')}",
            "decision": "KEEP_S2_HELD",
            "resume_condition": "non-generated positive support exists and renewed source robustness gate passes",
            "not_allowed": "no heldout/hard validation or general Top1 claim from this candidate",
        },
        {
            "parking_area": "dq_backlog_route",
            "status": "open_reference_only",
            "evidence": f"data_quality_backlog_route_handed_off={dq_metrics.get('data_quality_backlog_route_handed_off')}; total_priority_backlog_rows={dq_metrics.get('total_priority_backlog_rows')}",
            "decision": "KEEP_DQ_BACKLOG_NON_LEARNING",
            "resume_condition": "owner lanes produce accepted artifacts; rows are reviewed as independent evidence only after acceptance",
            "not_allowed": "do not convert backlog rows directly into training labels, recall rules, ranking features, thresholds, or gain",
        },
        {
            "parking_area": "validation_splits",
            "status": "closed",
            "evidence": "heldout_used_for_selection=false; hard_used_for_selection=false",
            "decision": "KEEP_HELDOUT_HARD_CLOSED",
            "resume_condition": "separate validation gate after source/DQ evidence passes",
            "not_allowed": "heldout/hard cannot be used for selection",
        },
    ]


def _s2_independent_evidence_requirements(s2_hold: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = s2_hold.get("metrics", {})
    return [
        {
            "requirement_id": "S2_NON_GENERATED_POSITIVE_NET",
            "current_value": metrics.get("non_generated_positive_net"),
            "required_before_reentry": ">0 and directionally consistent on eligible dev/OOF evidence",
            "acceptance_check": "positive net appears outside global_repair_decision_table.csv and is not caused by taxonomy-empty artifacts",
            "current_status": "missing",
            "learning_boundary": "required_before_s2_resume",
        },
        {
            "requirement_id": "S2_NON_GENERATED_SOURCE_COUNT",
            "current_value": metrics.get("non_generated_positive_source_count"),
            "required_before_reentry": ">=2 independent non-generated sources",
            "acceptance_check": "source support spans at least two non-generated source files or trace families",
            "current_status": "missing",
            "learning_boundary": "required_before_source_robustness_pass",
        },
        {
            "requirement_id": "S2_GENERATED_POSITIVE_NET_SHARE",
            "current_value": metrics.get("generated_positive_net_share"),
            "required_before_reentry": "<=0.5 or explicitly justified by independent evidence",
            "acceptance_check": "generated-source share no longer dominates the positive net claim",
            "current_status": "failed_now",
            "learning_boundary": "blocks_general_top1_claim",
        },
        {
            "requirement_id": "S2_LOSS_BUDGET_CARRY_FORWARD",
            "current_value": "hit1_loss=14 from S2 freeze gate",
            "required_before_reentry": "loss budget still passes on eligible independent evidence",
            "acceptance_check": "independent support does not hide new loss slices or widened loss budget",
            "current_status": "carry_forward_required",
            "learning_boundary": "required_before_validation_gate",
        },
        {
            "requirement_id": "S2_LEAKAGE_FALLBACK_CONTRACT_CARRY_FORWARD",
            "current_value": "passed in completed dev/OOF execution and freeze gate",
            "required_before_reentry": "no forbidden identifiers, no gate relaxation, no online fallback change",
            "acceptance_check": "future evidence inventory remains dev/OOF-only and offline",
            "current_status": "carry_forward_required",
            "learning_boundary": "required_before_any_execution_resume",
        },
    ]


def _dq_acceptance_checkpoints(dq_handoff: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    boundaries = {
        row.get("backlog_area"): row
        for row in dq_handoff.get("acceptance_boundary_register", [])
    }
    for row in dq_handoff.get("backlog_route_handoff", []):
        boundary = boundaries.get(row.get("backlog_area"), {})
        rows.append(
            {
                "checkpoint_id": f"DQ_ACCEPT_{row.get('backlog_area')}",
                "backlog_area": row.get("backlog_area"),
                "priority": row.get("priority"),
                "owner_lane": row.get("owner_lane"),
                "row_count": row.get("row_count"),
                "minimum_artifact": boundary.get("minimum_artifact", row.get("required_evidence_output")),
                "pass_condition": boundary.get("pass_condition", row.get("acceptance_check")),
                "reentry_gate": row.get("reentry_gate"),
                "current_status": boundary.get("current_status", "pending_artifact_and_manual_acceptance"),
                "learning_boundary": row.get("learning_boundary"),
            }
        )
    rows.append(
        {
            "checkpoint_id": "DQ_ACCEPT_NO_DIRECT_LEARNING",
            "backlog_area": "all_backlog_areas",
            "priority": "P0",
            "owner_lane": "learning_boundary",
            "row_count": dq_handoff.get("metrics", {}).get("total_priority_backlog_rows"),
            "minimum_artifact": "explicit non-learning declaration in any re-entry report",
            "pass_condition": "backlog rows are not counted as Top1 gain, training labels, recall rules, ranking features, or safety-gate thresholds",
            "reentry_gate": "independent non-generated evidence inventory only; no direct learning from backlog rows",
            "current_status": "global_boundary_always_required",
            "learning_boundary": "exclude_direct_learning_use",
        }
    )
    return rows


def _future_reentry_gates(s2_requirements: list[dict[str, Any]], dq_checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": "S2_SOURCE_ROBUSTNESS_REENTRY",
            "required_inputs": "S2 non-generated positive net, >=2 independent non-generated sources, generated share no longer dominant",
            "current_blockers": "; ".join(
                row["requirement_id"]
                for row in s2_requirements
                if row["current_status"] in {"missing", "failed_now"}
            ),
            "pass_action": "allow a future read-only source robustness review, not automatic validation",
            "fail_action": "keep S2 held",
            "current_status": "blocked",
        },
        {
            "gate_id": "S1_RECALL_REENTRY",
            "required_inputs": "accepted source provenance, query_family_empty, top1_family coverage, and label mixture artifacts",
            "current_blockers": "; ".join(
                row["checkpoint_id"]
                for row in dq_checkpoints
                if str(row["current_status"]).startswith("pending") or str(row["current_status"]).startswith("not_ready")
            ),
            "pass_action": "allow future independent recall evidence inventory, not recall rule patch",
            "fail_action": "keep S1 recall learning deferred",
            "current_status": "blocked",
        },
        {
            "gate_id": "VALIDATION_REENTRY",
            "required_inputs": "S2/S1 evidence gate pass plus explicit validation-stage authorization",
            "current_blockers": "source robustness blocked; DQ acceptance pending; no explicit validation gate",
            "pass_action": "open separate validation review only",
            "fail_action": "no heldout/hard validation",
            "current_status": "blocked",
        },
        {
            "gate_id": "IMPLEMENTATION_REENTRY",
            "required_inputs": "post-validation implementation proposal with rollback, observability, and default-off boundary",
            "current_blockers": "no validation candidate frozen; no implementation authorization",
            "pass_action": "open separate implementation readiness review",
            "fail_action": "no GoalSearcher/ranking/feature whitelist change",
            "current_status": "blocked",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "reenter_learning_now",
            "reason": "S2 independent evidence and DQ acceptance checkpoints are not ready.",
            "allowed_after": "future read-only re-entry review after all relevant checkpoints pass",
        },
        {
            "blocked_action": "run_s2_or_s3_execution",
            "reason": "10.24 is an evidence-wait checkpoint, not an execution stage.",
            "allowed_after": "separate explicit execution authorization",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation",
            "reason": "No candidate has passed source/DQ re-entry gates.",
            "allowed_after": "future source robustness pass plus explicit validation gate",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "No learnable evidence lane is open.",
            "allowed_after": "future explicitly scoped dev/OOF execution after evidence gates pass",
        },
        {
            "blocked_action": "patch_rules_change_ranking_goal_searcher_or_feature_whitelist",
            "reason": "10.24 does not authorize implementation.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "S2 positive net remains generated-source dominated.",
            "allowed_after": "future independent non-generated evidence review",
        },
        {
            "blocked_action": "count_dq_backlog_rows_as_learning_evidence",
            "reason": "DQ backlog route remains non-learning and acceptance artifacts are pending.",
            "allowed_after": "never directly; only later independent cleaned evidence may be reviewed",
        },
    ]


def _metrics(
    parking_decisions: list[dict[str, Any]],
    s2_requirements: list[dict[str, Any]],
    dq_checkpoints: list[dict[str, Any]],
    reentry_gates: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    s2_hold: dict[str, Any],
    dq_handoff: dict[str, Any],
) -> dict[str, Any]:
    s2 = s2_hold.get("metrics", {})
    dq = dq_handoff.get("metrics", {})
    s2_blocking = sum(1 for row in s2_requirements if row["current_status"] in {"missing", "failed_now"})
    dq_pending = sum(
        1
        for row in dq_checkpoints
        if str(row["current_status"]).startswith("pending") or str(row["current_status"]).startswith("not_ready")
    )
    blocked_gate_count = sum(1 for row in reentry_gates if row["current_status"] == "blocked")
    return {
        "parking_decision_count": len(parking_decisions),
        "s2_evidence_requirement_count": len(s2_requirements),
        "s2_blocking_requirement_count": s2_blocking,
        "dq_acceptance_checkpoint_count": len(dq_checkpoints),
        "dq_pending_checkpoint_count": dq_pending,
        "future_reentry_gate_count": len(reentry_gates),
        "future_reentry_gate_blocked_count": blocked_gate_count,
        "blocked_action_count": len(blocked_actions),
        "learning_reentry_parked": True,
        "s2_candidate_held": s2.get("s2_candidate_held"),
        "s2_validation_allowed_now": s2.get("validation_allowed_now"),
        "generated_positive_net_share": s2.get("generated_positive_net_share"),
        "non_generated_positive_net": s2.get("non_generated_positive_net"),
        "non_generated_positive_source_count": s2.get("non_generated_positive_source_count"),
        "total_priority_backlog_rows": dq.get("total_priority_backlog_rows"),
        "data_quality_backlog_route_handed_off": dq.get("data_quality_backlog_route_handed_off"),
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "training_allowed": False,
        "implementation_allowed": False,
        "whatif_execution_allowed": False,
        "threshold_change_allowed": False,
        "ranking_change_allowed": False,
        "goal_searcher_change_allowed": False,
        "feature_whitelist_edit_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.24 Taxonomy/Data-quality Backlog Parked Checkpoint And S2 Independent Evidence-wait Review",
        "",
        "Read-only checkpoint confirming learning remains parked until independent non-generated evidence and DQ acceptance artifacts exist.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["learning_reentry_parked", metrics["learning_reentry_parked"]],
                ["s2_blocking_requirement_count", metrics["s2_blocking_requirement_count"]],
                ["dq_pending_checkpoint_count", metrics["dq_pending_checkpoint_count"]],
                ["future_reentry_gate_blocked_count", metrics["future_reentry_gate_blocked_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## S2 Evidence Requirements",
        "",
        _md_table(
            [["requirement_id", "current_value", "required_before_reentry", "current_status"]]
            + [
                [row["requirement_id"], row["current_value"], row["required_before_reentry"], row["current_status"]]
                for row in report["s2_independent_evidence_requirements"]
            ]
        ),
        "",
        "## Future Re-entry Gates",
        "",
        _md_table(
            [["gate_id", "current_status", "pass_action", "fail_action"]]
            + [[row["gate_id"], row["current_status"], row["pass_action"], row["fail_action"]] for row in report["future_reentry_gates"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.24 DQ backlog parked checkpoint and S2 independent evidence-wait review")
    parser.add_argument("--strategy-return", default=str(DEFAULT_STRATEGY_RETURN))
    parser.add_argument("--s2-hold", default=str(DEFAULT_S2_HOLD))
    parser.add_argument("--dq-handoff", default=str(DEFAULT_DQ_HANDOFF))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    strategy_return = _read_json(Path(args.strategy_return))
    s2_hold = _read_json(Path(args.s2_hold))
    dq_handoff = _read_json(Path(args.dq_handoff))

    parking_decisions = _parking_decisions(strategy_return, s2_hold, dq_handoff)
    s2_requirements = _s2_independent_evidence_requirements(s2_hold)
    dq_checkpoints = _dq_acceptance_checkpoints(dq_handoff)
    reentry_gates = _future_reentry_gates(s2_requirements, dq_checkpoints)
    blocked_actions = _blocked_actions()
    metrics = _metrics(parking_decisions, s2_requirements, dq_checkpoints, reentry_gates, blocked_actions, s2_hold, dq_handoff)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "parking_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_parking_decisions.csv")),
        "s2_independent_evidence_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_s2_independent_evidence_requirements.csv")),
        "dq_acceptance_checkpoints_csv": str(output_prefix.with_name(output_prefix.name + "_dq_acceptance_checkpoints.csv")),
        "future_reentry_gates_csv": str(output_prefix.with_name(output_prefix.name + "_future_reentry_gates.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.24 taxonomy/data-quality backlog parked checkpoint and S2 independent evidence-wait review",
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
            "strategy_return": str(Path(args.strategy_return)),
            "s2_hold": str(Path(args.s2_hold)),
            "dq_handoff": str(Path(args.dq_handoff)),
        },
        "metrics": metrics,
        "parking_decisions": parking_decisions,
        "s2_independent_evidence_requirements": s2_requirements,
        "dq_acceptance_checkpoints": dq_checkpoints,
        "future_reentry_gates": reentry_gates,
        "blocked_actions": blocked_actions,
        "decision": (
            "Keep learning re-entry parked. S2 cannot resume because independent non-generated support is still missing and generated source share remains 1.0. "
            "S1 recall learning cannot resume because DQ acceptance artifacts are still pending. Future re-entry requires a separate read-only review after the listed "
            "S2 evidence requirements and DQ acceptance checkpoints pass; this stage opens no execution, validation, training, or implementation."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.24 only records parked learning state and evidence-wait checkpoints. It does not execute S2/S3, run what-if, train, tune, expand candidates, "
            "run heldout/hard validation or selection, change thresholds, patch rules, modify ranking or GoalSearcher, edit feature whitelist, relax gates, claim "
            "general Top1 gain, convert DQ backlog rows into learning evidence, or connect online."
        ),
        "next_stage": {
            "stage": "10.25 evidence-wait closure and pause/request gate",
            "goal": (
                "Read-only decide whether to pause the 10.x learning loop awaiting external DQ/evidence artifacts, or request explicit user-provided independent "
                "non-generated evidence inputs for a future re-entry review."
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

    _write_csv(
        Path(artifacts["parking_decisions_csv"]),
        parking_decisions,
        ["parking_area", "status", "evidence", "decision", "resume_condition", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["s2_independent_evidence_requirements_csv"]),
        s2_requirements,
        ["requirement_id", "current_value", "required_before_reentry", "acceptance_check", "current_status", "learning_boundary"],
    )
    _write_csv(
        Path(artifacts["dq_acceptance_checkpoints_csv"]),
        dq_checkpoints,
        ["checkpoint_id", "backlog_area", "priority", "owner_lane", "row_count", "minimum_artifact", "pass_condition", "reentry_gate", "current_status", "learning_boundary"],
    )
    _write_csv(
        Path(artifacts["future_reentry_gates_csv"]),
        reentry_gates,
        ["gate_id", "required_inputs", "current_blockers", "pass_action", "fail_action", "current_status"],
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
