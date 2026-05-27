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
SPLITS_EXPANDED = PROJECT_ROOT / "data" / "goal_search" / "splits_expanded"

DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_13x_observable_low_conf_margin_freeze_gate_review_summary.json"
DEFAULT_FROZEN_MANIFEST = AGENT_STATE / "goal_13x_observable_low_conf_margin_freeze_gate_review_frozen_candidate_manifest.json"
DEFAULT_THRESHOLD = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof_threshold_manifest.json"
DEFAULT_HELDOUT = SPLITS_EXPANDED / "heldout.jsonl"
DEFAULT_HARD = SPLITS_EXPANDED / "hard.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_observable_low_conf_margin_validation_boundary_go_no_go"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
REQUIRED_GO_TEXT = "go: run 13.28 heldout/hard A/B validation for frozen T1G_A1_low_conf_q25"

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


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


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


def boundary_rows(candidate_id: str, threshold: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "boundary": "candidate_scope",
            "decision": f"frozen_{candidate_id}_only",
            "details": "Validation may evaluate only the 13.26 frozen conservative zero-loss low-confidence candidate.",
        },
        {
            "boundary": "gate_scope",
            "decision": "observable_low_conf_q25_only",
            "details": f"Gate is fixed as top1 confidence <= {threshold.get('confidence_q25')} on 0-100 confidence scale; no baseline_rank/positive_rank/label branch.",
        },
        {
            "boundary": "threshold_policy",
            "decision": "frozen_dev_oof_threshold_manifest",
            "details": "Validation must use the 13.25 threshold manifest exactly; heldout/hard may not tune q25/q35 or confidence scale.",
        },
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "Heldout/hard may not switch to A3/A4, choose another variant, adjust gates, or change feature toggles.",
        },
        {
            "boundary": "comparison_design",
            "decision": "baseline_vs_frozen_low_conf_ab",
            "details": "A valid run compares current baseline ranking against the fixed low-confidence reranker on identical split rows.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_switch",
            "details": "No GoalSearcher production wiring, rollout, fallback change, or threshold change is allowed in validation.",
        },
    ]


def command_rows(candidate_id: str) -> list[dict[str, Any]]:
    manifest = "reports/agent_state/goal_13x_observable_low_conf_margin_freeze_gate_review_frozen_candidate_manifest.json"
    threshold = "reports/agent_state/goal_13x_observable_low_conf_margin_dev_oof_threshold_manifest.json"
    return [
        {
            "order": 1,
            "phase": "heldout_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_13x_observable_low_conf_margin_validation_ab.py "
                "--split heldout --input data/goal_search/splits_expanded/heldout.jsonl "
                f"--frozen-candidate-manifest {manifest} --threshold-manifest {threshold} "
                f"--candidate-id {candidate_id} "
                "--output-prefix reports/agent_state/goal_13x_observable_low_conf_margin_validation_heldout"
            ),
            "status": "not_executed_in_13_27",
        },
        {
            "order": 2,
            "phase": "hard_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_13x_observable_low_conf_margin_validation_ab.py "
                "--split hard --input data/goal_search/splits_expanded/hard.jsonl "
                f"--frozen-candidate-manifest {manifest} --threshold-manifest {threshold} "
                f"--candidate-id {candidate_id} "
                "--output-prefix reports/agent_state/goal_13x_observable_low_conf_margin_validation_hard"
            ),
            "status": "not_executed_in_13_27",
        },
        {
            "order": 3,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "python tools/goal_13x_observable_low_conf_margin_validation_package_review.py",
            "status": "not_executed_in_13_27",
        },
    ]


def artifact_rows() -> list[dict[str, Any]]:
    return [
        {"artifact": "heldout_summary_json", "required": True, "purpose": "heldout split A/B metrics and loss budget"},
        {"artifact": "heldout_details_jsonl", "required": True, "purpose": "per-row before/after low-confidence gate audit"},
        {"artifact": "heldout_gate_coverage_csv", "required": True, "purpose": "low-confidence q25 gate coverage and outcome"},
        {"artifact": "heldout_loss_slices_csv", "required": True, "purpose": "losses by source/province/fold/query/top1 family"},
        {"artifact": "hard_summary_json", "required": True, "purpose": "hard split robustness metrics"},
        {"artifact": "hard_details_jsonl", "required": True, "purpose": "per-row hard split audit"},
        {"artifact": "hard_gate_coverage_csv", "required": True, "purpose": "hard low-confidence gate coverage"},
        {"artifact": "hard_loss_slices_csv", "required": True, "purpose": "hard losses by slice"},
        {"artifact": "package_review_summary_json", "required": True, "purpose": "final validation pass/fail and release gate recommendation"},
    ]


