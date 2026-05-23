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
DEFAULT_STAGE_10_7 = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_authorization_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_explicit_dev_oof_execution_go_no_go_confirmation"


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


def _confirmation_status(stage_10_7: dict[str, Any], explicit_go: bool) -> list[dict[str, Any]]:
    rows = []
    for confirmation in stage_10_7.get("required_user_confirmations", []):
        rows.append(
            {
                "confirmation_id": confirmation["confirmation_id"],
                "required_confirmation": confirmation["required_confirmation"],
                "status": "confirmed_by_explicit_go" if explicit_go else "not_confirmed",
                "default_without_confirmation": confirmation["default_without_confirmation"],
                "effective_action": "allow_future_execution_stage" if explicit_go else "do_not_execute",
            }
        )
    return rows


def _go_no_go_decision(stage_10_7: dict[str, Any], explicit_go: bool) -> list[dict[str, Any]]:
    metrics = stage_10_7.get("metrics", {})
    ready = metrics.get("ready_to_request_explicit_execution") is True
    go_allowed = explicit_go and ready
    return [
        {
            "decision_area": "explicit_go_signal",
            "observed": f"explicit_go_requested={explicit_go}",
            "decision": "go_signal_present" if explicit_go else "go_signal_absent",
            "effective_action": "continue_to_execution_stage" if go_allowed else "do_not_execute",
            "reason": "execution must be explicitly requested; this stage alone is not an execution request",
        },
        {
            "decision_area": "authorization_readiness",
            "observed": f"ready_to_request_explicit_execution={ready}",
            "decision": "ready" if ready else "not_ready",
            "effective_action": "eligible_if_explicit_go" if ready else "hold",
            "reason": "10.7 authorization checks must pass before execution can even be requested",
        },
        {
            "decision_area": "final_go_no_go",
            "observed": f"explicit_go_requested={explicit_go}; ready_to_request_explicit_execution={ready}",
            "decision": "GO_TO_FUTURE_EXECUTION_STAGE" if go_allowed else "NO_GO_DO_NOT_EXECUTE",
            "effective_action": "open_future_dev_oof_execution_stage" if go_allowed else "hold_at_read_only_boundary",
            "reason": "default without explicit go is do_not_execute",
        },
    ]


def _execution_hold_decisions(stage_10_7: dict[str, Any], explicit_go: bool) -> list[dict[str, Any]]:
    rows = []
    for hold in stage_10_7.get("pre_execution_hold_points", []):
        released = explicit_go and hold["hold_point"] == "before_start"
        rows.append(
            {
                "hold_point": hold["hold_point"],
                "condition": hold["condition"],
                "status": "released_by_explicit_go" if released else "hold",
                "required_action": "may_continue_to_preflight_in_future_execution_stage" if released else hold["required_action"],
                "source_lock": hold["source_lock"],
            }
        )
    return rows


def _blocked_actions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_dev_oof_experiment",
            "reason": "10.8 is go/no-go confirmation only; this run did not open execution." if not explicit_go else "10.8 only records go; execution must occur in a separate stage.",
            "allowed_after": "later explicit execution stage using locked 10.6 scope" if explicit_go else "explicit user go request plus separate execution stage",
        },
        {
            "blocked_action": "train_ltr_model",
            "reason": "training is execution, not confirmation.",
            "allowed_after": "separate dev/OOF execution stage, if explicitly opened",
        },
        {
            "blocked_action": "tune_objective_or_threshold",
            "reason": "confirmation cannot optimize objective or thresholds.",
            "allowed_after": "future execution may score locked variants; tuning remains bounded by locked criteria",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "locked 10.6 candidate matrix uses current feature toggles.",
            "allowed_after": "separate feature proposal plus leakage preflight",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "go/no-go is offline planning only.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
    ]


def _next_options(explicit_go: bool) -> list[dict[str, Any]]:
    if explicit_go:
        return [
            {
                "option_id": "NEXT_EXECUTE_DEV_OOF",
                "status": "available_after_this_stage",
                "description": "Open a separate dev/OOF-only execution stage using the locked 10.6 scope.",
                "requires": "all 10.7 confirmations remain accepted; no heldout/hard selection; complete artifacts",
            }
        ]
    return [
        {
            "option_id": "NEXT_REQUEST_EXPLICIT_GO",
            "status": "available_if_user_wants_execution",
            "description": "User may explicitly request first dev/OOF-only execution from locked 10.6 scope.",
            "requires": "clear instruction to execute, not just continue review",
        },
        {
            "option_id": "NEXT_KEEP_HELD",
            "status": "default",
            "description": "Keep S2 execution held and continue strategy review or pause the execution lane.",
            "requires": "no additional action",
        },
    ]


