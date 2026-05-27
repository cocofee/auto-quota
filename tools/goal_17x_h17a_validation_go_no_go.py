from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_FREEZE_SUMMARY = AGENT_STATE / "goal_17x_h17a_freeze_gate_validation_boundary_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_17x_h17a_validation_go_no_go"
DEFAULT_HELDOUT = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "heldout_validation.jsonl"
DEFAULT_HARD = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "hard_validation.jsonl"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_multifield.jsonl"

REQUIRED_GO_TEXT = "go: run 17.17 heldout/hard validation for frozen H17_A"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _status(value: bool) -> str:
    return "present" if value else "missing"


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "order": 1,
            "phase": "frozen_h17a_validation",
            "allowed_after_explicit_go": True,
            "command": (
                "python tools\\goal_17x_h17a_heldout_hard_validation.py "
                "--freeze-summary reports\\agent_state\\goal_17x_h17a_freeze_gate_validation_boundary_summary.json "
                "--index data\\goal_search\\oss_recall_index_17x_multifield.jsonl "
                "--output-prefix reports\\agent_state\\goal_17x_h17a_heldout_hard_validation"
            ),
            "status": "not_executed_in_17_16",
        },
        {
            "order": 2,
            "phase": "validation_package_review",
            "allowed_after_explicit_go": True,
            "command": "Review the frozen H17_A heldout/hard summary, scorecard, row audit, stop conditions, and family signal artifacts.",
            "status": "not_executed_in_17_16",
        },
    ]


def _boundary_rows() -> list[dict[str, Any]]:
    return [
        {
            "boundary": "candidate_scope",
            "decision": "frozen_H17_A_only",
            "details": "Validate only H17_A: TopK=3, support>=2, source_family>=1, overlap>=2, broad mode, core families concrete/pump/rebar.",
        },
        {
            "boundary": "family_scope",
            "decision": "pipe_support_veto_must_hold",
            "details": "H17_A must not include pipe or support candidates; H17_B heldout/hard pipe failure is diagnostic only.",
        },
        {
            "boundary": "selection_policy",
            "decision": "validation_only_not_selection",
            "details": "Heldout/hard may not tune thresholds, re-admit families, change support/source/overlap guards, or select a new candidate.",
        },
        {
            "boundary": "comparison_design",
            "decision": "baseline_vs_frozen_H17_A_ab",
            "details": "A valid run compares current baseline ranking against the frozen H17_A recall prior on identical heldout/hard rows.",
        },
        {
            "boundary": "online_boundary",
            "decision": "no_online_or_default_enablement",
            "details": "Validation cannot change GoalSearcher defaults, enable OSS recall online, or modify production thresholds.",
        },
    ]


def _required_artifacts() -> list[dict[str, Any]]:
    return [
        {"artifact": "summary_json", "required": True, "purpose": "final heldout/hard pass/fail and headline metrics"},
        {"artifact": "summary_md", "required": True, "purpose": "human-readable validation interpretation"},
        {"artifact": "scorecard_csv", "required": True, "purpose": "heldout/hard/all Top1/Top5/Top20/Top80 and loss metrics"},
        {"artifact": "row_audit_csv", "required": True, "purpose": "per-row before/after and generated candidate audit"},
        {"artifact": "stop_conditions_csv", "required": True, "purpose": "release-blocking validation gates"},
        {"artifact": "family_signal_csv", "required": True, "purpose": "concrete/pump/rebar slice robustness"},
    ]


def _stop_conditions(explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {"condition": "no_explicit_validation_go", "action": "do_not_validate", "triggered_now": not explicit_go},
        {"condition": "freeze_summary_missing_or_not_h17a", "action": "stop_and_report", "triggered_now": False},
        {"condition": "candidate_contract_changed", "action": "invalidate_run", "triggered_now": False},
        {"condition": "pipe_or_support_reintroduced", "action": "invalidate_run", "triggered_now": False},
        {"condition": "threshold_or_family_tuned_on_heldout_hard", "action": "invalidate_run", "triggered_now": False},
        {"condition": "artifact_missing_or_schema_invalid", "action": "stop_and_report", "triggered_now": False},
        {"condition": "any_top1_loss", "action": "stop_do_not_release", "triggered_now": False},
        {"condition": "false_candidate_dominance", "action": "stop_do_not_release", "triggered_now": False},
        {"condition": "single_source_or_single_family_dominance", "action": "stop_source_dominated", "triggered_now": False},
        {"condition": "goal_searcher_or_online_default_changed", "action": "stop_and_reject", "triggered_now": False},
    ]


def _go_requirements(explicit_go: bool, freeze_ready: bool, heldout: Path, hard: Path, index: Path) -> list[dict[str, Any]]:
    return [
        {
            "requirement": "explicit_validation_go_text",
            "status": _status(explicit_go),
            "needed_text": REQUIRED_GO_TEXT,
        },
        {
            "requirement": "h17a_freeze_summary_ready",
            "status": _status(freeze_ready),
            "needed_text": str(DEFAULT_FREEZE_SUMMARY),
        },
        {
            "requirement": "heldout_split_available",
            "status": _status(heldout.exists()),
            "needed_text": str(heldout),
        },
        {
            "requirement": "hard_split_available",
            "status": _status(hard.exists()),
            "needed_text": str(hard),
        },
        {
            "requirement": "oss_17x_index_available",
            "status": _status(index.exists()),
            "needed_text": str(index),
        },
    ]


