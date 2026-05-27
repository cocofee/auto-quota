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
DEFAULT_BROADER_REVIEW = AGENT_STATE / "goal_10x_broader_strategy_review_after_s1_closure_summary.json"
DEFAULT_LANE_STATUS = AGENT_STATE / "goal_10x_broader_strategy_review_after_s1_closure_lane_status.csv"
DEFAULT_EVIDENCE_WAIT = AGENT_STATE / "goal_10x_broader_strategy_review_after_s1_closure_evidence_wait_contract.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_broader_strategy_review_after_s1_closure_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_no_active_learning_lane_evidence_wait_checkpoint"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    pause_decisions: list[dict[str, Any]],
    reentry_requirements: list[dict[str, Any]],
    stop_conditions: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.46 No-active-learning-lane Evidence-wait Checkpoint",
        "",
        "Read-only checkpoint for pausing the 10.x loop while waiting for valid evidence or explicit go.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["active_learning_lane_count", metrics["active_learning_lane_count"]],
                ["evidence_wait_track_count", metrics["evidence_wait_track_count"]],
                ["checkpoint_decision", metrics["checkpoint_decision"]],
                ["pause_10x_loop_now", metrics["pause_10x_loop_now"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Pause Decisions",
        "",
        _md_table(
            [["decision_item", "decision", "rationale"]]
            + [[row["decision_item"], row["decision"], row["rationale"]] for row in pause_decisions]
        ),
        "",
        "## Re-entry Requirements",
        "",
        _md_table(
            [["lane", "required_before_reentry", "source_or_owner", "reentry_gate"]]
            + [
                [row["lane"], row["required_before_reentry"], row["source_or_owner"], row["reentry_gate"]]
                for row in reentry_requirements
            ]
        ),
        "",
        "## Stop Conditions",
        "",
        _md_table(
            [["condition", "action", "reason"]]
            + [[row["condition"], row["action"], row["reason"]] for row in stop_conditions]
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
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Checkpoint no-active-learning-lane evidence wait state")
    parser.add_argument("--broader-review", default=str(DEFAULT_BROADER_REVIEW))
    parser.add_argument("--lane-status", default=str(DEFAULT_LANE_STATUS))
    parser.add_argument("--evidence-wait", default=str(DEFAULT_EVIDENCE_WAIT))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    broader_review = _read_json(Path(args.broader_review))
    broader_metrics = broader_review["metrics"]
    lane_status = _read_csv(Path(args.lane_status))
    evidence_wait = _read_csv(Path(args.evidence_wait))
    blocked_actions = _read_csv(Path(args.blocked_actions))

    reentry_requirements = [
        {
            "lane": row["lane"],
            "required_before_reentry": row["reopen_condition"],
            "source_or_owner": next(
                (
                    item["owner_or_source"]
                    for item in evidence_wait
                    if row["lane"].startswith(item["evidence_track"].split("_")[0])
                ),
                "lane owner or evidence provider",
            ),
            "reentry_gate": "future read-only re-entry review before any execution or implementation",
        }
        for row in lane_status
    ]
    pause_decisions = [
        {
            "decision_item": "10x_loop_status",
            "decision": "PAUSE_AWAITING_EVIDENCE_OR_EXPLICIT_GO",
            "rationale": "10.45 found active_learning_lane_count=0 and all known lanes parked or blocked.",
        },
        {
            "decision_item": "automatic_goal_advance",
            "decision": "STOP_AUTO_ADVANCING_LEARNING_STAGES",
            "rationale": "Further read-only stages would only restate blockers unless new evidence or explicit go arrives.",
        },
        {
            "decision_item": "next_valid_action",
            "decision": "WAIT_FOR_INPUT_OR_USER_REDIRECT",
            "rationale": "Valid next input is one lane-specific evidence package, explicit S3/DQ go, or explicit user request to define a new strategy outside current lanes.",
        },
        {
            "decision_item": "algorithm_change",
            "decision": "DO_NOT_CHANGE_ALGORITHM",
            "rationale": "No lane has passed re-entry requirements and no implementation or training authorization exists.",
        },
    ]
    stop_conditions = [
        {
            "condition": "no_new_evidence_or_explicit_go",
            "action": "pause_and_report_wait_state",
            "reason": "The current loop has no active learning lane.",
        },
        {
            "condition": "request_to_train_tune_or_implement_without_reentry",
            "action": "refuse_and_point_to_reentry_requirements",
            "reason": "Training/implementation would violate evidence gates.",
        },
        {
            "condition": "heldout_or_hard_requested_for_selection",
            "action": "stop_and_preserve_validation_boundary",
            "reason": "Heldout/hard remain validation-only and cannot be used for selection.",
        },
        {
            "condition": "new_valid_evidence_package_arrives",
            "action": "open_read_only_reentry_review_for_that_lane_only",
            "reason": "Evidence must be checked before any execution or implementation.",
        },
        {
            "condition": "explicit_user_redirects_to_new_strategy",
            "action": "start_read_only_strategy_definition_for_new_lane",
            "reason": "A new strategy can be defined, but still cannot execute by default.",
        },
    ]
    final_blocked_actions = blocked_actions + [
        {
            "blocked_action": "continue_goal_auto_advance_without_new_input",
            "reason": "10.46 pauses the loop because active_learning_lane_count=0.",
            "allowed_after": "new evidence package, explicit go, or explicit user redirect",
        },
        {
            "blocked_action": "treat_pause_as_success_metric",
            "reason": "Pause is a governance decision, not an accuracy improvement.",
            "allowed_after": "never; accuracy still requires future validated evidence",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "pause_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_pause_decisions.csv")),
        "reentry_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_reentry_requirements.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": broader_review["stage"],
        "active_learning_lane_count": broader_metrics["active_learning_lane_count"],
        "parked_or_blocked_lane_count": broader_metrics["parked_or_blocked_lane_count"],
        "evidence_wait_track_count": len(evidence_wait),
        "reentry_requirement_count": len(reentry_requirements),
        "stop_condition_count": len(stop_conditions),
        "checkpoint_decision": "pause_awaiting_evidence_or_explicit_go",
        "pause_10x_loop_now": True,
        "new_evidence_present": False,
        "explicit_go_present": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.46 no-active-learning-lane evidence-wait checkpoint",
        "read_only": True,
        "checkpoint_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Pause the 10.x learning loop awaiting new evidence or explicit go. S1, S2, S3, and DQ implementation each have clear re-entry requirements, "
            "but none are satisfied now. Do not auto-advance learning stages, train, tune, implement, or select on heldout/hard until a lane-specific input arrives and passes read-only re-entry review."
        ),
        "anti_drift_conclusion": (
            "10.46 only records the evidence-wait checkpoint and stop conditions. It does not train, tune, expand candidates, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, implement DQ fixes, or convert DQ backlog rows into learning evidence."
        ),
        "next_stage": {
            "stage": "paused awaiting evidence or explicit go",
            "goal": "Resume only when a valid lane-specific evidence package, explicit go, or explicit user redirect is supplied.",
            "default": "pause; do not continue auto-advancing learning stages",
        },
    }

    _write_csv(Path(artifacts["pause_decisions_csv"]), pause_decisions, ["decision_item", "decision", "rationale"])
    _write_csv(Path(artifacts["reentry_requirements_csv"]), reentry_requirements, ["lane", "required_before_reentry", "source_or_owner", "reentry_gate"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, ["condition", "action", "reason"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), final_blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, pause_decisions, reentry_requirements, stop_conditions)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
