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
DEFAULT_1063_SUMMARY = AGENT_STATE / "goal_10x_s6_inventory_artifact_acceptance_gate_summary.json"
DEFAULT_NEXT_GATE = AGENT_STATE / "goal_10x_s6_inventory_artifact_acceptance_gate_next_gate.csv"
DEFAULT_INVENTORY = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_artifact_definition_failure_mode_inventory.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition"


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


def _planned_fix_lane(row: dict[str, str]) -> str:
    mode = row.get("failure_mode", "")
    if mode.startswith("parser_unrecognized"):
        return "parser_query_family_hint_planning"
    if mode in {"top1_taxonomy_coverage_gap", "book_label_empty_taxonomy_gap"}:
        return "taxonomy_top1_family_coverage_planning"
    return "manual_review_only"


def _risk_level(row: dict[str, str]) -> str:
    source_family = row.get("source_family", "")
    flags = row.get("secondary_flags", "")
    if "wrong_book_boundary" in flags:
        return "high"
    if source_family in {"v36_trace_or_shadow", "accepted_oss_canonicalizer_alignment", "accepted_oss_speed_chain"}:
        return "medium"
    return "low"


def _owner_evidence_required(row: dict[str, str]) -> str:
    lane = _planned_fix_lane(row)
    if lane == "parser_query_family_hint_planning":
        return "owner-confirm query text family, matched_hint validity, before_family=<empty>, after_family proposal, and source provenance acceptance"
    if lane == "taxonomy_top1_family_coverage_planning":
        return "owner-confirm top1 quota family, before_top1_family=<empty>, after_top1_family proposal, and affected quota/domain scope"
    return "owner manual review required"


def _acceptance_check(row: dict[str, str]) -> str:
    lane = _planned_fix_lane(row)
    if lane == "parser_query_family_hint_planning":
        return "candidate has exact query pattern, proposed query_family, negative examples, dev-only dry-run boundary, and rollback plan"
    if lane == "taxonomy_top1_family_coverage_planning":
        return "candidate has exact quota/top1_id mapping, proposed family, source book scope, collision check, and rollback plan"
    return "candidate remains review-only"


