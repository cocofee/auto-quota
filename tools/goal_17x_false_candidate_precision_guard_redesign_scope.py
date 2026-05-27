from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
H17A_DEV_SUMMARY = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_summary.json"
H17A_DEV_SCORECARD = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_scorecard.csv"
H17A_VALIDATION = AGENT_STATE / "goal_17x_h17a_heldout_hard_validation_summary.json"
H17A_CLOSURE = AGENT_STATE / "goal_17x_h17a_validation_failed_closure_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_false_candidate_precision_guard_redesign_scope"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _guard_matrix() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "P17_A_strong_multifield_guard",
            "role": "lead precision redesign",
            "scope": "Keep H17_A families concrete,pump,rebar; add candidate-level strong evidence gate.",
            "observable_guard": "exact_name OR source_family>=2 + support>=4 + overlap>=3 + quota_name_overlap>=1",
            "expected_effect": "Reduce false-only candidates while preserving most H17_A positive Top1 movement.",
            "dev_oof_pass_gate": "top1_loss=0; delta_top1>=2; false_candidates <= 25; positive_candidates >= 7",
            "blocked": "Do not infer thresholds from heldout/hard; grid values are dev/OOF-only design knobs.",
        },
        {
            "candidate_id": "P17_B_topk1_strong_guard",
            "role": "candidate-count suppression branch",
            "scope": "Same evidence gate as P17_A but inject at most one OSS candidate per row.",
            "observable_guard": "TopK=1 + strong_multifield_guard",
            "expected_effect": "Cut generated/false candidates sharply; risk is losing Top5/Top20 movement.",
            "dev_oof_pass_gate": "top1_loss=0; delta_top1>=2; false_candidates <= 18; no Top80 loss",
            "blocked": "Do not release from this scope; only define a future dev/OOF what-if candidate.",
        },
        {
            "candidate_id": "P17_C_family_specific_guard",
            "role": "family noise balancing branch",
            "scope": "Concrete requires stronger support/source evidence; pump/rebar can keep slightly looser but still observable guards.",
            "observable_guard": "concrete: source_family>=2 + support>=4 + overlap>=3; pump/rebar: exact_name OR support>=3 + overlap>=2",
            "expected_effect": "Address concrete/pump false rate without erasing rebar's cleaner signal.",
            "dev_oof_pass_gate": "top1_loss=0; delta_top1>=2; every positive family keeps >=1 positive movement if present",
            "blocked": "Family thresholds must be selected on dev/OOF only, then frozen before any validation.",
        },
        {
            "candidate_id": "P17_D_observable_rank1_veto",
            "role": "rank1 safety wrapper",
            "scope": "Wrap P17_A/B/C with an online-observable baseline-rank1 protection veto.",
            "observable_guard": "If baseline rank1 has strong family/book/name compatibility, challenger must be exact_name OR source_family>=2 + support>=6 + overlap>=4.",
            "expected_effect": "Protect current correct-looking rank1 while allowing high-evidence OSS recall wins.",
            "dev_oof_pass_gate": "top1_loss=0; delta_top1>=2; false_candidates below wrapped candidate; no new Top80 loss",
            "blocked": "Do not use expected_id or validation labels in the online-observable veto.",
        },
    ]


def _audit_field_manifest() -> list[dict[str, Any]]:
    return [
        {"field": "oss_recall_exact_name", "required": True, "source": "OSS prior candidate audit", "purpose": "Strong identity evidence for low-noise injection."},
        {"field": "oss_recall_support", "required": True, "source": "OSS index aggregate", "purpose": "Measure how many OSS bill-quota pairs support the candidate."},
        {"field": "oss_recall_source_family_count", "required": True, "source": "OSS index aggregate", "purpose": "Avoid single-source-family candidate dominance."},
        {"field": "oss_recall_overlap", "required": True, "source": "query/candidate token audit", "purpose": "Generic lexical compatibility guard."},
        {"field": "oss_recall_quota_name_overlap", "required": True, "source": "quota name token audit", "purpose": "Reject candidates that match bill text but not quota naming."},
        {"field": "oss_recall_quota_specific_overlap", "required": True, "source": "quota material/action/spec token audit", "purpose": "Prefer candidates with action/material/spec evidence."},
        {"field": "baseline_rank1_family_compat", "required": True, "source": "current baseline ranking audit", "purpose": "Observable rank1 protection without expected_id leakage."},
        {"field": "prior_candidate_rank_position", "required": True, "source": "A/B row audit", "purpose": "Analyze whether false candidates are top-heavy or tail noise."},
    ]


def _command_contract() -> list[dict[str, Any]]:
    return [
        {
            "stage": "17.20 future explicit go only",
            "command": "python tools\\goal_17x_false_candidate_precision_guard_dev_oof_shadow.py --candidate-matrix fixed_17_19_scope",
            "allowed_after": "explicit go to implement/run the fixed P17_A/B/C/D dev/OOF-only matrix",
            "blocked_now": True,
        },
        {
            "stage": "future validation only after freeze",
            "command": "heldout/hard validation command must be generated only after a dev/OOF freeze gate passes",
            "allowed_after": "future P17 freeze gate plus explicit validation go",
            "blocked_now": True,
        },
    ]


