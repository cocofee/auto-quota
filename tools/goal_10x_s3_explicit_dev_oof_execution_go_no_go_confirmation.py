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
DEFAULT_STAGE_10_16 = AGENT_STATE / "goal_10x_s3_offline_whatif_execution_authorization_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_explicit_dev_oof_execution_go_no_go_confirmation"


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


def _confirmation_status(stage_10_16: dict[str, Any], explicit_go: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for confirmation in stage_10_16.get("required_user_confirmations", []):
        rows.append(
            {
                "confirmation_id": confirmation.get("confirmation_id"),
                "required_confirmation": confirmation.get("required_confirmation"),
                "status": "confirmed_by_explicit_go" if explicit_go else "not_confirmed",
                "default_without_confirmation": confirmation.get("default_without_confirmation"),
                "effective_action": "allow_future_execution_stage" if explicit_go else "do_not_execute",
            }
        )
    return rows


def _go_no_go_decision(stage_10_16: dict[str, Any], explicit_go: bool) -> list[dict[str, Any]]:
    metrics = stage_10_16.get("metrics", {})
    ready = metrics.get("ready_to_request_explicit_go") is True
    go_allowed = explicit_go and ready
    return [
        {
            "decision_area": "explicit_go_signal",
            "observed": f"explicit_go_present={explicit_go}",
            "decision": "go_signal_present" if explicit_go else "go_signal_absent",
            "effective_action": "continue_to_separate_execution_stage" if go_allowed else "do_not_execute",
            "reason": "execution must be explicitly requested; a go/no-go confirmation stage is not itself execution",
        },
        {
            "decision_area": "authorization_readiness",
            "observed": f"ready_to_request_explicit_go={ready}",
            "decision": "ready" if ready else "not_ready",
            "effective_action": "eligible_if_explicit_go" if ready else "hold",
            "reason": "10.16 authorization checks must pass before execution can even be requested",
        },
        {
            "decision_area": "final_go_no_go",
            "observed": f"explicit_go_present={explicit_go}; ready_to_request_explicit_go={ready}",
            "decision": "GO_TO_SEPARATE_S3_DEV_OOF_EXECUTION_STAGE" if go_allowed else "NO_GO_DO_NOT_EXECUTE",
            "effective_action": "open_future_s3_dev_oof_execution_stage" if go_allowed else "hold_at_read_only_boundary",
            "reason": "default without explicit go is do_not_execute",
        },
    ]


def _execution_hold_decisions(stage_10_16: dict[str, Any], explicit_go: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hold in stage_10_16.get("pre_execution_hold_points", []):
        released = explicit_go and hold.get("hold_point") == "before_start"
        rows.append(
            {
                "hold_point": hold.get("hold_point"),
                "condition": hold.get("condition"),
                "status": "released_by_explicit_go_for_separate_execution_stage" if released else "hold",
                "required_action": "may_continue_to_preflight_in_future_execution_stage" if released else hold.get("required_action"),
                "source_lock": hold.get("source_lock"),
            }
        )
    return rows


def _automation_stop_decisions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {
            "automation_rule": "read_only_auto_advance",
            "status": "allowed",
            "decision": "automation may continue future read-only checkpoints",
            "reason": "Read-only review, closure, plan, and dashboard updates remain safe to automate.",
        },
        {
            "automation_rule": "execution_boundary",
            "status": "stop" if not explicit_go else "requires_separate_execution_stage",
            "decision": "do_not_execute" if not explicit_go else "do_not_execute_inside_10_17",
            "reason": "Automatic Goal mode must not run S3 what-if execution without explicit go in a dedicated execution stage.",
        },
        {
            "automation_rule": "current_user_signal",
            "status": "no_go_by_default" if not explicit_go else "go_recorded_for_future_stage",
            "decision": "hold" if not explicit_go else "open_only_separate_execution_stage",
            "reason": "The current request restates default do_not_execute and does not explicitly say to run the what-if.",
        },
    ]


def _blocked_actions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s3_offline_whatif",
            "reason": "10.17 is go/no-go confirmation only; no explicit execution go is present." if not explicit_go else "10.17 records go only; execution must occur in a separate stage.",
            "allowed_after": "explicit user go plus separate S3 dev/OOF execution stage" if not explicit_go else "separate S3 dev/OOF execution stage with all 10.14 artifacts",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "go/no-go confirmation does not tune or alter policy.",
            "allowed_after": "separate implementation proposal after future evidence, if ever reached",
        },
        {
            "blocked_action": "expand_candidate_policy_matrix",
            "reason": "10.14 candidate matrix remains frozen.",
            "allowed_after": "new plan-definition review, not inside go/no-go confirmation",
        },
        {
            "blocked_action": "enable_compatibility_switch_or_connect_online",
            "reason": "default-off and no GoalSearcher integration remain locked.",
            "allowed_after": "post-validation integration readiness review, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "S3 go/no-go confirmation is not model training or ranking-objective tuning.",
            "allowed_after": "separate explicitly authorized execution or training lane",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only after freeze.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "10.17 does not change features, ranking, or GoalSearcher.",
            "allowed_after": "separate feature/ranking proposal and leakage review",
        },
    ]


