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
DEFAULT_STAGE_10_24 = AGENT_STATE / "goal_10x_taxonomy_data_quality_backlog_parked_checkpoint_s2_independent_evidence_wait_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_evidence_wait_closure_pause_request_gate"


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


def _closure_gates(stage_10_24: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_24.get("metrics", {})
    return [
        {
            "gate": "learning_reentry_parked",
            "status": "pass" if metrics.get("learning_reentry_parked") is True else "fail",
            "observed": f"learning_reentry_parked={metrics.get('learning_reentry_parked')}",
            "decision": "do_not_reenter_learning",
            "not_allowed": "no rank/recall learning lane opens now",
        },
        {
            "gate": "s2_independent_evidence_missing",
            "status": "pass" if metrics.get("s2_blocking_requirement_count", 0) > 0 else "fail",
            "observed": (
                f"s2_blocking_requirement_count={metrics.get('s2_blocking_requirement_count')}; "
                f"non_generated_positive_net={metrics.get('non_generated_positive_net')}; "
                f"non_generated_positive_source_count={metrics.get('non_generated_positive_source_count')}"
            ),
            "decision": "pause_s2_until_external_evidence",
            "not_allowed": "no S2 resume, validation, implementation, or general gain claim",
        },
        {
            "gate": "dq_acceptance_pending",
            "status": "pass" if metrics.get("dq_pending_checkpoint_count", 0) > 0 else "fail",
            "observed": f"dq_pending_checkpoint_count={metrics.get('dq_pending_checkpoint_count')}",
            "decision": "pause_s1_until_dq_artifacts",
            "not_allowed": "no recall learning from DQ backlog rows",
        },
        {
            "gate": "all_future_reentry_gates_blocked",
            "status": "pass" if metrics.get("future_reentry_gate_blocked_count", 0) == metrics.get("future_reentry_gate_count", -1) else "fail",
            "observed": (
                f"future_reentry_gate_blocked_count={metrics.get('future_reentry_gate_blocked_count')}; "
                f"future_reentry_gate_count={metrics.get('future_reentry_gate_count')}"
            ),
            "decision": "close_current_auto_learning_loop",
            "not_allowed": "no automatic next learning stage",
        },
        {
            "gate": "execution_and_validation_boundary",
            "status": "pass",
            "observed": "training_allowed=false; implementation_allowed=false; heldout_used_for_selection=false; hard_used_for_selection=false",
            "decision": "remain_read_only",
            "not_allowed": "no execution, heldout/hard validation, training, tuning, thresholds, or online changes",
        },
    ]


def _pause_request_options() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "PAUSE_10X_LEARNING_LOOP_AWAIT_EXTERNAL_EVIDENCE",
            "status": "selected_default",
            "description": "Pause the current 10.x learning loop because all future re-entry gates are blocked.",
            "why": "No accepted DQ artifacts or independent non-generated evidence inputs are available in the workspace.",
            "next_boundary": "no automatic learning advance; resume only after explicit evidence package or accepted DQ artifacts",
        },
        {
            "option_id": "REQUEST_USER_PROVIDED_EVIDENCE_INPUTS",
            "status": "available_not_selected",
            "description": "Ask the user to provide a concrete independent evidence package for future read-only re-entry review.",
            "why": "S2 requires non-generated positive support and S1 requires accepted DQ/provenance artifacts.",
            "next_boundary": "future evidence intake/re-entry review only; still no execution or validation",
        },
        {
            "option_id": "CONTINUE_AUTO_READ_ONLY_MINING",
            "status": "rejected",
            "description": "Continue advancing read-only stages without new evidence.",
            "why": "That would create process churn while all re-entry gates remain blocked.",
            "next_boundary": "blocked until new external evidence exists",
        },
    ]