def acceptance_rows() -> list[dict[str, Any]]:
    return [
        {"check": "heldout_hit1_net", "target": "> 0", "required_for_release_gate": True},
        {"check": "hard_hit1_net", "target": ">= 0", "required_for_release_gate": True},
        {"check": "heldout_rank1_loss_count", "target": "<= 1 preferred; hard stop if material regression", "required_for_release_gate": True},
        {"check": "hard_rank1_loss_count", "target": "<= 1 preferred; hard stop if material regression", "required_for_release_gate": True},
        {"check": "gate_coverage", "target": "nonzero and comparable to frozen low-conf q25 scope; no global rerank", "required_for_release_gate": True},
        {"check": "heldout_hard_used_for_selection", "target": "false", "required_for_release_gate": True},
        {"check": "source_province_fold_concentration", "target": "validation gains not solely explained by Zhejiang/fold-4 concentration", "required_for_release_gate": True},
        {"check": "threshold_integrity", "target": "confidence_q25=40.66, confidence_scale=0_100, no validation tuning", "required_for_release_gate": True},
    ]


def stop_rows(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "candidate_id_not_T1G_A1_low_conf_q25", "action": "stop_and_report", "triggered_now": False},
        {"condition": "threshold_manifest_changed_or_recomputed_on_validation", "action": "invalidate_run", "triggered_now": False},
        {"condition": "label_derived_gate_used", "action": "invalidate_run", "triggered_now": False},
        {"condition": "heldout_or_hard_used_for_selection", "action": "invalidate_run", "triggered_now": False},
        {"condition": "rank1_loss_budget_failed", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
        {"condition": "GoalSearcher_or_online_threshold_changed", "action": "stop_and_reject", "triggered_now": False},
    ]


