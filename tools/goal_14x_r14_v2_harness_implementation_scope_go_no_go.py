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
DEFAULT_AUTH = AGENT_STATE / "goal_14x_r14_v2_dev_oof_execution_authorization_gate_summary.json"
DEFAULT_PLAN = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_summary.json"
DEFAULT_CANDIDATES = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_candidate_matrix.csv"
DEFAULT_REQUIRED = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_required_artifacts.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_r14_v2_harness_implementation_scope_go_no_go"
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
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _allowed_files() -> list[dict[str, Any]]:
    return [
        {
            "path": _safe_rel(EXECUTION_SCRIPT),
            "action": "create",
            "purpose": "dev/OOF-only execution harness for fixed 14.8 R14 v2 candidate matrix",
        },
        {
            "path": _safe_rel(FREEZE_SCRIPT),
            "action": "create",
            "purpose": "read-only freeze/no-freeze gate over R14 v2 dev/OOF artifacts",
        },
        {
            "path": "reports/agent_state/goal_14x_r14_v2_bolder_rank1_safe_dev_oof_*",
            "action": "create_outputs_only_after_explicit_run_go",
            "purpose": "required dev/OOF scorecard, loss audit, gate coverage, rank1 preservation, robustness artifacts",
        },
        {
            "path": "reports/agent_state/goal_current_roadmap_status_20260526_14x.md",
            "action": "update",
            "purpose": "roadmap status after authorized implementation/execution",
        },
        {
            "path": "reports/agent_state/goal_learning_roadmap_dashboard.html",
            "action": "update",
            "purpose": "dashboard status after authorized implementation/execution",
        },
    ]


