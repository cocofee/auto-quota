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
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_13x_expanded_reranker_candidate_freeze_gate_summary.json"
DEFAULT_FROZEN_MANIFEST = AGENT_STATE / "goal_13x_expanded_reranker_candidate_freeze_gate_frozen_candidate_manifest.json"
DEFAULT_HELDOUT = SPLITS_EXPANDED / "heldout.jsonl"
DEFAULT_HARD = SPLITS_EXPANDED / "hard.jsonl"
DEFAULT_SPLIT_SUMMARY = SPLITS_EXPANDED / "split_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_validation_boundary_go_no_go"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


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
            "decision": "frozen_13_10_candidate_only",
            "details": "Validation may evaluate only OBJ_B_loss_budgeted_top1_net__FT_EXCLUDE_PARAMETER_EXACT_GAP_FEATURES from the 13.12 frozen manifest.",
        },
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "Heldout/hard may not choose a new candidate, tune hyperparameters, change feature toggles, expand the matrix, or change thresholds.",
        },
        {
            "boundary": "comparison_design",
            "decision": "baseline_vs_frozen_candidate_ab",
            "details": "A valid run must compare current baseline ranking against the frozen reranker candidate on the same heldout/hard rows.",
        },
        {
            "boundary": "claim_scope",
            "decision": "no_general_top1_claim_until_validation_passes",
            "details": "Dev/OOF freeze evidence remains OSS XML top80-present ranking evidence until independent validation passes.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_switch",
            "details": "Validation cannot edit GoalSearcher, connect online, alter fallback behavior, or change production thresholds.",
        },
    ]


def _command_contract(candidate_id: str) -> list[dict[str, Any]]:
    return [
        {
            "order": 1,
            "phase": "heldout_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_13x_expanded_reranker_validation_ab.py "
                "--split heldout --input data/goal_search/splits_expanded/heldout.jsonl "
                "--frozen-candidate-manifest reports/agent_state/goal_13x_expanded_reranker_candidate_freeze_gate_frozen_candidate_manifest.json "
                "--candidate-id " + candidate_id + " "
                "--output-prefix reports/agent_state/goal_13x_expanded_reranker_validation_heldout"
            ),
            "status": "not_executed_in_13_13",
        },
        {
            "order": 2,
            "phase": "hard_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_13x_expanded_reranker_validation_ab.py "
                "--split hard --input data/goal_search/splits_expanded/hard.jsonl "
                "--frozen-candidate-manifest reports/agent_state/goal_13x_expanded_reranker_candidate_freeze_gate_frozen_candidate_manifest.json "
                "--candidate-id " + candidate_id + " "
                "--output-prefix reports/agent_state/goal_13x_expanded_reranker_validation_hard"
            ),
            "status": "not_executed_in_13_13",
        },
        {
            "order": 3,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "python tools/goal_13x_expanded_reranker_validation_package_review.py",
            "status": "not_executed_in_13_13",
        },
    ]


def _required_artifacts() -> list[dict[str, Any]]:
    return [
        {"artifact": "heldout_ab_summary_json", "required": True, "purpose": "heldout split baseline-vs-frozen hit1/hit5/top80 metrics and loss budget"},
        {"artifact": "heldout_details_jsonl", "required": True, "purpose": "per-row heldout before/after ranking audit"},
        {"artifact": "heldout_loss_slices_csv", "required": True, "purpose": "heldout losses by province/source/query_family/top1_family/rank bucket"},
        {"artifact": "heldout_source_slices_csv", "required": True, "purpose": "heldout source-family/source-file concentration check"},
        {"artifact": "hard_ab_summary_json", "required": True, "purpose": "hard split robustness metrics"},
        {"artifact": "hard_details_jsonl", "required": True, "purpose": "per-row hard before/after ranking audit"},
        {"artifact": "hard_loss_slices_csv", "required": True, "purpose": "hard losses by province/source/query_family/top1_family/rank bucket"},
        {"artifact": "hard_source_slices_csv", "required": True, "purpose": "hard source-family/source-file concentration check"},
        {"artifact": "validation_package_review_summary_json", "required": True, "purpose": "final read-only validation pass/fail package"},
    ]


