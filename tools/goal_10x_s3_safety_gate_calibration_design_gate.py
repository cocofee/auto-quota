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
DEFAULT_STAGE_10_12 = AGENT_STATE / "goal_10x_s3_safety_gate_policy_loss_budget_review_summary.json"
DEFAULT_STAGE_10_11 = AGENT_STATE / "goal_10x_broader_strategy_reentry_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_safety_gate_calibration_design_gate"


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


def _gate_decisions(stage_10_12: dict[str, Any], stage_10_11: dict[str, Any]) -> list[dict[str, Any]]:
    metrics_10_12 = stage_10_12.get("metrics", {})
    metrics_10_11 = stage_10_11.get("metrics", {})
    return [
        {
            "gate": "s3_lane_selected",
            "status": "pass" if metrics_10_11.get("selected_next_strategy_id") == "S3_safety_gate_calibration_v2_plan" else "fail",
            "observed": f"selected_next_strategy_id={metrics_10_11.get('selected_next_strategy_id')}",
            "required": "10.11 selected S3 as the next non-execution lane",
            "decision": "allow_s3_design_gate",
        },
        {
            "gate": "policy_contract_complete",
            "status": "pass" if metrics_10_12.get("policy_contract_count", 0) >= 5 else "fail",
            "observed": f"policy_contract_count={metrics_10_12.get('policy_contract_count')}",
            "required": "scope, frozen comparator, raw upside, compatibility reference, and relation scope",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "oof_requirements_complete",
            "status": "pass" if metrics_10_12.get("oof_requirement_count", 0) >= 5 else "fail",
            "observed": f"oof_requirement_count={metrics_10_12.get('oof_requirement_count')}",
            "required": "OOF-only selection, gain/loss balance, residual diagnosis, relation audit, source/taxonomy visibility",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "loss_budget_complete",
            "status": "pass" if metrics_10_12.get("loss_budget_item_count", 0) >= 5 else "fail",
            "observed": (
                f"loss_budget_item_count={metrics_10_12.get('loss_budget_item_count')}; "
                f"selected_gate_hit1_loss={metrics_10_12.get('selected_gate_hit1_loss')}; "
                f"compat_oof_new_residual_loss={metrics_10_12.get('compat_oof_new_residual_loss')}"
            ),
            "required": "new loss, rescue gain, saved loss, neutral override, and comparator net budgets",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "residual_slices_complete",
            "status": "pass" if metrics_10_12.get("residual_slice_count", 0) >= 6 else "fail",
            "observed": f"residual_slice_count={metrics_10_12.get('residual_slice_count')}",
            "required": "outcome, diagnosis, relation, source/province, taxonomy, rank/book/margin slices",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "freeze_validation_boundaries_complete",
            "status": "pass" if metrics_10_12.get("freeze_validation_boundary_count", 0) >= 5 else "fail",
            "observed": f"freeze_validation_boundary_count={metrics_10_12.get('freeze_validation_boundary_count')}",
            "required": "freeze-before-validation, default-off, no-threshold-change, fallback, and data-quality boundaries",
            "decision": "sufficient_for_plan_definition",
        },
        {
            "gate": "implementation_boundary_closed",
            "status": (
                "pass"
                if metrics_10_12.get("threshold_change_allowed") is False
                and metrics_10_12.get("implementation_allowed") is False
                and metrics_10_12.get("training_allowed") is False
                and metrics_10_12.get("heldout_used_for_selection") is False
                else "fail"
            ),
            "observed": (
                f"threshold_change_allowed={metrics_10_12.get('threshold_change_allowed')}; "
                f"implementation_allowed={metrics_10_12.get('implementation_allowed')}; "
                f"training_allowed={metrics_10_12.get('training_allowed')}; "
                f"heldout_used_for_selection={metrics_10_12.get('heldout_used_for_selection')}"
            ),
            "required": "10.13 remains read-only and cannot tune, implement, or select on heldout/hard",
            "decision": "do_not_execute_or_implement",
        },
    ]


