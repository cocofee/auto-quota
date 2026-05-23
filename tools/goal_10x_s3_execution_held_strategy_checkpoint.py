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
DEFAULT_STAGE_10_17 = AGENT_STATE / "goal_10x_s3_explicit_dev_oof_execution_go_no_go_confirmation_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s3_execution_held_strategy_checkpoint"


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


def _strategy_options(stage_10_17: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_17.get("metrics", {})
    return [
        {
            "option_id": "KEEP_S3_EXECUTION_HELD",
            "status": "selected_default",
            "basis": f"go_no_go_decision={metrics.get('go_no_go_decision')}; explicit_go_present={metrics.get('explicit_go_present')}; active_hold_count={metrics.get('active_hold_count')}",
            "action": "keep S3 execution lane held and do not run dev/OOF what-if",
            "requires": "no additional user action",
            "risk": "no new S3 what-if evidence is produced",
        },
        {
            "option_id": "ASK_USER_FOR_EXPLICIT_GO",
            "status": "available_not_selected",
            "basis": f"ready_to_request_explicit_go={metrics.get('ready_to_request_explicit_go')}",
            "action": "ask for a clear S3 dev/OOF what-if execution request that accepts reviewed 10.14 scope and artifact/stop conditions",
            "requires": "explicit instruction to execute the S3 what-if, not another review-only next step",
            "risk": "would open a separate dev/OOF execution stage if user confirms",
        },
        {
            "option_id": "PARK_S3_AND_RETURN_TO_BROADER_STRATEGY",
            "status": "available_next_gate",
            "basis": "10.17 recorded NO_GO_DO_NOT_EXECUTE and automatic Goal mode can continue read-only checkpoints",
            "action": "park the S3 execution lane and return to broader 10.x strategy review",
            "requires": "10.19 parking and strategy-return gate",
            "risk": "may defer S3 execution evidence, but avoids process churn around an unapproved execution lane",
        },
    ]


def _checkpoint_decisions(stage_10_17: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = stage_10_17.get("metrics", {})
    return [
        {
            "decision_area": "execution_state",
            "decision": "KEEP_HELD",
            "evidence": f"go_no_go_decision={metrics.get('go_no_go_decision')}; execution_performed={metrics.get('execution_performed')}",
            "effective_action": "do_not_execute",
            "not_allowed": "no S3 dev/OOF what-if scoring, training, tuning, or ranking change",
        },
        {
            "decision_area": "default_path",
            "decision": "KEEP_S3_EXECUTION_HELD",
            "evidence": "10.17 default without explicit go is do_not_execute",
            "effective_action": "preserve reviewed 10.14 S3 plan for possible future explicit request",
            "not_allowed": "no implicit execution from 'next step' phrasing or automation",
        },
        {
            "decision_area": "strategy_path",
            "decision": "MOVE_TO_PARKING_GATE",
            "evidence": "alternatives documented: ask explicit go or park and return to broader strategy",
            "effective_action": "next stage should decide whether to formally park S3 execution and return to broader 10.x strategy review",
            "not_allowed": "no automatic pivot to what-if execution or online integration",
        },
        {
            "decision_area": "automation_boundary",
            "decision": "AUTO_READ_ONLY_OK_EXECUTION_STOP",
            "evidence": "Goal read-only auto advance is active; 10.17 automation boundary says execution must stop without explicit go",
            "effective_action": "allow only read-only checkpoints and dashboard updates",
            "not_allowed": "no automated what-if execution, training, tuning, threshold changes, or implementation",
        },
        {
            "decision_area": "heldout_boundary",
            "decision": "REMAIN_CLOSED_FOR_SELECTION",
            "evidence": f"heldout_used_for_selection={metrics.get('heldout_used_for_selection')}",
            "effective_action": "heldout/hard remain validation-only after a frozen candidate exists",
            "not_allowed": "no heldout/hard policy, relation, threshold, or approval selection",
        },
    ]


def _carry_forward_locks(stage_10_17: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hold in stage_10_17.get("execution_hold_decisions", []):
        rows.append(
            {
                "lock_id": f"HOLD_{hold.get('hold_point')}",
                "source": hold.get("source_lock"),
                "condition": hold.get("condition"),
                "status": hold.get("status"),
                "carry_forward_action": hold.get("required_action"),
            }
        )
    for blocked in stage_10_17.get("blocked_actions", []):
        rows.append(
            {
                "lock_id": f"BLOCK_{blocked.get('blocked_action')}",
                "source": "10.17 blocked actions",
                "condition": blocked.get("reason"),
                "status": "blocked",
                "carry_forward_action": blocked.get("allowed_after"),
            }
        )
    for automation in stage_10_17.get("automation_stop_decisions", []):
        rows.append(
            {
                "lock_id": f"AUTO_{automation.get('automation_rule')}",
                "source": "10.17 automation stop decisions",
                "condition": automation.get("reason"),
                "status": automation.get("status"),
                "carry_forward_action": automation.get("decision"),
            }
        )
    return rows


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {
            "blocked_action": "run_s3_offline_whatif",
            "reason": "10.18 selected keep-held checkpoint; no explicit S3 execution go was provided.",
            "allowed_after": "explicit user go request plus separate S3 dev/OOF execution stage",
        },
        {
            "blocked_action": "change_safety_gate_threshold_or_mode",
            "reason": "checkpoint does not score or tune S3 candidate policies.",
            "allowed_after": "separate implementation proposal after future evidence, if ever reached",
        },
        {
            "blocked_action": "expand_candidate_policy_matrix",
            "reason": "reviewed 10.14 candidate matrix remains the only future S3 scope.",
            "allowed_after": "new plan-definition review, not inside held checkpoint",
        },
        {
            "blocked_action": "enable_compatibility_switch_or_connect_online",
            "reason": "no implementation path is opened by keep-held.",
            "allowed_after": "post-validation integration readiness review, if ever reached",
        },
        {
            "blocked_action": "train_or_tune_ltr",
            "reason": "training is outside the S3 execution-held checkpoint.",
            "allowed_after": "separate explicitly authorized execution or training lane",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "heldout/hard remain validation-only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "edit_feature_whitelist_or_ranking_code",
            "reason": "feature/ranking changes are outside the held S3 execution lane.",
            "allowed_after": "separate feature/ranking proposal and leakage review",
        },
    ]


def _next_recommendations() -> list[dict[str, Any]]:
    return [
        {
            "recommendation_id": "NEXT_PARK_S3_EXECUTION_LANE",
            "priority": "P0",
            "recommendation": "Move to a read-only parking and strategy-return gate for the S3 execution lane.",
            "why": "10.17 produced NO_GO_DO_NOT_EXECUTE; continuing execution authorization loops without go adds process churn.",
        },
        {
            "recommendation_id": "NEXT_RETURN_TO_BROADER_10X_STRATEGY",
            "priority": "P1",
            "recommendation": "After parking S3 execution, re-enter broader 10.x strategy review for non-execution lanes or deferred levers.",
            "why": "Accuracy remains far from target, but execution has not been authorized.",
        },
        {
            "recommendation_id": "NEXT_KEEP_EXPLICIT_GO_AVAILABLE",
            "priority": "P2",
            "recommendation": "Keep the reviewed 10.14 plan available if the user later explicitly requests S3 dev/OOF execution.",
            "why": "The plan and gate checks passed, but execution requires a separate explicit go.",
        },
    ]


def _metrics(
    strategy_options: list[dict[str, Any]],
    checkpoint_decisions: list[dict[str, Any]],
    carry_forward_locks: list[dict[str, Any]],
    blocked_actions: list[dict[str, Any]],
    next_recommendations: list[dict[str, Any]],
    stage_10_17: dict[str, Any],
) -> dict[str, Any]:
    selected_count = sum(1 for row in strategy_options if row["status"] == "selected_default")
    metrics_10_17 = stage_10_17.get("metrics", {})
    return {
        "strategy_option_count": len(strategy_options),
        "selected_option_count": selected_count,
        "checkpoint_decision_count": len(checkpoint_decisions),
        "carry_forward_lock_count": len(carry_forward_locks),
        "blocked_action_count": len(blocked_actions),
        "next_recommendation_count": len(next_recommendations),
        "selected_default": "KEEP_S3_EXECUTION_HELD",
        "next_gate_recommendation": "PARK_S3_EXECUTION_LANE",
        "go_no_go_decision": metrics_10_17.get("go_no_go_decision"),
        "explicit_go_present": metrics_10_17.get("explicit_go_present"),
        "execution_performed": False,
        "execution_remains_held": True,
        "automation_read_only_auto_advance_active": True,
        "whatif_execution_allowed": False,
        "implementation_allowed": False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "heldout_used_for_selection": False,
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 10.18 S3 Execution-Held Strategy Checkpoint",
        "",
        "Read-only checkpoint after the 10.17 no-go decision. The selected default is to keep S3 execution held and move to a parking/strategy-return gate.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_default", metrics["selected_default"]],
                ["next_gate_recommendation", metrics["next_gate_recommendation"]],
                ["go_no_go_decision", metrics["go_no_go_decision"]],
                ["explicit_go_present", metrics["explicit_go_present"]],
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
    parser = argparse.ArgumentParser(description="Stage 10.18 S3 execution-held strategy checkpoint")
    parser.add_argument("--stage-10-17", default=str(DEFAULT_STAGE_10_17))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_10_17 = _read_json(Path(args.stage_10_17))
    strategy_options = _strategy_options(stage_10_17)
    checkpoint_decisions = _checkpoint_decisions(stage_10_17)
    carry_forward_locks = _carry_forward_locks(stage_10_17)
    blocked_actions = _blocked_actions()
    next_recommendations = _next_recommendations()
    metrics = _metrics(
        strategy_options,
        checkpoint_decisions,
        carry_forward_locks,
        blocked_actions,
        next_recommendations,
        stage_10_17,
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
        "stage": "Goal LTR v1 / stage 10.18 S3 execution-held strategy checkpoint",
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
            "stage_10_17_go_no_go": str(Path(args.stage_10_17)),
        },
        "metrics": metrics,
        "strategy_options": strategy_options,
        "checkpoint_decisions": checkpoint_decisions,
        "carry_forward_locks": carry_forward_locks,
        "blocked_actions": blocked_actions,
        "next_recommendations": next_recommendations,
        "decision": (
            "Select KEEP_S3_EXECUTION_HELD as the default checkpoint decision. S3 execution remains held because 10.17 recorded "
            "NO_GO_DO_NOT_EXECUTE and no explicit execution request was provided. The recommended next read-only gate is to park the S3 execution lane "
            "and return to broader 10.x strategy review, while preserving the reviewed 10.14 plan for any future explicit go."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 10.18 only checkpoints the held S3 execution lane. It does not run S3 what-if, train, tune, change thresholds, patch rules, change ranking, "
            "modify GoalSearcher, edit the feature whitelist, use heldout/hard for selection, relax gates, enable a switch, or connect online."
        ),
        "next_stage": {
            "stage": "10.19 S3 execution lane parking and strategy-return gate",
            "goal": (
                "Read-only decide whether to formally park the S3 execution lane and return to broader 10.x strategy review, or ask the user for explicit execution go."
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

    _write_csv(Path(artifacts["strategy_options_csv"]), strategy_options, ["option_id", "status", "basis", "action", "requires", "risk"])
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
    _write_csv(Path(artifacts["next_recommendations_csv"]), next_recommendations, ["recommendation_id", "priority", "recommendation", "why"])
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
