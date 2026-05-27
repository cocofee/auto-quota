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
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review_summary.json"
DEFAULT_FROZEN = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review_frozen_candidate.json"
DEFAULT_HELDOUT = SPLITS_EXPANDED / "heldout.jsonl"
DEFAULT_HARD = SPLITS_EXPANDED / "hard.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_r14_v2_validation_boundary_go_no_go"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_STATUS_MD = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
VALIDATION_SCRIPT = PROJECT_ROOT / "tools" / "goal_14x_r14_v2_bolder_rank1_safe_validation_ab.py"
PACKAGE_REVIEW_SCRIPT = PROJECT_ROOT / "tools" / "goal_14x_r14_v2_bolder_rank1_safe_validation_package_review.py"

REQUIRED_GO_TEXT = "go: run 14.14 heldout/hard A/B validation for frozen R14V2_E"


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


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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


def _boundary_rows(frozen: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "boundary": "candidate_scope",
            "decision": "frozen_R14V2_E_only",
            "details": f"Validation may evaluate only {frozen.get('candidate_id')} from the 14.12 frozen manifest.",
        },
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "Heldout/hard may not switch to R14V2_A/D/B, tune gates, change thresholds, change feature toggles, or expand candidate matrix.",
        },
        {
            "boundary": "comparison_design",
            "decision": "baseline_vs_frozen_R14V2_E_ab",
            "details": "A valid run compares current baseline ranking against fixed R14V2_E policy on identical heldout/hard split rows.",
        },
        {
            "boundary": "risk_policy",
            "decision": "validate_because_dev_oof_positive_and_zero_loss",
            "details": "R14V2_E froze with dev/OOF +11 Top1 net, zero Top1/rank1 loss, higher coverage than R14_A, and source share within budget.",
        },
        {
            "boundary": "claim_scope",
            "decision": "no_general_top1_claim_until_validation_passes",
            "details": "Current evidence is dev/OOF only; no heldout/hard claim exists yet.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_switch",
            "details": "Validation cannot edit GoalSearcher, connect online, alter fallback behavior, or change production thresholds.",
        },
    ]


def _command_contract() -> list[dict[str, Any]]:
    manifest = "reports/agent_state/goal_14x_r14_v2_bolder_rank1_safe_freeze_gate_review_frozen_candidate.json"
    return [
        {
            "order": 1,
            "phase": "validation_harness_compile",
            "allowed_after_explicit_go": True,
            "command": "python -m py_compile tools/goal_14x_r14_v2_bolder_rank1_safe_validation_ab.py tools/goal_14x_r14_v2_bolder_rank1_safe_validation_package_review.py",
            "status": "not_executed_in_14_13",
        },
        {
            "order": 2,
            "phase": "heldout_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_14x_r14_v2_bolder_rank1_safe_validation_ab.py "
                "--split heldout --input data/goal_search/splits_expanded/heldout.jsonl "
                f"--frozen-candidate-manifest {manifest} "
                "--candidate-id R14V2_E_rank1_shadow_no_demote "
                "--output-prefix reports/agent_state/goal_14x_r14_v2_bolder_rank1_safe_validation_heldout"
            ),
            "status": "not_executed_in_14_13",
        },
        {
            "order": 3,
            "phase": "hard_ab_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools/goal_14x_r14_v2_bolder_rank1_safe_validation_ab.py "
                "--split hard --input data/goal_search/splits_expanded/hard.jsonl "
                f"--frozen-candidate-manifest {manifest} "
                "--candidate-id R14V2_E_rank1_shadow_no_demote "
                "--output-prefix reports/agent_state/goal_14x_r14_v2_bolder_rank1_safe_validation_hard"
            ),
            "status": "not_executed_in_14_13",
        },
        {
            "order": 4,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "python tools/goal_14x_r14_v2_bolder_rank1_safe_validation_package_review.py",
            "status": "not_executed_in_14_13",
        },
    ]


