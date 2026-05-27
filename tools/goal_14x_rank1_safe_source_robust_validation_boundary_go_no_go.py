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

DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_14x_rank1_safe_source_robust_freeze_gate_summary.json"
DEFAULT_FROZEN = AGENT_STATE / "goal_14x_rank1_safe_source_robust_freeze_gate_frozen_candidate.json"
DEFAULT_HELDOUT = SPLITS_EXPANDED / "heldout.jsonl"
DEFAULT_HARD = SPLITS_EXPANDED / "hard.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_validation_boundary_go_no_go"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_STATUS_MD = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"

REQUIRED_GO_TEXT = "go: run 14.6 heldout/hard A/B validation for frozen R14_A"

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
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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


def boundary_rows() -> list[dict[str, Any]]:
    return [
        {
            "boundary": "candidate_scope",
            "decision": "frozen_R14_A_only",
            "details": "Validation may evaluate only R14_A_rank1_veto_strong_challenger from the 14.4 frozen manifest.",
        },
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "Heldout/hard may not switch to R14_D/R14_B, tune q70/q75/q80, change gates, change feature toggles, or expand the candidate matrix.",
        },
        {
            "boundary": "comparison_design",
            "decision": "baseline_vs_frozen_R14_A_ab",
            "details": "A valid run compares current baseline ranking against the fixed R14_A rank1-safe strong-challenger policy on identical split rows.",
        },
        {
            "boundary": "risk_policy",
            "decision": "validate_because_zero_loss_but_small_signal",
            "details": "R14_A is worth validating because dev/OOF loss is zero, but its +3 net and 5/2155 coverage make release unlikely unless validation is clean.",
        },
        {
            "boundary": "claim_scope",
            "decision": "no_general_top1_claim_until_validation_passes",
            "details": "Freeze evidence remains balanced-OSS dev/OOF evidence; no heldout/hard claim exists yet.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_switch",
            "details": "Validation cannot edit GoalSearcher, connect online, alter fallback behavior, or change production thresholds.",
        },
    ]


def command_contract() -> list[dict[str, Any]]:
    manifest = "reports/agent_state/goal_14x_rank1_safe_source_robust_freeze_gate_frozen_candidate.json"
    return [
        {
            "order": 1,
            "phase": "heldout_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_14x_rank1_safe_source_robust_validation_ab.py "
                "--split heldout --input data/goal_search/splits_expanded/heldout.jsonl "
                f"--frozen-candidate-manifest {manifest} "
                "--candidate-id R14_A_rank1_veto_strong_challenger "
                "--output-prefix reports/agent_state/goal_14x_rank1_safe_source_robust_validation_heldout"
            ),
            "status": "not_executed_in_14_5",
        },
        {
            "order": 2,
            "phase": "hard_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_14x_rank1_safe_source_robust_validation_ab.py "
                "--split hard --input data/goal_search/splits_expanded/hard.jsonl "
                f"--frozen-candidate-manifest {manifest} "
                "--candidate-id R14_A_rank1_veto_strong_challenger "
                "--output-prefix reports/agent_state/goal_14x_rank1_safe_source_robust_validation_hard"
            ),
            "status": "not_executed_in_14_5",
        },
        {
            "order": 3,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "python tools/goal_14x_rank1_safe_source_robust_validation_package_review.py",
            "status": "not_executed_in_14_5",
        },
    ]


def required_artifacts() -> list[dict[str, Any]]:
    return [
        {"artifact": "heldout_ab_summary_json", "required": True, "purpose": "heldout baseline-vs-frozen hit1/hit5 metrics and loss budget"},
        {"artifact": "heldout_details_jsonl", "required": True, "purpose": "per-row heldout before/after audit"},
        {"artifact": "heldout_gate_coverage_csv", "required": True, "purpose": "R14_A strong-challenger gate coverage and outcomes"},
        {"artifact": "heldout_loss_slices_csv", "required": True, "purpose": "heldout losses by province/source/query_family/top1_family/rank bucket"},
        {"artifact": "heldout_source_concentration_csv", "required": True, "purpose": "check whether validation net is source/province/fold dominated"},
        {"artifact": "hard_ab_summary_json", "required": True, "purpose": "hard split robustness metrics"},
        {"artifact": "hard_details_jsonl", "required": True, "purpose": "per-row hard before/after audit"},
        {"artifact": "hard_gate_coverage_csv", "required": True, "purpose": "hard split R14_A gate coverage"},
        {"artifact": "hard_loss_slices_csv", "required": True, "purpose": "hard losses by slice"},
        {"artifact": "validation_package_review_summary_json", "required": True, "purpose": "final validation pass/fail and release-gate recommendation"},
    ]


