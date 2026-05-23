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
DEFAULT_STAGE_10_13 = AGENT_STATE / "goal_10x_s3_safety_gate_calibration_design_gate_summary.json"
DEFAULT_STAGE_10_12 = AGENT_STATE / "goal_10x_s3_safety_gate_policy_loss_budget_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_offline_whatif_plan_definition"


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


def _candidate_policy_matrix(stage_10_12: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_12.get("metrics", {})
    return [
        {
            "candidate_id": "POL_A_SELECTED_GATE_COMPARATOR",
            "policy_family": "frozen_comparator",
            "policy_variant": str(metrics.get("selected_gate_variant")),
            "relation_scope": "current selected safety gate only",
            "role": "frozen_comparator",
            "selection_source": "dev_oof_only",
            "heldout_hard_use": "validation_only_after_freeze",
            "whatif_execution_allowed_in_10_14": "no",
        },
        {
            "candidate_id": "POL_B_RELATION_FREEZE_CANDIDATES",
            "policy_family": "relation_level_compatibility",
            "policy_variant": "freeze sleeve_support_taxonomy_alias and valve_duct_air_system_neighbor",
            "relation_scope": "freeze_candidate_relations from 10.12",
            "role": "future_candidate",
            "selection_source": "dev_oof_only",
            "heldout_hard_use": "validation_only_after_freeze",
            "whatif_execution_allowed_in_10_14": "no",
        },
        {
            "candidate_id": "POL_C_FREEZE_PLUS_NARROW_CANDIDATES",
            "policy_family": "relation_freeze_narrow",
            "policy_variant": "freeze candidates plus narrow sleeve_duct_closed_wall_neighbor",
            "relation_scope": "freeze + narrow candidate relations from 10.12",
            "role": "future_candidate",
            "selection_source": "dev_oof_only",
            "heldout_hard_use": "validation_only_after_freeze",
            "whatif_execution_allowed_in_10_14": "no",
        },
        {
            "candidate_id": "POL_D_LOGGING_ONLY_COMPATIBILITY",
            "policy_family": "diagnostic_logging_only",
            "policy_variant": "log compatible relations without changing gate decision",
            "relation_scope": "all 10.12 relation slices, no override",
            "role": "diagnostic_comparator",
            "selection_source": "dev_oof_only",
            "heldout_hard_use": "validation_only_after_freeze",
            "whatif_execution_allowed_in_10_14": "no",
        },
        {
            "candidate_id": "POL_E_BASELINE_FALLBACK_ONLY",
            "policy_family": "fallback_floor",
            "policy_variant": "baseline fallback comparator with no compatibility override",
            "relation_scope": "fallback only",
            "role": "safety_floor_comparator",
            "selection_source": "dev_oof_only",
            "heldout_hard_use": "validation_only_after_freeze",
            "whatif_execution_allowed_in_10_14": "no",
        },
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "contract_id": "CC_10_14_ALLOWED",
            "scope": "current_stage",
            "contract": "10.14 may only define the S3 what-if plan and emit manifest files.",
            "required_guard": "read_only=true; whatif_execution_allowed=false; threshold_change_allowed=false",
            "forbidden": "no what-if execution, no threshold tuning, no switch wiring",
        },
        {
            "contract_id": "CC_FUTURE_WHATIF_ENTRY",
            "scope": "future_stage_only",
            "contract": "A later what-if execution stage must consume this frozen plan and run dev/OOF only.",
            "required_guard": "--frozen-s3-plan; --dev-oof-only; --no-heldout-selection; --emit-loss-audit",
            "forbidden": "no candidate expansion, no heldout/hard selection, no online integration",
        },
        {
            "contract_id": "CC_FUTURE_OUTPUT_ATOMICITY",
            "scope": "future_stage_outputs",
            "contract": "Future what-if execution is incomplete unless every required artifact family is emitted together.",
            "required_guard": "scorecard + relation audit + loss budget + residual slices + fallback/default-off report",
            "forbidden": "no scorecard-only success claim",
        },
        {
            "contract_id": "CC_FUTURE_FREEZE_BOUNDARY",
            "scope": "future_stage_validation",
            "contract": "Heldout/hard can only be used after candidate policy, relation scope, and loss gates are frozen.",
            "required_guard": "--frozen-policy; --validation-only",
            "forbidden": "no validation-set tuning or relation selection",
        },
    ]