def _stop_conditions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "frozen_candidate_missing_or_changed", "action": "stop_and_report", "triggered_now": False},
        {"condition": "heldout_or_hard_used_for_candidate_selection", "action": "invalidate_run", "triggered_now": False},
        {"condition": "validation_harness_changes_online_goal_searcher", "action": "stop_and_reject", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
        {"condition": "new_loss_budget_failed", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "single_source_or_single_province_gain_dominates", "action": "stop_source_dominated", "triggered_now": False},
        {"condition": "fallback_contract_or_top80_scope_broken", "action": "stop_before_release_gate", "triggered_now": False},
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "heldout_hit1_net", "target": "> 0", "required_for_release_gate": True},
        {"check": "hard_hit1_net", "target": ">= 0", "required_for_release_gate": True},
        {"check": "total_new_hit1_loss_count", "target": "reviewed and within loss budget", "required_for_release_gate": True},
        {"check": "source_family_gain_concentration", "target": "no single source_family dominates validation net", "required_for_release_gate": True},
        {"check": "heldout_hard_used_for_selection", "target": "false", "required_for_release_gate": True},
        {"check": "fallback_contract_preserved", "target": "true", "required_for_release_gate": True},
        {"check": "top80_recall_claim_scope", "target": "ranking only unless retrieval evidence is separately validated", "required_for_release_gate": True},
    ]


