from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
PLAN_SUMMARY = AGENT_STATE / "goal_17x_precision_hardening_plan_summary.json"
PLAN_MATRIX = AGENT_STATE / "goal_17x_precision_hardening_plan_candidate_matrix.csv"
TOP3_SUMMARY = AGENT_STATE / "goal_17x_top3_guarded_dev_oof_shadow_summary.json"
VALIDATION_CLOSURE = AGENT_STATE / "goal_17x_default_off_harness_validation_closure_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_precision_hardening_execution_scope"


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


def _candidate_scope(plan_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_id = {row["candidate_id"]: row for row in plan_rows}
    base_contract = "TopK=3; min_support=2; min_source_families=1; min_overlap=2; intervention_mode=broad; dev/OOF baseline_only rows only"
    return [
        {
            "candidate_id": "H17_A_lossless_family_veto_pipe_support",
            "scope_role": "first dev/OOF candidate",
            "source_plan_idea": by_id["H17_A_lossless_family_veto_pipe_support"]["idea"],
            "allowed_core_families": "concrete,pump,rebar",
            "guard_contract": base_contract + "; block pipe/support completely",
            "harness_delta": "Parameterize the 17.4 Top3 shadow harness with core_families=concrete,pump,rebar and unchanged TopK/support/source/overlap settings.",
            "comparison_baseline": "Compare against 17.4 Top3 all-family dev/OOF shadow and 17.2 broad row audit; validation slices are diagnostic only.",
            "pass_condition": "Top1 loss=0, delta_top1>0, delta_top5>0, false_rate<0.85, and positive groups are not collapsed to one family.",
            "stop_condition": "Stop if Top1 loss>0, delta_top1<=0, false_rate>=0.85, no family diversity, heldout/hard is read, or online/default config changes.",
        },
        {
            "candidate_id": "H17_B_pipe_strict_evidence_gate",
            "scope_role": "optional re-admission branch after H17_A",
            "source_plan_idea": by_id["H17_B_pipe_strict_evidence_gate"]["idea"],
            "allowed_core_families": "concrete,pump,rebar plus pipe under strict branch only",
            "guard_contract": base_contract + "; pipe candidates require stronger observable evidence variants, evaluated only on dev/OOF",
            "harness_delta": "Add pipe-strict variants such as source_family>=2, quota_specific_overlap>=2, quota_name_overlap>=1, exact_name, and rank1-protection veto combinations.",
            "comparison_baseline": "Compare against H17_A, not against heldout/hard; broad pipe validation loss is diagnostic only and cannot set final thresholds.",
            "pass_condition": "Pipe branch has Top1 loss=0 and materially lower false rate than broad pipe while adding positive Top1/Top5 movement over H17_A.",
            "stop_condition": "Keep pipe vetoed if any pipe strict variant has Top1 loss>0, false candidate dominance, or no positive movement over H17_A.",
        },
        {
            "candidate_id": "H17_C_support_strict_evidence_gate",
            "scope_role": "optional support re-admission branch after H17_A",
            "source_plan_idea": by_id["H17_C_support_strict_evidence_gate"]["idea"],
            "allowed_core_families": "concrete,pump,rebar plus support under strict branch only",
            "guard_contract": base_contract + "; support candidates require support identity/evidence guards, evaluated only on dev/OOF",
            "harness_delta": "Add support-strict variants such as exact_name, source_family>=2, support>=4 or support>=6, quota_specific_overlap>=1, and rank1-protection veto combinations.",
            "comparison_baseline": "Compare against H17_A; validation support loss is diagnostic only and cannot be used for selection.",
            "pass_condition": "Support branch has Top1 loss=0 and keeps positive Top1/Top5 movement with false rate below the broad support risk level.",
            "stop_condition": "Keep support vetoed if loss appears, movement vanishes, or support false-only groups dominate the branch.",
        },
        {
            "candidate_id": "H17_D_rank1_protection_veto",
            "scope_role": "cross-cutting veto candidate",
            "source_plan_idea": by_id["H17_D_rank1_protection_veto"]["idea"],
            "allowed_core_families": "can wrap H17_A/H17_B/H17_C branches; no new families",
            "guard_contract": base_contract + "; protect baseline rank1 unless challenger evidence is strong and observable",
            "harness_delta": "Add a veto layer: if baseline expected rank is 1, allow a challenger only with strong multi-field evidence such as exact_name or source_family>=2 plus quota_specific_overlap>=2 plus quota_name_overlap>=1.",
            "comparison_baseline": "Compare against H17_A and each strict branch on dev/OOF row audit; do not copy heldout/hard loss rows into rule constants.",
            "pass_condition": "Top1 loss remains 0 while retaining most H17_A positive Top1 gain and reducing false-only interventions.",
            "stop_condition": "Do not freeze if the veto erases most H17_A movement or if any wrapped branch introduces Top1 loss.",
        },
    ]


def _artifact_manifest() -> list[dict[str, str]]:
    return [
        {
            "artifact": "per_candidate_summary_json",
            "required": "yes",
            "path_pattern": "reports/agent_state/goal_17x_h17*_dev_oof_shadow_summary.json",
            "purpose": "Machine-readable headline, decision, guard config, and anti-drift flags.",
        },
        {
            "artifact": "per_candidate_scorecard_csv",
            "required": "yes",
            "path_pattern": "reports/agent_state/goal_17x_h17*_dev_oof_shadow_scorecard.csv",
            "purpose": "Top1/Top5/Top20/Top80 movement, losses, generated/positive/false counts by all/family/bucket.",
        },
        {
            "artifact": "per_candidate_row_audit_csv",
            "required": "yes",
            "path_pattern": "reports/agent_state/goal_17x_h17*_dev_oof_shadow_row_audit.csv",
            "purpose": "Row-level intervention audit with expected ids, candidate ids, ranks, source/family evidence, and loss flags.",
        },
        {
            "artifact": "aggregate_comparison_csv",
            "required": "yes",
            "path_pattern": "reports/agent_state/goal_17x_precision_hardening_dev_oof_comparison.csv",
            "purpose": "H17_A/B/C/D side-by-side comparison and freeze/no-go basis.",
        },
        {
            "artifact": "stop_conditions_csv",
            "required": "yes",
            "path_pattern": "reports/agent_state/goal_17x_precision_hardening_dev_oof_stop_conditions.csv",
            "purpose": "Gate status for no-heldout/hard, loss budget, movement, false-rate, source/family concentration, and default-off behavior.",
        },
    ]


def _command_contract() -> list[dict[str, str]]:
    return [
        {
            "stage": "17.11 future explicit go only",
            "command": "python tools\\goal_17x_precision_hardening_dev_oof_shadow.py --candidate H17_A --output-prefix reports\\agent_state\\goal_17x_h17a_dev_oof_shadow",
            "allowed_after": "explicit user go to implement/run dev/OOF hardening harness",
            "blocked_now": "yes",
        },
        {
            "stage": "17.11 future explicit go only",
            "command": "python tools\\goal_17x_precision_hardening_dev_oof_shadow.py --candidate H17_B --baseline reports\\agent_state\\goal_17x_h17a_dev_oof_shadow_summary.json",
            "allowed_after": "H17_A result exists and explicit go allows optional pipe re-admission branch",
            "blocked_now": "yes",
        },
        {
            "stage": "17.11 future explicit go only",
            "command": "python tools\\goal_17x_precision_hardening_dev_oof_shadow.py --candidate H17_C --baseline reports\\agent_state\\goal_17x_h17a_dev_oof_shadow_summary.json",
            "allowed_after": "H17_A result exists and explicit go allows optional support re-admission branch",
            "blocked_now": "yes",
        },
        {
            "stage": "17.11 future explicit go only",
            "command": "python tools\\goal_17x_precision_hardening_dev_oof_shadow.py --candidate H17_D --wrap-candidates H17_A,H17_B,H17_C",
            "allowed_after": "At least one branch result exists and explicit go allows rank1-protection veto evaluation",
            "blocked_now": "yes",
        },
    ]


def _stop_conditions() -> list[dict[str, str]]:
    return [
        {"check": "dev_oof_only", "required_status": "pass", "failure_action": "stop immediately; do not read heldout/hard"},
        {"check": "top1_loss_guard", "required_status": "top1_losses=0", "failure_action": "candidate no-go; do not freeze"},
        {"check": "positive_movement", "required_status": "delta_top1>0 and delta_top5>0", "failure_action": "candidate no-go or park branch"},
        {"check": "false_candidate_risk", "required_status": "false_rate<0.85 or materially below branch baseline", "failure_action": "harden guard or keep family vetoed"},
        {"check": "single_family_concentration", "required_status": "positive groups not collapsed to one family", "failure_action": "do not claim generalizable lift"},
        {"check": "default_off_boundary", "required_status": "no default enablement, no online integration, no GoalSearcher default change", "failure_action": "revert candidate stage and report drift"},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.10 Dev/OOF-Only Precision Hardening Execution Scope",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Assumptions",
        "",
    ]
    lines.extend(f"- {item}" for item in report["assumptions"])
    lines.extend(
        [
            "",
            "## Candidate Harness Boundary",
            "",
            "| candidate | role | allowed families | pass condition |",
            "|---|---|---|---|",
        ]
    )
    for row in report["candidate_scope"]:
        lines.append(f"| {row['candidate_id']} | {row['scope_role']} | {row['allowed_core_families']} | {row['pass_condition']} |")
    lines.extend(
        [
            "",
            "## Required Artifacts",
            "",
            "| artifact | required | purpose |",
            "|---|---|---|",
        ]
    )
    for row in report["artifact_manifest"]:
        lines.append(f"| {row['artifact']} | {row['required']} | {row['purpose']} |")
    lines.extend(
        [
            "",
            "## Stop Conditions",
            "",
            "| check | required status | failure action |",
            "|---|---|---|",
        ]
    )
    for row in report["stop_conditions"]:
        lines.append(f"| {row['check']} | {row['required_status']} | {row['failure_action']} |")
    lines.extend(
        [
            "",
            "## Next Boundary",
            "",
            report["next_boundary"],
            "",
            "## Anti-Drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    plan = _read_json(PLAN_SUMMARY)
    top3 = _read_json(TOP3_SUMMARY)
    validation = _read_json(VALIDATION_CLOSURE)
    plan_rows = _read_csv(PLAN_MATRIX)
    candidate_scope = _candidate_scope(plan_rows)
    artifact_manifest = _artifact_manifest()
    command_contract = _command_contract()
    stop_conditions = _stop_conditions()

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    candidate_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_candidate_scope.csv")
    artifact_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_artifact_manifest.csv")
    command_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_command_contract.csv")
    stop_csv = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_stop_conditions.csv")

    report = {
        "stage": "17.10 dev/OOF-only precision hardening execution scope",
        "decision": "scope_locked_request_explicit_dev_oof_harness_go",
        "assumptions": [
            "OSS remains high-trust human quantity-surveyor data, but the 17.x broad Top3 prior still needs loss and false-candidate hardening.",
            "Heldout/hard results from 17.8 are diagnostic only and must not be used to tune or select final thresholds.",
            "17.10 locks the future dev/OOF harness boundary; it does not execute H17_A/B/C/D and does not implement online behavior.",
        ],
        "input_evidence": {
            "plan_decision": plan["decision"],
            "dev_oof_top3_headline": top3["headline"],
            "validation_headline_all": validation["headline"]["all"],
            "validation_failed_gates": validation["failed_stop_conditions"],
        },
        "candidate_scope": candidate_scope,
        "artifact_manifest": artifact_manifest,
        "command_contract": command_contract,
        "stop_conditions": stop_conditions,
        "allowed_future_code_boundary": [
            "Add or parameterize a dev/OOF-only shadow harness under tools/goal_17x_*.",
            "Reuse existing OssRecallPriorSource and 17.4 evaluation helpers where possible.",
            "Add focused tests only for candidate config parsing, default-off behavior, and no-heldout/hard input boundary.",
        ],
        "blocked_actions": [
            "Do not run heldout/hard.",
            "Do not train or tune a model.",
            "Do not default-enable OSS recall.",
            "Do not integrate online behavior or change GoalSearcher defaults.",
            "Do not expand beyond concrete/pipe/pump/rebar/support in this hardening scope.",
            "Do not overwrite 16.x locked artifacts or defaults.",
        ],
        "next_boundary": (
            "Next is 17.11 explicit dev/OOF hardening harness implementation/run go-no-go. "
            "Default is do_not_execute until the user explicitly authorizes implementation/run of the fixed H17_A/B/C/D dev/OOF matrix."
        ),
        "execution_performed": False,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "candidate_scope_csv": str(candidate_csv),
            "artifact_manifest_csv": str(artifact_csv),
            "command_contract_csv": str(command_csv),
            "stop_conditions_csv": str(stop_csv),
        },
        "anti_drift_conclusion": (
            "17.10 only locked the dev/OOF shadow harness scope for H17_A/B/C/D. "
            "It did not execute candidates, train, tune, read heldout/hard, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(
        candidate_csv,
        candidate_scope,
        [
            "candidate_id",
            "scope_role",
            "source_plan_idea",
            "allowed_core_families",
            "guard_contract",
            "harness_delta",
            "comparison_baseline",
            "pass_condition",
            "stop_condition",
        ],
    )
    _write_csv(artifact_csv, artifact_manifest, ["artifact", "required", "path_pattern", "purpose"])
    _write_csv(command_csv, command_contract, ["stage", "command", "allowed_after", "blocked_now"])
    _write_csv(stop_csv, stop_conditions, ["check", "required_status", "failure_action"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
