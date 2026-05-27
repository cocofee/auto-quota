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
SPLITS_EXPANDED = PROJECT_ROOT / "data" / "goal_search" / "splits_expanded"
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_13x_top1_loss_guarded_freeze_gate_review_summary.json"
DEFAULT_FROZEN_MANIFEST = AGENT_STATE / "goal_13x_top1_loss_guarded_freeze_gate_review_frozen_candidate_manifest.json"
DEFAULT_HELDOUT = SPLITS_EXPANDED / "heldout.jsonl"
DEFAULT_HARD = SPLITS_EXPANDED / "hard.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_conflict_guard_validation_boundary_go_no_go"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
REQUIRED_GO_TEXT = "go: run 13.21 heldout/hard A/B validation for frozen T1G_B_conflict_guard"


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


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


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


def _boundary_rows() -> list[dict[str, Any]]:
    return [
        {
            "boundary": "candidate_scope",
            "decision": "frozen_T1G_B_conflict_guard_only",
            "details": "Validation may evaluate only the 13.19 frozen deployable conflict-gated candidate.",
        },
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "Heldout/hard may not choose another gate, tune thresholds, modify conflict definitions, or change feature toggles.",
        },
        {
            "boundary": "comparison_design",
            "decision": "baseline_vs_frozen_conflict_guard_ab",
            "details": "A valid run must compare current baseline ranking against the fixed conflict guard on identical rows.",
        },
        {
            "boundary": "gate_scope",
            "decision": "observable_conflict_gate_only",
            "details": "Validation must not use label-derived positive rank, known answer position, or hit5 rescue veto as online gate inputs.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_switch",
            "details": "No GoalSearcher production wiring, rollout, fallback change, or threshold change is allowed in validation.",
        },
    ]


def _command_rows(candidate_id: str) -> list[dict[str, Any]]:
    return [
        {
            "order": 1,
            "phase": "heldout_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_13x_conflict_guard_validation_ab.py "
                "--split heldout --input data/goal_search/splits_expanded/heldout.jsonl "
                "--frozen-candidate-manifest reports/agent_state/goal_13x_top1_loss_guarded_freeze_gate_review_frozen_candidate_manifest.json "
                f"--candidate-id {candidate_id} "
                "--output-prefix reports/agent_state/goal_13x_conflict_guard_validation_heldout"
            ),
            "status": "not_executed_in_13_20",
        },
        {
            "order": 2,
            "phase": "hard_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_13x_conflict_guard_validation_ab.py "
                "--split hard --input data/goal_search/splits_expanded/hard.jsonl "
                "--frozen-candidate-manifest reports/agent_state/goal_13x_top1_loss_guarded_freeze_gate_review_frozen_candidate_manifest.json "
                f"--candidate-id {candidate_id} "
                "--output-prefix reports/agent_state/goal_13x_conflict_guard_validation_hard"
            ),
            "status": "not_executed_in_13_20",
        },
        {
            "order": 3,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "python tools/goal_13x_conflict_guard_validation_package_review.py",
            "status": "not_executed_in_13_20",
        },
    ]


def _artifact_rows() -> list[dict[str, Any]]:
    return [
        {"artifact": "heldout_summary_json", "required": True, "purpose": "heldout split A/B metrics and loss budget"},
        {"artifact": "heldout_details_jsonl", "required": True, "purpose": "per-row before/after conflict-gate audit"},
        {"artifact": "heldout_gate_coverage_csv", "required": True, "purpose": "how often conflict gate applied and with what outcome"},
        {"artifact": "heldout_loss_slices_csv", "required": True, "purpose": "losses by source/province/query/top1 family"},
        {"artifact": "hard_summary_json", "required": True, "purpose": "hard split robustness metrics"},
        {"artifact": "hard_details_jsonl", "required": True, "purpose": "per-row hard split audit"},
        {"artifact": "hard_gate_coverage_csv", "required": True, "purpose": "hard conflict-gate coverage"},
        {"artifact": "hard_loss_slices_csv", "required": True, "purpose": "hard losses by slice"},
        {"artifact": "package_review_summary_json", "required": True, "purpose": "final validation pass/fail"},
    ]