def _required_user_evidence_inputs(stage_10_24: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in stage_10_24.get("s2_independent_evidence_requirements", []):
        if item.get("current_status") in {"missing", "failed_now"}:
            rows.append(
                {
                    "input_id": item.get("requirement_id"),
                    "input_type": "s2_independent_non_generated_evidence",
                    "minimum_content": item.get("required_before_reentry"),
                    "acceptance_check": item.get("acceptance_check"),
                    "current_status": item.get("current_status"),
                    "future_review_use": "read-only source robustness re-entry review",
                }
            )
    for item in stage_10_24.get("dq_acceptance_checkpoints", []):
        if str(item.get("current_status")).startswith("pending"):
            rows.append(
                {
                    "input_id": item.get("checkpoint_id"),
                    "input_type": "dq_acceptance_artifact",
                    "minimum_content": item.get("minimum_artifact"),
                    "acceptance_check": item.get("pass_condition"),
                    "current_status": item.get("current_status"),
                    "future_review_use": "read-only S1/S2 re-entry prerequisite review",
                }
            )
    return rows


def _automation_boundary() -> list[dict[str, Any]]:
    return [
        {
            "boundary_id": "AUTO_ADVANCE_READ_ONLY",
            "status": "should_pause_for_learning_loop",
            "decision": "do_not_auto_create_more_learning_stages_without_new_evidence",
            "reason": "10.24 shows all future re-entry gates blocked.",
            "allowed_next": "manual user evidence package or accepted DQ artifacts can reopen a read-only review",
        },
        {
            "boundary_id": "NO_EXECUTION_BY_AUTOMATION",
            "status": "active",
            "decision": "automation_must_not_execute",
            "reason": "execution/training/validation remain prohibited.",
            "allowed_next": "explicit user go in a separate stage, after evidence gates pass",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "continue_10x_learning_auto_advance_without_new_evidence",
            "reason": "All future re-entry gates are blocked and no new evidence inputs exist.",
            "allowed_after": "new external evidence package or accepted DQ artifacts",
        },
        {
            "blocked_action": "run_s2_or_s3_execution",
            "reason": "The selected path is pause/request, not execution.",
            "allowed_after": "separate explicit execution authorization after evidence gates pass",
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
            "reason": "No implementation candidate is validated or authorized.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "S2 remains source-dominated and evidence-waiting.",
            "allowed_after": "future independent non-generated evidence review",
        },
        {
            "blocked_action": "count_dq_backlog_rows_as_learning_evidence",
            "reason": "DQ backlog rows remain non-learning prerequisites.",
            "allowed_after": "never directly; only later independent cleaned evidence may be reviewed",
        },
    ]


def _metrics(
    gates: list[dict[str, Any]],
    options: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    automation: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    stage_10_24: dict[str, Any],
) -> dict[str, Any]:
    source_metrics = stage_10_24.get("metrics", {})
    pass_count = sum(1 for row in gates if row["status"] == "pass")
    return {
        "closure_gate_count": len(gates),
        "closure_gate_pass_count": pass_count,
        "closure_gate_fail_count": len(gates) - pass_count,
        "pause_request_option_count": len(options),
        "required_user_evidence_input_count": len(inputs),
        "automation_boundary_count": len(automation),
        "blocked_action_count": len(blocked),
        "selected_path": "PAUSE_10X_LEARNING_LOOP_AWAIT_EXTERNAL_EVIDENCE",
        "request_user_evidence_inputs": True,
        "auto_learning_advance_should_pause": True,
        "learning_reentry_parked": True,
        "s2_blocking_requirement_count": source_metrics.get("s2_blocking_requirement_count"),
        "dq_pending_checkpoint_count": source_metrics.get("dq_pending_checkpoint_count"),
        "future_reentry_gate_blocked_count": source_metrics.get("future_reentry_gate_blocked_count"),
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
        "# Stage 10.25 Evidence-wait Closure And Pause/request Gate",
        "",
        "Read-only closure gate for the current 10.x learning loop. The selected path pauses learning until external evidence exists.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_path", metrics["selected_path"]],
                ["auto_learning_advance_should_pause", metrics["auto_learning_advance_should_pause"]],
                ["required_user_evidence_input_count", metrics["required_user_evidence_input_count"]],
                ["s2_blocking_requirement_count", metrics["s2_blocking_requirement_count"]],
                ["dq_pending_checkpoint_count", metrics["dq_pending_checkpoint_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Options",
        "",
        _md_table(
            [["option_id", "status", "why", "next_boundary"]]
            + [[row["option_id"], row["status"], row["why"], row["next_boundary"]] for row in report["pause_request_options"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.25 evidence-wait closure and pause/request gate")
    parser.add_argument("--stage-10-24", default=str(DEFAULT_STAGE_10_24))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_24 = _read_json(Path(args.stage_10_24))

    closure_gates = _closure_gates(stage_10_24)
    options = _pause_request_options()
    inputs = _required_user_evidence_inputs(stage_10_24)
    automation = _automation_boundary()
    blocked = _blocked_actions()
    metrics = _metrics(closure_gates, options, inputs, automation, blocked, stage_10_24)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_gates_csv": str(output_prefix.with_name(output_prefix.name + "_closure_gates.csv")),
        "pause_request_options_csv": str(output_prefix.with_name(output_prefix.name + "_pause_request_options.csv")),
        "required_user_evidence_inputs_csv": str(output_prefix.with_name(output_prefix.name + "_required_user_evidence_inputs.csv")),
        "automation_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_automation_boundary.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.25 evidence-wait closure and pause/request gate",
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
            "stage_10_24_checkpoint": str(Path(args.stage_10_24)),
        },
        "metrics": metrics,
        "closure_gates": closure_gates,
        "pause_request_options": options,
        "required_user_evidence_inputs": inputs,
        "automation_boundary": automation,
        "blocked_actions": blocked,
        "decision": (
            "Select PAUSE_10X_LEARNING_LOOP_AWAIT_EXTERNAL_EVIDENCE. The current 10.x learning loop should stop auto-advancing because S2 independent evidence "
            "is missing, DQ acceptance artifacts are pending, and all future re-entry gates are blocked. A future read-only re-entry review can be opened only after "
            "the user or upstream owner provides the listed independent non-generated evidence inputs or accepted DQ artifacts."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.25 only closes the current evidence-wait loop and records the pause/request boundary. It does not execute S2/S3, run what-if, train, tune, "
            "expand candidates, run heldout/hard validation or selection, change thresholds, patch rules, modify ranking or GoalSearcher, edit feature whitelist, "
            "relax gates, claim general Top1 gain, convert DQ backlog rows into learning evidence, or connect online."
        ),
        "next_stage": {
            "stage": "10.x learning loop paused awaiting external evidence",
            "goal": (
                "Do not auto-advance learning stages. Resume only when explicit independent non-generated evidence inputs or accepted DQ artifacts are provided for "
                "a future read-only re-entry review."
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
                "automatic learning-stage advance without new evidence",
            ],
        },
    }

    _write_csv(Path(artifacts["closure_gates_csv"]), closure_gates, ["gate", "status", "observed", "decision", "not_allowed"])
    _write_csv(Path(artifacts["pause_request_options_csv"]), options, ["option_id", "status", "description", "why", "next_boundary"])
    _write_csv(
        Path(artifacts["required_user_evidence_inputs_csv"]),
        inputs,
        ["input_id", "input_type", "minimum_content", "acceptance_check", "current_status", "future_review_use"],
    )
    _write_csv(Path(artifacts["automation_boundary_csv"]), automation, ["boundary_id", "status", "decision", "reason", "allowed_next"])
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