def stop_conditions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "frozen_candidate_missing_or_changed", "action": "stop_and_report", "triggered_now": False},
        {"condition": "candidate_reselected_using_heldout_or_hard", "action": "invalidate_run", "triggered_now": False},
        {"condition": "threshold_or_gate_tuned_on_validation", "action": "invalidate_run", "triggered_now": False},
        {"condition": "R14_D_or_other_candidate_smuggled_into_validation", "action": "invalidate_run", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
        {"condition": "heldout_or_hard_hit1_net_negative", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "new_rank1_loss_exceeds_budget", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "single_source_or_single_province_gain_dominates", "action": "stop_source_dominated", "triggered_now": False},
        {"condition": "GoalSearcher_or_online_threshold_changed", "action": "stop_and_reject", "triggered_now": False},
    ]


def acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "heldout_hit1_net", "target": ">= 0 minimum; >0 preferred", "required_for_release_gate": True},
        {"check": "hard_hit1_net", "target": ">= 0", "required_for_release_gate": True},
        {"check": "heldout_rank1_loss_count", "target": "0 preferred; hard stop if material regression", "required_for_release_gate": True},
        {"check": "hard_rank1_loss_count", "target": "0 preferred; hard stop if material regression", "required_for_release_gate": True},
        {"check": "gate_coverage", "target": "nonzero; must remain narrow strong-challenger scope", "required_for_release_gate": True},
        {"check": "source_province_concentration", "target": "validation gains not solely explained by Zhejiang/source/fold concentration", "required_for_release_gate": True},
        {"check": "heldout_hard_used_for_selection", "target": "false", "required_for_release_gate": True},
        {"check": "claim_scope", "target": "no release claim unless package review passes", "required_for_release_gate": True},
    ]


