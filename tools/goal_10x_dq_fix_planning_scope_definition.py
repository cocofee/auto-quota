from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_ROUTE_GATE = AGENT_STATE / "goal_10x_label_taxonomy_mixture_acceptance_gate_summary.json"
DEFAULT_FIX_INPUTS = AGENT_STATE / "goal_10x_label_taxonomy_mixture_acceptance_gate_dq_fix_planning_inputs.csv"
DEFAULT_TOP1_DOMAIN = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_domain_rollup.csv"
DEFAULT_TOP1_DISPOSITION = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_disposition_rollup.csv"
DEFAULT_LABEL_MIX = AGENT_STATE / "goal_10x_label_taxonomy_mixture_separation_artifact_audit_separation_rollup.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_fix_planning_scope_definition"


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
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _to_int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _risk(generated_rows: int, support_rows: int, accepted_oss_rows: int) -> str:
    if support_rows == 0:
        return "unknown"
    generated_rate = generated_rows / support_rows
    if generated_rate >= 0.9 and accepted_oss_rows == 0:
        return "high_generated_source_risk"
    if generated_rate >= 0.5:
        return "mixed_source_risk"
    return "lower_source_risk_needs_owner_acceptance"


def _top1_candidates(domain_rows: list[dict[str, str]], disposition_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    disposition_support = {row["accepted_family_disposition"]: _to_int(row["support_rows"]) for row in disposition_rows}
    candidates: list[dict[str, Any]] = []
    for row in domain_rows:
        domain = row["domain"]
        support = _to_int(row["support_rows"])
        generated = _to_int(row["generated_rows"])
        accepted_oss = _to_int(row["accepted_oss_rows"])
        recommended = row.get("recommended_disposition", "")
        if domain == "pipe":
            fix_family = "top1_family_pipe_taxonomy_and_book_label_backfill"
            owner_action = "Review pipe top1_family empty and book-label-empty rows; define accepted pipe taxonomy/book-label backfill candidates."
        elif domain == "valve":
            fix_family = "top1_family_valve_taxonomy_label_boundary"
            owner_action = "Separate valve taxonomy-empty rows from label-mixture rows before any future repair implementation."
        elif domain == "other":
            fix_family = "top1_family_cross_domain_absorption_cleanup"
            owner_action = "Mark cross-domain absorption rows as cleanup-only and prevent them from being used as learning evidence."
        else:
            fix_family = f"top1_family_{domain}_taxonomy_coverage_backfill"
            owner_action = f"Review {domain} top1_family empty rows and decide whether taxonomy coverage should be backfilled."
        candidates.append(
            {
                "candidate_id": f"dqfix_top1_{domain}",
                "fix_family": fix_family,
                "source_artifact": "10.31/10.32 accepted top1_family coverage artifact",
                "scope": f"domain={domain}; recommended_disposition={recommended}",
                "support_rows": support,
                "generated_rows": generated,
                "accepted_oss_rows": accepted_oss,
                "source_risk": _risk(generated, support, accepted_oss),
                "evidence_note": row.get("example_queries", ""),
                "owner_action": owner_action,
                "acceptance_checks": (
                    "owner confirms source/provenance; each proposed taxonomy/book-label change has before/after rows; "
                    "generated rows remain excluded from learning; no heldout/hard selection"
                ),
                "implementation_boundary": "planning_only_no_data_edits_no_rules_no_goal_searcher_change",
                "learning_boundary": "dq_backlog_only_not_learning_evidence",
            }
        )
    # Add disposition-level candidate for the largest cross-domain-independent issue family.
    if disposition_support.get("same_domain_taxonomy_empty", 0):
        candidates.append(
            {
                "candidate_id": "dqfix_top1_same_domain_taxonomy_empty_backfill",
                "fix_family": "same_domain_taxonomy_empty_backfill",
                "source_artifact": "10.31/10.32 accepted top1_family coverage artifact",
                "scope": "all domains where query/top1 are same-domain but top1_family is empty",
                "support_rows": disposition_support["same_domain_taxonomy_empty"],
                "generated_rows": "",
                "accepted_oss_rows": "",
                "source_risk": "mixed_source_risk",
                "evidence_note": "same_domain_taxonomy_empty is the largest top1_family coverage disposition",
                "owner_action": "Define taxonomy coverage fields and owner acceptance checklist for same-domain empty-family backfill.",
                "acceptance_checks": "all affected rows map to stable owner-approved family; no cross-domain examples folded into same-domain backfill",
                "implementation_boundary": "planning_only_no_taxonomy_write",
                "learning_boundary": "dq_backlog_only_not_learning_evidence",
            }
        )
    return candidates


def _label_mix_candidates(label_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    action_by_class = {
        "valve_overlap": "Review valve-adjacent terms such as insert valves, filters, pressure reducers, and water hammer eliminators as taxonomy mapping candidates.",
        "overbroad_labels": "Split overbroad valve labels from instrument, sanitary, container, faucet, and well objects.",
        "cross_domain_absorption": "Keep cross-domain absorption as cleanup-only and prevent leakage into rank/recall learning.",
        "true_taxonomy_gaps": "Backfill true taxonomy gaps only if owner confirms stable family/book semantics.",
    }
    candidates: list[dict[str, Any]] = []
    for row in label_rows:
        cls = row["separation_class"]
        support = _to_int(row["support_rows"])
        generated = _to_int(row["generated_rows"])
        accepted_oss = _to_int(row["accepted_oss_rows"])
        candidates.append(
            {
                "candidate_id": f"dqfix_labelmix_{cls}",
                "fix_family": f"label_taxonomy_mixture_{cls}",
                "source_artifact": "10.33/10.34 accepted label/taxonomy mixture artifact",
                "scope": f"separation_class={cls}",
                "support_rows": support,
                "generated_rows": generated,
                "accepted_oss_rows": accepted_oss,
                "source_risk": _risk(generated, support, accepted_oss),
                "evidence_note": row.get("example_queries", ""),
                "owner_action": action_by_class.get(cls, "Owner review before any repair planning."),
                "acceptance_checks": (
                    "owner accepts separation class; generated-source exclusion remains active; proposed fix does not alter ranking logic; "
                    "no online or heldout validation is run in planning"
                ),
                "implementation_boundary": "planning_only_no_data_edits_no_rules_no_goal_searcher_change",
                "learning_boundary": "dq_backlog_only_not_learning_evidence",
            }
        )
    return candidates


def _validation_plan(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "check_id": "owner_acceptance_required",
            "scope": "all DQ fix candidates",
            "required_evidence": "owner signs off candidate_id, source_artifact, support_rows, source_risk, and exact row examples",
            "pass_condition": "accepted/rejected status is explicit for every candidate",
        },
        {
            "check_id": "generated_source_exclusion",
            "scope": "generated-source rows",
            "required_evidence": "generated rows are flagged as DQ cleanup only",
            "pass_condition": "no generated row is used as learning evidence or S2 re-entry evidence",
        },
        {
            "check_id": "implementation_boundary",
            "scope": "future implementation stage",
            "required_evidence": "separate authorization exists before data/taxonomy/rule edits",
            "pass_condition": "10.35 artifacts remain planning-only",
        },
        {
            "check_id": "candidate_completeness",
            "scope": "candidate manifest",
            "required_evidence": f"{len(candidates)} candidates include owner_action, acceptance_checks, and implementation_boundary",
            "pass_condition": "no candidate lacks required planning fields",
        },
    ]


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    candidates: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.35 DQ Fix Planning Scope Definition",
        "",
        "Read-only scope definition for owner-reviewable DQ fix candidates. No fixes are implemented here.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", metrics["candidate_count"]],
                ["top1_candidate_count", metrics["top1_candidate_count"]],
                ["label_mixture_candidate_count", metrics["label_mixture_candidate_count"]],
                ["total_candidate_support_rows", metrics["total_candidate_support_rows"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Candidate Manifest",
        "",
        _md_table(
            [["candidate_id", "fix_family", "support_rows", "source_risk", "implementation_boundary"]]
            + [
                [
                    row["candidate_id"],
                    row["fix_family"],
                    row["support_rows"],
                    row["source_risk"],
                    row["implementation_boundary"],
                ]
                for row in candidates
            ]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "scope", "required_evidence", "pass_condition"]]
            + [[row["check_id"], row["scope"], row["required_evidence"], row["pass_condition"]] for row in validation_rows]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Define read-only DQ fix planning scope from accepted artifacts")
    parser.add_argument("--route-gate", default=str(DEFAULT_ROUTE_GATE))
    parser.add_argument("--fix-inputs", default=str(DEFAULT_FIX_INPUTS))
    parser.add_argument("--top1-domain", default=str(DEFAULT_TOP1_DOMAIN))
    parser.add_argument("--top1-disposition", default=str(DEFAULT_TOP1_DISPOSITION))
    parser.add_argument("--label-mixture", default=str(DEFAULT_LABEL_MIX))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    route_gate = _read_json(Path(args.route_gate))
    fix_inputs = _read_csv(Path(args.fix_inputs))
    top1_domain = _read_csv(Path(args.top1_domain))
    top1_disposition = _read_csv(Path(args.top1_disposition))
    label_mixture = _read_csv(Path(args.label_mixture))

    top1_candidates = _top1_candidates(top1_domain, top1_disposition)
    label_candidates = _label_mix_candidates(label_mixture)
    candidates = top1_candidates + label_candidates
    validation_rows = _validation_plan(candidates)
    blocked_actions = [
        {
            "blocked_action": "implement_dq_fixes_now",
            "reason": "10.35 defines owner-reviewable scope only; it does not authorize data, taxonomy, rule, or code edits.",
            "allowed_after": "future owner acceptance gate and explicit implementation authorization",
        },
        {
            "blocked_action": "treat_dq_fix_candidates_as_learning_evidence",
            "reason": "Candidates come from DQ backlog artifacts, many generated-source dominated.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS positive-net evidence",
        },
        {
            "blocked_action": "reopen_s2_training_or_candidate_expansion",
            "reason": "DQ fix planning is not S2 evidence and does not include new accepted-OSS gains.",
            "allowed_after": "explicit future go after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "10.35 has no implemented fix or model candidate to validate.",
            "allowed_after": "future validation gate after owner-accepted implementation scope, if authorized",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "No implementation boundary has been crossed.",
            "allowed_after": "post-owner-acceptance implementation review, if ever reached",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_manifest.csv")),
        "owner_acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_owner_acceptance_checks.csv")),
        "implementation_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_implementation_boundary.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    boundary_rows = [
        {
            "boundary": "planning_only",
            "status": "active",
            "rule": "10.35 may define candidates, evidence, risk, and checks, but cannot edit data/taxonomy/rules/code.",
        },
        {
            "boundary": "learning_reentry",
            "status": "blocked",
            "rule": "DQ candidates are not accepted-OSS positive-net learning evidence.",
        },
        {
            "boundary": "validation",
            "status": "blocked",
            "rule": "No heldout/hard validation or selection before a separately authorized implementation candidate exists.",
        },
        {
            "boundary": "online_goal_searcher",
            "status": "blocked",
            "rule": "No GoalSearcher, threshold, feature whitelist, or online behavior change.",
        },
    ]
    metrics = {
        "selected_route_from_10_34": route_gate["metrics"]["selected_remaining_route"],
        "fix_input_count": len(fix_inputs),
        "candidate_count": len(candidates),
        "top1_candidate_count": len(top1_candidates),
        "label_mixture_candidate_count": len(label_candidates),
        "high_generated_source_risk_candidate_count": sum(1 for row in candidates if row["source_risk"] == "high_generated_source_risk"),
        "mixed_source_risk_candidate_count": sum(1 for row in candidates if row["source_risk"] == "mixed_source_risk"),
        "lower_source_risk_candidate_count": sum(1 for row in candidates if row["source_risk"] == "lower_source_risk_needs_owner_acceptance"),
        "total_candidate_support_rows": sum(_to_int(row["support_rows"]) for row in candidates),
        "owner_acceptance_check_count": len(validation_rows),
        "implementation_boundary_count": len(boundary_rows),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.35 DQ fix planning scope definition",
        "read_only": True,
        "dq_fix_planning_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define an owner-reviewable DQ fix planning scope from the accepted top1_family coverage and label/taxonomy mixture artifacts. "
            "The scope creates candidate manifests, owner acceptance checks, and implementation boundaries only; it does not implement DQ fixes, train, validate, or reopen S2."
        ),
        "anti_drift_conclusion": (
            "10.35 only defines DQ fix planning scope. It does not edit data, taxonomy, rules, thresholds, GoalSearcher, feature whitelists, train or tune models, "
            "run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.36 DQ fix owner acceptance gate",
            "goal": "Read-only review the 10.35 candidate manifest and decide which DQ fix candidates, if any, are owner-accepted for a future implementation plan.",
            "default": "acceptance review only; no data edits, no rules, no training, no validation, and S2 remains parked",
        },
    }

    candidate_fields = [
        "candidate_id",
        "fix_family",
        "source_artifact",
        "scope",
        "support_rows",
        "generated_rows",
        "accepted_oss_rows",
        "source_risk",
        "evidence_note",
        "owner_action",
        "acceptance_checks",
        "implementation_boundary",
        "learning_boundary",
    ]
    _write_csv(Path(artifacts["candidate_manifest_csv"]), candidates, candidate_fields)
    _write_csv(Path(artifacts["owner_acceptance_checks_csv"]), validation_rows, ["check_id", "scope", "required_evidence", "pass_condition"])
    _write_csv(Path(artifacts["implementation_boundary_csv"]), boundary_rows, ["boundary", "status", "rule"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, candidates, validation_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
