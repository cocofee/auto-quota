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
DEFAULT_STAGE_10_9 = AGENT_STATE / "goal_10x_execution_held_strategy_checkpoint_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_execution_lane_parking_strategy_return_gate"


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


def _parking_gate_decisions(stage_10_9: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_9.get("metrics", {})
    return [
        {
            "gate": "s2_execution_lane_status",
            "status": "park",
            "evidence": f"selected_default={metrics.get('selected_default')}; execution_remains_held={metrics.get('execution_remains_held')}",
            "decision": "PARK_S2_EXECUTION_LANE",
            "not_allowed": "no dev/OOF execution from this stage",
        },
        {
            "gate": "explicit_go_absence",
            "status": "closed",
            "evidence": f"explicit_go_requested={metrics.get('explicit_go_requested')}; go_no_go_decision={metrics.get('go_no_go_decision')}",
            "decision": "NO_EXECUTION_WITHOUT_EXPLICIT_GO",
            "not_allowed": "no implicit execution from next-step wording",
        },
        {
            "gate": "strategy_return",
            "status": "open_read_only",
            "evidence": "S2 execution held; broader 10.x strategy still has deferred levers and target gap remains large",
            "decision": "RETURN_TO_BROADER_10X_STRATEGY_REVIEW",
            "not_allowed": "no automatic training, tuning, rule patch, or online integration",
        },
        {
            "gate": "heldout_boundary",
            "status": "closed_for_selection",
            "evidence": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "decision": "HELDOUT_HARD_REMAIN_VALIDATION_ONLY",
            "not_allowed": "no heldout/hard threshold, strategy, or candidate selection",
        },
    ]


def _parked_lane_record(stage_10_9: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_9.get("metrics", {})
    return [
        {
            "lane": "S2_ranking_objective_and_feature_strategy_execution",
            "parking_status": "parked",
            "parking_reason": "10.8 no-go and 10.9 keep-held checkpoint; no explicit execution request",
            "resume_condition": "explicit user go request plus separate dev/OOF-only execution stage",
            "preserved_assets": "10.6 locked candidate matrix, command contract, artifact manifest, stop conditions, approval criteria",
            "current_boundary": "read-only; no execution",
        },
        {
            "lane": "S2_design_and_scope_artifacts",
            "parking_status": "preserved_for_future",
            "parking_reason": f"candidate_matrix_rows available from prior lock; carry_forward_lock_count={metrics.get('carry_forward_lock_count')}",
            "resume_condition": "must re-check locks and leakage preflight before execution",
            "preserved_assets": "10.2-10.8 artifacts",
            "current_boundary": "diagnostic/reference only",
        },
    ]


def _strategy_return_options() -> list[dict[str, Any]]:
    return [
        {
            "option_id": "RETURN_TO_10X_STRATEGY_REVIEW",
            "status": "selected_default",
            "description": "Return to read-only broader 10.x strategy review after parking S2 execution.",
            "why": "S2 execution has no explicit go; continuing execution-planning stages would not add accuracy evidence.",
            "next_boundary": "read-only strategy review; no training or tuning",
        },
        {
            "option_id": "REQUEST_EXPLICIT_S2_GO",
            "status": "available_not_selected",
            "description": "Ask the user for explicit dev/OOF execution permission from locked scope.",
            "why": "10.7 established readiness to ask, but 10.8 recorded no-go.",
            "next_boundary": "requires explicit user go and separate execution stage",
        },
        {
            "option_id": "PARK_AND_PAUSE",
            "status": "available_not_selected",
            "description": "Park S2 execution lane and pause 10.x work until user chooses a strategy.",
            "why": "Avoids process churn if no execution or strategy review is desired.",
            "next_boundary": "no further action",
        },
    ]


def _return_readiness_checks(stage_10_9: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_9.get("metrics", {})
    return [
        {
            "check": "s2_execution_not_performed",
            "status": "pass" if metrics.get("execution_performed") is False else "fail",
            "observed": f"execution_performed={metrics.get('execution_performed')}",
            "required_for_return": "no execution results to interpret before returning to strategy",
        },
        {
            "check": "execution_remains_held",
            "status": "pass" if metrics.get("execution_remains_held") is True else "fail",
            "observed": f"execution_remains_held={metrics.get('execution_remains_held')}",
            "required_for_return": "S2 lane must remain parked, not half-open",
        },
        {
            "check": "locks_carried_forward",
            "status": "pass" if metrics.get("carry_forward_lock_count", 0) >= 10 else "fail",
            "observed": f"carry_forward_lock_count={metrics.get('carry_forward_lock_count')}",
            "required_for_return": "resume conditions and blocked actions must be preserved",
        },
        {
            "check": "no_implementation_allowed",
            "status": "pass" if metrics.get("implementation_allowed") is False and metrics.get("training_allowed") is False else "fail",
            "observed": f"implementation_allowed={metrics.get('implementation_allowed')}; training_allowed={metrics.get('training_allowed')}",
            "required_for_return": "return stage must remain read-only",
        },
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_dev_oof_experiment",
            "reason": "S2 execution lane is parked by 10.10.",
            "allowed_after": "explicit user go request plus separate execution stage",
        },
        {
            "blocked_action": "train_ltr_model",
            "reason": "parking/strategy-return gate does not train.",
            "allowed_after": "separate dev/OOF execution stage, if explicitly opened",
        },
        {
            "blocked_action": "tune_objective_or_threshold",
            "reason": "returning to strategy review cannot tune thresholds.",
            "allowed_after": "future execution may score locked variants under locked criteria",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "feature whitelist edits are outside parking gate.",
            "allowed_after": "separate feature proposal plus leakage preflight",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "no implementation path is opened by parking.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
    ]


def _metrics(
    parking_gate_decisions: list[dict[str, Any]],
    parked_lane_record: list[dict[str, Any]],
    strategy_return_options: list[dict[str, Any]],
    return_readiness_checks: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    stage_10_9: dict[str, Any],
) -> dict[str, Any]:
    check_pass = sum(1 for row in return_readiness_checks if row["status"] == "pass")
    metrics_10_9 = stage_10_9.get("metrics", {})
    return {
        "parking_gate_count": len(parking_gate_decisions),
        "parked_lane_record_count": len(parked_lane_record),
        "strategy_return_option_count": len(strategy_return_options),
        "return_readiness_check_count": len(return_readiness_checks),
        "return_readiness_pass_count": check_pass,
        "return_readiness_fail_count": len(return_readiness_checks) - check_pass,
        "blocked_action_count": len(blocked_actions),
        "selected_path": "PARK_S2_EXECUTION_LANE_AND_RETURN_TO_STRATEGY_REVIEW",
        "s2_execution_lane_parked": True,
        "strategy_return_selected": True,
        "explicit_go_requested": metrics_10_9.get("explicit_go_requested"),
        "execution_performed": False,
        "execution_remains_held": True,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.10 S2 Execution Lane Parking And Strategy-Return Gate",
        "",
        "Read-only parking gate after the 10.9 keep-held checkpoint. S2 execution is parked and the selected path returns to broader 10.x strategy review.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_path", metrics["selected_path"]],
                ["s2_execution_lane_parked", metrics["s2_execution_lane_parked"]],
                ["strategy_return_selected", metrics["strategy_return_selected"]],
                ["return_readiness_pass_count", metrics["return_readiness_pass_count"]],
                ["execution_performed", metrics["execution_performed"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Parking Gates",
        "",
        _md_table(
            [["gate", "status", "decision", "not_allowed"]]
            + [[row["gate"], row["status"], row["decision"], row["not_allowed"]] for row in report["parking_gate_decisions"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.10 S2 execution lane parking and strategy-return gate")
    parser.add_argument("--stage-10-9", default=str(DEFAULT_STAGE_10_9))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_9 = _read_json(Path(args.stage_10_9))
    parking_gate_decisions = _parking_gate_decisions(stage_10_9)
    parked_lane_record = _parked_lane_record(stage_10_9)
    strategy_return_options = _strategy_return_options()
    return_readiness_checks = _return_readiness_checks(stage_10_9)
    blocked_actions = _blocked_actions()
    metrics = _metrics(
        parking_gate_decisions,
        parked_lane_record,
        strategy_return_options,
        return_readiness_checks,
        blocked_actions,
        stage_10_9,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "parking_gate_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_parking_gate_decisions.csv")),
        "parked_lane_record_csv": str(output_prefix.with_name(output_prefix.name + "_parked_lane_record.csv")),
        "strategy_return_options_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_return_options.csv")),
        "return_readiness_checks_csv": str(output_prefix.with_name(output_prefix.name + "_return_readiness_checks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.10 S2 execution lane parking and strategy-return gate",
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
            "stage_10_9_summary": str(Path(args.stage_10_9)),
        },
        "metrics": metrics,
        "parking_gate_decisions": parking_gate_decisions,
        "parked_lane_record": parked_lane_record,
        "strategy_return_options": strategy_return_options,
        "return_readiness_checks": return_readiness_checks,
        "blocked_actions": blocked_actions,
        "decision": (
            "Select PARK_S2_EXECUTION_LANE_AND_RETURN_TO_STRATEGY_REVIEW. S2 execution remains parked until a separate explicit user go request opens execution. "
            "The next default step returns to broader read-only 10.x strategy review rather than continuing execution planning."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.10 only parks the S2 execution lane and gates return to strategy review. It does not run a dev/OOF experiment, train, tune, patch rules, "
            "change ranking, modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, or connect online."
        ),
        "next_stage": {
            "stage": "10.11 broader 10.x strategy re-entry review",
            "goal": "Read-only re-enter broader 10.x strategy after parking S2 execution, deciding which non-execution lane or deferred lever to review next.",
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
        Path(artifacts["parking_gate_decisions_csv"]),
        parking_gate_decisions,
        ["gate", "status", "evidence", "decision", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["parked_lane_record_csv"]),
        parked_lane_record,
        ["lane", "parking_status", "parking_reason", "resume_condition", "preserved_assets", "current_boundary"],
    )
    _write_csv(
        Path(artifacts["strategy_return_options_csv"]),
        strategy_return_options,
        ["option_id", "status", "description", "why", "next_boundary"],
    )
    _write_csv(
        Path(artifacts["return_readiness_checks_csv"]),
        return_readiness_checks,
        ["check", "status", "observed", "required_for_return"],
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