def gate_rows(
    freeze_summary: dict[str, Any],
    manifest: dict[str, Any],
    threshold: dict[str, Any],
    explicit_go: bool,
    heldout_rows: int,
    hard_rows: int,
) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "frozen_candidate_available",
            "status": "pass" if manifest.get("candidate_id") == "T1G_A1_low_conf_q25" and str(freeze_summary.get("decision", "")).startswith("freeze_") else "fail",
            "value": manifest.get("candidate_id", ""),
            "reason": "Validation boundary is only for the 13.26 frozen low-confidence candidate.",
        },
        {
            "gate": "zero_loss_freeze_confirmed",
            "status": "pass" if int(manifest.get("rank1_loss_count") or 0) == 0 and int(manifest.get("hit1_loss") or 0) == 0 else "fail",
            "value": f"hit1_loss={manifest.get('hit1_loss')}; rank1_loss={manifest.get('rank1_loss_count')}",
            "reason": "This boundary is justified by the conservative zero-loss freeze decision.",
        },
        {
            "gate": "threshold_manifest_fixed",
            "status": "pass" if threshold.get("confidence_scale") == "0_100" and float(threshold.get("confidence_q25") or 0) == 40.66 else "fail",
            "value": f"scale={threshold.get('confidence_scale')}; q25={threshold.get('confidence_q25')}",
            "reason": "Validation must use frozen dev/OOF threshold, not tune on validation.",
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
            "reason": "13.27 defines boundary only; it does not execute heldout/hard.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_validate_fix_boundary_inputs"
    elif explicit_go:
        decision = "validation_authorized_for_frozen_T1G_A1_low_conf_q25"
    else:
        decision = "validation_ready_but_do_not_validate_without_explicit_go"
    return rows, decision


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.27 Validation Boundary / Explicit Go-No-Go for Frozen T1G_A1",
        "",
        "Read-only boundary definition for possible heldout/hard A/B validation of the 13.26 frozen low-confidence q25 candidate.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Metrics",
        "",
        md_table(
            [
                ["metric", "value"],
                ["candidate_id", m["candidate_id"]],
                ["confidence_q25", m["confidence_q25"]],
                ["confidence_scale", m["confidence_scale"]],
                ["explicit_validation_go_present", m["explicit_validation_go_present"]],
                ["validation_allowed_now", m["validation_allowed_now"]],
                ["heldout_rows", m["heldout_rows"]],
                ["hard_rows", m["hard_rows"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Boundary Contract",
        "",
        md_table([["boundary", "decision", "details"]] + [[row["boundary"], row["decision"], row["details"]] for row in report["boundary_rows"]]),
        "",
        "## Acceptance Checks",
        "",
        md_table([["check", "target", "required_for_release_gate"]] + [[row["check"], row["target"], row["required_for_release_gate"]] for row in report["acceptance_rows"]]),
        "",
        "## Command Contract",
        "",
        md_table([["order", "phase", "status", "command"]] + [[row["order"], row["phase"], row["status"], row["command"]] for row in report["command_rows"]]),
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
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.27 validation boundary / explicit validation go-no-go for frozen T1G_A1_low_conf_q25 已完成。\n"
        f"结论：{report['decision']}。frozen candidate={m['candidate_id']}，confidence_q25={m['confidence_q25']}，heldout_rows={m['heldout_rows']}，hard_rows={m['hard_rows']}；本轮未执行验证。\n"
        f"下一步：只有你明确说 `{REQUIRED_GO_TEXT}`，才允许进入 heldout/hard A/B validation；否则保持 do_not_validate。\n"
        "禁止：无明确 go 跑 heldout/hard、用 heldout/hard 调阈值或重选 A3/A4、release、改 GoalSearcher、改线上阈值、引入 label-derived gate。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>13.27 validation boundary / explicit go-no-go for frozen T1G_A1_low_conf_q25</td>
            <td>Read-only validation boundary, command contract, stop conditions, and explicit-go requirement for frozen low-confidence candidate.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "13.27 validation boundary / explicit go-no-go for frozen T1G_A1_low_conf_q25" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.27 validation boundary / explicit go-no-go for frozen T1G_A1")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST)
    parser.add_argument("--threshold-manifest", type=Path, default=DEFAULT_THRESHOLD)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--explicit-validation-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    freeze_summary = read_json(args.freeze_summary)
    manifest = read_json(args.frozen_manifest)
    threshold = read_json(args.threshold_manifest)
    heldout_rows = line_count(args.heldout)
    hard_rows = line_count(args.hard)
    explicit_go = bool(args.explicit_validation_go)
    gates, decision = gate_rows(freeze_summary, manifest, threshold, explicit_go, heldout_rows, hard_rows)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "boundary_contract_csv": str(output_prefix.with_name(output_prefix.name + "_boundary_contract.csv")),
        "command_contract_csv": str(output_prefix.with_name(output_prefix.name + "_command_contract.csv")),
        "required_artifacts_csv": str(output_prefix.with_name(output_prefix.name + "_required_artifacts.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
    }
    report = {
        "stage": "13.27 validation boundary / explicit go-no-go for frozen T1G_A1_low_conf_q25",
        "read_only_review": True,
        "decision": decision,
        "required_go_text": REQUIRED_GO_TEXT,
        "metrics": {
            "candidate_id": manifest.get("candidate_id", ""),
            "confidence_q25": threshold.get("confidence_q25"),
            "confidence_scale": threshold.get("confidence_scale"),
            "explicit_validation_go_present": explicit_go,
            "validation_allowed_now": decision == "validation_authorized_for_frozen_T1G_A1_low_conf_q25",
            "heldout_rows": heldout_rows,
            "hard_rows": hard_rows,
        },
        "gate_rows": gates,
        "boundary_rows": boundary_rows(manifest.get("candidate_id", ""), threshold),
        "command_rows": command_rows(manifest.get("candidate_id", "")),
        "artifact_rows": artifact_rows(),
        "acceptance_rows": acceptance_rows(),
        "stop_rows": stop_rows(explicit_go),
        "artifacts": artifacts,
        "next_stage": {
            "id": "13.28",
            "name": "heldout/hard A/B validation for frozen T1G_A1_low_conf_q25",
            "recommended": f"If and only if the user says `{REQUIRED_GO_TEXT}`, run heldout/hard A/B validation. Otherwise keep do_not_validate.",
            "default": "do_not_validate",
        },
        "anti_drift_conclusion": (
            "13.27 is read-only. It defines validation boundaries for the frozen T1G_A1 candidate only. "
            "It does not run heldout/hard, tune thresholds, switch to A3/A4, release, edit GoalSearcher, or introduce label-derived gates."
        ),
    }
    write_csv(Path(artifacts["boundary_contract_csv"]), report["boundary_rows"], ["boundary", "decision", "details"])
    write_csv(Path(artifacts["command_contract_csv"]), report["command_rows"], ["order", "phase", "allowed_after_explicit_go", "command", "status"])
    write_csv(Path(artifacts["required_artifacts_csv"]), report["artifact_rows"], ["artifact", "required", "purpose"])
    write_csv(Path(artifacts["acceptance_checks_csv"]), report["acceptance_rows"], ["check", "target", "required_for_release_gate"])
    write_csv(Path(artifacts["stop_conditions_csv"]), report["stop_rows"], ["condition", "action", "triggered_now"])
    write_csv(Path(artifacts["gate_checks_csv"]), report["gate_rows"], ["gate", "status", "value", "reason"])
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    update_dashboard(args.dashboard, report)
    print(json.dumps({"decision": decision, "summary": safe_rel(artifacts["summary_json"]), "required_go_text": REQUIRED_GO_TEXT}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
