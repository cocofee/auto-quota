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

DEFAULT_PLAN = AGENT_STATE / "goal_14x_rank1_safe_source_robust_experiment_plan_definition_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix_build_authorization_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OSS_ROOT = Path("D:/广联达临时文件/oss_samples")
REQUIRED_GO_TEXT = "go: run 14.2 rank1-safe source-robust balanced OSS matrix build"

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


def build_gate_rows(plan: dict[str, Any], oss_root: Path, explicit_go: bool) -> tuple[list[dict[str, Any]], str]:
    command_rows = plan.get("command_contract", [])
    required_artifacts = plan.get("required_artifacts", [])
    stop_conditions = plan.get("stop_conditions", [])
    plan_gates = plan.get("gate_checks", [])
    build_command = next((row for row in command_rows if row.get("stage") == "14.2_if_explicit_go"), {})
    failed_plan_gates = [row for row in plan_gates if row.get("status") == "fail"]
    artifact_names = {row.get("artifact") for row in required_artifacts if row.get("required_at") == "14.2"}
    rows = [
        {
            "gate": "plan_ready",
            "status": "pass" if plan.get("decision") == "plan_ready_for_explicit_14_2_matrix_build_go_no_go" else "fail",
            "value": plan.get("decision", ""),
            "reason": "14.2 can authorize only after 14.1 plan is ready.",
        },
        {
            "gate": "oss_root_available",
            "status": "pass" if oss_root.exists() else "fail",
            "value": str(oss_root),
            "reason": "OSS source root must exist before matrix rebuild can be authorized.",
        },
        {
            "gate": "build_command_present",
            "status": "pass" if build_command else "fail",
            "value": build_command.get("command", ""),
            "reason": "14.2 command boundary must be explicit.",
        },
        {
            "gate": "matrix_artifacts_required",
            "status": "pass" if {"balanced_matrix_manifest", "feature_contract_report"}.issubset(artifact_names) else "fail",
            "value": "|".join(sorted(artifact_names)),
            "reason": "Matrix rebuild must emit balance and feature-safety artifacts.",
        },
        {
            "gate": "stop_conditions_present",
            "status": "pass" if len(stop_conditions) >= 8 else "fail",
            "value": len(stop_conditions),
            "reason": "Build must stop on source imbalance, fold leakage, label gates, or premature heldout/hard use.",
        },
        {
            "gate": "upstream_gate_checks_clean",
            "status": "pass" if not failed_plan_gates else "fail",
            "value": len(failed_plan_gates),
            "reason": "14.1 gate checks must not contain failures.",
        },
        {
            "gate": "training_blocked_in_14_2",
            "status": "pass" if "training" in build_command.get("forbidden", "") else "fail",
            "value": build_command.get("forbidden", ""),
            "reason": "14.2 may build matrix only; training stays for a later explicit go.",
        },
        {
            "gate": "heldout_hard_blocked",
            "status": "pass" if "heldout/hard" in build_command.get("forbidden", "") else "fail",
            "value": build_command.get("forbidden", ""),
            "reason": "14.2 must not read or select on heldout/hard.",
        },
        {
            "gate": "explicit_matrix_build_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": f"Matrix rebuild requires exact user authorization: {REQUIRED_GO_TEXT}",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_build_fix_authorization_inputs"
    elif explicit_go:
        decision = "authorized_for_14_2_balanced_matrix_build"
    else:
        decision = "matrix_build_ready_but_held_without_explicit_go"
    return rows, decision


def build_report(plan: dict[str, Any], oss_root: Path, explicit_go: bool, output_prefix: Path) -> dict[str, Any]:
    gate_rows, decision = build_gate_rows(plan, oss_root, explicit_go)
    build_command = next((row for row in plan.get("command_contract", []) if row.get("stage") == "14.2_if_explicit_go"), {})
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "build_contract_csv": str(output_prefix.with_name(output_prefix.name + "_build_contract.csv")),
        "approval_criteria_csv": str(output_prefix.with_name(output_prefix.name + "_approval_criteria.csv")),
    }
    build_contract = [
        {"item": "required_go_text", "value": REQUIRED_GO_TEXT},
        {"item": "oss_root", "value": str(oss_root)},
        {"item": "output_dir", "value": "reports/agent_state/goal_14x_rank1_safe_source_robust_matrix"},
        {"item": "source_family_cap", "value": "0.22 preferred; hard stop if post-build share >0.25"},
        {"item": "future_build_command", "value": build_command.get("command", "")},
        {"item": "forbidden", "value": "training; heldout/hard; online changes; GoalSearcher edits"},
    ]
    approval_criteria = [
        {"criterion": "build_scope", "required": "matrix/manifests only; no training"},
        {"criterion": "source_balance", "required": "source_family share <=0.22 preferred, hard stop >0.25"},
        {"criterion": "fold_safety", "required": "same source_file cannot cross OOF folds"},
        {"criterion": "feature_contract", "required": "strong challenger fields emitted and forbidden leakage fields excluded"},
        {"criterion": "taxonomy_empty", "required": "taxonomy-empty slice manifest emitted; cannot drive freeze alone"},
        {"criterion": "validation_boundary", "required": "heldout/hard not read, not selected, not tuned"},
    ]
    return {
        "stage": "14.2 rank1-safe source-robust balanced OSS matrix build authorization",
        "read_only_review": True,
        "decision": decision,
        "required_go_text": REQUIRED_GO_TEXT,
        "explicit_matrix_build_go_present": explicit_go,
        "metrics": {
            "oss_root_exists": oss_root.exists(),
            "command_contract_count": len(plan.get("command_contract", [])),
            "required_artifact_count": len(plan.get("required_artifacts", [])),
            "stop_condition_count": len(plan.get("stop_conditions", [])),
        },
        "gate_rows": gate_rows,
        "build_contract": build_contract,
        "approval_criteria": approval_criteria,
        "artifacts": artifacts,
        "next_stage": {
            "id": "14.2_build",
            "name": "balanced OSS dev/OOF matrix build",
            "recommended": f"If and only if the user says `{REQUIRED_GO_TEXT}`, run the balanced matrix build. Otherwise keep do_not_build.",
            "default": "do_not_build",
        },
        "anti_drift_conclusion": (
            "14.2 is read-only authorization only. It does not build matrices, train, run heldout/hard, release, edit GoalSearcher, or tune thresholds."
        ),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 14.2 Balanced OSS Matrix Build Authorization Gate",
        "",
        "Read-only go/no-go gate for building the rank1-safe source-robust OSS dev/OOF matrix.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Build Contract",
        "",
        md_table([["item", "value"]] + [[row["item"], row["value"]] for row in report["build_contract"]]),
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
        "当前阶段：14.2 rank1-safe source-robust balanced OSS matrix build authorization 已完成。\n"
        f"结论：{report['decision']}。OSS root exists={report['metrics']['oss_root_exists']}；本轮没有重建矩阵、没有训练。\n"
        f"下一步：只有你明确说 `{REQUIRED_GO_TEXT}`，才允许执行 balanced OSS dev/OOF matrix build；否则保持 do_not_build。\n"
        "禁止：无明确 go 重建矩阵、直接训练、跑 heldout/hard、release、改 GoalSearcher、调验证阈值。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>14.2 rank1-safe source-robust balanced OSS matrix build authorization</td>
            <td>Read-only authorization gate for balanced OSS dev/OOF matrix build.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "14.2 rank1-safe source-robust balanced OSS matrix build authorization" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.2 balanced OSS matrix build authorization gate")
    parser.add_argument("--plan-summary", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--oss-root", type=Path, default=DEFAULT_OSS_ROOT)
    parser.add_argument("--explicit-build-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    plan = read_json(args.plan_summary)
    report = build_report(plan, args.oss_root, bool(args.explicit_build_go), args.output_prefix)
    artifacts = report["artifacts"]
    write_csv(Path(artifacts["gate_checks_csv"]), report["gate_rows"], ["gate", "status", "value", "reason"])
    write_csv(Path(artifacts["build_contract_csv"]), report["build_contract"], ["item", "value"])
    write_csv(Path(artifacts["approval_criteria_csv"]), report["approval_criteria"], ["criterion", "required"])
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    update_dashboard(args.dashboard, report)
    print(json.dumps({"decision": report["decision"], "summary": safe_rel(artifacts["summary_json"]), "required_go_text": REQUIRED_GO_TEXT}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
