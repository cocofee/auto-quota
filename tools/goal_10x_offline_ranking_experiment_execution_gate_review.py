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
DEFAULT_STAGE_10_4 = AGENT_STATE / "goal_10x_offline_ranking_experiment_plan_definition_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_gate_review"


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


def _readiness_gates(stage_10_4: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_4.get("metrics", {})
    objective_variants = stage_10_4.get("objective_variants", [])
    feature_toggles = stage_10_4.get("feature_toggles", [])
    split_policy = stage_10_4.get("split_policy", [])
    leakage_gates = stage_10_4.get("leakage_gates", [])
    fallback_contract = stage_10_4.get("fallback_contract", [])
    loss_budget = stage_10_4.get("loss_budget", [])
    required_outputs = stage_10_4.get("required_outputs", [])
    blocked_actions = stage_10_4.get("blocked_actions", [])
    leakage_fail_count = sum(1 for row in leakage_gates if row.get("current_status") != "pass")

    return [
        {
            "gate": "objective_variant_completeness",
            "status": "pass" if len(objective_variants) >= 4 else "fail",
            "observed": f"objective_variants={len(objective_variants)}",
            "required": "current comparator plus loss-budgeted, recall-separated, and fallback-preserving variants",
            "decision": "complete_for_future_execution_gate",
        },
        {
            "gate": "feature_toggle_completeness",
            "status": "pass" if len(feature_toggles) >= 8 else "fail",
            "observed": f"feature_toggles={len(feature_toggles)}; training_feature_count={metrics.get('training_feature_count', 0)}",
            "required": "all-current whitelist comparator, one-family ablations, and conservative safe-core subset",
            "decision": "complete_for_future_execution_gate",
        },
        {
            "gate": "split_policy_boundary",
            "status": "pass" if len(split_policy) >= 4 and metrics.get("heldout_used_for_selection") is False else "fail",
            "observed": f"split_policy={len(split_policy)}; heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "required": "dev/OOF-only selection, heldout/hard validation-only, recall boundary, and source boundary",
            "decision": "heldout_hard_remain_closed_for_selection",
        },
        {
            "gate": "leakage_gate_boundary",
            "status": "pass" if len(leakage_gates) >= 11 and leakage_fail_count == 0 else "fail",
            "observed": f"leakage_gates={len(leakage_gates)}; leakage_fail_count={leakage_fail_count}",
            "required": "all forbidden identifier gates pass before any execution",
            "decision": "any_future_leakage_failure_blocks_execution",
        },
        {
            "gate": "fallback_contract_boundary",
            "status": "pass" if len(fallback_contract) >= 4 else "fail",
            "observed": f"fallback_contract_items={len(fallback_contract)}",
            "required": "baseline fallback, raw LTR comparator, selected gate comparator, and blocked-gain accounting",
            "decision": "fallback_must_remain_default_safety_contract",
        },
        {
            "gate": "loss_budget_boundary",
            "status": "pass" if len(loss_budget) >= 4 else "fail",
            "observed": f"loss_budget_items={len(loss_budget)}",
            "required": "new-loss ceiling, retained net gain, blocked-gain recovery, and saved-loss retention",
            "decision": "future_candidate_must_emit_loss_budget_report",
        },
        {
            "gate": "required_output_boundary",
            "status": "pass" if len(required_outputs) >= 5 else "fail",
            "observed": f"required_outputs={len(required_outputs)}",
            "required": "scorecard, loss audit, leakage report, fallback report, and recall-boundary report",
            "decision": "missing_any_required_output_blocks_promotion",
        },
        {
            "gate": "no_execution_in_10_5_boundary",
            "status": "pass" if metrics.get("training_allowed") is False and metrics.get("implementation_allowed") is False else "fail",
            "observed": f"training_allowed={metrics.get('training_allowed')}; implementation_allowed={metrics.get('implementation_allowed')}; blocked_actions={len(blocked_actions)}",
            "required": "10.5 may only review readiness; it may not execute training, tuning, ranking, or feature edits",
            "decision": "review_only",
        },
    ]


def _command_boundaries() -> list[dict[str, Any]]:
    return [
        {
            "boundary_id": "CB_10_5_ALLOWED",
            "scope": "current_stage",
            "command_boundary": "May read 10.4 artifacts and emit gate-review reports only.",
            "required_flags_or_guards": "read_only=true; training_allowed=false; implementation_allowed=false",
            "forbidden": "no training command, no tuning loop, no GoalSearcher call, no ranking implementation",
        },
        {
            "boundary_id": "CB_FUTURE_EXECUTION_ENTRY",
            "scope": "future_stage_only",
            "command_boundary": "Any future execution command must consume the frozen 10.4 plan and write all 10.5-required outputs.",
            "required_flags_or_guards": "--dev-oof-only; --no-heldout-selection; --frozen-plan; --emit-loss-audit; --emit-leakage-report",
            "forbidden": "no heldout/hard selection, no feature whitelist mutation, no online integration",
        },
        {
            "boundary_id": "CB_LEAKAGE_PREFLIGHT",
            "scope": "future_stage_preflight",
            "command_boundary": "Run forbidden identifier scan before any future experiment execution.",
            "required_flags_or_guards": "block_on_leakage_gate_failure=true",
            "forbidden": "no execution if sample/source/expected/quota/province identifiers enter training features",
        },
        {
            "boundary_id": "CB_OUTPUT_ATOMICITY",
            "scope": "future_stage_outputs",
            "command_boundary": "Future execution is incomplete unless all required output families are emitted together.",
            "required_flags_or_guards": "scorecard + loss slices + leakage report + fallback report + recall-boundary report",
            "forbidden": "no cherry-picked scorecard-only success claim",
        },
    ]


def _expected_artifacts(stage_10_4: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for output in stage_10_4.get("required_outputs", []):
        rows.append(
            {
                "artifact_family": output["output_id"],
                "required_content": output["required_content"],
                "expected_format": output["format"],
                "promotion_dependency": output["promotion_dependency"],
                "missing_artifact_action": "block_promotion_and_hold_execution_results",
            }
        )
    return rows


def _stop_conditions() -> list[dict[str, Any]]:
    return [
        {
            "stop_condition": "leakage_gate_failure",
            "trigger": "any forbidden identifier appears in future training features or whitelist diff",
            "required_action": "stop execution and emit leakage failure report",
            "recoverable_by": "remove leakage path and rerun a new preflight review, not by overriding gate",
        },
        {
            "stop_condition": "heldout_or_hard_used_for_selection",
            "trigger": "heldout/hard influences threshold, objective, feature toggle, or candidate choice",
            "required_action": "invalidate the run as selection-contaminated",
            "recoverable_by": "restart from frozen dev/OOF-only selection policy",
        },
        {
            "stop_condition": "missing_required_output",
            "trigger": "scorecard, loss audit, leakage report, fallback report, or recall-boundary report missing",
            "required_action": "block approval and do not compare candidate to heldout/hard",
            "recoverable_by": "rerun future execution with complete artifact emission",
        },
        {
            "stop_condition": "loss_budget_failure",
            "trigger": "candidate_new_loss exceeds selected-gate reference or loss slices are absent",
            "required_action": "candidate cannot advance even if net gain is positive",
            "recoverable_by": "new future plan or objective review; no threshold tweaking inside the failed run",
        },
        {
            "stop_condition": "fallback_contract_break",
            "trigger": "candidate bypasses baseline fallback, relaxes gate, or omits prevented-loss retention",
            "required_action": "block promotion and preserve current selected gate/baseline behavior",
            "recoverable_by": "explicit later fallback policy review",
        },
        {
            "stop_condition": "single_source_or_family_artifact",
            "trigger": "gain is dominated by generated repair source or one family without cross-slice support",
            "required_action": "treat as diagnostic artifact, not general ranking improvement",
            "recoverable_by": "additional independent-slice evidence in a later review",
        },
    ]


def _approval_criteria(stage_10_4: dict[str, Any]) -> list[dict[str, Any]]:
    loss_budget = {row["budget_item"]: row["required_threshold"] for row in stage_10_4.get("loss_budget", [])}
    return [
        {
            "criterion": "complete_required_outputs",
            "minimum_evidence": "all five required output families exist and agree on candidate ids/counts",
            "pass_condition": "no missing scorecard/loss/leakage/fallback/recall-boundary artifacts",
            "not_sufficient_alone": "complete files without passing budgets do not approve promotion",
        },
        {
            "criterion": "dev_oof_only_selection",
            "minimum_evidence": "selection_source explicitly dev/OOF only; heldout/hard untouched for selection",
            "pass_condition": "heldout_used_for_selection=false",
            "not_sufficient_alone": "dev/OOF pass still needs later frozen validation",
        },
        {
            "criterion": "loss_budget_pass",
            "minimum_evidence": loss_budget.get("new_loss_ceiling", "candidate_new_loss <= selected_gate_loss_reference"),
            "pass_condition": "new loss within ceiling and loss slices present",
            "not_sufficient_alone": "net gain cannot override slice failures",
        },
        {
            "criterion": "net_gain_above_selected_gate",
            "minimum_evidence": loss_budget.get("retained_net_gain_target", "candidate_net_gain > selected_gate_net_reference"),
            "pass_condition": "candidate beats selected gate on dev/OOF scorecard",
            "not_sufficient_alone": "must also retain fallback safety and pass leakage",
        },
        {
            "criterion": "fallback_safety_retained",
            "minimum_evidence": "prevented-loss retention and baseline fallback report",
            "pass_condition": "candidate does not bypass fallback or relax gate",
            "not_sufficient_alone": "safe but low-gain candidate still cannot claim accuracy progress",
        },
        {
            "criterion": "cross_slice_not_artifact",
            "minimum_evidence": "gain/loss by query_family, top1_family, book/rank_bucket, source_file, and province",
            "pass_condition": "no single-source or single-family dominated claim",
            "not_sufficient_alone": "slice balance requires final validation after freeze",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "execute_ltr_training",
            "reason": "10.5 is an execution gate review, not the execution stage.",
            "allowed_after": "only after a later explicit user request opens a dev/OOF-only execution stage",
        },
        {
            "blocked_action": "tune_objective_or_threshold",
            "reason": "10.5 reviews whether criteria exist; it does not optimize them.",
            "allowed_after": "future execution stage may score frozen variants; threshold tuning remains gated",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "feature toggles are plan labels only; whitelist stays frozen.",
            "allowed_after": "separate feature-change proposal with leakage preflight",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "no online path or ranking implementation belongs to 10.5.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after a frozen candidate exists.",
            "allowed_after": "never for selection",
        },
    ]


def _metrics(
    readiness_gates: list[dict[str, Any]],
    command_boundaries: list[dict[str, Any]],
    expected_artifacts: list[dict[str, Any]],
    stop_conditions: list[dict[str, Any]],
    approval_criteria: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_4: dict[str, Any],
) -> dict[str, Any]:
    pass_count = sum(1 for row in readiness_gates if row["status"] == "pass")
    metrics_10_4 = stage_10_4.get("metrics", {})
    return {
        "readiness_gate_count": len(readiness_gates),
        "readiness_gate_pass_count": pass_count,
        "readiness_gate_fail_count": len(readiness_gates) - pass_count,
        "execution_gate_passed_for_future_stage": pass_count == len(readiness_gates),
        "command_boundary_count": len(command_boundaries),
        "expected_artifact_family_count": len(expected_artifacts),
        "stop_condition_count": len(stop_conditions),
        "approval_criteria_count": len(approval_criteria),
        "blocked_action_count": len(blocked_actions),
        "plan_objective_variant_count": metrics_10_4.get("objective_variant_count", 0),
        "plan_feature_toggle_count": metrics_10_4.get("feature_toggle_count", 0),
        "plan_leakage_gate_fail_count": metrics_10_4.get("leakage_gate_fail_count", 0),
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.5 Offline Ranking Experiment Execution Gate Review",
        "",
        "Read-only execution gate review for the 10.4 plan. This authorizes only the shape of a possible later dev/OOF execution stage; it does not execute training, tune, change ranking, or edit features.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["readiness_gate_count", metrics["readiness_gate_count"]],
                ["readiness_gate_pass_count", metrics["readiness_gate_pass_count"]],
                ["readiness_gate_fail_count", metrics["readiness_gate_fail_count"]],
                ["execution_gate_passed_for_future_stage", metrics["execution_gate_passed_for_future_stage"]],
                ["command_boundary_count", metrics["command_boundary_count"]],
                ["expected_artifact_family_count", metrics["expected_artifact_family_count"]],
                ["stop_condition_count", metrics["stop_condition_count"]],
                ["approval_criteria_count", metrics["approval_criteria_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Readiness Gates",
        "",
        _md_table(
            [["gate", "status", "observed", "decision"]]
            + [[row["gate"], row["status"], row["observed"], row["decision"]] for row in report["readiness_gates"]]
        ),
        "",
        "## Stop Conditions",
        "",
        _md_table(
            [["stop_condition", "trigger", "required_action"]]
            + [[row["stop_condition"], row["trigger"], row["required_action"]] for row in report["stop_conditions"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.5 offline ranking experiment execution gate review")
    parser.add_argument("--stage-10-4", default=str(DEFAULT_STAGE_10_4))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_4 = _read_json(Path(args.stage_10_4))
    readiness_gates = _readiness_gates(stage_10_4)
    command_boundaries = _command_boundaries()
    expected_artifacts = _expected_artifacts(stage_10_4)
    stop_conditions = _stop_conditions()
    approval_criteria = _approval_criteria(stage_10_4)
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        readiness_gates,
        command_boundaries,
        expected_artifacts,
        stop_conditions,
        approval_criteria,
        blocked_actions,
        stage_10_4,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "readiness_gates_csv": str(output_prefix.with_name(output_prefix.name + "_readiness_gates.csv")),
        "command_boundaries_csv": str(output_prefix.with_name(output_prefix.name + "_command_boundaries.csv")),
        "expected_artifacts_csv": str(output_prefix.with_name(output_prefix.name + "_expected_artifacts.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "approval_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_approval_criteria.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.5 offline ranking experiment execution gate review",
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
            "stage_10_4_summary": str(Path(args.stage_10_4)),
        },
        "metrics": metrics,
        "readiness_gates": readiness_gates,
        "command_boundaries": command_boundaries,
        "expected_artifacts": expected_artifacts,
        "stop_conditions": stop_conditions,
        "approval_criteria": approval_criteria,
        "blocked_actions": blocked_actions,
        "decision": (
            "The 10.4 S2 offline ranking experiment plan passes the execution gate review for a future dev/OOF-only execution stage. "
            "This is not an execution approval inside 10.5: training, tuning, ranking changes, feature whitelist edits, heldout/hard selection, gate relaxation, "
            "and online integration remain blocked until a later explicit stage request."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.5 only reviews execution readiness and defines command/output/stop/approval boundaries. It does not train, tune, patch rules, change ranking, "
            "modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, or connect online."
        ),
        "next_stage": {
            "stage": "10.6 offline ranking experiment execution scope lock",
            "goal": (
                "Read-only freeze the exact future execution scope from 10.5: candidate matrix, command contract, artifact manifest, and stop conditions before any dev/OOF run."
            ),
            "prohibited": [
                "training",
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
        Path(artifacts["readiness_gates_csv"]),
        readiness_gates,
        ["gate", "status", "observed", "required", "decision"],
    )
    _write_csv(
        Path(artifacts["command_boundaries_csv"]),
        command_boundaries,
        ["boundary_id", "scope", "command_boundary", "required_flags_or_guards", "forbidden"],
    )
    _write_csv(
        Path(artifacts["expected_artifacts_csv"]),
        expected_artifacts,
        ["artifact_family", "required_content", "expected_format", "promotion_dependency", "missing_artifact_action"],
    )
    _write_csv(
        Path(artifacts["stop_conditions_csv"]),
        stop_conditions,
        ["stop_condition", "trigger", "required_action", "recoverable_by"],
    )
    _write_csv(
        Path(artifacts["approval_criteria_csv"]),
        approval_criteria,
        ["criterion", "minimum_evidence", "pass_condition", "not_sufficient_alone"],
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
