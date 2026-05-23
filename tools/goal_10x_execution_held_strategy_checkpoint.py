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
DEFAULT_STAGE_10_8 = AGENT_STATE / "goal_10x_explicit_dev_oof_execution_go_no_go_confirmation_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_execution_held_strategy_checkpoint"


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


def _strategy_options(stage_10_8: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_8.get("metrics", {})
    return [
        {
            "option_id": "KEEP_S2_EXECUTION_HELD",
            "status": "selected_default",
            "basis": f"go_no_go_decision={metrics.get('go_no_go_decision')}; explicit_go_requested={metrics.get('explicit_go_requested')}; active_hold_count={metrics.get('active_hold_count')}",
            "action": "keep execution lane held and do not run dev/OOF experiment",
            "requires": "no additional user action",
            "risk": "no new S2 experimental signal is produced",
        },
        {
            "option_id": "ASK_USER_FOR_EXPLICIT_GO",
            "status": "available_not_selected",
            "basis": f"ready_to_request_explicit_execution={metrics.get('ready_to_request_explicit_execution')}",
            "action": "ask for a clear execution request that accepts locked scope and artifact/stop conditions",
            "requires": "explicit user instruction to execute, not another review-only next step",
            "risk": "would open a separate dev/OOF execution stage if user confirms",
        },
        {
            "option_id": "RETURN_TO_STRATEGY_REVIEW",
            "status": "available_not_selected",
            "basis": "execution is held; accuracy lane can revisit S1/S3/S4 or broader 10.x strategy without running S2",
            "action": "pivot back to read-only strategy review or compare deferred levers",
            "requires": "new strategy-review stage definition",
            "risk": "may delay S2 evidence collection",
        },
    ]


def _checkpoint_decisions(stage_10_8: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_8.get("metrics", {})
    return [
        {
            "decision_area": "execution_state",
            "decision": "KEEP_HELD",
            "evidence": f"go_no_go_decision={metrics.get('go_no_go_decision')}; execution_performed={metrics.get('execution_performed')}",
            "effective_action": "do_not_execute",
            "not_allowed": "no dev/OOF scoring, training, tuning, or ranking change",
        },
        {
            "decision_area": "default_path",
            "decision": "KEEP_S2_EXECUTION_HELD",
            "evidence": "10.8 default without explicit go is do_not_execute",
            "effective_action": "preserve locked 10.6 scope for possible future explicit request",
            "not_allowed": "no implicit execution from 'next step' phrasing",
        },
        {
            "decision_area": "strategy_path",
            "decision": "CHECKPOINT_COMPLETE",
            "evidence": "alternatives documented: ask explicit go or return to strategy review",
            "effective_action": "next stage should close or park the S2 execution lane unless user explicitly says execute",
            "not_allowed": "no automatic pivot to training or online integration",
        },
        {
            "decision_area": "heldout_boundary",
            "decision": "REMAIN_CLOSED_FOR_SELECTION",
            "evidence": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "effective_action": "heldout/hard remain validation-only after a frozen candidate exists",
            "not_allowed": "no heldout/hard threshold or strategy selection",
        },
    ]


def _carry_forward_locks(stage_10_8: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for hold in stage_10_8.get("execution_hold_decisions", []):
        rows.append(
            {
                "lock_id": f"HOLD_{hold['hold_point']}",
                "source": hold["source_lock"],
                "condition": hold["condition"],
                "status": hold["status"],
                "carry_forward_action": hold["required_action"],
            }
        )
    for blocked in stage_10_8.get("blocked_actions", []):
        rows.append(
            {
                "lock_id": f"BLOCK_{blocked['blocked_action']}",
                "source": "10.8 blocked actions",
                "condition": blocked["reason"],
                "status": "blocked",
                "carry_forward_action": blocked["allowed_after"],
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_dev_oof_experiment",
            "reason": "10.9 selected keep-held checkpoint; no explicit execution go was provided.",
            "allowed_after": "explicit user go request plus separate execution stage",
        },
        {
            "blocked_action": "train_ltr_model",
            "reason": "training is not part of an execution-held checkpoint.",
            "allowed_after": "separate dev/OOF execution stage, if explicitly opened",
        },
        {
            "blocked_action": "tune_objective_or_threshold",
            "reason": "checkpoint does not score or tune candidate variants.",
            "allowed_after": "future execution may score locked variants under locked criteria",
        },
        {
            "blocked_action": "edit_feature_whitelist",
            "reason": "feature changes are outside the held S2 execution lane.",
            "allowed_after": "separate feature proposal plus leakage preflight",
        },
        {
            "blocked_action": "change_ranking_or_goal_searcher",
            "reason": "no implementation path is opened by keep-held.",
            "allowed_after": "post-validation integration review, if ever reached",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
    ]


def _next_recommendations() -> list[dict[str, Any]]:
    return [
        {
            "recommendation_id": "NEXT_CLOSE_OR_PARK_S2_EXECUTION_LANE",
            "priority": "P0",
            "recommendation": "Close or park the S2 execution lane as held until the user explicitly asks to execute.",
            "why": "10.8 produced NO_GO_DO_NOT_EXECUTE; continuing execution planning without go adds process churn.",
        },
        {
            "recommendation_id": "NEXT_RETURN_TO_READ_ONLY_STRATEGY",
            "priority": "P1",
            "recommendation": "Return to read-only strategy review of deferred levers or decide whether to ask the user for explicit S2 execution.",
            "why": "Accuracy remains far from target, but execution has not been authorized.",
        },
    ]


def _metrics(
    strategy_options: list[dict[str, Any]],
    checkpoint_decisions: list[dict[str, Any]],
    carry_forward_locks: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    next_recommendations: list[dict[str, Any]],
    stage_10_8: dict[str, Any],
) -> dict[str, Any]:
    selected_count = sum(1 for row in strategy_options if row["status"] == "selected_default")
    metrics_10_8 = stage_10_8.get("metrics", {})
    return {
        "strategy_option_count": len(strategy_options),
        "selected_option_count": selected_count,
        "checkpoint_decision_count": len(checkpoint_decisions),
        "carry_forward_lock_count": len(carry_forward_locks),
        "blocked_action_count": len(blocked_actions),
        "next_recommendation_count": len(next_recommendations),
        "selected_default": "KEEP_S2_EXECUTION_HELD",
        "go_no_go_decision": metrics_10_8.get("go_no_go_decision"),
        "explicit_go_requested": metrics_10_8.get("explicit_go_requested"),
        "execution_performed": False,
        "execution_remains_held": True,
        "implementation_allowed": False,
        "training_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.9 Execution-Held Strategy Checkpoint",
        "",
        "Read-only checkpoint after the 10.8 no-go decision. The selected default is to keep S2 execution held.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_default", metrics["selected_default"]],
                ["go_no_go_decision", metrics["go_no_go_decision"]],
                ["explicit_go_requested", metrics["explicit_go_requested"]],
                ["execution_remains_held", metrics["execution_remains_held"]],
                ["strategy_option_count", metrics["strategy_option_count"]],
                ["carry_forward_lock_count", metrics["carry_forward_lock_count"]],
                ["training_allowed", metrics["training_allowed"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Strategy Options",
        "",
        _md_table(
            [["option_id", "status", "action", "requires"]]
            + [[row["option_id"], row["status"], row["action"], row["requires"]] for row in report["strategy_options"]]
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
    parser = argparse.ArgumentParser(description="Stage 10.9 execution-held strategy checkpoint")
    parser.add_argument("--stage-10-8", default=str(DEFAULT_STAGE_10_8))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_8 = _read_json(Path(args.stage_10_8))
    strategy_options = _strategy_options(stage_10_8)
    checkpoint_decisions = _checkpoint_decisions(stage_10_8)
    carry_forward_locks = _carry_forward_locks(stage_10_8)
    blocked_actions = _blocked_actions()
    next_recommendations = _next_recommendations()
    metrics = _metrics(
        strategy_options,
        checkpoint_decisions,
        carry_forward_locks,
        blocked_actions,
        next_recommendations,
        stage_10_8,
    )

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "strategy_options_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_options.csv")),
        "checkpoint_decisions_csv": str(output_prefix.with_name(output_prefix.name + "_checkpoint_decisions.csv")),
        "carry_forward_locks_csv": str(output_prefix.with_name(output_prefix.name + "_carry_forward_locks.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
        "next_recommendations_csv": str(output_prefix.with_name(output_prefix.name + "_next_recommendations.csv")),
    }

    report = {
        "stage": "Goal LTR v1 / stage 10.9 execution-held strategy checkpoint",
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
            "stage_10_8_summary": str(Path(args.stage_10_8)),
        },
        "metrics": metrics,
        "strategy_options": strategy_options,
        "checkpoint_decisions": checkpoint_decisions,
        "carry_forward_locks": carry_forward_locks,
        "blocked_actions": blocked_actions,
        "next_recommendations": next_recommendations,
        "decision": (
            "Select KEEP_S2_EXECUTION_HELD as the default checkpoint decision. The S2 execution lane remains parked because 10.8 recorded "
            "NO_GO_DO_NOT_EXECUTE and no explicit execution request was provided. Future execution still requires a separate explicit user go request."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.9 only checkpoints the held execution lane. It does not run a dev/OOF experiment, train, tune, patch rules, change ranking, modify GoalSearcher, "
            "edit the feature whitelist, use heldout/hard for selection, relax gates, or connect online."
        ),
        "next_stage": {
            "stage": "10.10 S2 execution lane parking and strategy-return gate",
            "goal": (
                "Read-only decide whether to park the S2 execution lane and return to broader 10.x strategy review, or ask the user for explicit execution go."
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
        Path(artifacts["strategy_options_csv"]),
        strategy_options,
        ["option_id", "status", "basis", "action", "requires", "risk"],
    )
    _write_csv(
        Path(artifacts["checkpoint_decisions_csv"]),
        checkpoint_decisions,
        ["decision_area", "decision", "evidence", "effective_action", "not_allowed"],
    )
    _write_csv(
        Path(artifacts["carry_forward_locks_csv"]),
        carry_forward_locks,
        ["lock_id", "source", "condition", "status", "carry_forward_action"],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_csv(
        Path(artifacts["next_recommendations_csv"]),
        next_recommendations,
        ["recommendation_id", "priority", "recommendation", "why"],
    )
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