def _required_artifacts() -> list[dict[str, Any]]:
    return [
        {"artifact": "heldout_ab_summary_json", "required": True, "purpose": "heldout baseline-vs-frozen hit1/hit5 metrics and loss budget"},
        {"artifact": "heldout_details_jsonl", "required": True, "purpose": "per-row heldout before/after audit"},
        {"artifact": "heldout_gate_coverage_csv", "required": True, "purpose": "R14V2_E gate coverage and outcomes"},
        {"artifact": "heldout_loss_slices_csv", "required": True, "purpose": "heldout losses by province/source/query_family/top1_family/rank bucket"},
        {"artifact": "heldout_source_concentration_csv", "required": True, "purpose": "check whether validation net is source/province/fold dominated"},
        {"artifact": "hard_ab_summary_json", "required": True, "purpose": "hard split robustness metrics"},
        {"artifact": "hard_details_jsonl", "required": True, "purpose": "per-row hard before/after audit"},
        {"artifact": "hard_gate_coverage_csv", "required": True, "purpose": "hard split R14V2_E gate coverage"},
        {"artifact": "hard_loss_slices_csv", "required": True, "purpose": "hard losses by slice"},
        {"artifact": "validation_package_review_summary_json", "required": True, "purpose": "final validation pass/fail and release-gate recommendation"},
    ]


def _stop_conditions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "validation_harness_missing_after_go", "action": "create_or_stop_before_validation", "triggered_now": False},
        {"condition": "frozen_candidate_missing_or_changed", "action": "stop_and_report", "triggered_now": False},
        {"condition": "candidate_reselected_using_heldout_or_hard", "action": "invalidate_run", "triggered_now": False},
        {"condition": "threshold_or_gate_tuned_on_validation", "action": "invalidate_run", "triggered_now": False},
        {"condition": "other_R14V2_candidate_smuggled_into_validation", "action": "invalidate_run", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
        {"condition": "heldout_or_hard_hit1_net_negative", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "new_rank1_loss_exceeds_budget", "action": "stop_before_release_gate", "triggered_now": False},
        {"condition": "single_source_or_single_province_gain_dominates", "action": "stop_source_dominated", "triggered_now": False},
        {"condition": "GoalSearcher_or_online_threshold_changed", "action": "stop_and_reject", "triggered_now": False},
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "heldout_hit1_net", "target": ">= 0 minimum; >0 preferred", "required_for_release_gate": True},
        {"check": "hard_hit1_net", "target": ">= 0", "required_for_release_gate": True},
        {"check": "heldout_rank1_loss_count", "target": "0 preferred; hard stop if material regression", "required_for_release_gate": True},
        {"check": "hard_rank1_loss_count", "target": "0 preferred; hard stop if material regression", "required_for_release_gate": True},
        {"check": "gate_coverage", "target": "nonzero; should remain bounded relative to dev/OOF applied rate", "required_for_release_gate": True},
        {"check": "source_province_concentration", "target": "validation gains not solely explained by one source/province", "required_for_release_gate": True},
        {"check": "heldout_hard_used_for_selection", "target": "false", "required_for_release_gate": True},
        {"check": "claim_scope", "target": "no release claim unless package review passes", "required_for_release_gate": True},
    ]