def _artifact_manifest() -> list[dict[str, Any]]:
    return [
        {
            "artifact_family": "candidate_policy_scorecard",
            "locked_future_path_pattern": "reports/agent_state/goal_10x_s3_offline_whatif_candidate_policy_scorecard.*",
            "required_content": "candidate id, comparator, OOF net vs selected gate, rescued gain, new residual loss, saved loss retained, neutral override count",
            "expected_format": "csv + summary json",
            "missing_artifact_action": "block_approval_and_hold_results",
        },
        {
            "artifact_family": "relation_level_audit",
            "locked_future_path_pattern": "reports/agent_state/goal_10x_s3_offline_whatif_relation_level_audit.*",
            "required_content": "freeze/narrow/low-support/suspect/out-of-scope relations by split",
            "expected_format": "csv + markdown",
            "missing_artifact_action": "block_approval_and_hold_results",
        },
        {
            "artifact_family": "loss_budget_gate_report",
            "locked_future_path_pattern": "reports/agent_state/goal_10x_s3_offline_whatif_loss_budget_gate_report.*",
            "required_content": "new-loss ceiling, rescue gain floor, saved-loss retention, neutral override visibility, net-vs-comparator reporting",
            "expected_format": "json + csv",
            "missing_artifact_action": "block_approval_and_hold_results",
        },
        {
            "artifact_family": "residual_slice_report",
            "locked_future_path_pattern": "reports/agent_state/goal_10x_s3_offline_whatif_residual_slice_report.*",
            "required_content": "outcome, diagnosis, relation, source/province, taxonomy, rank/book/margin slices",
            "expected_format": "csv tables per slice",
            "missing_artifact_action": "block_approval_and_hold_results",
        },
        {
            "artifact_family": "fallback_default_off_report",
            "locked_future_path_pattern": "reports/agent_state/goal_10x_s3_offline_whatif_fallback_default_off_report.*",
            "required_content": "baseline fallback retention, selected gate comparator, default-off switch status, no GoalSearcher integration evidence",
            "expected_format": "csv + markdown",
            "missing_artifact_action": "block_approval_and_hold_results",
        },
        {
            "artifact_family": "selection_boundary_report",
            "locked_future_path_pattern": "reports/agent_state/goal_10x_s3_offline_whatif_selection_boundary_report.*",
            "required_content": "dev/OOF-only selection proof and heldout/hard untouched-for-selection declaration",
            "expected_format": "json",
            "missing_artifact_action": "invalidate_run_as_selection_unclear",
        },
    ]


def _stop_conditions() -> list[dict[str, Any]]:
    return [
        {
            "stop_condition": "heldout_or_hard_selection_contamination",
            "trigger": "heldout/hard influences threshold, relation, candidate, loss gate, or approval choice",
            "required_action": "invalidate future what-if run",
            "recoverable_by": "restart from dev/OOF-only frozen plan",
        },
        {
            "stop_condition": "missing_required_artifact",
            "trigger": "any artifact family in the 10.14 manifest is absent",
            "required_action": "block approval and do not interpret scorecard",
            "recoverable_by": "rerun future what-if with complete artifact emission",
        },
        {
            "stop_condition": "new_residual_loss_over_budget",
            "trigger": "new residual loss is hidden, unbucketed, or exceeds frozen loss ceiling",
            "required_action": "candidate cannot advance even if rescue gain is positive",
            "recoverable_by": "new plan review, not threshold tweaking inside failed run",
        },
        {
            "stop_condition": "fallback_or_default_off_break",
            "trigger": "candidate bypasses baseline fallback, relaxes selected gate, or enables switch behavior",
            "required_action": "block promotion and preserve current default-off behavior",
            "recoverable_by": "separate integration readiness review after frozen validation",
        },
        {
            "stop_condition": "source_or_taxonomy_artifact",
            "trigger": "gain is source-dominated, taxonomy-empty dominated, or depends on data-quality backlog rows",
            "required_action": "treat as diagnostic artifact, not calibration improvement",
            "recoverable_by": "independent-slice evidence or data-quality re-entry review",
        },
        {
            "stop_condition": "single_relation_dominance",
            "trigger": "claim relies on one relation without supporting residual slices",
            "required_action": "block general S3 improvement claim",
            "recoverable_by": "relation-specific review or broader cross-relation evidence",
        },
    ]


