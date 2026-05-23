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
DEFAULT_STAGE_10_5 = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_gate_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_scope_lock"


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


def _candidate_matrix(stage_10_4: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for objective in stage_10_4.get("objective_variants", []):
        for feature in stage_10_4.get("feature_toggles", []):
            candidate_id = f"{objective['variant_id']}__{feature['toggle_id']}"
            is_comparator = (
                objective["variant_id"] == "OBJ_A_current_lambda_rank_baseline"
                and feature["toggle_id"] == "FT_ALL_CURRENT_WHITELIST"
            )
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "objective_variant": objective["variant_id"],
                    "feature_toggle": feature["toggle_id"],
                    "objective_family": objective["objective_family"],
                    "feature_family": feature["feature_family"],
                    "role": "frozen_comparator" if is_comparator else "future_candidate",
                    "selection_source": "dev_oof_only",
                    "heldout_hard_use": "validation_only_after_freeze",
                    "requires_future_training_stage": objective["requires_training_later"],
                    "scope_status": "locked_for_future_execution",
                }
            )
    return rows


def _command_contract(stage_10_5: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {
            "contract_id": "CC_10_6_ALLOWED",
            "scope": "current_stage",
            "contract": "10.6 may only lock scope and emit manifest files.",
            "required_guard": "read_only=true; training_allowed=false; implementation_allowed=false",
            "forbidden": "no experiment execution, no model fit, no ranking output mutation",
        },
        {
            "contract_id": "CC_FUTURE_ENTRY",
            "scope": "future_stage_only",
            "contract": "A later execution stage must consume this scope-lock summary and the frozen 10.4 plan.",
            "required_guard": "--scope-lock-summary; --frozen-plan; --dev-oof-only; --no-heldout-selection",
            "forbidden": "no ad hoc candidate expansion outside locked matrix",
        },
    ]
    for boundary in stage_10_5.get("command_boundaries", []):
        rows.append(
            {
                "contract_id": f"CC_{boundary['boundary_id']}",
                "scope": boundary["scope"],
                "contract": boundary["command_boundary"],
                "required_guard": boundary["required_flags_or_guards"],
                "forbidden": boundary["forbidden"],
            }
        )
    return rows


