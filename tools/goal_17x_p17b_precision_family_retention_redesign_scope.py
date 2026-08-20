from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
RETENTION_RESCUE_SUMMARY = AGENT_STATE / "goal_17x_p17b_retention_rescue_dev_oof_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_p17b_precision_family_retention_redesign_scope"


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


def _candidate_matrix() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "P17_H_p17f_plus_rebar_specific_rescue",
            "role": "rebar retention rescue",
            "base_trunk": "P17_F TopK=1 strong trunk",
            "top_k": 1,
            "family_scope": "concrete,pump,rebar; rescue branch only for rebar",
            "observable_guard": "P17_F trunk OR rebar candidate with exact_name OR source_family>=2 + support>=3 + overlap>=2 + quota_name_overlap>=1 + quota_specific_overlap>=1",
            "expected_effect": "Try to recover rebar positive family without opening pump/pipe/support noise.",
            "pass_gate": "top1_loss=0; delta_top1>=5; false_candidates<=10; positive_candidates>=8; positive_family_count>=2",
            "stop_condition": "Stop if the rebar branch adds any Top1 loss or false_candidates>10.",
        },
        {
            "candidate_id": "P17_I_p17f_plus_pump_specific_rescue",
            "role": "pump retention rescue",
            "base_trunk": "P17_F TopK=1 strong trunk",
            "top_k": 1,
            "family_scope": "concrete,pump,rebar; rescue branch only for pump",
            "observable_guard": "P17_F trunk OR pump candidate with exact_name OR source_family>=2 + support>=4 + overlap>=3 + quota_name_overlap>=1 + quota_specific_overlap>=1",
            "expected_effect": "Test whether pump needs a stricter support/source gate than rebar to avoid false candidates.",
            "pass_gate": "top1_loss=0; delta_top1>=5; false_candidates<=10; positive_candidates>=8; positive_family_count>=2",
            "stop_condition": "Stop if pump rescue does not add a positive family or raises false_candidates above P17_F by more than 3.",
        },
        {
            "candidate_id": "P17_J_p17f_plus_family_specific_rescue",
            "role": "combined pump/rebar rescue",
            "base_trunk": "P17_F TopK=1 strong trunk",
            "top_k": 1,
            "family_scope": "concrete,pump,rebar; separate pump/rebar compatibility guards",
            "observable_guard": "P17_F trunk OR rebar guard from P17_H OR pump guard from P17_I; branch candidates must have quota_name_overlap>=1 and quota_specific_overlap>=1 unless exact_name",
            "expected_effect": "Combine branch-specific evidence only after both families have stronger identity compatibility than P17_G.",
            "pass_gate": "top1_loss=0; delta_top1>=5; delta_top5>=3; false_candidates<=12; positive_family_count>=2",
            "stop_condition": "Stop if combined rescue behaves like P17_G false expansion rather than P17_F precision.",
        },
        {
            "candidate_id": "P17_K_p17f_plus_capped_second_family_slot",
            "role": "rank-position rescue cap",
            "base_trunk": "P17_F TopK=1 strong trunk",
            "top_k": 2,
            "family_scope": "first candidate from P17_F; optional second candidate only for pump/rebar",
            "observable_guard": "accepted_count=0 uses P17_F trunk; accepted_count=1 requires pump/rebar + source_family>=2 + support>=5 + overlap>=3 + quota_name_overlap>=1 + quota_specific_overlap>=2",
            "expected_effect": "Retain P17_G's Top5 upside while preventing broad false candidate spillover.",
            "pass_gate": "top1_loss=0; delta_top1>=5; delta_top5>=4; false_candidates<=15; positive_family_count>=2",
            "stop_condition": "Stop if second-slot false candidates exceed 8 or total false_candidates>15.",
        },
    ]