def _loss_budget_gates(stage_10_12: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_12.get("loss_budget", []):
        rows.append(
            {
                "gate_id": row.get("budget_id"),
                "metric": row.get("metric"),
                "reference_value": row.get("reference_value"),
                "required_gate": row.get("future_ceiling"),
                "failure_action": row.get("promotion_block_if"),
            }
        )
    return rows


def _approval_criteria() -> list[dict[str, Any]]:
    return [
        {
            "criterion": "complete_artifact_manifest",
            "minimum_evidence": "all six 10.14 artifact families exist and agree on candidate ids",
            "pass_condition": "no missing artifact family",
            "not_sufficient_alone": "complete files without passing loss gates do not approve anything",
        },
        {
            "criterion": "dev_oof_only_selection",
            "minimum_evidence": "selection boundary report says heldout_used_for_selection=false",
            "pass_condition": "heldout/hard untouched until frozen validation",
            "not_sufficient_alone": "dev/OOF success still needs frozen validation before integration",
        },
        {
            "criterion": "loss_budget_pass",
            "minimum_evidence": "new loss, rescue gain, saved loss, neutral override, and net-vs-comparator gates all pass",
            "pass_condition": "no hidden or over-budget new residual loss",
            "not_sufficient_alone": "positive net gain cannot override fallback or artifact failures",
        },
        {
            "criterion": "relation_level_audit_pass",
            "minimum_evidence": "freeze/narrow/suspect/low-support/out-of-scope relation rows are separated",
            "pass_condition": "no single relation or suspect relation is claimed as general improvement",
            "not_sufficient_alone": "relation-specific pass cannot justify broad gate relaxation",
        },
        {
            "criterion": "fallback_default_off_retained",
            "minimum_evidence": "baseline fallback and default-off switch status are preserved",
            "pass_condition": "no GoalSearcher integration and no switch enablement",
            "not_sufficient_alone": "safe default-off what-if still requires later approval to execute",
        },
        {
            "criterion": "source_taxonomy_artifact_clear",
            "minimum_evidence": "source/province/taxonomy-empty slices are reported and not dominant",
            "pass_condition": "data-quality backlog rows remain diagnostic, not learning evidence",
            "not_sufficient_alone": "clean slices are necessary but not enough without split-level gain/loss",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_offline_whatif",
            "reason": "10.14 defines the plan only.",
            "allowed_after": "separate explicit S3 what-if execution authorization and execution stage",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "candidate policies are named but not tuned.",
            "allowed_after": "future dev/OOF-only execution plus later implementation review, if ever reached",
        },
        {
            "blocked_action": "enable_compatibility_switch_or_connect_online",
            "reason": "default-off and no GoalSearcher integration remain locked.",
            "allowed_after": "post-freeze validation integration readiness review, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "S3 what-if plan does not train or tune models.",
            "allowed_after": "separate explicitly opened execution stage",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "10.14 does not change features, ranking, or GoalSearcher.",
            "allowed_after": "separate feature/ranking proposal and leakage review",
        },
    ]


def _metrics(
    candidate_matrix: list[dict[str, Any]],
    command_contract: list[dict[str, Any]],
    artifact_manifest: list[dict[str, Any]],
    stop_conditions: list[dict[str, Any]],
    loss_budget_gates: list[dict[str, Any]],
    approval_criteria: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_13: dict[str, Any],
) -> dict[str, Any]:
    metrics_10_13 = stage_10_13.get("metrics", {})
    return {
        "candidate_policy_count": len(candidate_matrix),
        "future_candidate_count": sum(1 for row in candidate_matrix if row["role"] == "future_candidate"),
        "comparator_count": sum(1 for row in candidate_matrix if "comparator" in row["role"] or row["role"].endswith("_comparator")),
        "command_contract_count": len(command_contract),
        "artifact_manifest_count": len(artifact_manifest),
        "stop_condition_count": len(stop_conditions),
        "loss_budget_gate_count": len(loss_budget_gates),
        "approval_criteria_count": len(approval_criteria),
        "blocked_action_count": len(blocked_actions),
        "source_design_gate_passed": metrics_10_13.get("s3_design_gate_passed"),
        "plan_definition_complete": True,
        "whatif_execution_allowed": False,
        "threshold_change_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.14 S3 Offline What-if Plan Definition",
        "",
        "Read-only plan definition for a future S3 offline what-if. This defines the candidate policy matrix, command contract, required artifacts, stop conditions, loss-budget gates, and approval criteria. It does not execute a what-if.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_policy_count", metrics["candidate_policy_count"]],
                ["future_candidate_count", metrics["future_candidate_count"]],
                ["command_contract_count", metrics["command_contract_count"]],
                ["artifact_manifest_count", metrics["artifact_manifest_count"]],
                ["stop_condition_count", metrics["stop_condition_count"]],
                ["loss_budget_gate_count", metrics["loss_budget_gate_count"]],
                ["approval_criteria_count", metrics["approval_criteria_count"]],
                ["whatif_execution_allowed", metrics["whatif_execution_allowed"]],
            ]
        ),
        "",
        "## Candidate Matrix",
        "",
        _md_table(
            [["candidate_id", "policy_family", "role", "whatif_execution_allowed_in_10_14"]]
            + [[row["candidate_id"], row["policy_family"], row["role"], row["whatif_execution_allowed_in_10_14"]] for row in report["candidate_policy_matrix"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.14 S3 offline what-if plan definition")
    parser.add_argument("--stage-10-13", default=str(DEFAULT_STAGE_10_13))
    parser.add_argument("--stage-10-12", default=str(DEFAULT_STAGE_10_12))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_13 = _read_json(Path(args.stage_10_13))
    stage_10_12 = _read_json(Path(args.stage_10_12))

    candidate_matrix = _candidate_policy_matrix(stage_10_12)
    command_contract = _command_contract()
    artifact_manifest = _artifact_manifest()
    stop_conditions = _stop_conditions()
    loss_budget_gates = _loss_budget_gates(stage_10_12)
    approval_criteria = _approval_criteria()
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        candidate_matrix,
        command_contract,
        artifact_manifest,
        stop_conditions,
        loss_budget_gates,
        approval_criteria,
        blocked_actions,
        stage_10_13,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_policy_matrix_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_policy_matrix.csv")),
        "command_contract_csv": str(output_prefix.with_name(output_prefix.name + "_command_contract.csv")),
        "artifact_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_manifest.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "loss_budget_gates_csv": str(output_prefix.with_name(output_prefix.name + "_loss_budget_gates.csv")),
        "approval_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_approval_criteria.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.14 S3 offline what-if plan definition",
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
            "stage_10_13_design_gate": str(Path(args.stage_10_13)),
            "stage_10_12_policy_loss_budget": str(Path(args.stage_10_12)),
        },
        "metrics": metrics,
        "candidate_policy_matrix": candidate_matrix,
        "command_contract": command_contract,
        "artifact_manifest": artifact_manifest,
        "stop_conditions": stop_conditions,
        "loss_budget_gates": loss_budget_gates,
        "approval_criteria": approval_criteria,
        "blocked_actions": blocked_actions,
        "decision": (
            "Define the future S3 offline what-if plan and keep it read-only. The plan freezes a five-row candidate policy matrix, "
            "OOF-only command contract, six required artifact families, six stop conditions, five loss-budget gates, and six approval criteria. "
            "This does not execute a what-if, change thresholds, tune, patch rules, change ranking, modify GoalSearcher, or connect online."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.14 defines the S3 what-if plan only. It does not run a what-if, train, tune, change thresholds, patch rules, change ranking, "
            "modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, enable a switch, or connect online."
        ),
        "next_stage": {
            "stage": "10.15 S3 offline what-if execution gate review",
            "goal": (
                "Read-only review whether the 10.14 S3 what-if plan is complete enough for a possible later execution authorization stage, including command boundaries, "
                "expected artifacts, stop conditions, and approval criteria. Still no what-if execution or implementation."
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

    _write_csv(
        Path(artifacts["candidate_policy_matrix_csv"]),
        candidate_matrix,
        ["candidate_id", "policy_family", "policy_variant", "relation_scope", "role", "selection_source", "heldout_hard_use", "whatif_execution_allowed_in_10_14"],
    )
    _write_csv(Path(artifacts["command_contract_csv"]), command_contract, ["contract_id", "scope", "contract", "required_guard", "forbidden"])
    _write_csv(
        Path(artifacts["artifact_manifest_csv"]),
        artifact_manifest,
        ["artifact_family", "locked_future_path_pattern", "required_content", "expected_format", "missing_artifact_action"],
    )
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, ["stop_condition", "trigger", "required_action", "recoverable_by"])
    _write_csv(Path(artifacts["loss_budget_gates_csv"]), loss_budget_gates, ["gate_id", "metric", "reference_value", "required_gate", "failure_action"])
    _write_csv(Path(artifacts["approval_criteria_csv"]), approval_criteria, ["criterion", "minimum_evidence", "pass_condition", "not_sufficient_alone"])
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
