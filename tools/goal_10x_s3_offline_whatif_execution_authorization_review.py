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
DEFAULT_STAGE_10_15 = AGENT_STATE / "goal_10x_s3_offline_whatif_execution_gate_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_offline_whatif_execution_authorization_review"


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


def _authorization_checks(stage_10_15: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_15.get("metrics", {})
    return [
        {
            "check_id": "AUTH_GATE_REVIEW_PASSED",
            "status": "pass" if metrics.get("future_authorization_review_allowed") is True else "fail",
            "observed": f"future_authorization_review_allowed={metrics.get('future_authorization_review_allowed')}; execution_gate_fail_count={metrics.get('execution_gate_fail_count')}",
            "required": "10.15 execution gate review must pass before asking for execution authorization.",
            "decision": "ready_to_consider_explicit_go_request",
        },
        {
            "check_id": "AUTH_CANDIDATE_POLICY_SCOPE",
            "status": "pass" if metrics.get("source_candidate_policy_count") == 5 else "fail",
            "observed": f"source_candidate_policy_count={metrics.get('source_candidate_policy_count')}",
            "required": "Future execution must consume the reviewed five-row 10.14 candidate policy matrix.",
            "decision": "candidate_scope_frozen",
        },
        {
            "check_id": "AUTH_COMMAND_BOUNDARY",
            "status": "pass" if metrics.get("command_boundary_review_count", 0) >= 5 else "fail",
            "observed": f"command_boundary_review_count={metrics.get('command_boundary_review_count')}",
            "required": "Command boundaries must cover current-stage non-execution and future dev/OOF-only entry.",
            "decision": "future_command_contract_ready",
        },
        {
            "check_id": "AUTH_ARTIFACT_MANIFEST",
            "status": "pass" if metrics.get("artifact_readiness_count") == 6 else "fail",
            "observed": f"artifact_readiness_count={metrics.get('artifact_readiness_count')}",
            "required": "All six required artifact families must be ready before any future run.",
            "decision": "required_outputs_ready",
        },
        {
            "check_id": "AUTH_STOP_APPROVAL_LOCK",
            "status": "pass" if metrics.get("stop_condition_review_count") == 6 and metrics.get("approval_review_count") == 6 else "fail",
            "observed": f"stop_condition_review_count={metrics.get('stop_condition_review_count')}; approval_review_count={metrics.get('approval_review_count')}",
            "required": "Stop conditions and approval criteria must be locked before execution.",
            "decision": "evaluation_policy_frozen",
        },
        {
            "check_id": "AUTH_LOSS_BUDGET_SOURCE",
            "status": "pass" if metrics.get("source_loss_budget_gate_count") == 5 else "fail",
            "observed": f"source_loss_budget_gate_count={metrics.get('source_loss_budget_gate_count')}",
            "required": "Five loss-budget gates must remain frozen for future scoring.",
            "decision": "loss_budget_frozen",
        },
        {
            "check_id": "AUTH_HELDOUT_BOUNDARY",
            "status": "pass" if metrics.get("heldout_used_for_selection") is False else "fail",
            "observed": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "required": "Heldout/hard cannot be used for threshold, relation, policy, or approval selection.",
            "decision": "heldout_hard_stay_closed_for_selection",
        },
        {
            "check_id": "AUTH_NO_EXECUTION_IN_10_16",
            "status": "pass"
            if metrics.get("future_execution_authorized") is False
            and metrics.get("whatif_execution_allowed") is False
            and metrics.get("training_allowed") is False
            and metrics.get("implementation_allowed") is False
            else "fail",
            "observed": f"future_execution_authorized={metrics.get('future_execution_authorized')}; whatif_execution_allowed={metrics.get('whatif_execution_allowed')}; training_allowed={metrics.get('training_allowed')}; implementation_allowed={metrics.get('implementation_allowed')}",
            "required": "10.16 may review authorization only; it must not execute the what-if or implement policy.",
            "decision": "review_only",
        },
    ]


def _authorization_decisions() -> list[dict[str, Any]]:
    return [
        {
            "decision_id": "REQ_S3_DEV_OOF_WHATIF",
            "decision": "ready_to_request_explicit_user_go",
            "rationale": "10.15 passed the execution gate, but execution is a distinct action and must be explicitly opened by the user.",
            "current_stage_action": "do_not_execute",
            "next_allowed_action": "Ask for explicit go/no-go for a dev/OOF-only S3 what-if from the reviewed 10.14 plan.",
        },
        {
            "decision_id": "AUTHORIZATION_STATUS",
            "decision": "not_authorized_without_explicit_go",
            "rationale": "The current 10.16 request says read-only authorization review and default without explicit go is do_not_execute.",
            "current_stage_action": "emit authorization review package only",
            "next_allowed_action": "10.17 can collect explicit go/no-go; absent go remains held.",
        },
        {
            "decision_id": "EXECUTION_STATUS",
            "decision": "execution_held",
            "rationale": "Passing authorization checks is not the same as executing a what-if.",
            "current_stage_action": "hold execution",
            "next_allowed_action": "Execute only in a later explicitly authorized execution stage, if ever requested.",
        },
        {
            "decision_id": "HELDOUT_HARD_STATUS",
            "decision": "remain_validation_only",
            "rationale": "Future dev/OOF execution cannot use heldout/hard for policy, threshold, relation, or approval selection.",
            "current_stage_action": "keep heldout/hard out of selection",
            "next_allowed_action": "Validation-only after freeze, never selection.",
        },
    ]


def _required_user_confirmations() -> list[dict[str, Any]]:
    return [
        {
            "confirmation_id": "CONFIRM_EXPLICIT_GO",
            "required_confirmation": "The user must explicitly say to run the first dev/OOF-only S3 offline what-if.",
            "why_required": "Prevents an automatic execution jump from a read-only authorization review.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_REVIEWED_PLAN_ONLY",
            "required_confirmation": "Use only the reviewed 10.14 five-row candidate policy matrix.",
            "why_required": "Prevents candidate expansion after gate review.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_DEV_OOF_ONLY",
            "required_confirmation": "Use dev/OOF only; do not use heldout/hard for selection.",
            "why_required": "Prevents validation-set tuning and strategy leakage.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_COMPLETE_ARTIFACTS",
            "required_confirmation": "Emit all six required artifact families before interpreting results.",
            "why_required": "Prevents scorecard-only claims and missing loss/fallback/selection-boundary evidence.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_STOP_CONDITIONS",
            "required_confirmation": "Stop on heldout/hard contamination, missing artifacts, loss-budget failure, fallback break, source/taxonomy artifact, or single-relation dominance.",
            "why_required": "Prevents overriding failures inside a run.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_NO_ONLINE_CHANGE",
            "required_confirmation": "Do not change GoalSearcher, online ranking, feature whitelist, gates, switches, or production wiring.",
            "why_required": "Keeps the work offline and reversible.",
            "default_without_confirmation": "do_not_execute",
        },
    ]


def _pre_execution_hold_points() -> list[dict[str, Any]]:
    return [
        {
            "hold_point": "before_start",
            "condition": "explicit user go for S3 dev/OOF what-if is absent",
            "required_action": "hold; do not run any what-if scoring command",
            "source_lock": "10.16 authorization review",
        },
        {
            "hold_point": "scope_diff",
            "condition": "future command differs from the reviewed 10.14 candidate policy matrix or command contract",
            "required_action": "hold; return to plan definition or gate review",
            "source_lock": "10.14 plan and 10.15 gate review",
        },
        {
            "hold_point": "artifact_plan",
            "condition": "not all six artifact families are scheduled for emission",
            "required_action": "hold; no partial scorecard-only run",
            "source_lock": "10.14 artifact manifest",
        },
        {
            "hold_point": "heldout_hard_boundary",
            "condition": "heldout/hard appears in policy, threshold, relation, or approval selection",
            "required_action": "invalidate proposed run and hold",
            "source_lock": "10.14 selection boundary",
        },
        {
            "hold_point": "online_or_switch_boundary",
            "condition": "any command would modify GoalSearcher, switches, ranking code, or online wiring",
            "required_action": "hold; route to separate implementation readiness review",
            "source_lock": "default-off and no-online contract",
        },
    ]


def _automation_policy() -> list[dict[str, Any]]:
    return [
        {
            "policy_id": "AUTO_ADVANCE_READ_ONLY",
            "decision": "allowed_for_future_goal_mode",
            "scope": "read-only review, plan, inventory, closure, and dashboard update stages",
            "guard": "must stop before execution/training/tuning/threshold/ranking/online changes",
        },
        {
            "policy_id": "AUTO_STOP_AT_EXECUTION",
            "decision": "required",
            "scope": "any stage that would run what-if, train, tune, or change implementation",
            "guard": "requires explicit user go in the current thread",
        },
        {
            "policy_id": "AUTO_USE_DASHBOARD_NEXT_PROMPT",
            "decision": "recommended",
            "scope": "future automatic Goal mode",
            "guard": "read current stage from reports/agent_state/goal_learning_roadmap_dashboard.html before each step",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s3_offline_whatif",
            "reason": "10.16 is authorization review only; no explicit execution go is present.",
            "allowed_after": "later explicit user go opens a dev/OOF-only S3 execution stage",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "authorization review does not tune or alter policy.",
            "allowed_after": "separate implementation proposal after future evidence, if ever reached",
        },
        {
            "blocked_action": "expand_candidate_policy_matrix",
            "reason": "10.14 candidate matrix is frozen for any future execution request.",
            "allowed_after": "new plan-definition review, not inside authorization review",
        },
        {
            "blocked_action": "enable_compatibility_switch_or_connect_online",
            "reason": "default-off and no GoalSearcher integration remain locked.",
            "allowed_after": "post-validation integration readiness review, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "S3 authorization review is not model training or ranking-objective tuning.",
            "allowed_after": "separate explicitly authorized execution or training lane",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "10.16 does not change features, ranking, or GoalSearcher.",
            "allowed_after": "separate feature/ranking proposal and leakage review",
        },
    ]


def _metrics(
    authorization_checks: list[dict[str, Any]],
    authorization_decisions: list[dict[str, Any]],
    required_user_confirmations: list[dict[str, Any]],
    pre_execution_hold_points: list[dict[str, Any]],
    automation_policy: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_15: dict[str, Any],
) -> dict[str, Any]:
    pass_count = sum(1 for row in authorization_checks if row["status"] == "pass")
    source_metrics = stage_10_15.get("metrics", {})
    return {
        "authorization_check_count": len(authorization_checks),
        "authorization_check_pass_count": pass_count,
        "authorization_check_fail_count": len(authorization_checks) - pass_count,
        "ready_to_request_explicit_go": pass_count == len(authorization_checks),
        "explicit_go_present": False,
        "execution_authorized": False,
        "execution_performed": False,
        "default_decision_without_go": "do_not_execute",
        "authorization_decision_count": len(authorization_decisions),
        "required_user_confirmation_count": len(required_user_confirmations),
        "pre_execution_hold_point_count": len(pre_execution_hold_points),
        "automation_policy_count": len(automation_policy),
        "blocked_action_count": len(blocked_actions),
        "source_execution_gate_pass_count": source_metrics.get("execution_gate_pass_count", 0),
        "source_execution_gate_fail_count": source_metrics.get("execution_gate_fail_count", 0),
        "future_authorization_review_allowed": source_metrics.get("future_authorization_review_allowed"),
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.16 S3 Offline What-if Execution Authorization Review",
        "",
        "Read-only authorization review for the reviewed 10.14 S3 offline what-if plan. It decides whether the project is ready to request explicit go/no-go for a first dev/OOF-only execution. It does not execute the what-if.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["authorization_check_count", metrics["authorization_check_count"]],
                ["authorization_check_pass_count", metrics["authorization_check_pass_count"]],
                ["authorization_check_fail_count", metrics["authorization_check_fail_count"]],
                ["ready_to_request_explicit_go", metrics["ready_to_request_explicit_go"]],
                ["explicit_go_present", metrics["explicit_go_present"]],
                ["execution_authorized", metrics["execution_authorized"]],
                ["execution_performed", metrics["execution_performed"]],
                ["default_decision_without_go", metrics["default_decision_without_go"]],
            ]
        ),
        "",
        "## Authorization Checks",
        "",
        _md_table(
            [["check_id", "status", "observed", "decision"]]
            + [[row["check_id"], row["status"], row["observed"], row["decision"]] for row in report["authorization_checks"]]
        ),
        "",
        "## Automation Policy",
        "",
        _md_table(
            [["policy_id", "decision", "scope", "guard"]]
            + [[row["policy_id"], row["decision"], row["scope"], row["guard"]] for row in report["automation_policy"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.16 S3 offline what-if execution authorization review")
    parser.add_argument("--stage-10-15", default=str(DEFAULT_STAGE_10_15))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_15 = _read_json(Path(args.stage_10_15))
    authorization_checks = _authorization_checks(stage_10_15)
    authorization_decisions = _authorization_decisions()
    required_user_confirmations = _required_user_confirmations()
    pre_execution_hold_points = _pre_execution_hold_points()
    automation_policy = _automation_policy()
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        authorization_checks,
        authorization_decisions,
        required_user_confirmations,
        pre_execution_hold_points,
        automation_policy,
        blocked_actions,
        stage_10_15,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "authorization_checks_csv": str(output_prefix.with_name(output_prefix.name + "_authorization_checks.csv")),
        "authorization_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_authorization_decisions.csv")),
        "required_user_confirmations_csv": str(output_prefix.with_name(output_prefix.name + "_required_user_confirmations.csv")),
        "pre_execution_hold_points_csv": str(output_prefix.with_name(output_prefix.name + "_pre_execution_hold_points.csv")),
        "automation_policy_csv": str(output_prefix.with_name(output_prefix.name + "_automation_policy.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.16 S3 offline what-if execution authorization review",
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
            "stage_10_15_gate_review": str(Path(args.stage_10_15)),
        },
        "metrics": metrics,
        "authorization_checks": authorization_checks,
        "authorization_decisions": authorization_decisions,
        "required_user_confirmations": required_user_confirmations,
        "pre_execution_hold_points": pre_execution_hold_points,
        "automation_policy": automation_policy,
        "blocked_actions": blocked_actions,
        "decision": (
            "The reviewed 10.14 S3 offline what-if plan is ready to request an explicit go/no-go for a future dev/OOF-only execution, "
            "but 10.16 does not grant execution authorization because no explicit go is present. The default decision remains do_not_execute. "
            "Automatic Goal mode may continue read-only stages, but must stop before what-if execution, training, tuning, threshold changes, ranking changes, "
            "GoalSearcher changes, feature whitelist edits, heldout/hard selection, switch enablement, gate relaxation, or online integration."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.16 performs authorization review only. It does not run a what-if, train, tune, change thresholds, patch rules, change ranking, "
            "modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, enable a switch, or connect online. "
            "Without explicit go, the execution path remains held."
        ),
        "next_stage": {
            "stage": "10.17 S3 explicit dev/OOF execution go/no-go confirmation",
            "goal": (
                "Read-only collect an explicit go/no-go for first dev/OOF-only S3 offline what-if execution from the reviewed 10.14 plan. "
                "Default without explicit go remains do_not_execute; automatic Goal mode must stop here if no go is present."
            ),
            "prohibited": [
                "what-if execution without explicit go",
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

    _write_csv(
        Path(artifacts["authorization_checks_csv"]),
        authorization_checks,
        ["check_id", "status", "observed", "required", "decision"],
    )
    _write_csv(
        Path(artifacts["authorization_decisions_csv"]),
        authorization_decisions,
        ["decision_id", "decision", "rationale", "current_stage_action", "next_allowed_action"],
    )
    _write_csv(
        Path(artifacts["required_user_confirmations_csv"]),
        required_user_confirmations,
        ["confirmation_id", "required_confirmation", "why_required", "default_without_confirmation"],
    )
    _write_csv(
        Path(artifacts["pre_execution_hold_points_csv"]),
        pre_execution_hold_points,
        ["hold_point", "condition", "required_action", "source_lock"],
    )
    _write_csv(Path(artifacts["automation_policy_csv"]), automation_policy, ["policy_id", "decision", "scope", "guard"])
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