def _metrics(
    confirmation_status: list[dict[str, Any]],
    go_no_go_decision: list[dict[str, Any]],
    execution_hold_decisions: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    explicit_go: bool,
    stage_10_7: dict[str, Any],
) -> dict[str, Any]:
    confirmed_count = sum(1 for row in confirmation_status if row["status"] == "confirmed_by_explicit_go")
    hold_count = sum(1 for row in execution_hold_decisions if row["status"] == "hold")
    final_decision = [row for row in go_no_go_decision if row["decision_area"] == "final_go_no_go"][0]["decision"]
    return {
        "explicit_go_requested": explicit_go,
        "go_no_go_decision": final_decision,
        "required_confirmation_count": len(confirmation_status),
        "confirmed_count": confirmed_count,
        "unconfirmed_count": len(confirmation_status) - confirmed_count,
        "execution_hold_point_count": len(execution_hold_decisions),
        "active_hold_count": hold_count,
        "blocked_action_count": len(blocked_actions),
        "ready_to_request_explicit_execution": stage_10_7.get("metrics", {}).get("ready_to_request_explicit_execution") is True,
        "auto_execution_authorized": False,
        "execution_performed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.8 Explicit Dev/OOF Execution Go/No-Go Confirmation",
        "",
        "Read-only go/no-go confirmation for the first dev/OOF-only execution from the locked 10.6 scope. Default without explicit go is do_not_execute.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["explicit_go_requested", metrics["explicit_go_requested"]],
                ["go_no_go_decision", metrics["go_no_go_decision"]],
                ["required_confirmation_count", metrics["required_confirmation_count"]],
                ["confirmed_count", metrics["confirmed_count"]],
                ["unconfirmed_count", metrics["unconfirmed_count"]],
                ["active_hold_count", metrics["active_hold_count"]],
                ["auto_execution_authorized", metrics["auto_execution_authorized"]],
                ["execution_performed", metrics["execution_performed"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Go/No-Go",
        "",
        _md_table(
            [["decision_area", "observed", "decision", "effective_action"]]
            + [
                [row["decision_area"], row["observed"], row["decision"], row["effective_action"]]
                for row in report["go_no_go_decision"]
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
    parser = argparse.ArgumentParser(description="Stage 10.8 explicit dev/OOF execution go/no-go confirmation")
    parser.add_argument("--stage-10-7", default=str(DEFAULT_STAGE_10_7))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--explicit-go", action="store_true", help="Record an explicit go signal; default is no-go/do_not_execute.")
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_7 = _read_json(Path(args.stage_10_7))
    explicit_go = bool(args.explicit_go)
    confirmation_status = _confirmation_status(stage_10_7, explicit_go)
    go_no_go_decision = _go_no_go_decision(stage_10_7, explicit_go)
    execution_hold_decisions = _execution_hold_decisions(stage_10_7, explicit_go)
    blocked_actions = _blocked_actions(explicit_go)
    next_options = _next_options(explicit_go)
    metrics = _metrics(
        confirmation_status,
        go_no_go_decision,
        execution_hold_decisions,
        blocked_actions,
        explicit_go,
        stage_10_7,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "go_no_go_decision_csv": str(output_prefix.with_name(output_prefix.name + "_go_no_go_decision.csv")),
        "confirmation_status_csv": str(output_prefix.with_name(output_prefix.name + "_confirmation_status.csv")),
        "execution_hold_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_execution_hold_decisions.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
    }

    final_decision = metrics["go_no_go_decision"]
    report = {
        "stage": "Goal LTR v1 / stage 10.8 explicit dev/OOF execution go/no-go confirmation",
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
            "stage_10_7_summary": str(Path(args.stage_10_7)),
        },
        "metrics": metrics,
        "go_no_go_decision": go_no_go_decision,
        "confirmation_status": confirmation_status,
        "execution_hold_decisions": execution_hold_decisions,
        "blocked_actions": blocked_actions,
        "next_options": next_options,
        "decision": (
            f"Record {final_decision}. No explicit go signal was provided in 10.8, so execution remains held and no dev/OOF experiment is run."
            if not explicit_go
            else "Record GO_TO_FUTURE_EXECUTION_STAGE. This stage still does not execute; it only permits a separate future execution stage."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.8 only records go/no-go confirmation. It does not run a dev/OOF experiment, train, tune, patch rules, change ranking, modify GoalSearcher, "
            "edit the feature whitelist, use heldout/hard for selection, relax gates, or connect online."
        ),
        "next_stage": {
            "stage": "10.9 execution-held strategy checkpoint",
            "goal": (
                "Read-only decide whether to keep S2 execution held, ask the user for an explicit go request, or pivot back to strategy review. No execution unless explicitly requested."
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
        Path(artifacts["go_no_go_decision_csv"]),
        go_no_go_decision,
        ["decision_area", "observed", "decision", "effective_action", "reason"],
    )
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
