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
DEFAULT_ROBUSTNESS_SUMMARY = AGENT_STATE / "goal_10x_s2_independent_source_robustness_gate_summary.json"
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_10x_s2_candidate_freeze_validation_gate_summary.json"
DEFAULT_EXECUTION_SUMMARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_execution_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_source_dominated_candidate_hold_strategy_return_gate"


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


def _hold_decisions(robustness_summary: dict[str, Any], freeze_summary: dict[str, Any]) -> list[dict[str, Any]]:
    robustness_metrics = robustness_summary.get("metrics", {})
    freeze_metrics = freeze_summary.get("metrics", {})
    return [
        {
            "decision_id": "keep_s2_candidate_held",
            "status": "selected",
            "evidence": (
                f"robustness_decision={robustness_metrics.get('robustness_decision')}; "
                f"freeze_decision={freeze_metrics.get('freeze_decision')}"
            ),
            "decision": "KEEP_S2_HELD_DO_NOT_FREEZE",
            "not_allowed": "do not treat the diagnostic lead as a frozen validation candidate",
        },
        {
            "decision_id": "block_validation",
            "status": "selected",
            "evidence": (
                f"validation_allowed_now={robustness_metrics.get('validation_allowed_now')}; "
                f"non_generated_positive_net={robustness_metrics.get('non_generated_positive_net')}"
            ),
            "decision": "NO_HELDOUT_HARD_VALIDATION",
            "not_allowed": "do not run heldout/hard validation from source-dominated dev/OOF evidence",
        },
        {
            "decision_id": "require_independent_evidence",
            "status": "selected",
            "evidence": (
                f"generated_positive_net_share={robustness_metrics.get('generated_positive_net_share')}; "
                f"non_generated_positive_source_count={robustness_metrics.get('non_generated_positive_source_count')}"
            ),
            "decision": "REQUEST_INDEPENDENT_NON_GENERATED_EVIDENCE_BEFORE_RESUME",
            "not_allowed": "do not claim general Top1 gain from generated-source dominance",
        },
        {
            "decision_id": "return_to_strategy",
            "status": "selected_default",
            "evidence": "S2 diagnostic lead is held; continuing validation planning adds no clean accuracy evidence.",
            "decision": "RETURN_TO_BROADER_10X_STRATEGY_REVIEW",
            "not_allowed": "do not expand candidates or retrain inside this hold gate",
        },
    ]


def _strategy_return_options() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "KEEP_S2_HELD_AND_RETURN_TO_STRATEGY_REVIEW",
            "status": "selected_default",
            "description": "Keep the S2 diagnostic lead parked and return to broader 10.x strategy review.",
            "why": "The positive dev/OOF net is generated-source dominated and has no independent non-generated support.",
            "next_boundary": "read-only strategy review unless a separate evidence-collection or execution stage is explicitly opened",
        },
        {
            "option_id": "REQUEST_INDEPENDENT_NON_GENERATED_EVIDENCE",
            "status": "available_as_backlog",
            "description": "Define a future evidence collection lane for non-generated, cross-source dev/OOF support.",
            "why": "The source gate can only be reopened after positive support exists outside global_repair_decision_table.csv.",
            "next_boundary": "evidence-definition or data-quality lane; no validation until evidence passes source robustness",
        },
        {
            "option_id": "CONTINUE_S2_VALIDATION_ANYWAY",
            "status": "rejected",
            "description": "Proceed to heldout/hard validation despite source dominance.",
            "why": "This would convert a source artifact into a generalization claim.",
            "next_boundary": "blocked",
        },
    ]


