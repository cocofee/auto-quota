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
DEFAULT_PLAN = AGENT_STATE / "goal_12x_numeric_spec_tier_minimal_plan_definition_summary.json"
DEFAULT_COMMAND = AGENT_STATE / "goal_12x_numeric_spec_tier_minimal_plan_definition_command_contract.csv"
DEFAULT_LOSS = AGENT_STATE / "goal_12x_numeric_spec_tier_minimal_plan_definition_loss_budget.csv"
DEFAULT_STOPS = AGENT_STATE / "goal_12x_numeric_spec_tier_minimal_plan_definition_stop_conditions.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_numeric_spec_tier_whatif_authorization_gate"
REQUIRED_GO_TEXT = "go: run 12.6 dev/OOF-only numeric/spec tier what-if"


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
        "# 12.5 Numeric/Spec Tier What-if Authorization Gate",
        "",
        "Read-only authorization gate for a future dev/OOF-only what-if.",
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
    current = (
        "当前状态：12.5 numeric/spec tier offline what-if authorization gate 已完成。"
        f"authorization_decision={report['metrics']['authorization_decision']}；"
        f"explicit_go_present={str(report['metrics']['explicit_go_present']).lower()}；"
        f"execution_allowed_now={str(report['metrics']['execution_allowed_now']).lower()}；"
        f"plan_rows={report['metrics']['plan_rows']}。"
    )
    next_text = (
        "下一步：默认 hold / do_not_execute。只有用户明确说 go: run 12.6 dev/OOF-only numeric/spec tier what-if，"
        "才允许进入 12.6 dev/OOF-only what-if execution。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：无明确 go 执行 what-if、直接实现、训练、调参、改阈值、改 GoalSearcher、"
            "使用 heldout/hard 做选择、或放宽 12.4 的 same-family numeric/spec guards。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.4 numeric/spec tier minimal plan definition</td>"
    row = (
        "          <tr>\n"
        "            <td>12.5 numeric/spec tier what-if authorization gate</td>\n"
        "            <td>只读判断是否授权 dev/OOF-only what-if；默认无明确 go 不执行。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_numeric_spec_tier_whatif_authorization_gate_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_numeric_spec_tier_whatif_authorization_gate_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--explicit-go", action="store_true")
    parser.add_argument("--plan-summary", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--command-contract", type=Path, default=DEFAULT_COMMAND)
    parser.add_argument("--loss-budget", type=Path, default=DEFAULT_LOSS)
    parser.add_argument("--stop-conditions", type=Path, default=DEFAULT_STOPS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    plan = _read_json(args.plan_summary)
    command_contract_source = _read_csv(args.command_contract)
    loss_budget_source = _read_csv(args.loss_budget)
    stop_conditions_source = _read_csv(args.stop_conditions)
    pm = plan["metrics"]
    explicit_go = bool(args.explicit_go)
    execution_allowed_now = explicit_go and bool(pm["explicit_go_required_for_whatif"])
    authorization_decision = "authorize_12_6_dev_oof_whatif" if execution_allowed_now else "hold_do_not_execute_request_explicit_go"

    authorization_checks = [
        {"check": "plan_exists", "status": "pass", "evidence": str(args.plan_summary)},
        {"check": "plan_rows", "status": "pass" if int(pm["plan_rows"]) == 9 else "fail", "evidence": str(pm["plan_rows"])},
        {"check": "implementation_allowed_now", "status": "pass" if not pm["implementation_allowed_now"] else "fail", "evidence": str(pm["implementation_allowed_now"])},
        {"check": "future_whatif_requires_go", "status": "pass" if pm["explicit_go_required_for_whatif"] else "fail", "evidence": str(pm["explicit_go_required_for_whatif"])},
        {"check": "explicit_go_present", "status": "pass" if explicit_go else "missing", "evidence": str(explicit_go)},
        {"check": "heldout_hard_boundary", "status": "pass", "evidence": "dev/OOF-only; no heldout/hard selection"},
    ]
    execution_request = [
        {
            "required_text": REQUIRED_GO_TEXT,
            "meaning": "Allow a dev/OOF-only what-if for the guarded same-family numeric/spec tier comparator plan.",
            "default_without_text": "do_not_execute",
        }
    ]
    command_boundary = [
        {
            "phase": "12.5_current",
            "allowed": False,
            "command": "no execution in 12.5",
            "outputs": "authorization package only",
        },
        {
            "phase": "12.6_after_explicit_go",
            "allowed": execution_allowed_now,
            "command": "python tools/goal_12x_numeric_spec_tier_whatif.py --split dev --output-prefix reports/agent_state/goal_12x_numeric_spec_tier_whatif",
            "outputs": "summary, row details, scorecard, guard coverage, loss audit, source/province/family slices, rollback report",
        },
    ]
    approval_criteria = [
        {"criterion": "dev_oof_only", "required": "No heldout/hard use for selection."},
        {"criterion": "guards_preserved", "required": "same-family, comparable param type, candidate rank_2_5, no family conflict, fallback no-op."},
        {"criterion": "loss_budget", "required": "new_loss_count must be 0 preferred; any unexplained loss stops implementation."},
        {"criterion": "source_robustness", "required": "max source gain share <= 0.5 before any future claim."},
        {"criterion": "artifact_complete", "required": "scorecard, row details, guard coverage, loss audit, rollback report all present."},
    ]
    blocked_actions = [
        {"action": "execute_whatif_without_go", "blocked": not explicit_go, "reason": "12.5 default is do_not_execute"},
        {"action": "implement_numeric_spec_comparator", "blocked": True, "reason": "12.5 only authorizes future what-if after explicit go, not implementation"},
        {"action": "train_or_tune", "blocked": True, "reason": "outside numeric/spec what-if boundary"},
        {"action": "change_thresholds", "blocked": True, "reason": "no threshold plan"},
        {"action": "use_heldout_hard_for_selection", "blocked": True, "reason": "split policy forbids it"},
        {"action": "wire_goal_searcher", "blocked": True, "reason": "requires separate integration gate"},
    ]
    metrics = {
        "authorization_decision": authorization_decision,
        "explicit_go_present": explicit_go,
        "execution_allowed_now": execution_allowed_now,
        "plan_rows": int(pm["plan_rows"]),
        "param_types": int(pm["param_types"]),
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "heldout_hard_allowed_for_selection": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "authorization_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_authorization_checks.csv")),
        "execution_request_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_execution_request.csv")),
        "command_boundary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_boundary.csv")),
        "approval_criteria_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_approval_criteria.csv")),
        "loss_budget_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_budget_review.csv")),
        "stop_conditions_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions_review.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    decision = (
        "Authorize 12.6 dev/OOF-only what-if under the 12.4 guarded numeric/spec tier plan."
        if execution_allowed_now
        else "Hold execution and request explicit go. 12.5 does not execute the what-if because the required go text is absent."
    )
    report = {
        "stage": "Goal LTR v1 / 12.5 numeric/spec tier offline what-if authorization gate",
        "read_only": True,
        "source_artifacts": {
            "plan_summary": str(args.plan_summary),
            "command_contract": str(args.command_contract),
            "loss_budget": str(args.loss_budget),
            "stop_conditions": str(args.stop_conditions),
        },
        "metrics": metrics,
        "decision": decision,
        "required_user_text": REQUIRED_GO_TEXT,
        "source_contract_snapshot": {
            "command_contract_rows": len(command_contract_source),
            "loss_budget_rows": len(loss_budget_source),
            "stop_condition_rows": len(stop_conditions_source),
        },
        "anti_drift_conclusion": (
            "12.5 is read-only. It does not execute what-if, implement, train, tune, change thresholds, edit taxonomy rows, "
            "edit feature whitelists, reopen 11.x, wire GoalSearcher, or use heldout/hard for selection."
        ),
        "next_stage": {
            "stage": "12.6 dev/OOF-only numeric/spec tier what-if",
            "default": "do_not_execute_without_explicit_go",
            "required_user_text": REQUIRED_GO_TEXT,
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["authorization_checks_csv"]), authorization_checks, list(authorization_checks[0].keys()))
    _write_csv(Path(artifacts["execution_request_csv"]), execution_request, list(execution_request[0].keys()))
    _write_csv(Path(artifacts["command_boundary_csv"]), command_boundary, list(command_boundary[0].keys()))
    _write_csv(Path(artifacts["approval_criteria_csv"]), approval_criteria, list(approval_criteria[0].keys()))
    _write_csv(Path(artifacts["loss_budget_review_csv"]), loss_budget_source, list(loss_budget_source[0].keys()))
    _write_csv(Path(artifacts["stop_conditions_review_csv"]), stop_conditions_source, list(stop_conditions_source[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
