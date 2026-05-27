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
DEFAULT_PLAN = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_summary.json"
DEFAULT_CANDIDATES = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_candidate_matrix.csv"
DEFAULT_COMMANDS = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_command_contract.csv"
DEFAULT_STOPS = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_stop_conditions.csv"
DEFAULT_REQUIRED = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_required_artifacts.csv"
DEFAULT_OUTPUT = AGENT_STATE / "goal_14x_r14_v2_dev_oof_execution_authorization_gate_summary.json"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
EXECUTION_SCRIPT = PROJECT_ROOT / "tools" / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof_execute.py"
FREEZE_SCRIPT = PROJECT_ROOT / "tools" / "goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review.py"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _status(value: bool) -> str:
    return "pass" if value else "fail"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    check_rows = [["check", "status", "evidence"]]
    for row in report["gate_checks"]:
        check_rows.append([row["check"], row["status"], row["evidence"]])
    lines = [
        "# 14.9 R14 v2 Dev/OOF Execution Authorization Gate",
        "",
        "This is a read-only authorization review. It does not train, tune, read heldout/hard, or implement the future execution harness.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Reason: {report['decision_reason']}",
        "",
        "## Checks",
        "",
        _md_table(check_rows),
        "",
        "## Required Explicit Go",
        "",
        report["required_go_phrase"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **14.9 R14 v2 dev/OOF execution authorization gate completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        report["decision_reason"],
        "",
        "## Current Boundary",
        "",
        "- Default remains `do_not_train`.",
        "- No heldout/hard access.",
        "- No release or GoalSearcher integration.",
        "- Future execution requires the R14 v2 dev/OOF harness plus an explicit user go.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.9 R14 v2 dev/OOF execution authorization gate 已完成。\n"
        f"结论：{report['decision']}。原因：{report['decision_reason']}\n"
        "下一步建议：14.10 R14 v2 dev/OOF execution harness implementation scope / explicit go gate。默认不训练；只有明确 go 才允许实现 dev/OOF harness 并随后跑 dev/OOF-only execution。\n"
        "禁止：用 heldout/hard、发布 R14_A、直接切 R14_D、改阈值、上线、改 GoalSearcher。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.9 R14 v2 dev/OOF execution authorization gate" not in text:
        row = f"""          <tr>
            <td>14.9 R14 v2 dev/OOF execution authorization gate</td>
            <td>Read-only go/no-go review for the R14 v2 dev/OOF execution scope; no training was run.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {report['updated_at']} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.9 R14 v2 dev/OOF execution authorization gate")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--candidate-matrix", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--command-contract", type=Path, default=DEFAULT_COMMANDS)
    parser.add_argument("--stop-conditions", type=Path, default=DEFAULT_STOPS)
    parser.add_argument("--required-artifacts", type=Path, default=DEFAULT_REQUIRED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    plan = _read_json(args.plan)
    candidates = _read_csv(args.candidate_matrix)
    commands = _read_csv(args.command_contract)
    stops = _read_csv(args.stop_conditions)
    required = _read_csv(args.required_artifacts)

    execution_command = next((row for row in commands if row.get("stage") == "14.9_if_explicit_go"), {})
    has_no_heldout_command = "heldout" not in execution_command.get("command", "").lower() and "hard" not in execution_command.get("command", "").lower()
    candidate_count_ok = len(candidates) >= 3
    stop_conditions_ok = all(
        key in {row.get("condition") for row in stops}
        for key in [
            "heldout_or_hard_read_before_new_freeze",
            "rank1_loss_count_gt_1",
            "GoalSearcher_or_online_threshold_changed",
        ]
    )
    required_artifacts_ok = all(str(row.get("required")).lower() == "true" for row in required)
    execution_script_exists = EXECUTION_SCRIPT.exists()
    freeze_script_exists = FREEZE_SCRIPT.exists()

    gate_checks = [
        {"check": "14.8_plan_ready", "status": _status(plan.get("decision") == "plan_ready_request_explicit_dev_oof_execution_go"), "evidence": str(plan.get("decision"))},
        {"check": "candidate_matrix_present", "status": _status(candidate_count_ok), "evidence": f"{len(candidates)} candidates"},
        {"check": "execution_command_dev_oof_only", "status": _status(bool(execution_command) and has_no_heldout_command), "evidence": execution_command.get("command", "<missing>")},
        {"check": "stop_conditions_cover_safety", "status": _status(stop_conditions_ok), "evidence": f"{len(stops)} stop conditions"},
        {"check": "required_artifacts_manifest_present", "status": _status(required_artifacts_ok), "evidence": f"{len(required)} required artifacts"},
        {"check": "execution_harness_exists", "status": _status(execution_script_exists), "evidence": _safe_rel(EXECUTION_SCRIPT)},
        {"check": "freeze_gate_harness_exists", "status": _status(freeze_script_exists), "evidence": _safe_rel(FREEZE_SCRIPT)},
    ]

    if not execution_script_exists:
        decision = "do_not_execute_harness_missing_request_explicit_harness_go"
        reason = "The R14 v2 plan and command boundary are ready, but the dev/OOF execution harness does not exist yet."
        next_stage = {
            "recommended": "14.10 R14 v2 dev/OOF execution harness implementation scope / explicit go gate",
            "description": "Define or request explicit go to implement the dev/OOF-only execution harness, then run only the fixed 14.8 candidate matrix if authorized.",
            "default": "do_not_implement_or_train",
        }
    elif any(row["status"] == "fail" for row in gate_checks[:5]):
        decision = "do_not_execute_scope_incomplete"
        reason = "One or more required scope checks failed."
        next_stage = {
            "recommended": "14.10 R14 v2 scope repair review",
            "description": "Repair the incomplete plan/contract before any execution authorization.",
            "default": "do_not_train",
        }
    else:
        decision = "execution_ready_but_wait_for_explicit_go"
        reason = "The dev/OOF-only execution scope is ready, but no explicit execution go was provided in this stage."
        next_stage = {
            "recommended": "explicit go required for 14.10 R14 v2 dev/OOF execution",
            "description": "Provide explicit go to run the fixed dev/OOF-only command; otherwise remain held.",
            "default": "do_not_train",
        }

    output_md = args.output.with_suffix(".md")
    report = {
        "stage": "14.9 R14 v2 dev/OOF execution authorization gate",
        "read_only_review": True,
        "decision": decision,
        "decision_reason": reason,
        "gate_checks": gate_checks,
        "required_go_phrase": "go: implement/run 14.10 R14 v2 dev/OOF-only execution harness and fixed candidate matrix",
        "next_stage": next_stage,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": {
            "summary_json": str(args.output),
            "summary_md": str(output_md),
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "anti_drift_conclusion": (
            "14.9 is read-only. It did not train, implement the execution harness, read heldout/hard, tune thresholds, "
            "release R14_A, switch to R14_D, edit GoalSearcher, or change online behavior."
        ),
    }
    _write_json(args.output, report)
    _write_markdown(output_md, report)
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(args.output), "decision": decision, "next": next_stage["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
