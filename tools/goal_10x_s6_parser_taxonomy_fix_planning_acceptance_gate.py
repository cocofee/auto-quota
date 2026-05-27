from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_1064_SUMMARY = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_summary.json"
DEFAULT_MANIFEST = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_planning_manifest.csv"
DEFAULT_SCOPE_ROLLUP = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_scope_rollup.csv"
DEFAULT_EVIDENCE = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_evidence_requirements.csv"
DEFAULT_RISK = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_risk_register.csv"
DEFAULT_EXPLICIT_GO = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_explicit_go_requirements.csv"
DEFAULT_NEXT_GATE = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_next_gate.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate"


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


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    acceptance_checks: list[dict[str, Any]],
    request_package: list[dict[str, Any]],
    next_gate: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.65 S6 Parser/Taxonomy Fix Planning Acceptance Gate",
        "",
        "Read-only acceptance gate for the 16-candidate S6 parser/taxonomy fix planning package.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["planning_package_accepted", metrics["planning_package_accepted"]],
                ["request_explicit_go_and_owner_mappings", metrics["request_explicit_go_and_owner_mappings"]],
                ["planning_candidate_rows", metrics["planning_candidate_rows"]],
                ["implementation_ready_rows", metrics["implementation_ready_rows"]],
                ["acceptance_pass_count", metrics["acceptance_pass_count"]],
                ["acceptance_fail_count", metrics["acceptance_fail_count"]],
            ]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_checks]
        ),
        "",
        "## Request Package",
        "",
        _md_table(
            [["package_item", "required", "description"]]
            + [[row["package_item"], row["required"], row["description"]] for row in request_package]
        ),
        "",
        "## Next Gate",
        "",
        _md_table(
            [["next_stage", "status", "scope", "not_allowed"]]
            + [[row["next_stage"], row["status"], row["scope"], row["not_allowed"]] for row in next_gate]
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
    parser = argparse.ArgumentParser(description="Accept S6 parser/taxonomy fix planning package")
    parser.add_argument("--summary-1064", default=str(DEFAULT_1064_SUMMARY))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--scope-rollup", default=str(DEFAULT_SCOPE_ROLLUP))
    parser.add_argument("--evidence-requirements", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--risk-register", default=str(DEFAULT_RISK))
    parser.add_argument("--explicit-go", default=str(DEFAULT_EXPLICIT_GO))
    parser.add_argument("--next-gate-1064", default=str(DEFAULT_NEXT_GATE))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1064 = _read_json(Path(args.summary_1064))
    manifest = _read_csv(Path(args.manifest))
    scope_rollup = _read_csv(Path(args.scope_rollup))
    evidence_requirements = _read_csv(Path(args.evidence_requirements))
    risk_register = _read_csv(Path(args.risk_register))
    explicit_go = _read_csv(Path(args.explicit_go))
    next_gate_1064 = _read_csv(Path(args.next_gate_1064))
    blocked_input = _read_csv(Path(args.blocked_actions))
    m1064 = summary_1064["metrics"]

    lane_counts = Counter(row.get("planned_fix_lane", "") for row in manifest)
    risk_counts = Counter(row.get("risk_level", "") for row in manifest)
    all_explicit_go = all(row.get("explicit_go_required") == "true" for row in manifest)
    all_not_ready = all(row.get("implementation_ready") == "false" for row in manifest)
    all_not_learning = all(row.get("learning_use_allowed") == "false" for row in manifest)
    has_owner_evidence = all(row.get("owner_evidence_required") for row in manifest)
    has_rollback = all(row.get("rollback_boundary") for row in manifest)
    has_acceptance = all(row.get("acceptance_check") for row in manifest)
    required_go_ids = {row.get("requirement_id", "") for row in explicit_go if row.get("required") == "true"}
    required_go_complete = {
        "EXPLICIT_IMPLEMENTATION_GO",
        "OWNER_ACCEPTED_ROW_MAPPINGS",
        "DRY_RUN_AND_LOSS_AUDIT_PLAN",
        "NO_HELDOUT_SELECTION",
    }.issubset(required_go_ids)

    acceptance_checks = [
        {
            "check_id": "AC01_SCOPE_PACKAGE_PRESENT",
            "status": "pass" if manifest and scope_rollup and evidence_requirements and risk_register and explicit_go else "fail",
            "evidence": f"manifest_rows={len(manifest)}; scope_rows={len(scope_rollup)}; evidence_requirements={len(evidence_requirements)}; risk_register={len(risk_register)}; explicit_go={len(explicit_go)}",
            "decision": "The 10.64 planning package artifacts are present.",
        },
        {
            "check_id": "AC02_CANDIDATE_ACCOUNTING",
            "status": "pass" if len(manifest) == _int(m1064.get("planning_candidate_rows")) == 16 else "fail",
            "evidence": f"manifest_rows={len(manifest)}; planning_candidate_rows={m1064.get('planning_candidate_rows')}",
            "decision": "All 16 planning candidates are represented.",
        },
        {
            "check_id": "AC03_LANE_AND_RISK_SEPARATION",
            "status": "pass" if lane_counts.get("parser_query_family_hint_planning") == 9 and lane_counts.get("taxonomy_top1_family_coverage_planning") == 7 and risk_counts else "fail",
            "evidence": f"lane_counts={dict(lane_counts)}; risk_counts={dict(risk_counts)}",
            "decision": "Parser and taxonomy planning lanes are separated with visible risk levels.",
        },
        {
            "check_id": "AC04_EVIDENCE_ROLLBACK_ACCEPTANCE_DEFINED",
            "status": "pass" if has_owner_evidence and has_rollback and has_acceptance else "fail",
            "evidence": f"owner_evidence_defined={has_owner_evidence}; rollback_defined={has_rollback}; acceptance_defined={has_acceptance}",
            "decision": "Each candidate has evidence, acceptance, and rollback planning fields.",
        },
        {
            "check_id": "AC05_EXPLICIT_GO_REQUIREMENTS_COMPLETE",
            "status": "pass" if all_explicit_go and required_go_complete else "fail",
            "evidence": f"all_manifest_rows_require_go={all_explicit_go}; required_go_ids={sorted(required_go_ids)}",
            "decision": "The package is sufficient to request explicit implementation go and owner mappings.",
        },
        {
            "check_id": "AC06_NON_EXECUTION_CONTRACT",
            "status": "pass" if all_not_ready and all_not_learning and not m1064.get("implementation_allowed") else "fail",
            "evidence": f"all_implementation_ready_false={all_not_ready}; all_learning_use_false={all_not_learning}; implementation_allowed={m1064.get('implementation_allowed')}",
            "decision": "Acceptance does not authorize implementation or learning.",
        },
    ]
    fail_count = sum(1 for row in acceptance_checks if row["status"] != "pass")

    request_package = [
        {
            "package_item": "explicit_implementation_go",
            "required": "true",
            "description": "User/owner must explicitly authorize implementation after reviewing the 16-row package.",
        },
        {
            "package_item": "owner_accepted_before_after_mappings",
            "required": "true",
            "description": "Every row needs accepted before/after parser or taxonomy mapping, owner rationale, and rejection option.",
        },
        {
            "package_item": "source_provenance_acceptance",
            "required": "true",
            "description": "v36 trace/shadow and OSS-derived rows need accepted provenance notes before any implementation plan.",
        },
        {
            "package_item": "negative_examples_and_collision_checks",
            "required": "true",
            "description": "Parser hints need negative examples; taxonomy mappings need quota-family collision checks.",
        },
        {
            "package_item": "dev_oof_dry_run_loss_audit_plan",
            "required": "true",
            "description": "Future implementation plan must stay dev/OOF-only and define loss slices before any edit.",
        },
    ]

    owner_mapping_template = [
        {
            "inventory_id": row.get("inventory_id", ""),
            "planned_fix_lane": row.get("planned_fix_lane", ""),
            "query": row.get("query", ""),
            "before_value": row.get("query_family_before") if row.get("planned_fix_lane") == "parser_query_family_hint_planning" else row.get("top1_family_before"),
            "proposed_after_value": "",
            "owner_decision": "pending_accept_or_reject",
            "owner_rationale": "",
            "rollback_target": row.get("rollback_boundary", ""),
            "implementation_ready": "false",
        }
        for row in manifest
    ]

    next_gate = [
        {
            "next_stage": "10.66 S6 explicit implementation go / owner mapping request package review",
            "status": "request_package_allowed" if fail_count == 0 else "hold",
            "scope": "Read-only review whether explicit implementation go and complete owner accepted mappings have been provided; default remains do_not_implement.",
            "not_allowed": "no parser edit, no taxonomy edit, no data edit, no training, no implementation, no GoalSearcher change, no heldout/hard selection",
        }
    ]

    blocked_actions = blocked_input + [
        {
            "blocked_action": "implement_from_accepted_planning_package",
            "reason": "10.65 only allows requesting explicit go and owner mappings; the request package is not implementation authorization.",
            "allowed_after": "future explicit go plus complete owner-accepted mappings and implementation authorization",
        },
        {
            "blocked_action": "skip_owner_mapping_review",
            "reason": "Every candidate needs accepted before/after mapping or explicit rejection.",
            "allowed_after": "never",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "request_package_csv": str(output_prefix.with_name(output_prefix.name + "_request_package.csv")),
        "owner_mapping_template_csv": str(output_prefix.with_name(output_prefix.name + "_owner_mapping_template.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1064["stage"],
        "planning_candidate_rows": len(manifest),
        "parser_planning_rows": lane_counts.get("parser_query_family_hint_planning", 0),
        "taxonomy_planning_rows": lane_counts.get("taxonomy_top1_family_coverage_planning", 0),
        "high_risk_rows": risk_counts.get("high", 0),
        "medium_risk_rows": risk_counts.get("medium", 0),
        "low_risk_rows": risk_counts.get("low", 0),
        "request_package_item_count": len(request_package),
        "owner_mapping_template_rows": len(owner_mapping_template),
        "planning_package_accepted": fail_count == 0,
        "request_explicit_go_and_owner_mappings": fail_count == 0,
        "implementation_ready_rows": 0,
        "explicit_go_present": False,
        "owner_mappings_complete": False,
        "acceptance_pass_count": len(acceptance_checks) - fail_count,
        "acceptance_fail_count": fail_count,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.65 S6 parser/taxonomy fix planning acceptance gate",
        "read_only": True,
        "acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the 16-candidate S6 parser/taxonomy planning package as sufficient to request explicit implementation go and owner accepted mappings. "
            "This is a request package only: explicit_go_present=false, owner_mappings_complete=false, implementation_ready_rows=0, and default remains do_not_implement."
        ),
        "anti_drift_conclusion": (
            "10.65 only accepts a planning package and prepares a request package. It does not train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.66 S6 explicit implementation go / owner mapping request package review",
            "goal": "Read-only check whether explicit implementation go and complete owner accepted mappings have been provided; default remains do_not_implement.",
            "default": "request package review only; no parser edit, taxonomy edit, implementation, training, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["request_package_csv"]), request_package, ["package_item", "required", "description"])
    _write_csv(
        Path(artifacts["owner_mapping_template_csv"]),
        owner_mapping_template,
        ["inventory_id", "planned_fix_lane", "query", "before_value", "proposed_after_value", "owner_decision", "owner_rationale", "rollback_target", "implementation_ready"],
    )
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "status", "scope", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_checks, request_package, next_gate)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