def _acceptance_rows() -> list[dict[str, Any]]:
    return [
        {"check": "heldout_hit1_net", "target": "> 0", "required_for_release_gate": True},
        {"check": "hard_hit1_net", "target": ">= 0", "required_for_release_gate": True},
        {"check": "heldout_rank1_loss_count", "target": "within 13.19 rank1 loss budget", "required_for_release_gate": True},
        {"check": "hard_rank1_loss_count", "target": "within 13.19 rank1 loss budget", "required_for_release_gate": True},
        {"check": "conflict_gate_coverage", "target": "nonzero, but not global top80 rerank", "required_for_release_gate": True},
        {"check": "heldout_hard_used_for_selection", "target": "false", "required_for_release_gate": True},
        {"check": "source_concentration", "target": "no single source/province dominates positive net", "required_for_release_gate": True},
    ]


def _stop_rows(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "candidate_id_not_T1G_B_conflict_guard", "action": "stop_and_report", "triggered_now": False},
        {"condition": "label_derived_gate_used", "action": "invalidate_run", "triggered_now": False},
        {"condition": "heldout_or_hard_used_for_selection", "action": "invalidate_run", "triggered_now": False},
        {"condition": "rank1_loss_budget_failed", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
        {"condition": "GoalSearcher_or_threshold_changed", "action": "stop_and_reject", "triggered_now": False},
    ]