def _gate_rows(*, explicit_go: bool, freeze_summary: dict[str, Any], frozen_manifest: dict[str, Any], heldout_rows: int, hard_rows: int) -> tuple[list[dict[str, Any]], str]:
    freeze_decision = freeze_summary.get("decision")
    candidate_id = frozen_manifest.get("candidate_id", "")
    rows = [
        {
            "gate": "frozen_candidate_available",
            "status": "pass" if candidate_id and freeze_decision == "freeze_candidate_for_future_explicit_validation_go_no_go" else "fail",
            "value": candidate_id,
            "reason": "Validation may only start from the 13.12 frozen candidate.",
        },
        {
            "gate": "heldout_split_available",
            "status": "pass" if heldout_rows > 0 else "fail",
            "value": heldout_rows,
            "reason": "Heldout split must exist before validation can be authorized.",
        },
        {
            "gate": "hard_split_available",
            "status": "pass" if hard_rows > 0 else "fail",
            "value": hard_rows,
            "reason": "Hard split must exist before validation can be authorized.",
        },
        {
            "gate": "explicit_validation_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": "Default is do_not_validate unless the user explicitly authorizes heldout/hard validation.",
        },
        {
            "gate": "no_candidate_reselection",
            "status": "pass",
            "value": "fixed_candidate_only",
            "reason": "13.13 defines validation for the frozen candidate only.",
        },
        {
            "gate": "no_online_or_goal_searcher_change",
            "status": "pass",
            "value": "read_only_boundary",
            "reason": "This gate cannot alter online behavior or GoalSearcher.",
        },
    ]
    has_fail = any(row["status"] == "fail" for row in rows)
    if has_fail:
        decision = "do_not_validate_fix_validation_inputs"
    elif not explicit_go:
        decision = "validation_ready_but_do_not_validate_without_explicit_go"
    else:
        decision = "validation_authorized_for_frozen_candidate"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.13 Validation Boundary / Explicit Validation Go-No-Go",
        "",
        "Read-only boundary definition for possible heldout/hard A/B validation of the frozen 13.10 expanded OSS XML reranker candidate. No validation was executed in this stage unless explicit go is present.",
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
                ["freeze_decision", m["freeze_decision"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Command Contract",
        "",
        _md_table([["order", "phase", "status", "command"]] + [[row["order"], row["phase"], row["status"], row["command"]] for row in report["command_contract"]]),
        "",
        "## Required Artifacts",
        "",
        _md_table([["artifact", "required", "purpose"]] + [[row["artifact"], row["required"], row["purpose"]] for row in report["required_artifacts"]]),
        "",
        "## Stop Conditions",
        "",
        _md_table([["condition", "action", "triggered_now"]] + [[row["condition"], row["action"], row["triggered_now"]] for row in report["stop_conditions"]]),
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
        "当前阶段：13.13 validation boundary / explicit validation go-no-go 已完成。\n"
        f"结论：{report['decision']}。frozen_candidate={m['candidate_id']}，heldout_rows={m['heldout_rows']}，hard_rows={m['hard_rows']}，explicit_validation_go_present={m['explicit_validation_go_present']}。\n"
        "下一步：只有你明确说 go: run 13.14 heldout/hard A/B validation for frozen 13.10 expanded reranker candidate，才允许实现/运行 validation；否则保持 do_not_validate。\n"
        "禁止：无明确 go 跑 heldout/hard、重新选择候选、重新训练、扩矩阵、上线、改 GoalSearcher、改阈值、把 dev/OOF freeze 宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.13 validation boundary / explicit validation go-no-go" not in text:
        rows = f"""          <tr>
            <td>13.13 validation boundary / explicit validation go-no-go</td>
            <td>Read-only validation boundary, command contract, required artifacts, stop conditions, and explicit-go requirements for the frozen 13.10 reranker candidate.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.12 expanded reranker candidate freeze gate</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.13 read-only validation boundary / explicit validation go-no-go")
    parser.add_argument("--explicit-validation-go", action="store_true")
    parser.add_argument("--freeze-summary", default=str(DEFAULT_FREEZE_SUMMARY))
    parser.add_argument("--frozen-manifest", default=str(DEFAULT_FROZEN_MANIFEST))
    parser.add_argument("--heldout", default=str(DEFAULT_HELDOUT))
    parser.add_argument("--hard", default=str(DEFAULT_HARD))
    parser.add_argument("--split-summary", default=str(DEFAULT_SPLIT_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    args = parser.parse_args()

    freeze_summary = _read_json(Path(args.freeze_summary))
    frozen_manifest = _read_json(Path(args.frozen_manifest))
    split_summary = _read_json(Path(args.split_summary)) if Path(args.split_summary).exists() else {}
    heldout_rows = _line_count(Path(args.heldout))
    hard_rows = _line_count(Path(args.hard))
    explicit_go = bool(args.explicit_validation_go)
    gate_rows, decision = _gate_rows(
        explicit_go=explicit_go,
        freeze_summary=freeze_summary,
        frozen_manifest=frozen_manifest,
        heldout_rows=heldout_rows,
        hard_rows=hard_rows,
    )
    validation_allowed_now = decision == "validation_authorized_for_frozen_candidate"
    candidate_id = frozen_manifest.get("candidate_id", "")
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "boundary_contract_csv": str(output_prefix.with_name(output_prefix.name + "_boundary_contract.csv")),
        "command_contract_csv": str(output_prefix.with_name(output_prefix.name + "_command_contract.csv")),
        "required_artifacts_csv": str(output_prefix.with_name(output_prefix.name + "_required_artifacts.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
    }
    report = {
        "stage": "13.13 validation boundary / explicit validation go-no-go",
        "read_only": True,
        "decision": decision,
        "metrics": {
            "candidate_id": candidate_id,
            "freeze_decision": freeze_summary.get("decision"),
            "explicit_validation_go_present": explicit_go,
            "validation_allowed_now": validation_allowed_now,
            "heldout_rows": heldout_rows,
            "hard_rows": hard_rows,
            "heldout_path": _safe_rel(args.heldout),
            "hard_path": _safe_rel(args.hard),
            "split_summary_available": bool(split_summary),
        },
        "frozen_candidate": frozen_manifest,
        "gate_rows": gate_rows,
        "boundary_rows": _boundary_rows(),
        "command_contract": _command_contract(candidate_id),
        "required_artifacts": _required_artifacts(),
        "stop_conditions": _stop_conditions(explicit_go),
        "acceptance_checks": _acceptance_checks(),
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only validation boundary only: no heldout/hard validation was executed, no candidate was reselected, no training or matrix expansion occurred, no online integration, no threshold change, and no GoalSearcher edit.",
        "next_stage": {
            "recommended": "If and only if the user explicitly says `go: run 13.14 heldout/hard A/B validation for frozen 13.10 expanded reranker candidate`, implement/run the validation harness under this contract. Otherwise keep do_not_validate.",
            "default": "do_not_validate",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_csv(Path(artifacts["boundary_contract_csv"]), report["boundary_rows"], ["boundary", "decision", "details"])
    _write_csv(Path(artifacts["command_contract_csv"]), report["command_contract"], ["order", "phase", "allowed_after_explicit_go", "command", "status"])
    _write_csv(Path(artifacts["required_artifacts_csv"]), report["required_artifacts"], ["artifact", "required", "purpose"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), report["stop_conditions"], ["condition", "action", "triggered_now"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), report["acceptance_checks"], ["check", "target", "required_for_release_gate"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