def _evidence_requirements(robustness_summary: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = robustness_summary.get("metrics", {})
    return [
        {
            "requirement_id": "non_generated_positive_net",
            "current_value": metrics.get("non_generated_positive_net"),
            "required_before_resume": ">0 and directionally consistent",
            "purpose": "Show that the S2 candidate helps beyond the generated decision table.",
            "current_status": "missing",
        },
        {
            "requirement_id": "non_generated_positive_source_count",
            "current_value": metrics.get("non_generated_positive_source_count"),
            "required_before_resume": ">=2 independent non-generated sources",
            "purpose": "Avoid a single-source artifact before any general validation claim.",
            "current_status": "missing",
        },
        {
            "requirement_id": "generated_positive_net_share",
            "current_value": metrics.get("generated_positive_net_share"),
            "required_before_resume": "<=0.5 or explicitly justified by independent evidence",
            "purpose": "Prevent generated-source dominance from driving the freeze decision.",
            "current_status": "failed",
        },
        {
            "requirement_id": "loss_budget_still_passes",
            "current_value": "hit1_loss=14 from freeze gate",
            "required_before_resume": "candidate remains within locked loss budget on eligible dev/OOF evidence",
            "purpose": "Ensure new independent support does not hide loss expansion.",
            "current_status": "carry_forward_required",
        },
        {
            "requirement_id": "leakage_and_fallback_contract_still_clean",
            "current_value": "passed in completed dev/OOF execution and freeze gate",
            "required_before_resume": "no forbidden identifiers, no gate relaxation, no online fallback change",
            "purpose": "Keep any future resume inside the locked offline boundary.",
            "current_status": "carry_forward_required",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_heldout_or_hard_validation",
            "reason": "S2 source robustness failed; validation would be premature and source-dominated.",
            "allowed_after": "future independent-source robustness pass plus explicit validation stage",
        },
        {
            "blocked_action": "freeze_candidate_for_general_validation",
            "reason": "The diagnostic lead has no positive non-generated source support.",
            "allowed_after": "independent non-generated positive support exists and source gate passes",
        },
        {
            "blocked_action": "retrain_or_expand_candidate_matrix",
            "reason": "This gate only decides hold/return status over completed S2 artifacts.",
            "allowed_after": "separate explicitly authorized dev/OOF execution scope",
        },
        {
            "blocked_action": "change_ranking_goal_searcher_or_feature_whitelist",
            "reason": "The held candidate is not a validated implementation candidate.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "All positive net comes from global_repair_decision_table.csv.",
            "allowed_after": "future cross-source, non-generated evidence review",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "Heldout/hard remain validation-only and are not opened by this gate.",
            "allowed_after": "never for selection",
        },
    ]


def _metrics(
    hold_decisions: list[dict[str, Any]],
    strategy_return_options: list[dict[str, Any]],
    evidence_requirements: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    robustness_summary: dict[str, Any],
    freeze_summary: dict[str, Any],
    execution_summary: dict[str, Any],
) -> dict[str, Any]:
    robustness_metrics = robustness_summary.get("metrics", {})
    freeze_metrics = freeze_summary.get("metrics", {})
    execution_metrics = execution_summary.get("metrics", {})
    missing_requirements = sum(1 for row in evidence_requirements if row["current_status"] in {"missing", "failed"})
    return {
        "candidate_id": robustness_metrics.get("candidate_id") or freeze_metrics.get("selected_candidate_id"),
        "selected_path": "KEEP_S2_HELD_AND_RETURN_TO_STRATEGY_REVIEW",
        "hold_decision_count": len(hold_decisions),
        "selected_hold_decision_count": sum(1 for row in hold_decisions if row["status"].startswith("selected")),
        "strategy_return_option_count": len(strategy_return_options),
        "evidence_requirement_count": len(evidence_requirements),
        "missing_or_failed_evidence_requirement_count": missing_requirements,
        "blocked_action_count": len(blocked_actions),
        "source_robustness_decision": robustness_metrics.get("robustness_decision"),
        "freeze_decision": freeze_metrics.get("freeze_decision"),
        "generated_positive_net": robustness_metrics.get("generated_positive_net"),
        "generated_positive_net_share": robustness_metrics.get("generated_positive_net_share"),
        "non_generated_positive_net": robustness_metrics.get("non_generated_positive_net"),
        "non_generated_positive_source_count": robustness_metrics.get("non_generated_positive_source_count"),
        "validation_allowed_now": False,
        "s2_candidate_held": True,
        "s2_candidate_frozen_for_general_validation": False,
        "strategy_return_selected": True,
        "execution_candidate_count": execution_metrics.get("candidate_count"),
        "approval_candidate_count": execution_metrics.get("approval_candidate_count"),
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "training_allowed": False,
        "implementation_allowed": False,
        "ranking_change_allowed": False,
        "goal_searcher_change_allowed": False,
        "feature_whitelist_edit_allowed": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# S2 Source-dominated Candidate Hold And Strategy-return Gate",
        "",
        "Read-only hold gate after the independent-source robustness failure. The S2 diagnostic lead remains held and does not proceed to heldout/hard validation.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_id", metrics["candidate_id"]],
                ["selected_path", metrics["selected_path"]],
                ["source_robustness_decision", metrics["source_robustness_decision"]],
                ["generated_positive_net_share", metrics["generated_positive_net_share"]],
                ["non_generated_positive_net", metrics["non_generated_positive_net"]],
                ["non_generated_positive_source_count", metrics["non_generated_positive_source_count"]],
                ["validation_allowed_now", metrics["validation_allowed_now"]],
            ]
        ),
        "",
        "## Hold Decisions",
        "",
        _md_table(
            [["decision_id", "status", "decision", "not_allowed"]]
            + [[row["decision_id"], row["status"], row["decision"], row["not_allowed"]] for row in report["hold_decisions"]]
        ),
        "",
        "## Evidence Requirements",
        "",
        _md_table(
            [["requirement_id", "current_value", "required_before_resume", "current_status"]]
            + [
                [row["requirement_id"], row["current_value"], row["required_before_resume"], row["current_status"]]
                for row in report["evidence_requirements"]
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
    parser = argparse.ArgumentParser(description="S2 source-dominated candidate hold and strategy-return gate")
    parser.add_argument("--robustness-summary", default=str(DEFAULT_ROBUSTNESS_SUMMARY))
    parser.add_argument("--freeze-summary", default=str(DEFAULT_FREEZE_SUMMARY))
    parser.add_argument("--execution-summary", default=str(DEFAULT_EXECUTION_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    robustness_summary = _read_json(Path(args.robustness_summary))
    freeze_summary = _read_json(Path(args.freeze_summary))
    execution_summary = _read_json(Path(args.execution_summary))

    hold_decisions = _hold_decisions(robustness_summary, freeze_summary)
    strategy_return_options = _strategy_return_options()
    evidence_requirements = _evidence_requirements(robustness_summary)
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        hold_decisions,
        strategy_return_options,
        evidence_requirements,
        blocked_actions,
        robustness_summary,
        freeze_summary,
        execution_summary,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "hold_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_hold_decisions.csv")),
        "strategy_return_options_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_return_options.csv")),
        "evidence_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_requirements.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / S2 source-dominated candidate hold and strategy-return gate",
        "read_only": True,
        "dev_oof_only_review": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "no_heldout_hard_validation": True,
        "no_retraining": True,
        "no_candidate_expansion": True,
        "no_threshold_change": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_goal_searcher_change": True,
        "no_feature_whitelist_edit": True,
        "source_artifacts": {
            "robustness_summary": str(Path(args.robustness_summary)),
            "freeze_summary": str(Path(args.freeze_summary)),
            "execution_summary": str(Path(args.execution_summary)),
        },
        "metrics": metrics,
        "hold_decisions": hold_decisions,
        "strategy_return_options": strategy_return_options,
        "evidence_requirements": evidence_requirements,
        "blocked_actions": blocked_actions,
        "decision": (
            "Select KEEP_S2_HELD_AND_RETURN_TO_STRATEGY_REVIEW. The diagnostic lead remains useful as a dev/OOF artifact, "
            "but it is not a frozen validation candidate because generated_positive_net_share=1.0 and non_generated_positive_net=0. "
            "Before any resume toward validation, require independent non-generated positive support and a renewed source robustness pass."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "This stage only reads completed S2 dev/OOF summaries and records a hold/strategy-return decision. It does not train, tune, expand candidates, "
            "run heldout/hard validation or selection, change thresholds, patch rules, modify ranking or GoalSearcher, edit the feature whitelist, relax gates, "
            "claim general Top1 gain, or connect online."
        ),
        "next_stage": {
            "stage": "broader 10.x strategy return after S2 source-dominated hold",
            "goal": "Read-only decide the next non-execution strategy lane now that S2 remains held for lack of independent non-generated evidence.",
            "prohibited": [
                "training",
                "candidate expansion",
                "heldout/hard validation",
                "heldout/hard selection",
                "threshold changes",
                "rule patches",
                "ranking implementation",
                "GoalSearcher changes",
                "feature whitelist edits",
                "online integration",
                "claiming general Top1 gain from S2",
            ],
        },
    }

    _write_csv(
        Path(artifacts["hold_decisions_csv"]),
        hold_decisions,
        ["decision_id", "status", "evidence", "decision", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["strategy_return_options_csv"]),
        strategy_return_options,
        ["option_id", "status", "description", "why", "next_boundary"],
    )
    _write_csv(
        Path(artifacts["evidence_requirements_csv"]),
        evidence_requirements,
        ["requirement_id", "current_value", "required_before_resume", "purpose", "current_status"],
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
