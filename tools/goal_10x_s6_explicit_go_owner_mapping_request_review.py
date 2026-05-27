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
DEFAULT_1065_SUMMARY = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate_summary.json"
DEFAULT_OWNER_TEMPLATE = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate_owner_mapping_template.csv"
DEFAULT_REQUEST_PACKAGE = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate_request_package.csv"
DEFAULT_NEXT_GATE = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate_next_gate.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_acceptance_gate_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_explicit_go_owner_mapping_request_review"


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
    review_checks: list[dict[str, Any]],
    missing_items: list[dict[str, Any]],
    next_options: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.66 S6 Explicit Go / Owner Mapping Request Package Review",
        "",
        "Read-only review of whether explicit implementation go and owner accepted mappings have been provided.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["request_package_decision", metrics["request_package_decision"]],
                ["explicit_go_present", metrics["explicit_go_present"]],
                ["owner_mappings_complete", metrics["owner_mappings_complete"]],
                ["owner_after_values_missing", metrics["owner_after_values_missing"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Review Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in review_checks]
        ),
        "",
        "## Missing Items",
        "",
        _md_table(
            [["missing_item", "missing_count", "required_before"]]
            + [[row["missing_item"], row["missing_count"], row["required_before"]] for row in missing_items]
        ),
        "",
        "## Next Options",
        "",
        _md_table(
            [["option", "status", "rationale"]]
            + [[row["option"], row["status"], row["rationale"]] for row in next_options]
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
    parser = argparse.ArgumentParser(description="Review S6 explicit go and owner mapping request package")
    parser.add_argument("--summary-1065", default=str(DEFAULT_1065_SUMMARY))
    parser.add_argument("--owner-template", default=str(DEFAULT_OWNER_TEMPLATE))
    parser.add_argument("--request-package", default=str(DEFAULT_REQUEST_PACKAGE))
    parser.add_argument("--next-gate-1065", default=str(DEFAULT_NEXT_GATE))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1065 = _read_json(Path(args.summary_1065))
    owner_template = _read_csv(Path(args.owner_template))
    request_package = _read_csv(Path(args.request_package))
    next_gate_1065 = _read_csv(Path(args.next_gate_1065))
    blocked_input = _read_csv(Path(args.blocked_actions))
    m1065 = summary_1065["metrics"]

    owner_decision_counts = Counter(row.get("owner_decision", "") for row in owner_template)
    lane_counts = Counter(row.get("planned_fix_lane", "") for row in owner_template)
    proposed_after_missing = [row for row in owner_template if not row.get("proposed_after_value")]
    owner_rationale_missing = [row for row in owner_template if not row.get("owner_rationale")]
    pending_rows = [row for row in owner_template if row.get("owner_decision") == "pending_accept_or_reject"]
    implementation_ready_rows = [row for row in owner_template if row.get("implementation_ready") == "true"]
    explicit_go_present = False
    owner_mappings_complete = (
        bool(owner_template)
        and not proposed_after_missing
        and not owner_rationale_missing
        and not pending_rows
        and len(owner_template) == _int(m1065.get("owner_mapping_template_rows"))
    )
    implementation_allowed = explicit_go_present and owner_mappings_complete and len(implementation_ready_rows) == len(owner_template)

    review_checks = [
        {
            "check_id": "RG01_REQUEST_PACKAGE_EXISTS",
            "status": "pass" if request_package and owner_template and next_gate_1065 else "fail",
            "evidence": f"request_package_items={len(request_package)}; owner_mapping_rows={len(owner_template)}; next_gate_rows={len(next_gate_1065)}",
            "decision": "The 10.65 request package and owner mapping template exist.",
        },
        {
            "check_id": "RG02_EXPLICIT_GO_PRESENT",
            "status": "fail" if not explicit_go_present else "pass",
            "evidence": f"explicit_go_present={explicit_go_present}",
            "decision": "No explicit implementation go has been provided.",
        },
        {
            "check_id": "RG03_OWNER_MAPPINGS_COMPLETE",
            "status": "pass" if owner_mappings_complete else "fail",
            "evidence": f"owner_rows={len(owner_template)}; proposed_after_missing={len(proposed_after_missing)}; owner_rationale_missing={len(owner_rationale_missing)}; pending_rows={len(pending_rows)}",
            "decision": "Owner mappings are incomplete.",
        },
        {
            "check_id": "RG04_IMPLEMENTATION_READY_ROWS",
            "status": "fail" if implementation_ready_rows else "pass",
            "evidence": f"implementation_ready_rows={len(implementation_ready_rows)}",
            "decision": "No implementation-ready row exists, which preserves the held state.",
        },
        {
            "check_id": "RG05_NON_EXECUTION_CONTRACT",
            "status": "pass" if not implementation_allowed and not m1065.get("implementation_allowed") else "fail",
            "evidence": f"implementation_allowed={implementation_allowed}; upstream_implementation_allowed={m1065.get('implementation_allowed')}",
            "decision": "10.66 remains request-package review only.",
        },
    ]

    missing_items = [
        {
            "missing_item": "explicit_implementation_go",
            "missing_count": 1 if not explicit_go_present else 0,
            "required_before": "any parser/taxonomy implementation planning can be authorized",
        },
        {
            "missing_item": "owner_proposed_after_values",
            "missing_count": len(proposed_after_missing),
            "required_before": "any candidate can become implementation-ready",
        },
        {
            "missing_item": "owner_rationales",
            "missing_count": len(owner_rationale_missing),
            "required_before": "any candidate can be accepted or rejected",
        },
        {
            "missing_item": "owner_accept_or_reject_decisions",
            "missing_count": len(pending_rows),
            "required_before": "request package can leave held state",
        },
    ]

    next_options = [
        {
            "option": "keep_held_do_not_implement",
            "status": "selected_default",
            "rationale": "Explicit go is absent and all 16 owner mappings remain pending/incomplete.",
        },
        {
            "option": "provide_explicit_go_plus_owner_mappings",
            "status": "allowed_user_action",
            "rationale": "User/owner may provide explicit go plus complete accepted/rejected mappings for a future review.",
        },
        {
            "option": "return_to_broader_strategy_review",
            "status": "allowed_user_redirect",
            "rationale": "If mappings are not available, the S6 implementation lane can be parked and broader strategy can resume.",
        },
        {
            "option": "implement_now",
            "status": "blocked",
            "rationale": "No explicit go, no owner mappings, and no implementation-ready rows exist.",
        },
    ]

    blocked_actions = blocked_input + [
        {
            "blocked_action": "implement_parser_or_taxonomy_from_request_package",
            "reason": "explicit_go_present=false and owner_mappings_complete=false.",
            "allowed_after": "future explicit go plus complete owner accepted mappings and implementation authorization",
        },
        {
            "blocked_action": "mark_pending_owner_rows_as_accepted",
            "reason": "All owner rows remain pending with empty after values and rationales.",
            "allowed_after": "owner supplies accepted/rejected decisions and mappings",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "review_checks_csv": str(output_prefix.with_name(output_prefix.name + "_review_checks.csv")),
        "missing_items_csv": str(output_prefix.with_name(output_prefix.name + "_missing_items.csv")),
        "owner_mapping_status_csv": str(output_prefix.with_name(output_prefix.name + "_owner_mapping_status.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    owner_mapping_status = [
        {
            "inventory_id": row.get("inventory_id", ""),
            "planned_fix_lane": row.get("planned_fix_lane", ""),
            "query": row.get("query", ""),
            "owner_decision": row.get("owner_decision", ""),
            "proposed_after_value_present": bool(row.get("proposed_after_value")),
            "owner_rationale_present": bool(row.get("owner_rationale")),
            "implementation_ready": row.get("implementation_ready", ""),
            "status": "pending_owner_mapping",
        }
        for row in owner_template
    ]

    metrics = {
        "source_stage": summary_1065["stage"],
        "request_package_items": len(request_package),
        "owner_mapping_template_rows": len(owner_template),
        "parser_mapping_rows": lane_counts.get("parser_query_family_hint_planning", 0),
        "taxonomy_mapping_rows": lane_counts.get("taxonomy_top1_family_coverage_planning", 0),
        "owner_pending_rows": len(pending_rows),
        "owner_after_values_missing": len(proposed_after_missing),
        "owner_rationales_missing": len(owner_rationale_missing),
        "implementation_ready_rows": len(implementation_ready_rows),
        "explicit_go_present": explicit_go_present,
        "owner_mappings_complete": owner_mappings_complete,
        "implementation_allowed": implementation_allowed,
        "request_package_decision": "keep_held_do_not_implement",
        "review_pass_count": sum(1 for row in review_checks if row["status"] == "pass"),
        "review_fail_count": sum(1 for row in review_checks if row["status"] == "fail"),
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.66 S6 explicit implementation go / owner mapping request package review",
        "read_only": True,
        "request_package_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Keep the S6 parser/taxonomy fix lane held and do_not_implement. The request package exists, but explicit_go_present=false, "
            "owner_mappings_complete=false, all 16 owner rows remain pending, and no implementation-ready rows exist."
        ),
        "anti_drift_conclusion": (
            "10.66 only reviews whether explicit go and owner mappings have been provided. It does not train, tune, expand candidate matrices, run heldout/hard selection, "
            "change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.67 S6 implementation held / request closure",
            "goal": "Read-only close or hold the S6 implementation request loop: keep held unless explicit go plus complete owner mappings are provided, or user redirects to broader strategy.",
            "default": "keep held / do_not_implement",
        },
    }

    _write_csv(Path(artifacts["review_checks_csv"]), review_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["missing_items_csv"]), missing_items, ["missing_item", "missing_count", "required_before"])
    _write_csv(
        Path(artifacts["owner_mapping_status_csv"]),
        owner_mapping_status,
        ["inventory_id", "planned_fix_lane", "query", "owner_decision", "proposed_after_value_present", "owner_rationale_present", "implementation_ready", "status"],
    )
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, review_checks, missing_items, next_options)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