def build_gate_rows(
    *,
    freeze_summary: dict[str, Any],
    frozen: dict[str, Any],
    explicit_go: bool,
    heldout_rows: int,
    hard_rows: int,
    validation_script_exists: bool,
) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "freeze_completed",
            "status": "pass" if freeze_summary.get("decision") == "freeze_R14_A_for_future_validation_with_risk_notes" else "fail",
            "value": freeze_summary.get("decision", ""),
            "reason": "14.5 requires a frozen candidate from 14.4.",
        },
        {
            "gate": "frozen_candidate_is_R14_A",
            "status": "pass" if frozen.get("candidate_id") == "R14_A_rank1_veto_strong_challenger" else "fail",
            "value": frozen.get("candidate_id", ""),
            "reason": "Validation boundary is only for frozen R14_A.",
        },
        {
            "gate": "zero_loss_freeze",
            "status": "pass" if to_int(frozen.get("hit1_loss")) == 0 and to_int(frozen.get("rank1_loss_count")) == 0 else "fail",
            "value": f"hit1_loss={frozen.get('hit1_loss')}; rank1_loss={frozen.get('rank1_loss_count')}",
            "reason": "Validation is justified only because R14_A froze with zero observed Top1/rank1 loss.",
        },
        {
            "gate": "risk_notes_recorded",
            "status": "pass" if frozen.get("freeze_reason") else "fail",
            "value": frozen.get("freeze_reason", ""),
            "reason": "Small signal and concentration risks must travel into validation.",
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
            "gate": "validation_harness_needed",
            "status": "warn" if not validation_script_exists else "pass",
            "value": validation_script_exists,
            "reason": "A 14.x validation harness must be created or verified before actual validation execution.",
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
            "reason": "14.5 defines validation boundary only; it does not execute heldout/hard.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_validate_fix_boundary_inputs"
    elif explicit_go:
        decision = "validation_authorized_for_frozen_R14_A"
    else:
        decision = "validation_ready_request_explicit_go_but_do_not_validate_yet"
    return rows, decision


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 14.5 Validation Boundary / Explicit Validation Go-No-Go",
        "",
        "Read-only boundary definition for possible heldout/hard A/B validation of frozen R14_A. No validation was executed in this stage.",
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
                ["dev_oof_hit1_net", m["dev_oof_hit1_net"]],
                ["dev_oof_hit1_loss", m["dev_oof_hit1_loss"]],
                ["dev_oof_rank1_loss", m["dev_oof_rank1_loss"]],
                ["risk_notes", m["risk_notes"]],
                ["heldout_rows", m["heldout_rows"]],
                ["hard_rows", m["hard_rows"]],
                ["explicit_validation_go_present", m["explicit_validation_go_present"]],
                ["validation_allowed_now", m["validation_allowed_now"]],
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
        "## Command Contract",
        "",
        md_table([["order", "phase", "status", "command"]] + [[row["order"], row["phase"], row["status"], row["command"]] for row in report["command_contract"]]),
        "",
        "## Stop Conditions",
        "",
        md_table([["condition", "action", "triggered_now"]] + [[row["condition"], row["action"], row["triggered_now"]] for row in report["stop_conditions"]]),
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
        "当前阶段：14.5 validation boundary / explicit validation go-no-go 已完成。\n"
        f"结论：{report['decision']}；冻结候选 R14_A 仍未验证，当前默认 do_not_validate。\n"
        f"下一步：只有明确说 `{REQUIRED_GO_TEXT}`，才进入 14.6 heldout/hard A/B validation；否则不跑验证。\n"
        "禁止：用 heldout/hard 重新选候选、调阈值、上线、改 GoalSearcher、把 dev/OOF freeze 宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    replacements = {
        '<div class="value">14.4 freeze</div>': '<div class="value">14.5 ready</div>',
        '14.4 freeze gate 已完成；R14_A 被冻结为未来 validation candidate，但尚未验证、未上线。': '14.5 validation boundary 已完成；R14_A 可请求未来 heldout/hard validation，但当前仍未验证、未上线。',
        '<div class="value">14.5 validation gate</div>': '<div class="value">awaiting go</div>',
        '下一步只读定义是否允许 future heldout/hard A/B validation；默认不跑验证。': '下一步只有明确 validation go 才跑 14.6；否则保持 do_not_validate。',
        '14.4 已冻结 R14_A 作为未来 validation candidate：Top1 net +3、loss 0、rank1_loss 0；下一步 14.5 validation go/no-go。': '14.5 已定义 validation boundary：R14_A 可请求 14.6 heldout/hard A/B validation，但默认不执行。',
        '<text x="939" y="317" class="pointer-text">现在在这里：14.5 validation gate</text>': '<text x="939" y="317" class="pointer-text">现在在这里：等待 validation go</text>',
        '<text x="939" y="333" class="pointer-note">R14_A frozen; no validation yet</text>': '<text x="939" y="333" class="pointer-note">14.5 boundary done; no validation yet</text>',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if "14.5 validation boundary / explicit validation go-no-go summary" not in text:
        row = f"""          <tr>
            <td>14.5 validation boundary / explicit validation go-no-go summary</td>
            <td>Read-only boundary and command contract for possible future heldout/hard A/B validation of frozen R14_A.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def update_status(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    text = f"""# Current Goal Roadmap Status

Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')} Asia/Shanghai

## Where We Are

Current stage: **14.5 validation boundary completed**.

Next executable stage: **14.6 heldout/hard A/B validation for frozen R14_A**, but only after explicit validation go.

Meaning: `R14_A_rank1_veto_strong_challenger` is frozen and validation-ready by contract. It has **not** been validated on heldout/hard, released, wired into GoalSearcher, or used to change thresholds.

## Frozen Candidate

- Candidate: `{m['candidate_id']}`
- Dev/OOF Top1 net: `{m['dev_oof_hit1_net']}`
- Dev/OOF Top1 loss: `{m['dev_oof_hit1_loss']}`
- Dev/OOF rank1 loss: `{m['dev_oof_rank1_loss']}`
- Risk notes: `{m['risk_notes']}`

## Current Boundary

Default: `do_not_validate`.

To continue, provide exactly:

`{REQUIRED_GO_TEXT}`

Blocked until explicit validation go:

- heldout/hard A/B validation
- release
- GoalSearcher integration
- online threshold changes
- changing frozen candidate or gates using validation feedback
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.5 validation boundary / explicit validation go-no-go")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--frozen-candidate", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--explicit-validation-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--status-md", type=Path, default=DEFAULT_STATUS_MD)
    args = parser.parse_args()

    freeze = read_json(args.freeze_summary)
    frozen = read_json(args.frozen_candidate)
    heldout_rows = line_count(args.heldout)
    hard_rows = line_count(args.hard)
    validation_script_exists = (PROJECT_ROOT / "tools" / "goal_14x_rank1_safe_source_robust_validation_ab.py").exists()
    gates, decision = build_gate_rows(
        freeze_summary=freeze,
        frozen=frozen,
        explicit_go=args.explicit_validation_go,
        heldout_rows=heldout_rows,
        hard_rows=hard_rows,
        validation_script_exists=validation_script_exists,
    )
    output_prefix = args.output_prefix
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
        "stage": "14.5 validation boundary / explicit validation go-no-go",
        "read_only_review": True,
        "decision": decision,
        "required_go_text": REQUIRED_GO_TEXT,
        "metrics": {
            "candidate_id": frozen.get("candidate_id", ""),
            "dev_oof_hit1_net": frozen.get("hit1_net", 0),
            "dev_oof_hit1_loss": frozen.get("hit1_loss", 0),
            "dev_oof_rank1_loss": frozen.get("rank1_loss_count", 0),
            "risk_notes": frozen.get("freeze_reason", ""),
            "heldout_rows": heldout_rows,
            "hard_rows": hard_rows,
            "validation_script_exists": validation_script_exists,
            "explicit_validation_go_present": args.explicit_validation_go,
            "validation_allowed_now": args.explicit_validation_go and decision == "validation_authorized_for_frozen_R14_A",
        },
        "gate_rows": gates,
        "boundary_rows": boundary_rows(),
        "command_contract": command_contract(),
        "required_artifacts": required_artifacts(),
        "stop_conditions": stop_conditions(args.explicit_validation_go),
        "acceptance_checks": acceptance_checks(),
        "artifacts": artifacts,
        "next_stage": {
            "recommended": f"If you want to continue boldly, provide `{REQUIRED_GO_TEXT}`. Without that, default remains do_not_validate.",
            "default": "do_not_validate",
        },
        "anti_drift_conclusion": (
            "14.5 is read-only. It did not run heldout/hard, did not train, did not validate, did not release, "
            "did not edit GoalSearcher, and did not tune thresholds. It only defined the future validation boundary."
        ),
    }
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    write_csv(Path(artifacts["gate_checks_csv"]), gates, ["gate", "status", "value", "reason"])
    write_csv(Path(artifacts["boundary_contract_csv"]), report["boundary_rows"], ["boundary", "decision", "details"])
    write_csv(Path(artifacts["command_contract_csv"]), report["command_contract"], ["order", "phase", "allowed_after_explicit_go", "command", "status"])
    write_csv(Path(artifacts["required_artifacts_csv"]), report["required_artifacts"], ["artifact", "required", "purpose"])
    write_csv(Path(artifacts["stop_conditions_csv"]), report["stop_conditions"], ["condition", "action", "triggered_now"])
    write_csv(Path(artifacts["acceptance_checks_csv"]), report["acceptance_checks"], ["check", "target", "required_for_release_gate"])
    update_dashboard(args.dashboard, report)
    update_status(args.status_md, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
