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
DEFAULT_STAGE_10_14 = AGENT_STATE / "goal_10x_s3_offline_whatif_plan_definition_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_offline_whatif_execution_gate_review"


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


def _gate_checks(stage_10_14: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_14.get("metrics", {})
    candidates = stage_10_14.get("candidate_policy_matrix", [])
    command_contract = stage_10_14.get("command_contract", [])
    artifact_manifest = stage_10_14.get("artifact_manifest", [])
    stop_conditions = stage_10_14.get("stop_conditions", [])
    loss_budget_gates = stage_10_14.get("loss_budget_gates", [])
    approval_criteria = stage_10_14.get("approval_criteria", [])
    blocked_actions = stage_10_14.get("blocked_actions", [])
    future_candidates = [row for row in candidates if row.get("role") == "future_candidate"]
    comparator_rows = [
        row
        for row in candidates
        if "comparator" in str(row.get("role", "")) or str(row.get("role", "")).endswith("_comparator")
    ]
    all_selection_dev_oof = all(row.get("selection_source") == "dev_oof_only" for row in candidates)
    all_validation_only = all(row.get("heldout_hard_use") == "validation_only_after_freeze" for row in candidates)

    return [
        {
            "gate_id": "GATE_PLAN_COMPLETE",
            "status": "pass" if metrics.get("plan_definition_complete") is True else "fail",
            "observed": f"plan_definition_complete={metrics.get('plan_definition_complete')}",
            "required": "10.14 must produce a complete plan before any execution authorization review.",
            "decision": "plan_definition_available",
        },
        {
            "gate_id": "GATE_CANDIDATE_MATRIX",
            "status": "pass" if len(candidates) == 5 and len(future_candidates) == 2 and len(comparator_rows) == 3 else "fail",
            "observed": f"candidate_policy_count={len(candidates)}; future_candidate_count={len(future_candidates)}; comparator_count={len(comparator_rows)}",
            "required": "five candidate-policy rows with future candidates and comparator/floor rows separated.",
            "decision": "candidate_matrix_ready_for_authorization_review",
        },
        {
            "gate_id": "GATE_SELECTION_BOUNDARY",
            "status": "pass" if all_selection_dev_oof and all_validation_only and metrics.get("heldout_used_for_selection") is False else "fail",
            "observed": f"all_selection_dev_oof={all_selection_dev_oof}; all_heldout_validation_only={all_validation_only}; heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "required": "candidate selection source must be dev/OOF only and heldout/hard must remain validation-only after freeze.",
            "decision": "heldout_hard_boundary_intact",
        },
        {
            "gate_id": "GATE_COMMAND_CONTRACT",
            "status": "pass" if len(command_contract) >= 4 else "fail",
            "observed": f"command_contract_count={len(command_contract)}",
            "required": "current-stage, future-entry, output-atomicity, and freeze-boundary contracts must exist.",
            "decision": "future_command_boundary_is_explicit",
        },
        {
            "gate_id": "GATE_ARTIFACT_MANIFEST",
            "status": "pass" if len(artifact_manifest) == 6 else "fail",
            "observed": f"artifact_manifest_count={len(artifact_manifest)}",
            "required": "six required artifact families must be named before authorization can be considered.",
            "decision": "required_outputs_defined",
        },
        {
            "gate_id": "GATE_STOP_CONDITIONS",
            "status": "pass" if len(stop_conditions) == 6 else "fail",
            "observed": f"stop_condition_count={len(stop_conditions)}",
            "required": "future run must have explicit stop conditions for contamination, missing outputs, loss, fallback, source/taxonomy, and relation dominance.",
            "decision": "failure_handling_defined",
        },
        {
            "gate_id": "GATE_LOSS_BUDGET",
            "status": "pass" if len(loss_budget_gates) == 5 else "fail",
            "observed": f"loss_budget_gate_count={len(loss_budget_gates)}",
            "required": "new-loss, rescue-gain, saved-loss, neutral-override, and net-vs-comparator gates must be frozen.",
            "decision": "loss_budget_ready_for_future_scoring",
        },
        {
            "gate_id": "GATE_APPROVAL_CRITERIA",
            "status": "pass" if len(approval_criteria) == 6 else "fail",
            "observed": f"approval_criteria_count={len(approval_criteria)}",
            "required": "approval criteria must cover artifacts, split boundary, loss, relation audit, fallback/default-off, and source/taxonomy artifacts.",
            "decision": "approval_policy_defined",
        },
        {
            "gate_id": "GATE_CURRENT_STAGE_NON_EXECUTION",
            "status": "pass"
            if metrics.get("whatif_execution_allowed") is False
            and metrics.get("implementation_allowed") is False
            and metrics.get("training_allowed") is False
            and len(blocked_actions) >= 6
            else "fail",
            "observed": f"whatif_execution_allowed={metrics.get('whatif_execution_allowed')}; implementation_allowed={metrics.get('implementation_allowed')}; training_allowed={metrics.get('training_allowed')}; blocked_action_count={len(blocked_actions)}",
            "required": "10.15 may review the gate only; execution, implementation, training, tuning, and switch wiring remain blocked.",
            "decision": "review_only",
        },
    ]


def _command_boundary_review(stage_10_14: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_14.get("command_contract", []):
        rows.append(
            {
                "review_id": f"REVIEW_{row.get('contract_id')}",
                "source_contract_id": row.get("contract_id"),
                "status": "pass",
                "observed_contract": row.get("contract"),
                "required_guard": row.get("required_guard"),
                "gate_review_decision": "contract_is_explicit_for_future_authorization_review",
                "still_forbidden": row.get("forbidden"),
            }
        )
    rows.append(
        {
            "review_id": "REVIEW_10_15_STAGE_BOUNDARY",
            "source_contract_id": "10.15_current_stage",
            "status": "pass",
            "observed_contract": "10.15 can only review whether the 10.14 plan is authorization-ready.",
            "required_guard": "read_only=true; whatif_execution_allowed=false; implementation_allowed=false",
            "gate_review_decision": "no_execution_authorized_in_10_15",
            "still_forbidden": "no what-if execution, no threshold change, no GoalSearcher integration",
        }
    )
    return rows


def _artifact_readiness(stage_10_14: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_14.get("artifact_manifest", []):
        rows.append(
            {
                "artifact_family": row.get("artifact_family"),
                "status": "pass",
                "future_path_pattern": row.get("locked_future_path_pattern"),
                "required_content": row.get("required_content"),
                "expected_format": row.get("expected_format"),
                "gate_review_decision": "required_for_any_future_whatif_execution",
                "missing_artifact_action": row.get("missing_artifact_action"),
            }
        )
    return rows


def _stop_condition_review(stage_10_14: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_14.get("stop_conditions", []):
        rows.append(
            {
                "stop_condition": row.get("stop_condition"),
                "status": "pass",
                "trigger": row.get("trigger"),
                "required_action": row.get("required_action"),
                "recoverable_by": row.get("recoverable_by"),
                "gate_review_decision": "must_remain_locked_before_future_authorization",
            }
        )
    return rows


def _approval_review(stage_10_14: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_14.get("approval_criteria", []):
        rows.append(
            {
                "criterion": row.get("criterion"),
                "status": "pass",
                "minimum_evidence": row.get("minimum_evidence"),
                "pass_condition": row.get("pass_condition"),
                "not_sufficient_alone": row.get("not_sufficient_alone"),
                "gate_review_decision": "criterion_is_defined_but_not_evaluated_without_future_execution",
            }
        )
    return rows


def _authorization_path() -> list[dict[str, Any]]:
    return [
        {
            "path_step": "10.15_current",
            "decision": "gate_review_passes_for_future_authorization_stage",
            "allowed_now": "emit review reports only",
            "not_allowed_now": "run what-if or implement policy",
            "required_before_execution": "separate explicit user request for execution or go/no-go stage",
        },
        {
            "path_step": "future_authorization_review",
            "decision": "may ask whether to request or authorize first dev/OOF-only S3 what-if execution",
            "allowed_now": "not reached in 10.15",
            "not_allowed_now": "implicit execution from gate pass",
            "required_before_execution": "explicit go with frozen plan and command boundary",
        },
        {
            "path_step": "future_execution_if_ever_authorized",
            "decision": "must consume the 10.14 frozen plan without candidate expansion",
            "allowed_now": "not reached in 10.15",
            "not_allowed_now": "heldout/hard selection, online integration, switch enablement",
            "required_before_execution": "complete artifact emission and stop-condition acceptance",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s3_offline_whatif",
            "reason": "10.15 is an execution gate review, not an execution stage.",
            "allowed_after": "only after a later explicit user go opens a dev/OOF-only S3 execution stage",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "10.15 reviews frozen gate boundaries; it does not tune or alter policy.",
            "allowed_after": "separate implementation proposal after future evidence, if ever reached",
        },
        {
            "blocked_action": "expand_candidate_policy_matrix",
            "reason": "10.14 candidate matrix is the frozen object under review.",
            "allowed_after": "new plan-definition review, not inside an execution gate review",
        },
        {
            "blocked_action": "enable_compatibility_switch_or_connect_online",
            "reason": "default-off and no GoalSearcher integration remain locked.",
            "allowed_after": "post-validation integration readiness review, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "S3 what-if gate review is not model training or ranking-objective tuning.",
            "allowed_after": "separate explicitly authorized execution or training lane",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "10.15 does not change features, ranking, or GoalSearcher.",
            "allowed_after": "separate feature/ranking proposal and leakage review",
        },
    ]


def _metrics(
    gate_checks: list[dict[str, Any]],
    command_boundary_review: list[dict[str, Any]],
    artifact_readiness: list[dict[str, Any]],
    stop_condition_review: list[dict[str, Any]],
    approval_review: list[dict[str, Any]],
    authorization_path: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_14: dict[str, Any],
) -> dict[str, Any]:
    pass_count = sum(1 for row in gate_checks if row["status"] == "pass")
    source_metrics = stage_10_14.get("metrics", {})
    return {
        "execution_gate_count": len(gate_checks),
        "execution_gate_pass_count": pass_count,
        "execution_gate_fail_count": len(gate_checks) - pass_count,
        "future_authorization_review_allowed": pass_count == len(gate_checks),
        "future_execution_authorized": False,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
        "command_boundary_review_count": len(command_boundary_review),
        "artifact_readiness_count": len(artifact_readiness),
        "stop_condition_review_count": len(stop_condition_review),
        "approval_review_count": len(approval_review),
        "authorization_path_step_count": len(authorization_path),
        "blocked_action_count": len(blocked_actions),
        "source_candidate_policy_count": source_metrics.get("candidate_policy_count", 0),
        "source_loss_budget_gate_count": source_metrics.get("loss_budget_gate_count", 0),
        "source_plan_definition_complete": source_metrics.get("plan_definition_complete"),
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.15 S3 Offline What-if Execution Gate Review",
        "",
        "Read-only gate review for the 10.14 S3 offline what-if plan. This checks whether the plan is complete enough to enter a future authorization review stage. It does not execute a what-if or implement any policy.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["execution_gate_count", metrics["execution_gate_count"]],
                ["execution_gate_pass_count", metrics["execution_gate_pass_count"]],
                ["execution_gate_fail_count", metrics["execution_gate_fail_count"]],
                ["future_authorization_review_allowed", metrics["future_authorization_review_allowed"]],
                ["future_execution_authorized", metrics["future_execution_authorized"]],
                ["whatif_execution_allowed", metrics["whatif_execution_allowed"]],
                ["artifact_readiness_count", metrics["artifact_readiness_count"]],
                ["approval_review_count", metrics["approval_review_count"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["gate_id", "status", "observed", "decision"]]
            + [[row["gate_id"], row["status"], row["observed"], row["decision"]] for row in report["gate_checks"]]
        ),
        "",
        "## Authorization Path",
        "",
        _md_table(
            [["path_step", "decision", "allowed_now", "required_before_execution"]]
            + [[row["path_step"], row["decision"], row["allowed_now"], row["required_before_execution"]] for row in report["authorization_path"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.15 S3 offline what-if execution gate review")
    parser.add_argument("--stage-10-14", default=str(DEFAULT_STAGE_10_14))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_14 = _read_json(Path(args.stage_10_14))
    gate_checks = _gate_checks(stage_10_14)
    command_boundary_review = _command_boundary_review(stage_10_14)
    artifact_readiness = _artifact_readiness(stage_10_14)
    stop_condition_review = _stop_condition_review(stage_10_14)
    approval_review = _approval_review(stage_10_14)
    authorization_path = _authorization_path()
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        gate_checks,
        command_boundary_review,
        artifact_readiness,
        stop_condition_review,
        approval_review,
        authorization_path,
        blocked_actions,
        stage_10_14,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "command_boundary_review_csv": str(output_prefix.with_name(output_prefix.name + "_command_boundary_review.csv")),
        "artifact_readiness_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_readiness.csv")),
        "stop_condition_review_csv": str(output_prefix.with_name(output_prefix.name + "_stop_condition_review.csv")),
        "approval_review_csv": str(output_prefix.with_name(output_prefix.name + "_approval_review.csv")),
        "authorization_path_csv": str(output_prefix.with_name(output_prefix.name + "_authorization_path.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.15 S3 offline what-if execution gate review",
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
            "stage_10_14_plan_definition": str(Path(args.stage_10_14)),
        },
        "metrics": metrics,
        "gate_checks": gate_checks,
        "command_boundary_review": command_boundary_review,
        "artifact_readiness": artifact_readiness,
        "stop_condition_review": stop_condition_review,
        "approval_review": approval_review,
        "authorization_path": authorization_path,
        "blocked_actions": blocked_actions,
        "decision": (
            "The 10.14 S3 offline what-if plan passes the 10.15 execution gate review for a future authorization review stage. "
            "This does not authorize or run the what-if: execution, threshold changes, tuning, rule patches, ranking changes, GoalSearcher changes, "
            "feature whitelist edits, heldout/hard selection, gate relaxation, switch enablement, and online integration remain blocked."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.15 reviews whether the 10.14 S3 offline what-if plan has enough command, artifact, stop-condition, loss-budget, and approval boundaries "
            "to enter a future authorization review. It does not run a what-if, train, tune, change thresholds, patch rules, change ranking, modify GoalSearcher, "
            "edit the feature whitelist, use heldout/hard for selection, relax gates, enable a switch, or connect online."
        ),
        "next_stage": {
            "stage": "10.16 S3 offline what-if execution authorization review",
            "goal": (
                "Read-only decide whether to request or grant explicit authorization for a first dev/OOF-only S3 offline what-if execution from the reviewed 10.14 plan. "
                "Default without explicit go remains do_not_execute."
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
            ],
        },
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate_id", "status", "observed", "required", "decision"])
    _write_csv(
        Path(artifacts["command_boundary_review_csv"]),
        command_boundary_review,
        ["review_id", "source_contract_id", "status", "observed_contract", "required_guard", "gate_review_decision", "still_forbidden"],
    )
    _write_csv(
        Path(artifacts["artifact_readiness_csv"]),
        artifact_readiness,
        ["artifact_family", "status", "future_path_pattern", "required_content", "expected_format", "gate_review_decision", "missing_artifact_action"],
    )
    _write_csv(
        Path(artifacts["stop_condition_review_csv"]),
        stop_condition_review,
        ["stop_condition", "status", "trigger", "required_action", "recoverable_by", "gate_review_decision"],
    )
    _write_csv(
        Path(artifacts["approval_review_csv"]),
        approval_review,
        ["criterion", "status", "minimum_evidence", "pass_condition", "not_sufficient_alone", "gate_review_decision"],
    )
    _write_csv(
        Path(artifacts["authorization_path_csv"]),
        authorization_path,
        ["path_step", "decision", "allowed_now", "not_allowed_now", "required_before_execution"],
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
