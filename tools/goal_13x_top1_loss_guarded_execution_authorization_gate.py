from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_PLAN = AGENT_STATE / "goal_13x_top1_loss_guarded_experiment_plan_definition_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_top1_loss_guarded_execution_authorization_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
REQUIRED_GO_TEXT = "go: run 13.18 Top1-loss-guarded dev/OOF execution"


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _gate_rows(plan: dict[str, Any], explicit_go: bool) -> tuple[list[dict[str, Any]], str]:
    required_artifacts = plan.get("artifact_rows", [])
    stop_rows = plan.get("stop_rows", [])
    rows = [
        {
            "gate": "plan_ready",
            "status": "pass" if plan.get("decision") == "execution_plan_ready_waiting_for_explicit_dev_oof_go" else "fail",
            "value": plan.get("decision"),
            "reason": "13.18 can authorize execution only after 13.17 plan is ready.",
        },
        {
            "gate": "candidate_matrix_bounded",
            "status": "pass" if len(plan.get("candidate_rows", [])) == 6 else "fail",
            "value": len(plan.get("candidate_rows", [])),
            "reason": "Execution must use the bounded 6-candidate matrix.",
        },
        {
            "gate": "artifact_manifest_present",
            "status": "pass" if len(required_artifacts) >= 8 else "fail",
            "value": len(required_artifacts),
            "reason": "Execution must produce the required audit artifacts.",
        },
        {
            "gate": "stop_conditions_present",
            "status": "pass" if len(stop_rows) >= 6 else "fail",
            "value": len(stop_rows),
            "reason": "Execution must have explicit stop conditions.",
        },
        {
            "gate": "explicit_execution_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": f"Execution requires exact user authorization: {REQUIRED_GO_TEXT}",
        },
        {
            "gate": "heldout_hard_blocked",
            "status": "pass",
            "value": "blocked",
            "reason": "13.18 execution authorization covers dev/OOF only; heldout/hard remain blocked.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_execute_fix_plan_inputs"
    elif explicit_go:
        decision = "authorized_for_dev_oof_execution"
    else:
        decision = "execution_ready_but_held_without_explicit_go"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 13.18 Top1-Loss-Guarded Dev/OOF Execution Authorization Gate",
        "",
        "Read-only authorization gate for the 13.17 Top1-loss-guarded dev/OOF execution plan. This stage does not train unless explicit execution go is present.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Execution Contract",
        "",
        _md_table([["item", "value"]] + [[row["item"], row["value"]] for row in report["execution_contract"]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.18 Top1-loss-guarded dev/OOF execution authorization gate 已完成。\n"
        f"结论：{report['decision']}。explicit_execution_go_present={report['metrics']['explicit_execution_go_present']}；默认仍 do_not_execute。\n"
        f"下一步：只有你明确说 `{REQUIRED_GO_TEXT}`，才允许进入 dev/OOF 执行；否则保持 held。\n"
        "禁止：无明确 go 训练、跑 heldout/hard、上线、改 GoalSearcher、改阈值、全局重排 top80。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.18 Top1-loss-guarded dev/OOF execution authorization gate" not in text:
        rows = f"""          <tr>
            <td>13.18 Top1-loss-guarded dev/OOF execution authorization gate</td>
            <td>Read-only authorization gate for bounded guarded reranker dev/OOF execution.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.17 Top1-loss-guarded dev/OOF experiment plan definition</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.18 Top1-loss-guarded dev/OOF execution authorization gate")
    parser.add_argument("--plan-summary", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--explicit-execution-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    plan = _read_json(args.plan_summary)
    explicit_go = bool(args.explicit_execution_go)
    gate_rows, decision = _gate_rows(plan, explicit_go)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "execution_contract_csv": str(output_prefix.with_name(output_prefix.name + "_execution_contract.csv")),
    }
    execution_contract = [
        {"item": "required_go_text", "value": REQUIRED_GO_TEXT},
        {"item": "candidate_plan", "value": "reports/agent_state/goal_13x_top1_loss_guarded_experiment_plan_definition_candidate_matrix.csv"},
        {"item": "execution_command", "value": "python tools/goal_13x_top1_loss_guarded_dev_oof_execute.py --data-dir reports/agent_state/goal_13x_oss_xml_source_aware_training_matrix_expanded --candidate-plan reports/agent_state/goal_13x_top1_loss_guarded_experiment_plan_definition_candidate_matrix.csv --dev-oof-only --no-heldout-selection --emit-loss-audit"},
        {"item": "heldout_hard_policy", "value": "blocked in this execution stage"},
        {"item": "online_policy", "value": "no GoalSearcher edit, no threshold change, no release"},
    ]
    report = {
        "stage": "13.18 Top1-loss-guarded dev/OOF execution authorization gate",
        "read_only": True,
        "decision": decision,
        "metrics": {
            "explicit_execution_go_present": explicit_go,
            "candidate_count": len(plan.get("candidate_rows", [])),
            "artifact_count": len(plan.get("artifact_rows", [])),
            "stop_condition_count": len(plan.get("stop_rows", [])),
        },
        "gate_rows": gate_rows,
        "execution_contract": execution_contract,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only authorization gate only: no training, no heldout/hard validation, no online integration, no threshold change, and no GoalSearcher edit.",
        "next_stage": {
            "recommended": f"If and only if the user says `{REQUIRED_GO_TEXT}`, run the bounded dev/OOF execution. Otherwise keep do_not_execute.",
            "default": "do_not_execute",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_csv(Path(artifacts["execution_contract_csv"]), execution_contract, ["item", "value"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "explicit_execution_go_present": explicit_go, "required_go_text": REQUIRED_GO_TEXT}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
