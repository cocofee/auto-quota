from __future__ import annotations

import argparse
import csv
import json
import re
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
DEFAULT_BROADER_REVIEW = AGENT_STATE / "goal_12x_broader_strategy_review_after_electrical_box_parking_summary.json"
DEFAULT_DEFAULT_OPTIONS = AGENT_STATE / "goal_12x_broader_strategy_review_after_electrical_box_parking_default_options.csv"
DEFAULT_LANE_STATUS = AGENT_STATE / "goal_12x_broader_strategy_review_after_electrical_box_parking_lane_status.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_no_active_lane_pause_explicit_go_intake_gate"


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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 12.19 No-Active-Lane Pause / Explicit-Go Intake Gate",
        "",
        "Read-only intake gate after broader 12.x review found no active read-only lane.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    return "\n".join(lines) + "\n"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    current = (
        "当前状态：12.19 12.x no-active-lane pause / explicit-go intake gate 已完成。"
        f"default_decision={metrics['default_decision']}；"
        f"active_read_only_lane_count={metrics['active_read_only_lane_count']}；"
        f"recommended_explicit_go_route={metrics['recommended_explicit_go_route']}；"
        f"training_allowed_now={str(metrics['training_allowed_now']).lower()}；"
        f"implementation_allowed_now={str(metrics['implementation_allowed_now']).lower()}。"
    )
    next_text = (
        "下一步：暂停等待输入。若要继续实质提准，建议明确发送："
        "go: 进入 12C offline training/objective authorization gate。"
        "该 go 仍只会先打开训练/目标函数授权与计划边界，不会直接上线、不改 GoalSearcher、不用 heldout/hard 做选择。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：无 explicit go 时继续自动推进 12.x、自动训练、自动集成 GoalSearcher、自动实现规则、调参、改阈值、"
            "使用 heldout/hard 做选择、重开 parked 12A 子路线，或把 paused 状态说成算法收益。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.18 broader 12.x strategy review after electrical-box parking</td>"
    row = (
        "          <tr>\n"
        "            <td>12.19 no-active-lane pause / explicit-go intake gate</td>\n"
        "            <td>只读确认 12.x 默认暂停；若继续实质提准，需要用户明确进入 12C/12B/DQ 新 gate。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_no_active_lane_pause_explicit_go_intake_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_no_active_lane_pause_explicit_go_intake_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broader-review-summary", type=Path, default=DEFAULT_BROADER_REVIEW)
    parser.add_argument("--default-options", type=Path, default=DEFAULT_DEFAULT_OPTIONS)
    parser.add_argument("--lane-status", type=Path, default=DEFAULT_LANE_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    broader_review = _read_json(args.broader_review_summary)
    default_options = _read_csv(args.default_options)
    lane_status = _read_csv(args.lane_status)

    explicit_go_intake = [
        {
            "route": "12C_offline_training_objective_authorization_gate",
            "recommended": True,
            "go_phrase": "go: 进入 12C offline training/objective authorization gate",
            "what_it_allows_next": "Read-only/authorization definition of training objective scope, split policy, leakage gates, loss audit, and stop conditions.",
            "what_it_does_not_allow": "No immediate training, no online change, no GoalSearcher wiring, no heldout/hard selection.",
        },
        {
            "route": "12B_goal_searcher_integration_gate",
            "recommended": False,
            "go_phrase": "go: 进入 12B GoalSearcher integration gate",
            "what_it_allows_next": "Read-only/authorization definition for integrating already released 11.x scoped hints into GoalSearcher.",
            "what_it_does_not_allow": "No new learning claim, no broad Top1 claim, no unchecked online release.",
        },
        {
            "route": "12D_DQ_owner_mapping_route",
            "recommended": False,
            "go_phrase": "go: 进入 12D DQ/owner mapping route，并提供 accepted mappings/provenance package",
            "what_it_allows_next": "Review of accepted owner mappings/provenance for data-quality implementation gates.",
            "what_it_does_not_allow": "No DQ implementation without exact mappings, rollback, and validation boundary.",
        },
    ]
    pause_conditions = [
        {
            "condition": "no_explicit_go",
            "decision": "pause",
            "detail": "No active read-only 12A sublane remains; do not auto-advance.",
        },
        {
            "condition": "new_evidence_for_parked_12A",
            "decision": "reentry_review_only",
            "detail": "New evidence must satisfy lane-specific reentry requirements before any what-if or implementation.",
        },
        {
            "condition": "explicit_12C_go",
            "decision": "open_authorization_gate",
            "detail": "Start 12C offline training/objective authorization gate, not immediate training.",
        },
        {
            "condition": "explicit_12B_go",
            "decision": "open_integration_gate",
            "detail": "Start GoalSearcher integration gate, not immediate online release.",
        },
    ]
    lane_rollup = [
        {
            "lane_id": row.get("lane_id", ""),
            "status": row.get("status", ""),
            "intake_decision": (
                "pause_or_new_evidence_required"
                if row.get("lane_id") == "12A_candidate_pool_rank_position_loss_decomposition"
                else "explicit_go_required"
            ),
        }
        for row in lane_status
    ]
    forbidden_actions = [
        {"action": "auto_continue_12x", "status": "forbidden", "reason": "no active read-only lane remains"},
        {"action": "auto_train_or_tune", "status": "forbidden", "reason": "requires explicit 12C go"},
        {"action": "auto_wire_goal_searcher", "status": "forbidden", "reason": "requires explicit 12B go"},
        {"action": "auto_implement_rules", "status": "forbidden", "reason": "no implementation-ready lane"},
        {"action": "use_heldout_hard_for_selection", "status": "forbidden", "reason": "requires separate validation gate"},
    ]
    metrics = {
        "default_decision": "pause_12x_until_new_evidence_or_explicit_go",
        "active_read_only_lane_count": 0,
        "explicit_go_routes": len(explicit_go_intake),
        "recommended_explicit_go_route": "12C_offline_training_objective_authorization_gate",
        "training_allowed_now": False,
        "implementation_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "heldout_hard_selection_allowed_now": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "explicit_go_intake_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_explicit_go_intake.csv")),
        "pause_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_pause_conditions.csv")),
        "lane_rollup_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_lane_rollup.csv")),
        "forbidden_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_forbidden_actions.csv")),
    }
    decision = (
        "Pause 12.x by default. If the user wants continued substantive accuracy work, the recommended next explicit-go route is "
        "12C offline training/objective authorization gate. This gate does not itself authorize training or implementation."
    )
    report = {
        "stage": "Goal LTR v1 / 12.19 no-active-lane pause / explicit-go intake gate",
        "read_only": True,
        "source_artifacts": {
            "broader_review_summary": str(args.broader_review_summary),
            "default_options": str(args.default_options),
            "lane_status": str(args.lane_status),
        },
        "metrics": metrics,
        "decision": decision,
        "prior_stage_context": {
            "prior_stage": broader_review["stage"],
            "prior_recommended_default": broader_review["metrics"]["recommended_default"],
            "prior_explicit_go_routes": broader_review["metrics"]["explicit_go_routes"],
        },
        "anti_drift_conclusion": (
            "12.19 is read-only. It pauses 12.x unless the user provides explicit go. It does not train, tune, change thresholds, "
            "implement rules, wire GoalSearcher, run what-if, use heldout/hard for selection, reopen parked 12A lanes, or claim accuracy gain."
        ),
        "next_stage": {
            "stage": "pause awaiting explicit go or new evidence",
            "recommended_if_user_wants_progress": "12C offline training/objective authorization gate",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _write_csv(
        Path(artifacts["explicit_go_intake_csv"]),
        explicit_go_intake,
        ["route", "recommended", "go_phrase", "what_it_allows_next", "what_it_does_not_allow"],
    )
    _write_csv(Path(artifacts["pause_conditions_csv"]), pause_conditions, ["condition", "decision", "detail"])
    _write_csv(Path(artifacts["lane_rollup_csv"]), lane_rollup, ["lane_id", "status", "intake_decision"])
    _write_csv(Path(artifacts["forbidden_actions_csv"]), forbidden_actions, ["action", "status", "reason"])
    _update_dashboard(args.dashboard, report)

    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