def _blocked_actions(validation_allowed_now: bool, explicit_go: bool) -> list[dict[str, Any]]:
    return [
        {
            "action": "run_validation_now",
            "blocked": not validation_allowed_now,
            "reason": "explicit validation go is missing" if not explicit_go else "allowed only in the validation execution stage",
        },
        {"action": "tune_or_select_on_heldout_hard", "blocked": True, "reason": "heldout/hard are validation-only"},
        {"action": "reintroduce_pipe_or_support", "blocked": True, "reason": "H17_A frozen contract vetoes both families"},
        {"action": "default_enable_oss_recall", "blocked": True, "reason": "offline validation does not authorize online/default behavior"},
        {"action": "change_goal_searcher_defaults", "blocked": True, "reason": "17.16 is an authorization boundary only"},
        {"action": "claim_release_ready", "blocked": True, "reason": "requires a future validation package pass"},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["frozen_h17a_headline"]
    lines = [
        "# 17.16 H17_A Validation Go/No-Go",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Frozen H17_A Evidence",
        "",
        f"- dev/OOF Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`.",
        f"- Top1 wins/losses: `{h['top1_wins']}/{h['top1_losses']}`.",
        f"- generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`.",
        f"- false rate: `{h['prior_false_candidate_rate']}`.",
        "",
        "## Go Requirements",
        "",
        "| requirement | status | needed_text |",
        "|---|---|---|",
    ]
    for row in report["go_requirements"]:
        lines.append(f"| {row['requirement']} | {row['status']} | {row['needed_text']} |")
    lines.extend(["", "## Blocked Actions", "", "| action | blocked | reason |", "|---|---|---|"])
    for row in report["blocked_actions"]:
        lines.append(f"| {row['action']} | {row['blocked']} | {row['reason']} |")
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="17.16 explicit heldout/hard validation go/no-go for frozen H17_A")
    parser.add_argument("--explicit-validation-go", action="store_true")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE_SUMMARY)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    freeze = _read_json(args.freeze_summary)
    freeze_ready = freeze.get("decision") == "freeze_h17a_for_validation_request_boundary"
    explicit_go = bool(args.explicit_validation_go)
    prerequisites_ready = freeze_ready and args.heldout.exists() and args.hard.exists() and args.index.exists()
    validation_allowed_now = explicit_go and prerequisites_ready
    decision = "validation_authorized_for_next_execution_stage" if validation_allowed_now else "do_not_validate_yet"

    boundary_rows = _boundary_rows()
    command_contract = _command_contract()
    required_artifacts = _required_artifacts()
    stop_conditions = _stop_conditions(explicit_go)
    go_requirements = _go_requirements(explicit_go, freeze_ready, args.heldout, args.hard, args.index)
    blocked_actions = _blocked_actions(validation_allowed_now, explicit_go)

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    boundary_csv = args.output_prefix.with_name(args.output_prefix.name + "_validation_boundary.csv")
    command_csv = args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")
    artifacts_csv = args.output_prefix.with_name(args.output_prefix.name + "_required_artifacts.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    req_csv = args.output_prefix.with_name(args.output_prefix.name + "_go_requirements.csv")
    blocked_csv = args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")

    report = {
        "stage": "17.16 explicit heldout/hard validation go/no-go for frozen H17_A",
        "decision": decision,
        "explicit_validation_go_present": explicit_go,
        "validation_allowed_now": validation_allowed_now,
        "prerequisites_ready": prerequisites_ready,
        "frozen_candidate": "H17_A",
        "frozen_contract": {
            "top_k": 3,
            "min_support": 2,
            "min_source_families": 1,
            "min_overlap": 2,
            "intervention_mode": "broad",
            "core_families": ["concrete", "pump", "rebar"],
            "vetoed_families": ["pipe", "support"],
        },
        "frozen_h17a_headline": freeze["headline"],
        "metrics": {
            "heldout_rows": _line_count(args.heldout),
            "hard_rows": _line_count(args.hard),
            "validation_command_count": len(command_contract),
            "required_artifact_count": len(required_artifacts),
            "stop_condition_count": len(stop_conditions),
            "training_allowed": False,
            "threshold_change_allowed": False,
            "goal_searcher_change_allowed": False,
            "online_integration_allowed": False,
        },
        "validation_boundary": boundary_rows,
        "command_contract": command_contract,
        "required_artifacts": required_artifacts,
        "stop_conditions": stop_conditions,
        "go_requirements": go_requirements,
        "blocked_actions": blocked_actions,
        "next_stage": {
            "stage": "17.17 heldout/hard validation for frozen H17_A",
            "default": "do_not_execute_without_explicit_go",
            "required_user_text": REQUIRED_GO_TEXT,
        },
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "validation_boundary_csv": str(boundary_csv),
            "command_contract_csv": str(command_csv),
            "required_artifacts_csv": str(artifacts_csv),
            "stop_conditions_csv": str(stop_csv),
            "go_requirements_csv": str(req_csv),
            "blocked_actions_csv": str(blocked_csv),
        },
        "anti_drift_conclusion": (
            "17.16 is a read-only authorization boundary. It did not run heldout/hard validation, train, tune, "
            "change thresholds, reintroduce pipe/support, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(boundary_csv, boundary_rows, ["boundary", "decision", "details"])
    _write_csv(command_csv, command_contract, ["order", "phase", "allowed_after_explicit_go", "command", "status"])
    _write_csv(artifacts_csv, required_artifacts, ["artifact", "required", "purpose"])
    _write_csv(stop_csv, stop_conditions, ["condition", "action", "triggered_now"])
    _write_csv(req_csv, go_requirements, ["requirement", "status", "needed_text"])
    _write_csv(blocked_csv, blocked_actions, ["action", "blocked", "reason"])
    print(json.dumps({"decision": decision, "validation_allowed_now": validation_allowed_now, "required_go_text": REQUIRED_GO_TEXT}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
