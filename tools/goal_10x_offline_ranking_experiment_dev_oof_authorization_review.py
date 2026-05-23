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
DEFAULT_STAGE_10_6 = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_scope_lock_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_authorization_review"


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


def _authorization_checks(stage_10_6: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_6.get("metrics", {})
    return [
        {
            "check_id": "AUTH_SCOPE_LOCKED",
            "status": "pass" if metrics.get("scope_locked") is True else "fail",
            "observed": f"scope_locked={metrics.get('scope_locked')}",
            "required": "10.6 scope must be locked before asking for execution authorization",
            "decision": "ready_to_request_explicit_execution_permission",
        },
        {
            "check_id": "AUTH_CANDIDATE_MATRIX",
            "status": "pass" if metrics.get("candidate_matrix_row_count") == 32 else "fail",
            "observed": f"candidate_matrix_rows={metrics.get('candidate_matrix_row_count')}; future_candidates={metrics.get('future_candidate_count')}; frozen_comparator={metrics.get('frozen_comparator_count')}",
            "required": "4x8 candidate matrix with exactly one frozen comparator",
            "decision": "candidate_scope_frozen",
        },
        {
            "check_id": "AUTH_COMMAND_CONTRACT",
            "status": "pass" if metrics.get("command_contract_count", 0) >= 6 else "fail",
            "observed": f"command_contract_count={metrics.get('command_contract_count')}",
            "required": "future execution command contract must be explicit and dev/OOF-only",
            "decision": "future_command_contract_ready",
        },
        {
            "check_id": "AUTH_ARTIFACT_MANIFEST",
            "status": "pass" if metrics.get("artifact_manifest_count") == 5 else "fail",
            "observed": f"artifact_manifest_count={metrics.get('artifact_manifest_count')}",
            "required": "candidate scorecard, loss audit, leakage report, fallback report, and recall-boundary report",
            "decision": "required_outputs_ready",
        },
        {
            "check_id": "AUTH_STOP_AND_APPROVAL_LOCK",
            "status": "pass" if metrics.get("stop_condition_count") == 6 and metrics.get("approval_criteria_count") == 6 else "fail",
            "observed": f"stop_conditions={metrics.get('stop_condition_count')}; approval_criteria={metrics.get('approval_criteria_count')}",
            "required": "stop conditions and approval criteria must be locked before execution",
            "decision": "evaluation_policy_frozen",
        },
        {
            "check_id": "AUTH_HELDOUT_BOUNDARY",
            "status": "pass" if metrics.get("heldout_used_for_selection") is False else "fail",
            "observed": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "required": "heldout/hard cannot be used for threshold, strategy, candidate, or feature selection",
            "decision": "heldout_hard_stay_closed_for_selection",
        },
        {
            "check_id": "AUTH_NO_EXECUTION_IN_10_7",
            "status": "pass" if metrics.get("training_allowed") is False and metrics.get("implementation_allowed") is False else "fail",
            "observed": f"training_allowed={metrics.get('training_allowed')}; implementation_allowed={metrics.get('implementation_allowed')}",
            "required": "10.7 may authorize/request later execution but must not execute, train, tune, or change ranking",
            "decision": "review_only",
        },
    ]


def _execution_request_decisions() -> list[dict[str, Any]]:
    return [
        {
            "decision_id": "REQ_DEV_OOF_EXECUTION",
            "decision": "request_explicit_user_authorization",
            "rationale": "The 10.6 scope is locked, but execution is a distinct action that can train/score candidates and must be explicitly opened by the user.",
            "current_stage_action": "do_not_execute",
            "next_allowed_action": "user may explicitly request a dev/OOF-only execution stage using the locked scope",
        },
        {
            "decision_id": "AUTHORIZATION_STATUS",
            "decision": "not_auto_authorized",
            "rationale": "The current request asked for authorization review, not to run the first dev/OOF experiment.",
            "current_stage_action": "emit authorization package only",
            "next_allowed_action": "10.8 can be a read-only go/no-go confirmation or an explicit execution stage if the user says to execute",
        },
        {
            "decision_id": "HELDOUT_HARD_STATUS",
            "decision": "remain_closed",
            "rationale": "Future dev/OOF execution may not use heldout/hard for selection; those remain validation-only after freeze.",
            "current_stage_action": "keep heldout/hard out of selection",
            "next_allowed_action": "validation-only later, after a frozen candidate exists",
        },
    ]


def _required_user_confirmations(stage_10_6: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_6.get("metrics", {})
    return [
        {
            "confirmation_id": "CONFIRM_EXECUTE_SCOPE",
            "required_confirmation": f"Run only the locked 10.6 candidate matrix ({metrics.get('candidate_matrix_row_count')} rows).",
            "why_required": "Prevents ad hoc candidate expansion after scope lock.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_DEV_OOF_ONLY",
            "required_confirmation": "Use dev/OOF only; do not use heldout/hard for selection.",
            "why_required": "Prevents validation-set tuning and leakage into strategy selection.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_COMPLETE_ARTIFACTS",
            "required_confirmation": "Emit all five required artifact families before interpreting results.",
            "why_required": "Prevents scorecard-only promotion or missing loss/leakage/fallback reports.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_STOP_CONDITIONS",
            "required_confirmation": "Stop on leakage, heldout/hard selection contamination, missing outputs, loss budget failure, fallback break, or single-source/family artifact.",
            "why_required": "Prevents overriding failures inside a run.",
            "default_without_confirmation": "do_not_execute",
        },
        {
            "confirmation_id": "CONFIRM_NO_ONLINE_CHANGE",
            "required_confirmation": "Do not change GoalSearcher, online ranking, feature whitelist, gates, or production wiring.",
            "why_required": "Keeps the experiment offline and reversible.",
            "default_without_confirmation": "do_not_execute",
        },
    ]


def _pre_execution_hold_points(stage_10_6: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "hold_point": "before_start",
            "condition": "explicit user request for execution is absent",
            "required_action": "hold; no command that scores/trains candidates",
            "source_lock": "10.7 authorization review",
        },
        {
            "hold_point": "preflight_leakage",
            "condition": "forbidden identifier scan has not passed",
            "required_action": "hold; execution cannot start",
            "source_lock": "10.6 command contract",
        },
        {
            "hold_point": "artifact_plan",
            "condition": f"not all {len(stage_10_6.get('artifact_manifest', []))} artifact families are scheduled for emission",
            "required_action": "hold; no partial output plan",
            "source_lock": "10.6 artifact manifest",
        },
        {
            "hold_point": "scope_diff",
            "condition": "candidate matrix differs from locked 10.6 matrix",
            "required_action": "hold; return to scope lock or authorization review",
            "source_lock": "10.6 candidate matrix",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_dev_oof_experiment",
            "reason": "10.7 is authorization review only; current request does not explicitly say to execute.",
            "allowed_after": "later explicit user request opens execution stage",
        },
        {
            "blocked_action": "train_ltr_model",
            "reason": "training is execution, not authorization review.",
            "allowed_after": "future execution stage with explicit user authorization and locked scope",
        },
        {
            "blocked_action": "tune_objective_or_threshold",
            "reason": "10.7 may not optimize any objective or threshold.",
            "allowed_after": "future dev/OOF-only execution may score locked variants, still under stop conditions",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "10.6 matrix references current whitelist/toggles only.",
            "allowed_after": "separate feature proposal plus leakage preflight",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "authorization review is offline-only.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
    ]


def _metrics(
    authorization_checks: list[dict[str, Any]],
    execution_request_decisions: list[dict[str, Any]],
    required_user_confirmations: list[dict[str, Any]],
    pre_execution_hold_points: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_6: dict[str, Any],
) -> dict[str, Any]:
    pass_count = sum(1 for row in authorization_checks if row["status"] == "pass")
    metrics_10_6 = stage_10_6.get("metrics", {})
    return {
        "authorization_check_count": len(authorization_checks),
        "authorization_check_pass_count": pass_count,
        "authorization_check_fail_count": len(authorization_checks) - pass_count,
        "ready_to_request_explicit_execution": pass_count == len(authorization_checks),
        "auto_execution_authorized": False,
        "execution_performed": False,
        "execution_request_decision_count": len(execution_request_decisions),
        "required_user_confirmation_count": len(required_user_confirmations),
        "pre_execution_hold_point_count": len(pre_execution_hold_points),
        "blocked_action_count": len(blocked_actions),
        "locked_candidate_matrix_row_count": metrics_10_6.get("candidate_matrix_row_count", 0),
        "locked_future_candidate_count": metrics_10_6.get("future_candidate_count", 0),
        "locked_artifact_manifest_count": metrics_10_6.get("artifact_manifest_count", 0),
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.7 Offline Ranking Experiment Dev/OOF Execution Authorization Review",
        "",
        "Read-only authorization review for the locked 10.6 scope. It decides whether the project is ready to ask for an explicit dev/OOF-only execution request. It does not execute the experiment.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["authorization_check_count", metrics["authorization_check_count"]],
                ["authorization_check_pass_count", metrics["authorization_check_pass_count"]],
                ["authorization_check_fail_count", metrics["authorization_check_fail_count"]],
                ["ready_to_request_explicit_execution", metrics["ready_to_request_explicit_execution"]],
                ["auto_execution_authorized", metrics["auto_execution_authorized"]],
                ["execution_performed", metrics["execution_performed"]],
                ["required_user_confirmation_count", metrics["required_user_confirmation_count"]],
                ["pre_execution_hold_point_count", metrics["pre_execution_hold_point_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
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
    parser = argparse.ArgumentParser(description="Stage 10.7 dev/OOF execution authorization review")
    parser.add_argument("--stage-10-6", default=str(DEFAULT_STAGE_10_6))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_6 = _read_json(Path(args.stage_10_6))
    authorization_checks = _authorization_checks(stage_10_6)
    execution_request_decisions = _execution_request_decisions()
    required_user_confirmations = _required_user_confirmations(stage_10_6)
    pre_execution_hold_points = _pre_execution_hold_points(stage_10_6)
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        authorization_checks,
        execution_request_decisions,
        required_user_confirmations,
        pre_execution_hold_points,
        blocked_actions,
        stage_10_6,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "authorization_checks_csv": str(output_prefix.with_name(output_prefix.name + "_authorization_checks.csv")),
        "execution_request_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_execution_request_decisions.csv")),
        "required_user_confirmations_csv": str(output_prefix.with_name(output_prefix.name + "_required_user_confirmations.csv")),
        "pre_execution_hold_points_csv": str(output_prefix.with_name(output_prefix.name + "_pre_execution_hold_points.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.7 offline ranking experiment dev/OOF execution authorization review",
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
            "stage_10_6_summary": str(Path(args.stage_10_6)),
        },
        "metrics": metrics,
        "authorization_checks": authorization_checks,
        "execution_request_decisions": execution_request_decisions,
        "required_user_confirmations": required_user_confirmations,
        "pre_execution_hold_points": pre_execution_hold_points,
        "blocked_actions": blocked_actions,
        "decision": (
            "The locked 10.6 scope is ready to ask for an explicit future dev/OOF-only execution request, but 10.7 does not auto-authorize or run execution. "
            "A later user request must explicitly open the execution stage and accept the locked scope, dev/OOF-only selection, complete artifact emission, stop conditions, "
            "and no online/ranking/feature changes."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.7 is authorization review only. It does not run a dev/OOF experiment, train, tune, patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, "
            "use heldout/hard for selection, relax gates, or connect online."
        ),
        "next_stage": {
            "stage": "10.8 explicit dev/OOF execution go/no-go confirmation",
            "goal": (
                "Read-only collect a concrete go/no-go decision for the first dev/OOF-only execution from locked 10.6 scope; execute only if the user explicitly says to run it."
            ),
            "prohibited": [
                "training unless explicitly opened as execution",
                "tuning",
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
        Path(artifacts["execution_request_decisions_csv"]),
        execution_request_decisions,
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