def _acceptance_checks() -> list[dict[str, Any]]:
    return [
        {"check": "dev_oof_only", "target": "true", "failure_action": "invalidate scope execution"},
        {"check": "top1_loss_guard", "target": "0", "failure_action": "candidate no-go"},
        {"check": "h17a_lift_preservation", "target": "delta_top1>=2 and delta_top5>=3 versus H17_A dev/OOF baseline +3/+5", "failure_action": "park candidate as too conservative"},
        {"check": "false_candidate_reduction", "target": "false_candidates materially below H17_A dev/OOF false=40; preferred <=25", "failure_action": "redesign guard before freeze"},
        {"check": "positive_evidence_retention", "target": "positive_candidates >=7 and positive_family_count>=2", "failure_action": "candidate too narrow"},
        {"check": "online_observable_only", "target": "no expected_id, heldout/hard labels, or validation-derived row constants", "failure_action": "reject candidate"},
        {"check": "default_off_boundary", "target": "no default enablement, no GoalSearcher default change", "failure_action": "stop and report drift"},
    ]


def _blocked_actions() -> list[dict[str, Any]]:
    return [
        {"action": "run_dev_oof_shadow_now", "blocked": True, "reason": "17.19 defines scope only; execution requires explicit go."},
        {"action": "use_heldout_hard_to_pick_thresholds", "blocked": True, "reason": "heldout/hard are closure evidence only after 17.17."},
        {"action": "release_or_default_enable_h17a", "blocked": True, "reason": "17.18 stopped release due false-candidate dominance."},
        {"action": "change_goal_searcher_defaults", "blocked": True, "reason": "precision redesign is offline/default-off."},
        {"action": "reintroduce_pipe_support", "blocked": True, "reason": "17.19 scope keeps H17_A families only; any re-admission needs a separate dev/OOF branch."},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.19 Dev/OOF False-Candidate Precision Guard Redesign Scope",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Evidence Basis",
        "",
        f"- H17_A dev/OOF: `{report['h17a_dev_headline']['delta_top1']}/{report['h17a_dev_headline']['delta_top5']}/{report['h17a_dev_headline']['delta_top20']}/{report['h17a_dev_headline']['delta_top80']}`, loss `{report['h17a_dev_headline']['top1_losses']}`, false `{report['h17a_dev_headline']['prior_false_candidates']}`.",
        f"- H17_A validation: `{report['h17a_validation_headline']['delta_top1']}/{report['h17a_validation_headline']['delta_top5']}/{report['h17a_validation_headline']['delta_top20']}/{report['h17a_validation_headline']['delta_top80']}`, loss `{report['h17a_validation_headline']['top1_losses']}`, false `{report['h17a_validation_headline']['prior_false_candidates']}`.",
        "",
        "## Candidate Matrix",
        "",
        "| candidate | role | guard | dev/OOF pass gate |",
        "|---|---|---|---|",
    ]
    for row in report["guard_matrix"]:
        lines.append(f"| {row['candidate_id']} | {row['role']} | {row['observable_guard']} | {row['dev_oof_pass_gate']} |")
    lines.extend(["", "## Acceptance Checks", "", "| check | target | failure action |", "|---|---|---|"])
    for row in report["acceptance_checks"]:
        lines.append(f"| {row['check']} | {row['target']} | {row['failure_action']} |")
    lines.extend(["", "## Next Boundary", "", report["next_boundary"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    dev = _read_json(H17A_DEV_SUMMARY)
    dev_scorecard = _read_csv(H17A_DEV_SCORECARD)
    validation = _read_json(H17A_VALIDATION)
    closure = _read_json(H17A_CLOSURE)
    guard_matrix = _guard_matrix()
    audit_fields = _audit_field_manifest()
    command_contract = _command_contract()
    acceptance_checks = _acceptance_checks()
    blocked_actions = _blocked_actions()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    matrix_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_guard_matrix.csv")
    fields_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_audit_field_manifest.csv")
    commands_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_command_contract.csv")
    checks_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_acceptance_checks.csv")
    blocked_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv")

    report = {
        "stage": "17.19 dev/OOF false-candidate precision guard redesign scope",
        "decision": "scope_locked_request_explicit_dev_oof_precision_guard_execution_go",
        "read_only": True,
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used_for_design": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "h17a_dev_headline": dev["headline"],
        "h17a_validation_headline": validation["headline"]["all"],
        "closure_decision": closure["decision"],
        "dev_family_scorecard": [row for row in dev_scorecard if row.get("slice", "").startswith("family:")],
        "guard_matrix": guard_matrix,
        "audit_field_manifest": audit_fields,
        "command_contract": command_contract,
        "acceptance_checks": acceptance_checks,
        "blocked_actions": blocked_actions,
        "next_boundary": (
            "17.20 may implement/run the fixed P17_A/B/C/D dev/OOF-only precision-guard shadow matrix only after explicit go. "
            "No heldout/hard validation, online integration, default enablement, or GoalSearcher default change is allowed from 17.19."
        ),
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "guard_matrix_csv": str(matrix_csv),
            "audit_field_manifest_csv": str(fields_csv),
            "command_contract_csv": str(commands_csv),
            "acceptance_checks_csv": str(checks_csv),
            "blocked_actions_csv": str(blocked_csv),
        },
        "anti_drift_conclusion": (
            "17.19 only defines a dev/OOF-only redesign scope. It does not execute the matrix, train, tune from heldout/hard, "
            "change thresholds, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(matrix_csv, guard_matrix, ["candidate_id", "role", "scope", "observable_guard", "expected_effect", "dev_oof_pass_gate", "blocked"])
    _write_csv(fields_csv, audit_fields, ["field", "required", "source", "purpose"])
    _write_csv(commands_csv, command_contract, ["stage", "command", "allowed_after", "blocked_now"])
    _write_csv(checks_csv, acceptance_checks, ["check", "target", "failure_action"])
    _write_csv(blocked_csv, blocked_actions, ["action", "blocked", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next_boundary": report["next_boundary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
