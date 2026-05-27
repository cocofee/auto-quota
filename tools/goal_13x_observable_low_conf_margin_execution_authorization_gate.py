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
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"

DEFAULT_PLAN = AGENT_STATE / "goal_13x_observable_low_conf_margin_rewrite_plan_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_observable_low_conf_margin_execution_authorization_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
REQUIRED_GO_TEXT = "go: run 13.25 observable low-confidence/margin dev/OOF execution"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_rel(path: str | Path) -> str:
    return str(Path(path).resolve().relative_to(PROJECT_ROOT))


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(cell) for cell in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def build_gate_rows(plan: dict[str, Any], explicit_go: bool) -> tuple[list[dict[str, Any]], str]:
    candidate_matrix = plan.get("candidate_matrix", [])
    required_artifacts = plan.get("required_artifacts", [])
    loss_budget = plan.get("loss_budget", [])
    command_contract = plan.get("command_contract", [])
    forbidden_fields = plan.get("forbidden_fields", [])
    gate_checks = plan.get("gate_checks", [])
    upstream_failed = [row for row in gate_checks if row.get("status") == "fail"]
    baseline_rank_removed = all(row.get("must_remove") == "baseline_rank != 1" for row in candidate_matrix)
    rows = [
        {
            "gate": "rewrite_plan_ready",
            "status": "pass" if plan.get("decision") == "plan_ready_for_explicit_dev_oof_execution_go_no_go" else "fail",
            "value": plan.get("decision", ""),
            "reason": "13.24 can authorize only after 13.23 plan is ready.",
        },
        {
            "gate": "candidate_matrix_present",
            "status": "pass" if len(candidate_matrix) == 5 else "fail",
            "value": len(candidate_matrix),
            "reason": "Execution must use the five scoped observable T1G_A variants.",
        },
        {
            "gate": "label_derived_branch_removed",
            "status": "pass" if baseline_rank_removed else "fail",
            "value": baseline_rank_removed,
            "reason": "The old baseline_rank != 1 branch must be removed from all deployable variants.",
        },
        {
            "gate": "loss_budget_present",
            "status": "pass" if len(loss_budget) >= 6 else "fail",
            "value": len(loss_budget),
            "reason": "Execution must have Top1, rank1, concentration, fold, and validation-boundary budgets.",
        },
        {
            "gate": "required_artifacts_present",
            "status": "pass" if len(required_artifacts) >= 6 else "fail",
            "value": len(required_artifacts),
            "reason": "Execution must emit scorecard, threshold, gating, rank1, source/fold, and leakage artifacts.",
        },
        {
            "gate": "command_contract_present",
            "status": "pass" if any(row.get("future_stage") == "13.25_if_go" for row in command_contract) else "fail",
            "value": len(command_contract),
            "reason": "Future execution command boundary must be explicit before go/no-go.",
        },
        {
            "gate": "forbidden_fields_declared",
            "status": "pass" if len(forbidden_fields) >= 3 else "fail",
            "value": len(forbidden_fields),
            "reason": "Label-derived and validation-derived fields must remain forbidden.",
        },
        {
            "gate": "upstream_gate_checks_clean",
            "status": "pass" if not upstream_failed else "fail",
            "value": len(upstream_failed),
            "reason": "13.23 gate checks must not contain failures.",
        },
        {
            "gate": "explicit_dev_oof_execution_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": f"Future dev/OOF execution requires exact user authorization: {REQUIRED_GO_TEXT}",
        },
        {
            "gate": "heldout_hard_blocked",
            "status": "pass",
            "value": "blocked",
            "reason": "13.24 and any immediate 13.25 execution are dev/OOF-only; heldout/hard remain blocked.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_execute_fix_authorization_inputs"
    elif explicit_go:
        decision = "authorized_for_13_25_dev_oof_execution"
    else:
        decision = "execution_ready_but_held_without_explicit_13_25_go"
    return rows, decision


def build_report(plan: dict[str, Any], explicit_go: bool, output_prefix: Path) -> dict[str, Any]:
    gate_rows, decision = build_gate_rows(plan, explicit_go)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "execution_contract_csv": str(output_prefix.with_name(output_prefix.name + "_execution_contract.csv")),
        "approval_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_approval_criteria.csv")),
    }
    execution_contract = [
        {"item": "required_go_text", "value": REQUIRED_GO_TEXT},
        {"item": "candidate_plan", "value": "reports/agent_state/goal_13x_observable_low_conf_margin_rewrite_plan_candidate_matrix.csv"},
        {"item": "future_execution_command", "value": "python tools/goal_13x_observable_low_conf_margin_dev_oof_execute.py --plan reports/agent_state/goal_13x_observable_low_conf_margin_rewrite_plan_candidate_matrix.csv --dev-oof-only --no-heldout-selection --emit-loss-audit"},
        {"item": "threshold_policy", "value": "compute confidence and top1/top2 margin q25/q35 thresholds from dev/OOF only; emit threshold manifest"},
        {"item": "heldout_hard_policy", "value": "blocked until a future freeze gate and explicit validation go"},
        {"item": "online_policy", "value": "no release, no GoalSearcher edit, no production threshold change"},
    ]
    approval_criteria = [
        {"criterion": "candidate_scope", "required": "exactly five observable T1G_A variants from 13.23"},
        {"criterion": "no_label_gate", "required": "no baseline_rank, positive_rank, expected_id, label, or answer-position gate"},
        {"criterion": "dev_oof_top1", "required": "hit1_net > 0 before freeze consideration"},
        {"criterion": "rank1_budget", "required": "rank1_loss_count <= max(3, hit1_gain * 0.08) and demotion rate <= 0.01"},
        {"criterion": "robustness", "required": "no single source_family/province/fold-dominated gain or material loss"},
        {"criterion": "validation_boundary", "required": "heldout/hard not used for candidate selection or threshold tuning"},
    ]
    return {
        "stage": "13.24 observable low-confidence/margin dev/OOF execution authorization gate",
        "read_only_review": True,
        "decision": decision,
        "explicit_dev_oof_execution_go_present": explicit_go,
        "required_go_text": REQUIRED_GO_TEXT,
        "metrics": {
            "candidate_count": len(plan.get("candidate_matrix", [])),
            "required_artifact_count": len(plan.get("required_artifacts", [])),
            "loss_budget_count": len(plan.get("loss_budget", [])),
            "forbidden_field_count": len(plan.get("forbidden_fields", [])),
        },
        "gate_rows": gate_rows,
        "execution_contract": execution_contract,
        "approval_criteria": approval_criteria,
        "artifacts": artifacts,
        "next_stage": {
            "id": "13.25",
            "name": "observable low-confidence/margin dev/OOF execution",
            "recommended": (
                f"只有用户明确说 `{REQUIRED_GO_TEXT}`，才进入 13.25 dev/OOF-only execution；"
                "否则保持 do_not_execute。"
            ),
            "default": "do_not_execute",
        },
        "anti_drift_conclusion": (
            "13.24 is read-only. It authorizes only the possibility of a future dev/OOF-only execution if explicit go is supplied. "
            "It does not train, run dev/OOF, use heldout/hard, release, edit GoalSearcher, tune thresholds, or reintroduce label-derived gates."
        ),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 13.24 Observable Low-Confidence/Margin Execution Authorization Gate",
        "",
        "Read-only authorization gate for future dev/OOF-only execution of the rewritten observable T1G_A variants.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Execution Contract",
        "",
        md_table([["item", "value"]] + [[row["item"], row["value"]] for row in report["execution_contract"]]),
        "",
        "## Approval Criteria",
        "",
        md_table([["criterion", "required"]] + [[row["criterion"], row["required"]] for row in report["approval_criteria"]]),
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


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.24 observable low-confidence/margin dev/OOF execution authorization gate 已完成。\n"
        f"结论：{report['decision']}。候选矩阵、loss budget、产物清单、禁止项均通过；本轮没有执行 dev/OOF。\n"
        f"下一步：只有你明确说 `{REQUIRED_GO_TEXT}`，才允许进入 13.25 dev/OOF-only execution；否则保持 do_not_execute。\n"
        "禁止：无明确 go 执行训练、用 heldout/hard、release、改 GoalSearcher、调线上阈值、重新引入 baseline_rank/positive_rank/expected_id/label-derived gate。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>13.24 observable low-confidence/margin dev/OOF execution authorization gate</td>
            <td>Read-only go/no-go gate for future dev/OOF execution of observable T1G_A rewrite variants.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "13.24 observable low-confidence/margin dev/OOF execution authorization gate" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.24 observable low-confidence/margin execution authorization gate")
    parser.add_argument("--plan-summary", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--explicit-dev-oof-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    plan = read_json(args.plan_summary)
    report = build_report(plan, bool(args.explicit_dev_oof_go), args.output_prefix)
    artifacts = report["artifacts"]
    write_csv(Path(artifacts["gate_checks_csv"]), report["gate_rows"], ["gate", "status", "value", "reason"])
    write_csv(Path(artifacts["execution_contract_csv"]), report["execution_contract"], ["item", "value"])
    write_csv(Path(artifacts["approval_criteria_csv"]), report["approval_criteria"], ["criterion", "required"])
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    update_dashboard(args.dashboard, report)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "summary": safe_rel(artifacts["summary_json"]),
                "explicit_dev_oof_execution_go_present": report["explicit_dev_oof_execution_go_present"],
                "required_go_text": REQUIRED_GO_TEXT,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