def _gate_rows(freeze_summary: dict[str, Any], manifest: dict[str, Any], explicit_go: bool, heldout_rows: int, hard_rows: int) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "frozen_conflict_guard_available",
            "status": "pass" if manifest.get("candidate_id") == "T1G_B_conflict_guard" and freeze_summary.get("decision") == "freeze_deployable_conflict_guard_for_future_validation_go_no_go" else "fail",
            "value": manifest.get("candidate_id", ""),
            "reason": "Validation boundary is only for frozen deployable conflict guard.",
        },
        {
            "gate": "deployability_confirmed",
            "status": "pass" if manifest.get("deployability") == "deployable" else "fail",
            "value": manifest.get("deployability", ""),
            "reason": "Candidate must use observable gate inputs.",
        },
        {
            "gate": "heldout_split_available",
            "status": "pass" if heldout_rows > 0 else "fail",
            "value": heldout_rows,
            "reason": "Heldout split must exist for future validation.",
        },
        {
            "gate": "hard_split_available",
            "status": "pass" if hard_rows > 0 else "fail",
            "value": hard_rows,
            "reason": "Hard split must exist for future validation.",
        },
        {
            "gate": "explicit_validation_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": f"Validation execution requires exact user authorization: {REQUIRED_GO_TEXT}",
        },
        {
            "gate": "no_execution_in_boundary_gate",
            "status": "pass",
            "value": "read_only",
            "reason": "13.20 defines boundary only; it does not execute heldout/hard.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_validate_fix_boundary_inputs"
    elif explicit_go:
        decision = "validation_authorized_for_frozen_conflict_guard"
    else:
        decision = "validation_ready_but_do_not_validate_without_explicit_go"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.20 Validation Boundary / Explicit Go-No-Go for Frozen T1G_B Conflict Guard",
        "",
        "Read-only boundary definition for possible heldout/hard A/B validation of the 13.19 frozen deployable conflict-gated reranker candidate.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_id", m["candidate_id"]],
                ["explicit_validation_go_present", m["explicit_validation_go_present"]],
                ["validation_allowed_now", m["validation_allowed_now"]],
                ["heldout_rows", m["heldout_rows"]],
                ["hard_rows", m["hard_rows"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Command Contract",
        "",
        _md_table([["order", "phase", "status", "command"]] + [[row["order"], row["phase"], row["status"], row["command"]] for row in report["command_rows"]]),
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
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.20 validation boundary / explicit go-no-go for frozen T1G_B_conflict_guard 已完成。\n"
        f"结论：{report['decision']}。candidate={m['candidate_id']}，heldout_rows={m['heldout_rows']}，hard_rows={m['hard_rows']}，explicit_validation_go_present={m['explicit_validation_go_present']}。\n"
        f"下一步：只有你明确说 `{REQUIRED_GO_TEXT}`，才运行 heldout/hard A/B validation；否则保持 do_not_validate。\n"
        "禁止：无明确 go 跑 heldout/hard、用验证集选候选/调阈值、上线、改 GoalSearcher、使用标签派生 gate。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.20 validation boundary / explicit go-no-go for frozen T1G_B_conflict_guard" not in text:
        rows = f"""          <tr>
            <td>13.20 validation boundary / explicit go-no-go for frozen T1G_B_conflict_guard</td>
            <td>Read-only validation boundary, command contract, artifacts, stop conditions, and explicit-go requirements for frozen deployable conflict guard.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.19 Top1-loss-guarded scorecard/loss review and freeze gate</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.20 validation boundary / explicit go-no-go for frozen T1G_B conflict guard")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--explicit-validation-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    freeze_summary = _read_json(args.freeze_summary)
    manifest = _read_json(args.frozen_manifest)
    explicit_go = bool(args.explicit_validation_go)
    heldout_rows = _line_count(args.heldout)
    hard_rows = _line_count(args.hard)
    gate_rows, decision = _gate_rows(freeze_summary, manifest, explicit_go, heldout_rows, hard_rows)
    validation_allowed_now = decision == "validation_authorized_for_frozen_conflict_guard"
    command_rows = _command_rows(manifest.get("candidate_id", ""))
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "boundary_contract_csv": str(output_prefix.with_name(output_prefix.name + "_boundary_contract.csv")),
        "command_contract_csv": str(output_prefix.with_name(output_prefix.name + "_command_contract.csv")),
        "required_artifacts_csv": str(output_prefix.with_name(output_prefix.name + "_required_artifacts.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
    }
    report = {
        "stage": "13.20 validation boundary / explicit go-no-go for frozen T1G_B_conflict_guard",
        "read_only": True,
        "decision": decision,
        "metrics": {
            "candidate_id": manifest.get("candidate_id", ""),
            "explicit_validation_go_present": explicit_go,
            "validation_allowed_now": validation_allowed_now,
            "heldout_rows": heldout_rows,
            "hard_rows": hard_rows,
            "heldout_path": _safe_rel(args.heldout),
            "hard_path": _safe_rel(args.hard),
        },
        "frozen_candidate": manifest,
        "gate_rows": gate_rows,
        "boundary_rows": _boundary_rows(),
        "command_rows": command_rows,
        "required_artifacts": _artifact_rows(),
        "acceptance_checks": _acceptance_rows(),
        "stop_conditions": _stop_rows(explicit_go),
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only validation boundary only: no heldout/hard validation was executed, no candidate was reselected, no threshold tuning, no online integration, and no GoalSearcher edit.",
        "next_stage": {
            "recommended": f"If and only if the user says `{REQUIRED_GO_TEXT}`, run heldout/hard A/B validation under this contract. Otherwise keep do_not_validate.",
            "default": "do_not_validate",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_csv(Path(artifacts["boundary_contract_csv"]), report["boundary_rows"], ["boundary", "decision", "details"])
    _write_csv(Path(artifacts["command_contract_csv"]), command_rows, ["order", "phase", "allowed_after_explicit_go", "command", "status"])
    _write_csv(Path(artifacts["required_artifacts_csv"]), report["required_artifacts"], ["artifact", "required", "purpose"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), report["acceptance_checks"], ["check", "target", "required_for_release_gate"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), report["stop_conditions"], ["condition", "action", "triggered_now"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "metrics": report["metrics"], "required_go_text": REQUIRED_GO_TEXT}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