def _rollback_boundary(row: dict[str, str]) -> str:
    lane = _planned_fix_lane(row)
    if lane == "parser_query_family_hint_planning":
        return "rollback by removing proposed parser hint/pattern only; no ranking or retrieval rollback needed"
    if lane == "taxonomy_top1_family_coverage_planning":
        return "rollback by reverting proposed family mapping rows only; preserve original quota metadata snapshot"
    return "no implementation rollback because no implementation is authorized"


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
    scope_rows: list[dict[str, Any]],
    acceptance_checks: list[dict[str, Any]],
    explicit_go: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.64 S6 Parser/Taxonomy Fix Planning Scope Definition",
        "",
        "Read-only planning scope for 16 future parser/taxonomy fix candidates.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["planning_candidate_rows", metrics["planning_candidate_rows"]],
                ["parser_planning_rows", metrics["parser_planning_rows"]],
                ["taxonomy_planning_rows", metrics["taxonomy_planning_rows"]],
                ["high_risk_rows", metrics["high_risk_rows"]],
                ["scope_definition_decision", metrics["scope_definition_decision"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Planning Scope",
        "",
        _md_table(
            [["planned_fix_lane", "candidate_rows", "risk_summary", "planning_boundary"]]
            + [[row["planned_fix_lane"], row["candidate_rows"], row["risk_summary"], row["planning_boundary"]] for row in scope_rows]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_checks]
        ),
        "",
        "## Explicit Go Requirements",
        "",
        _md_table(
            [["requirement_id", "required", "description"]]
            + [[row["requirement_id"], row["required"], row["description"]] for row in explicit_go]
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
    parser = argparse.ArgumentParser(description="Define S6 parser/taxonomy fix planning scope")
    parser.add_argument("--summary-1063", default=str(DEFAULT_1063_SUMMARY))
    parser.add_argument("--next-gate-1063", default=str(DEFAULT_NEXT_GATE))
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1063 = _read_json(Path(args.summary_1063))
    next_gate_1063 = _read_csv(Path(args.next_gate_1063))
    inventory = _read_csv(Path(args.inventory))
    m1063 = summary_1063["metrics"]
    candidates = [row for row in inventory if row.get("future_fix_candidate") == "true"]

    manifest_rows: list[dict[str, Any]] = []
    for row in candidates:
        lane = _planned_fix_lane(row)
        risk_level = _risk_level(row)
        manifest_rows.append(
            {
                "inventory_id": row.get("inventory_id", ""),
                "planned_fix_lane": lane,
                "planning_status": "scope_defined_not_implementation_ready",
                "risk_level": risk_level,
                "query": row.get("query", ""),
                "province": row.get("province", ""),
                "source_file": row.get("source_file", ""),
                "source_family": row.get("source_family", ""),
                "failure_mode": row.get("failure_mode", ""),
                "secondary_flags": row.get("secondary_flags", ""),
                "matched_hint": row.get("matched_hint", ""),
                "inferred_bucket_or_domain": row.get("inferred_bucket_or_domain", ""),
                "query_family_before": row.get("query_family", ""),
                "top1_family_before": row.get("top1_family", ""),
                "top1_book_relation": row.get("top1_book_relation", ""),
                "rank_bucket": row.get("rank_bucket", ""),
                "evidence_note": row.get("evidence_note", ""),
                "owner_evidence_required": _owner_evidence_required(row),
                "acceptance_check": _acceptance_check(row),
                "rollback_boundary": _rollback_boundary(row),
                "explicit_go_required": "true",
                "implementation_ready": "false",
                "learning_use_allowed": "false",
            }
        )

    lane_counts = Counter(row["planned_fix_lane"] for row in manifest_rows)
    risk_counts = Counter(row["risk_level"] for row in manifest_rows)
    mode_counts = Counter(row["failure_mode"] for row in manifest_rows)
    source_counts = Counter(row["source_family"] for row in manifest_rows)
    scope_rows = []
    for lane, count in lane_counts.most_common():
        lane_risks = Counter(row["risk_level"] for row in manifest_rows if row["planned_fix_lane"] == lane)
        scope_rows.append(
            {
                "planned_fix_lane": lane,
                "candidate_rows": count,
                "risk_summary": "; ".join(f"{key}:{value}" for key, value in lane_risks.most_common()),
                "planning_boundary": "read-only scope only; requires owner evidence, exact mapping, rollback, validation boundary, and explicit go before any implementation",
            }
        )

    evidence_requirements = [
        {
            "requirement_id": "OWNER_AFTER_FAMILY_MAPPING",
            "required_for": "all_candidates",
            "description": "Owner must provide exact after-family value or explicitly reject the candidate.",
            "acceptance_check": "Every candidate has before value, after value, owner, rationale, and source row reference.",
        },
        {
            "requirement_id": "SOURCE_PROVENANCE_ACCEPTANCE",
            "required_for": "all_candidates",
            "description": "Source family/provenance must be accepted before any implementation planning proceeds.",
            "acceptance_check": "No v36 trace/shadow candidate proceeds without accepted provenance note.",
        },
        {
            "requirement_id": "NEGATIVE_EXAMPLES_OR_COLLISION_CHECK",
            "required_for": "parser_query_family_hint_planning",
            "description": "Parser hints need negative examples to avoid overbroad family assignment.",
            "acceptance_check": "Each parser hint candidate includes at least one same-token non-target or no-collision justification.",
        },
        {
            "requirement_id": "QUOTA_SCOPE_AND_COLLISION_CHECK",
            "required_for": "taxonomy_top1_family_coverage_planning",
            "description": "Taxonomy mapping needs exact quota/top1 scope and collision check.",
            "acceptance_check": "Each taxonomy candidate lists top1_id/top1_name scope and potential cross-domain collision review.",
        },
    ]

    risk_register = [
        {
            "risk_id": "SOURCE_TRACE_NOT_OWNER_ACCEPTED",
            "risk_level": "high" if source_counts.get("v36_trace_or_shadow", 0) else "medium",
            "affected_rows": source_counts.get("v36_trace_or_shadow", 0),
            "mitigation": "Require owner/source provenance acceptance before implementation authorization.",
        },
        {
            "risk_id": "WRONG_BOOK_BOUNDARY",
            "risk_level": "high",
            "affected_rows": sum(1 for row in manifest_rows if "wrong_book_boundary" in row["secondary_flags"]),
            "mitigation": "Do not implement until book boundary is reviewed separately from parser/taxonomy mapping.",
        },
        {
            "risk_id": "OVERBROAD_PARSER_HINT",
            "risk_level": "medium",
            "affected_rows": lane_counts.get("parser_query_family_hint_planning", 0),
            "mitigation": "Require negative examples and dry-run loss audit before any parser hint edit.",
        },
        {
            "risk_id": "TAXONOMY_COLLISION",
            "risk_level": "medium",
            "affected_rows": lane_counts.get("taxonomy_top1_family_coverage_planning", 0),
            "mitigation": "Require exact quota scope and family collision check before any mapping edit.",
        },
    ]

    acceptance_checks = [
        {
            "check_id": "SC01_1063_AUTHORIZED_PLANNING",
            "status": "pass" if m1063.get("future_planning_gate_allowed") else "fail",
            "evidence": f"future_planning_gate_allowed={m1063.get('future_planning_gate_allowed')}",
            "decision": "10.64 is allowed as read-only planning scope.",
        },
        {
            "check_id": "SC02_CANDIDATES_ACCOUNTED",
            "status": "pass" if len(candidates) == _int(m1063.get("planning_candidate_rows")) == len(manifest_rows) else "fail",
            "evidence": f"candidates={len(candidates)}; planning_candidate_rows={m1063.get('planning_candidate_rows')}; manifest_rows={len(manifest_rows)}",
            "decision": "All 16 candidates are included in the planning manifest.",
        },
        {
            "check_id": "SC03_SCOPE_SPLIT_DEFINED",
            "status": "pass" if lane_counts.get("parser_query_family_hint_planning", 0) and lane_counts.get("taxonomy_top1_family_coverage_planning", 0) else "fail",
            "evidence": f"lane_counts={dict(lane_counts)}",
            "decision": "Parser and taxonomy planning lanes are separated.",
        },
        {
            "check_id": "SC04_ROLLBACK_AND_GO_REQUIREMENTS_DEFINED",
            "status": "pass" if all(row["explicit_go_required"] == "true" and row["implementation_ready"] == "false" for row in manifest_rows) else "fail",
            "evidence": "all rows require explicit go and remain implementation_ready=false",
            "decision": "Planning scope includes explicit go and rollback boundary before implementation.",
        },
        {
            "check_id": "SC05_NON_EXECUTION_CONTRACT",
            "status": "pass",
            "evidence": "training_allowed=false; parser_edit_allowed=false; taxonomy_edit_allowed=false; implementation_allowed=false",
            "decision": "10.64 is planning scope only.",
        },
    ]
    fail_count = sum(1 for row in acceptance_checks if row["status"] != "pass")

    explicit_go_requirements = [
        {
            "requirement_id": "EXPLICIT_IMPLEMENTATION_GO",
            "required": "true",
            "description": "User/owner must explicitly authorize parser/taxonomy implementation after reviewing exact mappings.",
        },
        {
            "requirement_id": "OWNER_ACCEPTED_ROW_MAPPINGS",
            "required": "true",
            "description": "Every candidate needs accepted before/after mapping, owner rationale, and rollback target.",
        },
        {
            "requirement_id": "DRY_RUN_AND_LOSS_AUDIT_PLAN",
            "required": "true",
            "description": "Future implementation plan must define dev/OOF-only dry-run, affected slices, and loss budget before any edit.",
        },
        {
            "requirement_id": "NO_HELDOUT_SELECTION",
            "required": "true",
            "description": "Heldout/hard cannot be used to choose mappings or parser rules.",
        },
    ]

    next_gate = [
        {
            "next_stage": "10.65 S6 parser/taxonomy fix planning acceptance gate",
            "status": "allowed_read_only_acceptance_gate" if fail_count == 0 else "hold",
            "scope": "Read-only decide whether the 16-candidate planning package is sufficient to request explicit implementation go and owner mappings.",
            "not_allowed": "no parser edit, no taxonomy edit, no data edit, no training, no implementation, no GoalSearcher change, no heldout/hard selection",
        }
    ]

    blocked_actions = [
        {
            "blocked_action": "implement_parser_hint",
            "reason": "10.64 defines planning scope only; no exact owner-accepted mapping or explicit implementation go exists.",
            "allowed_after": "future explicit implementation authorization with accepted mappings and rollback plan",
        },
        {
            "blocked_action": "implement_taxonomy_mapping",
            "reason": "Taxonomy candidates still require owner after-family mappings, collision checks, and rollback boundary.",
            "allowed_after": "future explicit implementation authorization with accepted mappings and rollback plan",
        },
        {
            "blocked_action": "train_or_tune_from_candidates",
            "reason": "The 16 candidates are DQ/parser/taxonomy planning rows, not learning evidence.",
            "allowed_after": "future learning re-entry review with accepted non-generated effect evidence",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "10.64 uses no heldout/hard and does not select implementation mappings.",
            "allowed_after": "never for selection",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "planning_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_planning_manifest.csv")),
        "scope_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_scope_rollup.csv")),
        "evidence_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_requirements.csv")),
        "risk_register_csv": str(output_prefix.with_name(output_prefix.name + "_risk_register.csv")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "explicit_go_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_explicit_go_requirements.csv")),
        "next_gate_csv": str(output_prefix.with_name(output_prefix.name + "_next_gate.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1063["stage"],
        "planning_candidate_rows": len(manifest_rows),
        "parser_planning_rows": lane_counts.get("parser_query_family_hint_planning", 0),
        "taxonomy_planning_rows": lane_counts.get("taxonomy_top1_family_coverage_planning", 0),
        "planning_lane_count": len(lane_counts),
        "failure_mode_count": len(mode_counts),
        "source_family_count": len(source_counts),
        "high_risk_rows": risk_counts.get("high", 0),
        "medium_risk_rows": risk_counts.get("medium", 0),
        "low_risk_rows": risk_counts.get("low", 0),
        "evidence_requirement_count": len(evidence_requirements),
        "risk_register_count": len(risk_register),
        "explicit_go_requirement_count": len(explicit_go_requirements),
        "acceptance_pass_count": len(acceptance_checks) - fail_count,
        "acceptance_fail_count": fail_count,
        "scope_definition_decision": "planning_scope_defined" if fail_count == 0 else "hold_until_scope_complete",
        "implementation_ready_rows": 0,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.64 S6 parser/taxonomy fix planning scope definition",
        "read_only": True,
        "planning_scope_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Define the S6 parser/taxonomy fix planning scope for the 16 candidates. The package separates parser query-family hint planning from taxonomy top1-family coverage planning, "
            "records evidence requirements, risks, rollback boundaries, and explicit go requirements. No row is implementation-ready and no parser/taxonomy edit is authorized."
        ),
        "anti_drift_conclusion": (
            "10.64 only defines a read-only planning scope. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, modify GoalSearcher, "
            "edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.65 S6 parser/taxonomy fix planning acceptance gate",
            "goal": "Read-only decide whether the 16-candidate planning package is sufficient to request explicit implementation go and owner mappings.",
            "default": "acceptance gate only; no parser edit, taxonomy edit, implementation, training, or heldout/hard selection",
        },
    }

    _write_csv(
        Path(artifacts["planning_manifest_csv"]),
        manifest_rows,
        [
            "inventory_id",
            "planned_fix_lane",
            "planning_status",
            "risk_level",
            "query",
            "province",
            "source_file",
            "source_family",
            "failure_mode",
            "secondary_flags",
            "matched_hint",
            "inferred_bucket_or_domain",
            "query_family_before",
            "top1_family_before",
            "top1_book_relation",
            "rank_bucket",
            "evidence_note",
            "owner_evidence_required",
            "acceptance_check",
            "rollback_boundary",
            "explicit_go_required",
            "implementation_ready",
            "learning_use_allowed",
        ],
    )
    _write_csv(Path(artifacts["scope_rollup_csv"]), scope_rows, ["planned_fix_lane", "candidate_rows", "risk_summary", "planning_boundary"])
    _write_csv(Path(artifacts["evidence_requirements_csv"]), evidence_requirements, ["requirement_id", "required_for", "description", "acceptance_check"])
    _write_csv(Path(artifacts["risk_register_csv"]), risk_register, ["risk_id", "risk_level", "affected_rows", "mitigation"])
    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["explicit_go_requirements_csv"]), explicit_go_requirements, ["requirement_id", "required", "description"])
    _write_csv(Path(artifacts["next_gate_csv"]), next_gate, ["next_stage", "status", "scope", "not_allowed"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, scope_rows, acceptance_checks, explicit_go_requirements)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