def _field_manifest() -> list[dict[str, Any]]:
    return [
        {"field": "query_family", "required": True, "purpose": "Limit family scope to concrete/pump/rebar and apply pump/rebar branch rules."},
        {"field": "oss_recall_exact_name", "required": True, "purpose": "Allow high-confidence identity matches without extra compatibility fields."},
        {"field": "oss_recall_source_family_count", "required": True, "purpose": "Require independent OSS support for non-exact rescue candidates."},
        {"field": "oss_recall_support_count", "required": True, "purpose": "Avoid one-off weak OSS pair matches."},
        {"field": "oss_recall_overlap", "required": True, "purpose": "General bill/quota lexical compatibility."},
        {"field": "oss_recall_quota_name_overlap", "required": True, "purpose": "Require candidate quota-name compatibility for non-exact pump/rebar rescue."},
        {"field": "oss_recall_quota_specific_overlap", "required": True, "purpose": "Require material/action/spec compatibility for non-exact pump/rebar rescue."},
        {"field": "accepted_count", "required": True, "purpose": "Allow P17_K to cap the optional second candidate without broad TopK expansion."},
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "dev_oof_only", "target": "true", "failure_action": "invalidate execution"},
        {"check": "top1_loss_guard", "target": "top1_losses=0", "failure_action": "candidate no-go"},
        {"check": "p17f_precision_preservation", "target": "delta_top1>=5 and false_candidates<=12", "failure_action": "candidate too loose or too weak"},
        {"check": "family_retention", "target": "positive_candidates>=8 and positive_family_count>=2", "failure_action": "candidate failed the reason for 17.23"},
        {"check": "p17g_false_control", "target": "false_candidates materially below P17_G false=19; preferred <=12, hard max <=15", "failure_action": "redesign instead of freeze"},
        {"check": "online_observable_only", "target": "no expected_id, heldout/hard labels, or validation-derived row constants", "failure_action": "reject candidate"},
        {"check": "default_off_boundary", "target": "no default enablement or GoalSearcher default change", "failure_action": "stop and report drift"},
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": "17.24 future explicit go only",
            "command": "python tools\\goal_17x_p17b_precision_family_retention_dev_oof_shadow.py --candidate all --progress-every 10",
            "allowed_after": "explicit go to implement/run the fixed P17_H/I/J/K dev/OOF-only matrix",
            "blocked_now": True,
        },
        {
            "stage": "future validation only after freeze",
            "command": "heldout/hard validation command may be generated only after a dev/OOF freeze gate passes",
            "allowed_after": "future freeze gate plus explicit validation go",
            "blocked_now": True,
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.23 P17_B Precision + Family-Retention Redesign Scope",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Evidence Basis",
        "",
        f"- P17_F clean trunk: `{report['p17f']['delta_top1']}/{report['p17f']['delta_top5']}/{report['p17f']['delta_top20']}/{report['p17f']['delta_top80']}`, wins/losses `{report['p17f']['top1_wins']}/{report['p17f']['top1_losses']}`, false `{report['p17f']['prior_false_candidates']}`.",
        f"- P17_G rescue signal: `{report['p17g']['delta_top1']}/{report['p17g']['delta_top5']}/{report['p17g']['delta_top20']}/{report['p17g']['delta_top80']}`, wins/losses `{report['p17g']['top1_wins']}/{report['p17g']['top1_losses']}`, false `{report['p17g']['prior_false_candidates']}`.",
        "",
        "## Fixed Future Matrix",
        "",
        "| candidate | role | top_k | guard | pass gate |",
        "|---|---|---:|---|---|",
    ]
    for row in report["candidate_matrix"]:
        lines.append(f"| {row['candidate_id']} | {row['role']} | {row['top_k']} | {row['observable_guard']} | {row['pass_gate']} |")
    lines.extend(["", "## Acceptance Checks", "", "| check | target | failure action |", "|---|---|---|"])
    for row in report["acceptance_checks"]:
        lines.append(f"| {row['check']} | {row['target']} | {row['failure_action']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    source = _read_json(RETENTION_RESCUE_SUMMARY)
    if source.get("decision") != "no_freeze_candidate_all_retention_rescue_candidates_failed_dev_oof_gate":
        raise ValueError(f"unexpected 17.22 decision: {source.get('decision')}")

    by_candidate = {row["candidate"]: row for row in source["comparison"]}
    candidate_matrix = _candidate_matrix()
    field_manifest = _field_manifest()
    acceptance_checks = _acceptance_checks()
    command_contract = _command_contract()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    matrix_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_candidate_matrix.csv")
    fields_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_field_manifest.csv")
    checks_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_acceptance_checks.csv")
    commands_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_command_contract.csv")

    report = {
        "stage": "17.23 P17_B precision-plus-family-retention redesign scope",
        "decision": "scope_locked_request_explicit_p17hijk_dev_oof_execution_go",
        "read_only_scope": True,
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "source_stage": source["stage"],
        "source_decision": source["decision"],
        "p17f": by_candidate["P17_F"],
        "p17g": by_candidate["P17_G"],
        "candidate_matrix": candidate_matrix,
        "field_manifest": field_manifest,
        "acceptance_checks": acceptance_checks,
        "command_contract": command_contract,
        "next_boundary": (
            "17.24 may implement/run the fixed P17_H/I/J/K dev/OOF-only shadow matrix only after explicit go. "
            "No heldout/hard validation, default enablement, online integration, or GoalSearcher default change is allowed from 17.23."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "candidate_matrix_csv": str(matrix_csv),
            "field_manifest_csv": str(fields_csv),
            "acceptance_checks_csv": str(checks_csv),
            "command_contract_csv": str(commands_csv),
        },
        "anti_drift_conclusion": (
            "17.23 only defines the next dev/OOF-only precision + family-retention scope. It does not execute a matrix, train, tune from heldout/hard, "
            "default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(matrix_csv, candidate_matrix, ["candidate_id", "role", "base_trunk", "top_k", "family_scope", "observable_guard", "expected_effect", "pass_gate", "stop_condition"])
    _write_csv(fields_csv, field_manifest, ["field", "required", "purpose"])
    _write_csv(checks_csv, acceptance_checks, ["check", "target", "failure_action"])
    _write_csv(commands_csv, command_contract, ["stage", "command", "allowed_after", "blocked_now"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