def _gate_rows(
    *,
    freeze_summary: dict[str, Any],
    frozen: dict[str, Any],
    explicit_go: bool,
    heldout_rows: int,
    hard_rows: int,
    validation_script_exists: bool,
    package_review_exists: bool,
) -> tuple[list[dict[str, Any]], str]:
    rows = [
        {
            "gate": "freeze_completed",
            "status": "pass" if freeze_summary.get("decision") == "freeze_R14_v2_candidate_for_future_validation" else "fail",
            "value": freeze_summary.get("decision", ""),
            "reason": "14.13 requires a frozen candidate from 14.12.",
        },
        {
            "gate": "frozen_candidate_is_R14V2_E",
            "status": "pass" if frozen.get("candidate_id") == "R14V2_E_rank1_shadow_no_demote" else "fail",
            "value": frozen.get("candidate_id", ""),
            "reason": "Validation boundary is only for frozen R14V2_E.",
        },
        {
            "gate": "zero_loss_freeze",
            "status": "pass" if _int(frozen.get("hit1_loss")) == 0 and _int(frozen.get("rank1_loss_count")) == 0 else "fail",
            "value": f"hit1_loss={frozen.get('hit1_loss')}; rank1_loss={frozen.get('rank1_loss_count')}",
            "reason": "Validation is justified only because R14V2_E froze with zero observed Top1/rank1 loss.",
        },
        {
            "gate": "positive_dev_oof_signal",
            "status": "pass" if _int(frozen.get("hit1_net")) > 0 and _float(frozen.get("applied_group_rate")) > 0.00232 else "fail",
            "value": f"hit1_net={frozen.get('hit1_net')}; applied_rate={frozen.get('applied_group_rate')}",
            "reason": "R14V2_E must be materially less no-op than R14_A.",
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
            "gate": "validation_harness_exists",
            "status": "warn" if not validation_script_exists else "pass",
            "value": validation_script_exists,
            "reason": "A R14 v2 validation harness must exist or be created after explicit validation go.",
        },
        {
            "gate": "package_review_harness_exists",
            "status": "warn" if not package_review_exists else "pass",
            "value": package_review_exists,
            "reason": "A package review harness must exist or be created after explicit validation go.",
        },
        {
            "gate": "explicit_validation_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": "Default is do_not_validate unless user provides the exact explicit go.",
        },
    ]
    hard_fails = [row for row in rows if row["status"] == "fail"]
    if hard_fails:
        decision = "validation_not_ready_fix_failed_gates"
    elif explicit_go:
        decision = "validation_ready_and_explicit_go_present"
    else:
        decision = "validation_ready_request_explicit_go_but_do_not_validate_yet"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    f = report["frozen_candidate"]
    gate_table = [["gate", "status", "value", "reason"]]
    for row in report["gate_checks"]:
        gate_table.append([row["gate"], row["status"], row["value"], row["reason"]])
    lines = [
        "# 14.13 R14 v2 Validation Boundary / Explicit Go-No-Go",
        "",
        "Read-only boundary definition for future heldout/hard A/B validation. No validation was run.",
        "",
        "## Frozen Candidate",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_id", f.get("candidate_id")],
                ["dev_oof hit1 gain/loss/net", f"{f.get('hit1_gain')}/{f.get('hit1_loss')}/{f.get('hit1_net')}"],
                ["rank1_loss_count", f.get("rank1_loss_count")],
                ["hit5_net", f.get("hit5_net")],
                ["applied_group_rate", f.get("applied_group_rate")],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(gate_table),
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Default: `{report['default_action']}`",
        f"- Required explicit go: `{REQUIRED_GO_TEXT}`",
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    f = report["frozen_candidate"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **14.13 R14 v2 validation boundary / explicit validation go-no-go completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "Validation boundary is ready, but no heldout/hard validation has been run because explicit validation go was not provided in this stage.",
        "",
        "## Frozen Candidate",
        "",
        f"- Candidate: `{f.get('candidate_id')}`",
        f"- Dev/OOF Top1 gain/loss/net: `{f.get('hit1_gain')}/{f.get('hit1_loss')}/{f.get('hit1_net')}`",
        f"- rank1 loss: `{f.get('rank1_loss_count')}`",
        f"- applied group rate: `{f.get('applied_group_rate')}`",
        "",
        "## Current Boundary",
        "",
        "- Default remains `do_not_validate`.",
        "- Future validation may evaluate only frozen `R14V2_E_rank1_shadow_no_demote`.",
        "- Heldout/hard cannot be used for tuning, candidate switching, or threshold changes.",
        "- No release or GoalSearcher integration is allowed.",
        "",
        "Required explicit go:",
        "",
        f"`{REQUIRED_GO_TEXT}`",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    f = report["frozen_candidate"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.13 R14 v2 validation boundary / explicit validation go-no-go 已完成。\n"
        f"结论：{report['decision']}；默认 do_not_validate。frozen={f.get('candidate_id')}，dev/OOF Top1 net={f.get('hit1_net')}，rank1_loss={f.get('rank1_loss_count')}。\n"
        f"下一步：只有明确说 `{REQUIRED_GO_TEXT}`，才进入 14.14 heldout/hard A/B validation；否则不跑验证。\n"
        "禁止：用 heldout/hard 调参、切换候选、改阈值、上线、改 GoalSearcher。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.13 R14 v2 validation boundary" not in text:
        row = f"""          <tr>
            <td>14.13 R14 v2 validation boundary</td>
            <td>Read-only heldout/hard validation boundary and explicit go/no-go contract for frozen R14V2_E.</td>
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
    parser = argparse.ArgumentParser(description="14.13 R14 v2 validation boundary / explicit go-no-go")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--frozen-candidate", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--status-md", type=Path, default=DEFAULT_STATUS_MD)
    parser.add_argument("--explicit-validation-go", action="store_true")
    args = parser.parse_args()

    freeze_summary = _read_json(args.freeze_summary)
    frozen = _read_json(args.frozen_candidate)
    heldout_rows = _line_count(args.heldout)
    hard_rows = _line_count(args.hard)
    gate_checks, decision = _gate_rows(
        freeze_summary=freeze_summary,
        frozen=frozen,
        explicit_go=args.explicit_validation_go,
        heldout_rows=heldout_rows,
        hard_rows=hard_rows,
        validation_script_exists=VALIDATION_SCRIPT.exists(),
        package_review_exists=PACKAGE_REVIEW_SCRIPT.exists(),
    )

    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "boundary_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_boundary_contract.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "required_artifacts_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_required_artifacts.csv")),
        "stop_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")),
        "acceptance_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_acceptance_checks.csv")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
    }
    report = {
        "stage": "14.13 R14 v2 validation boundary / explicit validation go-no-go",
        "read_only_boundary": True,
        "decision": decision,
        "default_action": "do_not_validate",
        "explicit_validation_go": args.explicit_validation_go,
        "required_go_text": REQUIRED_GO_TEXT,
        "frozen_candidate": frozen,
        "heldout_rows": heldout_rows,
        "hard_rows": hard_rows,
        "validation_script_exists": VALIDATION_SCRIPT.exists(),
        "package_review_script_exists": PACKAGE_REVIEW_SCRIPT.exists(),
        "gate_checks": gate_checks,
        "boundary_contract": _boundary_rows(frozen),
        "command_contract": _command_contract(),
        "required_artifacts": _required_artifacts(),
        "stop_conditions": _stop_conditions(args.explicit_validation_go),
        "acceptance_checks": _acceptance_checks(),
        "artifacts": artifacts,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "anti_drift_conclusion": (
            "14.13 defined validation boundaries only. It did not run heldout/hard validation, train, tune thresholds, "
            "switch candidates, release code, edit GoalSearcher, or change online behavior."
        ),
        "next_stage": {
            "recommended": "14.14 heldout/hard A/B validation for frozen R14V2_E only after explicit validation go",
            "default": "do_not_validate",
        },
    }
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["boundary_contract_csv"]), report["boundary_contract"], ["boundary", "decision", "details"])
    _write_csv(Path(artifacts["command_contract_csv"]), report["command_contract"], ["order", "phase", "allowed_after_explicit_go", "command", "status"])
    _write_csv(Path(artifacts["required_artifacts_csv"]), report["required_artifacts"], ["artifact", "required", "purpose"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), report["stop_conditions"], ["condition", "action", "triggered_now"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), report["acceptance_checks"], ["check", "target", "required_for_release_gate"])
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "value", "reason"])
    _update_status(args.status_md, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "default": "do_not_validate"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
