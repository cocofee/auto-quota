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
DEFAULT_1066_SUMMARY = AGENT_STATE / "goal_10x_s6_explicit_go_owner_mapping_request_review_summary.json"
DEFAULT_MISSING = AGENT_STATE / "goal_10x_s6_explicit_go_owner_mapping_request_review_missing_items.csv"
DEFAULT_NEXT_OPTIONS = AGENT_STATE / "goal_10x_s6_explicit_go_owner_mapping_request_review_next_options.csv"
DEFAULT_OWNER_STATUS = AGENT_STATE / "goal_10x_s6_explicit_go_owner_mapping_request_review_owner_mapping_status.csv"
DEFAULT_BLOCKED = AGENT_STATE / "goal_10x_s6_explicit_go_owner_mapping_request_review_blocked_actions.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_implementation_held_request_closure"


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
    closure_checks: list[dict[str, Any]],
    stop_conditions: list[dict[str, Any]],
    reopen_requirements: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.67 S6 Implementation Held / Request Closure",
        "",
        "Read-only closure of the S6 parser/taxonomy implementation request loop.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closure_decision", metrics["closure_decision"]],
                ["explicit_go_present", metrics["explicit_go_present"]],
                ["owner_mappings_complete", metrics["owner_mappings_complete"]],
                ["owner_pending_rows", metrics["owner_pending_rows"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["return_to_broader_strategy_now", metrics["return_to_broader_strategy_now"]],
            ]
        ),
        "",
        "## Closure Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in closure_checks]
        ),
        "",
        "## Stop Conditions",
        "",
        _md_table(
            [["condition", "status", "effect"]]
            + [[row["condition"], row["status"], row["effect"]] for row in stop_conditions]
        ),
        "",
        "## Reopen Requirements",
        "",
        _md_table(
            [["requirement", "required_count", "status"]]
            + [[row["requirement"], row["required_count"], row["status"]] for row in reopen_requirements]
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
    parser = argparse.ArgumentParser(description="Close or hold S6 implementation request loop")
    parser.add_argument("--summary-1066", default=str(DEFAULT_1066_SUMMARY))
    parser.add_argument("--missing-items", default=str(DEFAULT_MISSING))
    parser.add_argument("--next-options", default=str(DEFAULT_NEXT_OPTIONS))
    parser.add_argument("--owner-status", default=str(DEFAULT_OWNER_STATUS))
    parser.add_argument("--blocked-actions", default=str(DEFAULT_BLOCKED))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1066 = _read_json(Path(args.summary_1066))
    missing_items = _read_csv(Path(args.missing_items))
    next_options_input = _read_csv(Path(args.next_options))
    owner_status = _read_csv(Path(args.owner_status))
    blocked_input = _read_csv(Path(args.blocked_actions))
    m1066 = summary_1066["metrics"]

    explicit_go_present = bool(m1066.get("explicit_go_present"))
    owner_mappings_complete = bool(m1066.get("owner_mappings_complete"))
    implementation_allowed = bool(m1066.get("implementation_allowed"))
    owner_pending_rows = _int(m1066.get("owner_pending_rows"))
    owner_after_values_missing = _int(m1066.get("owner_after_values_missing"))
    owner_rationales_missing = _int(m1066.get("owner_rationales_missing"))
    implementation_ready_rows = _int(m1066.get("implementation_ready_rows"))
    selected_option = next((row for row in next_options_input if row.get("status") == "selected_default"), {})

    closure_checks = [
        {
            "check_id": "CL01_PRIOR_REVIEW_COMPLETED",
            "status": "pass" if m1066.get("request_package_decision") == "keep_held_do_not_implement" else "fail",
            "evidence": f"request_package_decision={m1066.get('request_package_decision')}",
            "decision": "10.66 already selected keep_held_do_not_implement.",
        },
        {
            "check_id": "CL02_GO_AND_MAPPINGS_ABSENT",
            "status": "pass" if not explicit_go_present and not owner_mappings_complete else "fail",
            "evidence": f"explicit_go_present={explicit_go_present}; owner_mappings_complete={owner_mappings_complete}",
            "decision": "No implementation authorization package exists.",
        },
        {
            "check_id": "CL03_OWNER_ROWS_PENDING",
            "status": "pass" if owner_pending_rows == len(owner_status) and owner_after_values_missing == len(owner_status) else "fail",
            "evidence": f"owner_pending_rows={owner_pending_rows}; owner_status_rows={len(owner_status)}; owner_after_values_missing={owner_after_values_missing}",
            "decision": "All 16 owner mappings remain pending and incomplete.",
        },
        {
            "check_id": "CL04_NO_IMPLEMENTATION_READY_ROWS",
            "status": "pass" if implementation_ready_rows == 0 and not implementation_allowed else "fail",
            "evidence": f"implementation_ready_rows={implementation_ready_rows}; implementation_allowed={implementation_allowed}",
            "decision": "No row is implementation-ready.",
        },
        {
            "check_id": "CL05_NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "training_allowed=false; parser_edit_allowed=false; taxonomy_edit_allowed=false; heldout_selection_allowed=false",
            "decision": "10.67 is closure/hold only.",
        },
    ]
    fail_count = sum(1 for row in closure_checks if row["status"] != "pass")

    stop_conditions = [
        {
            "condition": "no_explicit_implementation_go",
            "status": "active",
            "effect": "do_not_implement",
        },
        {
            "condition": "owner_mappings_incomplete",
            "status": "active",
            "effect": "do_not_implement",
        },
        {
            "condition": "no_implementation_ready_rows",
            "status": "active",
            "effect": "keep S6 implementation lane held",
        },
        {
            "condition": "no_user_redirect_to_broader_strategy_in_this_stage",
            "status": "active",
            "effect": "do not auto-return to broader strategy",
        },
    ]

    reopen_requirements = [
        {
            "requirement": "explicit_implementation_go",
            "required_count": 1,
            "status": "missing",
        },
        {
            "requirement": "owner_proposed_after_values",
            "required_count": owner_after_values_missing,
            "status": "missing",
        },
        {
            "requirement": "owner_rationales",
            "required_count": owner_rationales_missing,
            "status": "missing",
        },
        {
            "requirement": "owner_accept_or_reject_decisions",
            "required_count": owner_pending_rows,
            "status": "missing",
        },
        {
            "requirement": "dev_oof_dry_run_loss_audit_plan_after_mappings",
            "required_count": 1,
            "status": "not_started",
        },
    ]

    next_options = [
        {
            "option": "pause_await_explicit_go_plus_owner_mappings",
            "status": "selected_default",
            "rationale": "S6 cannot proceed without explicit go and 16 complete owner mappings.",
        },
        {
            "option": "return_to_broader_strategy_review",
            "status": "available_if_user_redirects",
            "rationale": "If owner mappings are not available, user can explicitly redirect away from S6 implementation lane.",
        },
        {
            "option": "implement_now",
            "status": "blocked",
            "rationale": "No explicit go, no complete mappings, no implementation-ready rows.",
        },
    ]

    blocked_actions = blocked_input + [
        {
            "blocked_action": "auto_advance_s6_implementation",
            "reason": "S6 implementation lane is held; explicit go and mappings are missing.",
            "allowed_after": "future explicit go plus complete owner mappings and implementation authorization",
        },
        {
            "blocked_action": "auto_return_to_broader_strategy_without_user_redirect",
            "reason": "10.67 closes the current held state but user has not explicitly redirected in this stage.",
            "allowed_after": "user explicitly requests broader strategy return",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_checks_csv": str(output_prefix.with_name(output_prefix.name + "_closure_checks.csv")),
        "stop_conditions_csv": str(output_prefix.with_name(output_prefix.name + "_stop_conditions.csv")),
        "reopen_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_reopen_requirements.csv")),
        "next_options_csv": str(output_prefix.with_name(output_prefix.name + "_next_options.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1066["stage"],
        "closure_decision": "pause_await_explicit_go_plus_owner_mappings",
        "selected_prior_option": selected_option.get("option", ""),
        "request_package_decision": m1066.get("request_package_decision"),
        "explicit_go_present": explicit_go_present,
        "owner_mappings_complete": owner_mappings_complete,
        "owner_pending_rows": owner_pending_rows,
        "owner_after_values_missing": owner_after_values_missing,
        "owner_rationales_missing": owner_rationales_missing,
        "implementation_ready_rows": implementation_ready_rows,
        "implementation_allowed": False,
        "return_to_broader_strategy_now": False,
        "reopen_requirement_count": len(reopen_requirements),
        "active_stop_condition_count": len(stop_conditions),
        "closure_pass_count": len(closure_checks) - fail_count,
        "closure_fail_count": fail_count,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.67 S6 implementation held / request closure",
        "read_only": True,
        "held_closure_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Close the current S6 implementation request loop as held. Continue do_not_implement because explicit go is absent, "
            "all 16 owner mappings remain pending/incomplete, and no implementation-ready row exists. Do not auto-return to broader strategy unless the user explicitly redirects."
        ),
        "anti_drift_conclusion": (
            "10.67 only closes the held/request state. It does not train, tune, expand candidate matrices, run heldout/hard selection, change thresholds or rules, "
            "modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "paused awaiting explicit go + 16 complete owner mappings, or explicit broader strategy redirect",
            "goal": "Stop automatic S6 implementation progress until required inputs arrive or the user redirects.",
            "default": "pause / do_not_implement",
        },
    }

    _write_csv(Path(artifacts["closure_checks_csv"]), closure_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, ["condition", "status", "effect"])
    _write_csv(Path(artifacts["reopen_requirements_csv"]), reopen_requirements, ["requirement", "required_count", "status"])
    _write_csv(Path(artifacts["next_options_csv"]), next_options, ["option", "status", "rationale"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_checks, stop_conditions, reopen_requirements)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