def _artifact_manifest(stage_10_5: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for artifact in stage_10_5.get("expected_artifacts", []):
        artifact_family = artifact["artifact_family"]
        rows.append(
            {
                "artifact_family": artifact_family,
                "locked_future_path_pattern": f"reports/agent_state/goal_10x_offline_ranking_experiment_dev_oof_{artifact_family}.*",
                "required_content": artifact["required_content"],
                "expected_format": artifact["expected_format"],
                "missing_artifact_action": artifact["missing_artifact_action"],
                "scope_status": "required_for_future_execution",
            }
        )
    return rows


def _scope_lock_decisions(stage_10_4: dict[str, Any], stage_10_5: dict[str, Any]) -> list[dict[str, Any]]:
    metrics_10_4 = stage_10_4.get("metrics", {})
    metrics_10_5 = stage_10_5.get("metrics", {})
    return [
        {
            "decision_area": "candidate_matrix",
            "lock_decision": "locked",
            "evidence": f"objective_variants={metrics_10_4.get('objective_variant_count')}; feature_toggles={metrics_10_4.get('feature_toggle_count')}",
            "allowed_next": "future execution must use locked candidate matrix",
            "not_allowed": "no candidate expansion in execution stage",
        },
        {
            "decision_area": "command_contract",
            "lock_decision": "locked",
            "evidence": f"command_boundaries={metrics_10_5.get('command_boundary_count')}; execution_gate_passed={metrics_10_5.get('execution_gate_passed_for_future_stage')}",
            "allowed_next": "future command must be dev/OOF-only and consume frozen scope",
            "not_allowed": "no heldout/hard selection or online integration",
        },
        {
            "decision_area": "artifact_manifest",
            "lock_decision": "locked",
            "evidence": f"expected_artifact_family_count={metrics_10_5.get('expected_artifact_family_count')}",
            "allowed_next": "future execution must emit all manifest families",
            "not_allowed": "no scorecard-only success claim",
        },
        {
            "decision_area": "stop_conditions",
            "lock_decision": "locked",
            "evidence": f"stop_condition_count={metrics_10_5.get('stop_condition_count')}",
            "allowed_next": "future execution must stop on leakage, selection contamination, missing outputs, loss budget failure, fallback break, or artifact dominance",
            "not_allowed": "no override of stop conditions inside failed run",
        },
        {
            "decision_area": "approval_criteria",
            "lock_decision": "locked",
            "evidence": f"approval_criteria_count={metrics_10_5.get('approval_criteria_count')}",
            "allowed_next": "future execution may only be evaluated against locked criteria",
            "not_allowed": "no post-hoc approval criteria after seeing results",
        },
        {
            "decision_area": "implementation_boundary",
            "lock_decision": "locked_closed",
            "evidence": "training_allowed=false; implementation_allowed=false; heldout_used_for_selection=false",
            "allowed_next": "read-only authorization review only unless user explicitly requests execution later",
            "not_allowed": "no training, tuning, ranking change, GoalSearcher change, feature whitelist edit, gate relaxation, or online integration",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_dev_oof_experiment",
            "reason": "10.6 locks scope only; it does not execute the locked scope.",
            "allowed_after": "later explicit user request for a dev/OOF-only execution stage",
        },
        {
            "blocked_action": "train_ltr_model",
            "reason": "scope lock is not model fitting.",
            "allowed_after": "later execution stage with frozen scope and complete output contract",
        },
        {
            "blocked_action": "add_or_remove_features",
            "reason": "candidate matrix references current feature toggles only; whitelist stays frozen.",
            "allowed_after": "separate feature proposal plus leakage preflight",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "no online ranking implementation belongs to scope lock.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after a frozen candidate exists.",
            "allowed_after": "never for selection",
        },
    ]


def _metrics(
    candidate_matrix: list[dict[str, Any]],
    command_contract: list[dict[str, Any]],
    artifact_manifest: list[dict[str, Any]],
    stop_conditions: list[dict[str, Any]],
    approval_criteria: list[dict[str, Any]],
    scope_lock_decisions: list[dict[str, Any]],
    stage_10_4: dict[str, Any],
    stage_10_5: dict[str, Any],
) -> dict[str, Any]:
    future_candidates = [row for row in candidate_matrix if row["role"] == "future_candidate"]
    comparators = [row for row in candidate_matrix if row["role"] == "frozen_comparator"]
    return {
        "candidate_matrix_row_count": len(candidate_matrix),
        "future_candidate_count": len(future_candidates),
        "frozen_comparator_count": len(comparators),
        "objective_variant_count": stage_10_4.get("metrics", {}).get("objective_variant_count", 0),
        "feature_toggle_count": stage_10_4.get("metrics", {}).get("feature_toggle_count", 0),
        "command_contract_count": len(command_contract),
        "artifact_manifest_count": len(artifact_manifest),
        "stop_condition_count": len(stop_conditions),
        "approval_criteria_count": len(approval_criteria),
        "scope_lock_decision_count": len(scope_lock_decisions),
        "execution_gate_passed_for_future_stage": stage_10_5.get("metrics", {}).get("execution_gate_passed_for_future_stage") is True,
        "scope_locked": True,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.6 Offline Ranking Experiment Execution Scope Lock",
        "",
        "Read-only scope lock for a possible later dev/OOF-only execution stage. It freezes the candidate matrix, command contract, artifact manifest, stop conditions, and approval criteria. It does not run an experiment.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_matrix_row_count", metrics["candidate_matrix_row_count"]],
                ["future_candidate_count", metrics["future_candidate_count"]],
                ["frozen_comparator_count", metrics["frozen_comparator_count"]],
                ["command_contract_count", metrics["command_contract_count"]],
                ["artifact_manifest_count", metrics["artifact_manifest_count"]],
                ["stop_condition_count", metrics["stop_condition_count"]],
                ["approval_criteria_count", metrics["approval_criteria_count"]],
                ["scope_locked", metrics["scope_locked"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Scope Decisions",
        "",
        _md_table(
            [["decision_area", "lock_decision", "evidence", "not_allowed"]]
            + [
                [row["decision_area"], row["lock_decision"], row["evidence"], row["not_allowed"]]
                for row in report["scope_lock_decisions"]
            ]
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
    parser = argparse.ArgumentParser(description="Stage 10.6 offline ranking experiment execution scope lock")
    parser.add_argument("--stage-10-4", default=str(DEFAULT_STAGE_10_4))
    parser.add_argument("--stage-10-5", default=str(DEFAULT_STAGE_10_5))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_4 = _read_json(Path(args.stage_10_4))
    stage_10_5 = _read_json(Path(args.stage_10_5))
    candidate_matrix = _candidate_matrix(stage_10_4)
    command_contract = _command_contract(stage_10_5)
    artifact_manifest = _artifact_manifest(stage_10_5)
    stop_conditions = stage_10_5.get("stop_conditions", [])
    approval_criteria = stage_10_5.get("approval_criteria", [])
    scope_lock_decisions = _scope_lock_decisions(stage_10_4, stage_10_5)
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        candidate_matrix,
        command_contract,
        artifact_manifest,
        stop_conditions,
        approval_criteria,
        scope_lock_decisions,
        stage_10_4,
        stage_10_5,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_matrix_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_matrix.csv")),
        "command_contract_csv": str(output_prefix.with_name(output_prefix.name + "_command_contract.csv")),
        "artifact_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_manifest.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions_locked.csv")),
        "approval_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_approval_criteria_locked.csv")),
        "scope_lock_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_scope_lock_decisions.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.6 offline ranking experiment execution scope lock",
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
            "stage_10_5_summary": str(Path(args.stage_10_5)),
        },
        "metrics": metrics,
        "candidate_matrix": candidate_matrix,
        "command_contract": command_contract,
        "artifact_manifest": artifact_manifest,
        "stop_conditions_locked": stop_conditions,
        "approval_criteria_locked": approval_criteria,
        "scope_lock_decisions": scope_lock_decisions,
        "blocked_actions": blocked_actions,
        "decision": (
            "Lock the future S2 offline ranking experiment execution scope for a later dev/OOF-only run. The locked scope contains the 4x8 candidate matrix, "
            "command contract, required artifact manifest, stop conditions, and approval criteria. This does not execute the run, train a model, tune thresholds, "
            "change ranking, edit the feature whitelist, use heldout/hard for selection, relax gates, or connect online."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.6 only locks future execution scope. It does not train, tune, patch rules, change ranking, modify GoalSearcher, edit the feature whitelist, "
            "use heldout/hard for selection, relax gates, connect online, or run a dev/OOF experiment."
        ),
        "next_stage": {
            "stage": "10.7 offline ranking experiment dev/OOF execution authorization review",
            "goal": (
                "Read-only decide whether to ask for or authorize the first dev/OOF-only execution from the locked 10.6 scope; still no execution unless explicitly requested."
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
        Path(artifacts["candidate_matrix_csv"]),
        candidate_matrix,
        [
            "candidate_id",
            "objective_variant",
            "feature_toggle",
            "objective_family",
            "feature_family",
            "role",
            "selection_source",
            "heldout_hard_use",
            "requires_future_training_stage",
            "scope_status",
        ],
    )
    _write_csv(
        Path(artifacts["command_contract_csv"]),
        command_contract,
        ["contract_id", "scope", "contract", "required_guard", "forbidden"],
    )
    _write_csv(
        Path(artifacts["artifact_manifest_csv"]),
        artifact_manifest,
        [
            "artifact_family",
            "locked_future_path_pattern",
            "required_content",
            "expected_format",
            "missing_artifact_action",
            "scope_status",
        ],
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
    _write_csv(
        Path(artifacts["scope_lock_decisions_csv"]),
        scope_lock_decisions,
        ["decision_area", "lock_decision", "evidence", "allowed_next", "not_allowed"],
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