def _next_options(explicit_go: bool) -> list[dict[str, Any]]:
    if explicit_go:
        return [
            {
                "option_id": "NEXT_SEPARATE_S3_DEV_OOF_EXECUTION",
                "status": "available_after_this_stage",
                "description": "Open a separate dev/OOF-only S3 what-if execution stage using the reviewed 10.14 plan.",
                "requires": "all 10.16 confirmations remain accepted; no heldout/hard selection; complete artifacts",
            }
        ]
    return [
        {
            "option_id": "NEXT_KEEP_HELD",
            "status": "default",
            "description": "Keep S3 execution held and continue with an execution-held strategy checkpoint.",
            "requires": "no additional action",
        },
        {
            "option_id": "NEXT_REQUEST_EXPLICIT_GO",
            "status": "available_if_user_wants_execution",
            "description": "User may explicitly request first dev/OOF-only S3 execution from the reviewed 10.14 plan.",
            "requires": "clear instruction to execute the S3 what-if, not just continue review",
        },
        {
            "option_id": "NEXT_RETURN_TO_STRATEGY",
            "status": "available",
            "description": "Return to broader 10.x strategy review if S3 execution remains parked.",
            "requires": "read-only strategy checkpoint",
        },
    ]


def _metrics(
    confirmation_status: list[dict[str, Any]],
    go_no_go_decision: list[dict[str, Any]],
    execution_hold_decisions: list[dict[str, Any]],
    automation_stop_decisions: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    explicit_go: bool,
    stage_10_16: dict[str, Any],
) -> dict[str, Any]:
    confirmed_count = sum(1 for row in confirmation_status if row["status"] == "confirmed_by_explicit_go")
    hold_count = sum(1 for row in execution_hold_decisions if row["status"] == "hold")
    final_decision = [row for row in go_no_go_decision if row["decision_area"] == "final_go_no_go"][0]["decision"]
    source_metrics = stage_10_16.get("metrics", {})
    return {
        "explicit_go_present": explicit_go,
        "go_no_go_decision": final_decision,
        "required_confirmation_count": len(confirmation_status),
        "confirmed_count": confirmed_count,
        "unconfirmed_count": len(confirmation_status) - confirmed_count,
        "execution_hold_point_count": len(execution_hold_decisions),
        "active_hold_count": hold_count,
        "automation_stop_decision_count": len(automation_stop_decisions),
        "blocked_action_count": len(blocked_actions),
        "ready_to_request_explicit_go": source_metrics.get("ready_to_request_explicit_go") is True,
        "execution_authorized": False,
        "execution_performed": False,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.17 S3 Explicit Dev/OOF Execution Go/No-Go Confirmation",
        "",
        "Read-only go/no-go confirmation for first dev/OOF-only S3 offline what-if execution from the reviewed 10.14 plan. Default without explicit go is do_not_execute.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["explicit_go_present", metrics["explicit_go_present"]],
                ["go_no_go_decision", metrics["go_no_go_decision"]],
                ["required_confirmation_count", metrics["required_confirmation_count"]],
                ["confirmed_count", metrics["confirmed_count"]],
                ["unconfirmed_count", metrics["unconfirmed_count"]],
                ["active_hold_count", metrics["active_hold_count"]],
                ["execution_authorized", metrics["execution_authorized"]],
                ["execution_performed", metrics["execution_performed"]],
            ]
        ),
        "",
        "## Go/No-Go",
        "",
        _md_table(
            [["decision_area", "observed", "decision", "effective_action"]]
            + [[row["decision_area"], row["observed"], row["decision"], row["effective_action"]] for row in report["go_no_go_decision"]]
        ),
        "",
        "## Automation Boundary",
        "",
        _md_table(
            [["automation_rule", "status", "decision", "reason"]]
            + [[row["automation_rule"], row["status"], row["decision"], row["reason"]] for row in report["automation_stop_decisions"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.17 S3 explicit dev/OOF execution go/no-go confirmation")
    parser.add_argument("--stage-10-16", default=str(DEFAULT_STAGE_10_16))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--explicit-go", action="store_true", help="Record an explicit go signal; default is no-go/do_not_execute.")
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_16 = _read_json(Path(args.stage_10_16))
    explicit_go = bool(args.explicit_go)
    confirmation_status = _confirmation_status(stage_10_16, explicit_go)
    go_no_go_decision = _go_no_go_decision(stage_10_16, explicit_go)
    execution_hold_decisions = _execution_hold_decisions(stage_10_16, explicit_go)
    automation_stop_decisions = _automation_stop_decisions(explicit_go)
    blocked_actions = _blocked_actions(explicit_go)
    next_options = _next_options(explicit_go)
    metrics = _metrics(
        confirmation_status,
        go_no_go_decision,
        execution_hold_decisions,
        automation_stop_decisions,
        blocked_actions,
        explicit_go,
        stage_10_16,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "go_no_go_decision_csv": str(output_prefix.with_name(output_prefix.name + "_go_no_go_decision.csv")),
        "confirmation_status_csv": str(output_prefix.with_name(output_prefix.name + "_confirmation_status.csv")),
        "execution_hold_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_execution_hold_decisions.csv")),
        "automation_stop_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_automation_stop_decisions.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
    }

    final_decision = metrics["go_no_go_decision"]
    report = {
        "stage": "Goal LTR v1 / stage 10.17 S3 explicit dev/OOF execution go/no-go confirmation",
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
            "stage_10_16_authorization_review": str(Path(args.stage_10_16)),
        },
        "metrics": metrics,
        "go_no_go_decision": go_no_go_decision,
        "confirmation_status": confirmation_status,
        "execution_hold_decisions": execution_hold_decisions,
        "automation_stop_decisions": automation_stop_decisions,
        "blocked_actions": blocked_actions,
        "next_options": next_options,
        "decision": (
            f"Record {final_decision}. No explicit go signal was provided in 10.17, so S3 execution remains held and no dev/OOF what-if is run. "
            "Automatic Goal mode may continue only read-only checkpoints and must stop before execution."
            if not explicit_go
            else "Record GO_TO_SEPARATE_S3_DEV_OOF_EXECUTION_STAGE. This stage still does not execute; it only permits a separate future execution stage."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.17 only records explicit go/no-go confirmation. It does not run S3 what-if, train, tune, change thresholds, patch rules, change ranking, "
            "modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, enable a switch, or connect online."
        ),
        "next_stage": {
            "stage": "10.18 S3 execution-held strategy checkpoint",
            "goal": (
                "Read-only decide whether to keep S3 execution held, request explicit go again, or park the S3 execution lane and return to broader 10.x strategy review. "
                "No execution unless explicitly requested."
            ),
            "prohibited": [
                "what-if execution unless explicitly opened as execution",
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

    _write_csv(Path(artifacts["go_no_go_decision_csv"]), go_no_go_decision, ["decision_area", "observed", "decision", "effective_action", "reason"])
    _write_csv(
        Path(artifacts["confirmation_status_csv"]),
        confirmation_status,
        ["confirmation_id", "required_confirmation", "status", "default_without_confirmation", "effective_action"],
    )
    _write_csv(
        Path(artifacts["execution_hold_decisions_csv"]),
        execution_hold_decisions,
        ["hold_point", "condition", "status", "required_action", "source_lock"],
    )
    _write_csv(
        Path(artifacts["automation_stop_decisions_csv"]),
        automation_stop_decisions,
        ["automation_rule", "status", "decision", "reason"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option_id", "status", "description", "requires"])
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