def _implementation_steps(candidate_count: int, artifact_count: int) -> list[dict[str, Any]]:
    return [
        {
            "step": "reuse_14x_training_primitives",
            "detail": "Import/reuse 14.3 data loading, LightGBM training, feature whitelist, OOF fold assignment, scorecard writers where compatible.",
            "success_check": "no duplicated matrix parsing logic beyond R14 v2 gate-specific scoring",
        },
        {
            "step": "implement_fixed_candidate_matrix",
            "detail": f"Load exactly {candidate_count} candidates from the 14.8 candidate matrix; do not add, remove, or tune candidate rows during execution.",
            "success_check": "candidate ids in scorecard exactly match 14.8 matrix",
        },
        {
            "step": "implement_r14v2_gate_decisions",
            "detail": "Encode only online-observable gates: weak/conflict baseline, support score, model margin delta, taxonomy guard, and rank1 protection veto.",
            "success_check": "no expected_id, positive_rank, heldout/hard, source_family, province, or label-derived runtime gate",
        },
        {
            "step": "emit_required_artifacts",
            "detail": f"Write all {artifact_count} artifacts from the 14.8 required-artifact manifest.",
            "success_check": "all required outputs exist and are non-empty after execution",
        },
        {
            "step": "implement_freeze_gate",
            "detail": "Read only R14 v2 dev/OOF outputs and enforce 14.8 loss budget.",
            "success_check": "no heldout/hard access; no validation; no release decision beyond future validation recommendation",
        },
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "py_compile", "command": "python -m py_compile tools/goal_14x_r14_v2_bolder_rank1_safe_dev_oof_execute.py tools/goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review.py", "required": True},
        {"check": "dev_oof_only_flag_required", "command": "execution harness must require --dev-oof-only and --emit-loss-audit", "required": True},
        {"check": "no_heldout_hard_reads", "command": "static/check review: no splits_expanded/heldout.jsonl or hard.jsonl access in execution/freeze harness", "required": True},
        {"check": "candidate_matrix_fixed", "command": "scorecard ids must equal 14.8 candidate matrix ids", "required": True},
        {"check": "artifact_manifest_complete", "command": "all 14.8 required artifacts exist after execution", "required": True},
        {"check": "loss_budget_enforced", "command": "freeze gate hard-stops rank1_loss_count > 1 and no-op coverage <= R14_A applied rate", "required": True},
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "phase": "14.10_scope_only",
            "command": "python tools/goal_14x_r14_v2_harness_implementation_scope_go_no_go.py",
            "allowed": "read-only implementation scope and explicit-go package",
            "forbidden": "implement harness, train, heldout/hard, release, GoalSearcher edits",
        },
        {
            "phase": "14.11_if_explicit_go",
            "command": "go: implement/run 14.11 R14 v2 dev/OOF-only execution harness and fixed candidate matrix",
            "allowed": "create the two harness scripts, py_compile them, run dev/OOF-only execution, emit required artifacts",
            "forbidden": "heldout/hard, candidate tuning, threshold tuning from validation, online integration, GoalSearcher edits",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    file_rows = [["file/scope", "action", "purpose"]]
    for row in report["allowed_files"]:
        file_rows.append([row["path"], row["action"], row["purpose"]])
    check_rows = [["check", "command", "required"]]
    for row in report["acceptance_checks"]:
        check_rows.append([row["check"], row["command"], row["required"]])
    lines = [
        "# 14.10 R14 v2 Harness Implementation Scope / Explicit Go Gate",
        "",
        "This is a read-only scope lock. No harness was implemented and no dev/OOF training was run.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Reason: {report['decision_reason']}",
        "",
        "## Allowed Files",
        "",
        _md_table(file_rows),
        "",
        "## Acceptance Checks",
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
        "Current stage: **14.10 R14 v2 dev/OOF execution harness implementation scope / explicit go gate completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        report["decision_reason"],
        "",
        "## Current Boundary",
        "",
        "- Default remains `do_not_implement_or_train`.",
        "- The allowed implementation scope is locked to two harness scripts plus required dev/OOF artifacts.",
        "- No heldout/hard access is allowed.",
        "- No release, GoalSearcher integration, or online threshold changes are allowed.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "Required explicit go:",
        "",
        f"`{report['required_go_phrase']}`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.10 R14 v2 dev/OOF execution harness implementation scope / explicit go gate 已完成。\n"
        f"结论：{report['decision']}。默认仍 do_not_implement_or_train。\n"
        "下一步建议：14.11 R14 v2 dev/OOF-only harness implementation + execution。只有明确 go 才允许创建两个 harness 脚本，并只跑固定 14.8 candidate matrix 的 dev/OOF-only execution。\n"
        "需要明确说：go: implement/run 14.11 R14 v2 dev/OOF-only execution harness and fixed candidate matrix\n"
        "禁止：用 heldout/hard、调验证集参数、发布 R14_A、直接切 R14_D、改阈值、上线、改 GoalSearcher。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.10 R14 v2 harness implementation scope" not in text:
        row = f"""          <tr>
            <td>14.10 R14 v2 harness implementation scope</td>
            <td>Read-only scope/go-no-go package for implementing and running the R14 v2 dev/OOF-only execution harness.</td>
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
    parser = argparse.ArgumentParser(description="14.10 R14 v2 harness implementation scope / explicit go gate")
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--candidate-matrix", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--required-artifacts", type=Path, default=DEFAULT_REQUIRED)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    auth = _read_json(args.authorization)
    plan = _read_json(args.plan)
    candidates = _read_csv(args.candidate_matrix)
    required = _read_csv(args.required_artifacts)
    allowed_files = _allowed_files()
    implementation_steps = _implementation_steps(len(candidates), len(required))
    acceptance_checks = _acceptance_checks()
    command_contract = _command_contract()

    has_explicit_go = False
    if has_explicit_go:
        decision = "explicit_go_present_ready_to_implement_and_run"
        reason = "Explicit go was provided."
    else:
        decision = "do_not_implement_or_train_wait_for_explicit_go"
        reason = "No explicit implementation/run go was provided; 14.10 only locks the allowed scope."

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "allowed_files_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_allowed_files.csv")),
        "implementation_steps_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_implementation_steps.csv")),
        "acceptance_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_acceptance_checks.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    report = {
        "stage": "14.10 R14 v2 dev/OOF execution harness implementation scope / explicit go gate",
        "read_only_scope_lock": True,
        "decision": decision,
        "decision_reason": reason,
        "authorization_decision": auth.get("decision"),
        "plan_decision": plan.get("decision"),
        "candidate_count": len(candidates),
        "required_artifact_count": len(required),
        "execution_script_exists": EXECUTION_SCRIPT.exists(),
        "freeze_script_exists": FREEZE_SCRIPT.exists(),
        "allowed_files": allowed_files,
        "implementation_steps": implementation_steps,
        "acceptance_checks": acceptance_checks,
        "command_contract": command_contract,
        "required_go_phrase": "go: implement/run 14.11 R14 v2 dev/OOF-only execution harness and fixed candidate matrix",
        "next_stage": {
            "recommended": "14.11 R14 v2 dev/OOF-only harness implementation + execution",
            "description": "If explicitly authorized, create the dev/OOF execution and freeze-gate harnesses, run only the fixed 14.8 candidate matrix, and emit all required 14.8 artifacts.",
            "default": "do_not_implement_or_train",
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "14.10 is read-only scope locking. It did not implement harness scripts, run training, read heldout/hard, "
            "change candidates, tune thresholds, release code, edit GoalSearcher, or change online behavior."
        ),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["allowed_files_csv"]), allowed_files, ["path", "action", "purpose"])
    _write_csv(Path(artifacts["implementation_steps_csv"]), implementation_steps, ["step", "detail", "success_check"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, ["check", "command", "required"])
    _write_csv(Path(artifacts["command_contract_csv"]), command_contract, ["phase", "command", "allowed", "forbidden"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