def _plan_definition_requirements(stage_10_12: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_12.get("metrics", {})
    return [
        {
            "requirement_id": "PLAN_CANDIDATE_POLICY_MATRIX",
            "required_content": "Future plan must define candidate policy variants without changing thresholds in the gate stage.",
            "must_include": "current selected gate comparator; relation-level freeze/narrow candidates; logging-only comparator; baseline fallback comparator",
            "source_from_10_12": f"selected_gate_variant={metrics.get('selected_gate_variant')}",
            "not_allowed": "no ad hoc threshold/mode search in the plan gate",
        },
        {
            "requirement_id": "PLAN_OOF_ONLY_SPLIT_POLICY",
            "required_content": "Future plan must make dev_oof the only selection split and keep heldout/hard validation-only.",
            "must_include": "explicit no-heldout-selection guard and invalidation rule",
            "source_from_10_12": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "not_allowed": "no heldout/hard threshold, relation, or candidate selection",
        },
        {
            "requirement_id": "PLAN_LOSS_BUDGET_GATES",
            "required_content": "Future plan must carry forward all loss budgets as hard evaluation gates.",
            "must_include": "new residual loss ceiling, rescue gain floor, saved-loss retention, neutral override visibility, net-vs-comparator reporting",
            "source_from_10_12": f"loss_budget_item_count={metrics.get('loss_budget_item_count')}",
            "not_allowed": "no score-only success claim",
        },
        {
            "requirement_id": "PLAN_RESIDUAL_OUTPUTS",
            "required_content": "Future plan must require residual output tables for every slice named in 10.12.",
            "must_include": "outcome, diagnosis, relation, source/province, taxonomy, rank/book/margin outputs",
            "source_from_10_12": f"residual_slice_count={metrics.get('residual_slice_count')}",
            "not_allowed": "no cherry-picked relation-only scorecard",
        },
        {
            "requirement_id": "PLAN_FREEZE_AND_DEFAULT_OFF_CONTRACT",
            "required_content": "Future plan must freeze policy before validation and preserve default-off switch semantics.",
            "must_include": "freeze-before-validation, default-off, fallback-retention, data-quality-separation boundaries",
            "source_from_10_12": f"freeze_validation_boundary_count={metrics.get('freeze_validation_boundary_count')}",
            "not_allowed": "no online integration, no GoalSearcher change, no switch enablement",
        },
        {
            "requirement_id": "PLAN_STOP_CONDITIONS",
            "required_content": "Future plan must define stop conditions before any what-if execution is opened.",
            "must_include": "selection contamination, missing outputs, new-loss over budget, fallback bypass, source/taxonomy artifact, single-relation dominance",
            "source_from_10_12": "blocked_actions and loss budgets are explicit",
            "not_allowed": "no overriding failed gates after seeing results",
        },
    ]


def _carry_forward_boundaries(stage_10_12: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stage_10_12.get("freeze_validation_boundaries", []):
        rows.append(
            {
                "boundary_id": row.get("boundary_id"),
                "source": "10.12 freeze_validation_boundaries",
                "carry_forward_action": row.get("policy"),
                "not_allowed": row.get("not_allowed"),
            }
        )
    for row in stage_10_12.get("blocked_actions", []):
        rows.append(
            {
                "boundary_id": f"BLOCK_{row.get('blocked_action')}",
                "source": "10.12 blocked_actions",
                "carry_forward_action": row.get("reason"),
                "not_allowed": row.get("allowed_after"),
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_offline_whatif",
            "reason": "10.13 is a design gate only; it may only decide whether a plan definition stage is allowed.",
            "allowed_after": "future explicit what-if execution stage after plan definition and approval",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "threshold/mode changes remain outside this design gate.",
            "allowed_after": "separate future OOF-only experiment and explicit implementation review, if ever reached",
        },
        {
            "blocked_action": "enable_compatibility_switch_or_connect_online",
            "reason": "S3 remains offline/default-off and not connected to GoalSearcher.",
            "allowed_after": "post-freeze validation integration readiness review, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "S3 design gate does not train or tune models.",
            "allowed_after": "separate explicitly opened execution stage",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "feature and ranking implementation changes are outside S3 gate review.",
            "allowed_after": "separate feature/ranking proposal and leakage review",
        },
    ]


def _metrics(
    gates: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    stage_10_12: dict[str, Any],
) -> dict[str, Any]:
    pass_count = sum(1 for row in gates if row["status"] == "pass")
    metrics_10_12 = stage_10_12.get("metrics", {})
    return {
        "design_gate_count": len(gates),
        "design_gate_pass_count": pass_count,
        "design_gate_fail_count": len(gates) - pass_count,
        "whatif_plan_requirement_count": len(requirements),
        "carry_forward_boundary_count": len(boundaries),
        "blocked_action_count": len(blocked),
        "s3_design_gate_passed": pass_count == len(gates),
        "selected_gate_variant": metrics_10_12.get("selected_gate_variant"),
        "selected_gate_hit1_net": metrics_10_12.get("selected_gate_hit1_net"),
        "selected_gate_hit1_loss": metrics_10_12.get("selected_gate_hit1_loss"),
        "compat_oof_rescued_blocked_gain": metrics_10_12.get("compat_oof_rescued_blocked_gain"),
        "compat_oof_new_residual_loss": metrics_10_12.get("compat_oof_new_residual_loss"),
        "allow_future_whatif_plan_definition": pass_count == len(gates),
        "whatif_execution_allowed": False,
        "threshold_change_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.13 S3 Safety Gate Calibration Design Gate",
        "",
        "Read-only design gate for S3. This decides whether the 10.12 policy/loss-budget package is concrete enough to define a future offline what-if plan. It does not run a what-if or change thresholds.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["design_gate_pass_count", metrics["design_gate_pass_count"]],
                ["design_gate_fail_count", metrics["design_gate_fail_count"]],
                ["s3_design_gate_passed", metrics["s3_design_gate_passed"]],
                ["allow_future_whatif_plan_definition", metrics["allow_future_whatif_plan_definition"]],
                ["whatif_execution_allowed", metrics["whatif_execution_allowed"]],
                ["threshold_change_allowed", metrics["threshold_change_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Gate Decisions",
        "",
        _md_table(
            [["gate", "status", "observed", "decision"]]
            + [[row["gate"], row["status"], row["observed"], row["decision"]] for row in report["gate_decisions"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.13 S3 safety gate calibration design gate")
    parser.add_argument("--stage-10-12", default=str(DEFAULT_STAGE_10_12))
    parser.add_argument("--stage-10-11", default=str(DEFAULT_STAGE_10_11))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_12 = _read_json(Path(args.stage_10_12))
    stage_10_11 = _read_json(Path(args.stage_10_11))
    gates = _gate_decisions(stage_10_12, stage_10_11)
    requirements = _plan_definition_requirements(stage_10_12)
    boundaries = _carry_forward_boundaries(stage_10_12)
    blocked = _blocked_actions()
    metrics = _metrics(gates, requirements, boundaries, blocked, stage_10_12)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_gate_decisions.csv")),
        "whatif_plan_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_whatif_plan_requirements.csv")),
        "carry_forward_boundaries_csv": str(output_prefix.with_name(output_prefix.name + "_carry_forward_boundaries.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 10.13 S3 safety gate calibration design gate",
        "read_only": True,
        "eval_only": True,
        "dev_oof_for_selection_only": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "stage_10_12_s3_policy_loss_budget": str(Path(args.stage_10_12)),
            "stage_10_11_reentry": str(Path(args.stage_10_11)),
        },
        "metrics": metrics,
        "gate_decisions": gates,
        "whatif_plan_requirements": requirements,
        "carry_forward_boundaries": boundaries,
        "blocked_actions": blocked,
        "decision": (
            "Pass the S3 safety gate calibration design gate for future plan definition only. The 10.12 policy contract, OOF evidence requirements, "
            "loss budgets, residual slices, and freeze/validation boundaries are concrete enough to define an offline what-if plan in the next read-only stage. "
            "This does not allow what-if execution, threshold changes, tuning, rule patches, ranking changes, GoalSearcher changes, or online integration."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.13 only gates readiness for a future offline what-if plan definition. It does not run a what-if, train, tune, change thresholds, "
            "patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, connect online, "
            "or convert taxonomy/source backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "10.14 S3 offline what-if plan definition",
            "goal": (
                "Read-only define the future S3 offline what-if plan: candidate policy matrix, OOF-only command contract, required artifacts, stop conditions, "
                "loss-budget gates, and approval criteria. Still no what-if execution, threshold change, tuning, rule patch, ranking change, or implementation."
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

    _write_csv(Path(artifacts["gate_decisions_csv"]), gates, ["gate", "status", "observed", "required", "decision"])
    _write_csv(
        Path(artifacts["whatif_plan_requirements_csv"]),
        requirements,
        ["requirement_id", "required_content", "must_include", "source_from_10_12", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["carry_forward_boundaries_csv"]),
        boundaries,
        ["boundary_id", "source", "carry_forward_action", "not_allowed"],
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
