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
DEFAULT_AUTH_SUMMARY = AGENT_STATE / "goal_10x_dq_implementation_authorization_go_no_go_summary.json"
DEFAULT_MISSING = AGENT_STATE / "goal_10x_dq_implementation_authorization_go_no_go_missing_before_go.csv"
DEFAULT_AUTH_CHECKS = AGENT_STATE / "goal_10x_dq_implementation_authorization_go_no_go_authorization_checks.csv"
DEFAULT_READINESS = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_candidate_readiness.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_implementation_held_checkpoint"


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


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    route_rows: list[dict[str, Any]],
    request_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.39 DQ Implementation Held Checkpoint",
        "",
        "Read-only checkpoint after DQ implementation authorization was not granted.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["held_decision", metrics["held_decision"]],
                ["plan_ready_candidate_count", metrics["plan_ready_candidate_count"]],
                ["owner_after_values_missing", metrics["owner_after_values_missing"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Route Decision",
        "",
        _md_table(
            [["route_option", "decision", "rationale"]]
            + [[row["route_option"], row["decision"], row["rationale"]] for row in route_rows]
        ),
        "",
        "## Request Package",
        "",
        _md_table(
            [["request_id", "required_input", "status", "acceptance_condition"]]
            + [
                [row["request_id"], row["required_input"], row["status"], row["acceptance_condition"]]
                for row in request_rows
            ]
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
    parser = argparse.ArgumentParser(description="Read-only DQ implementation held checkpoint")
    parser.add_argument("--auth-summary", default=str(DEFAULT_AUTH_SUMMARY))
    parser.add_argument("--missing-before-go", default=str(DEFAULT_MISSING))
    parser.add_argument("--authorization-checks", default=str(DEFAULT_AUTH_CHECKS))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    auth_summary = _read_json(Path(args.auth_summary))
    missing_rows = _read_csv(Path(args.missing_before_go))
    auth_checks = _read_csv(Path(args.authorization_checks))
    readiness_rows = _read_csv(Path(args.readiness))
    auth_metrics = auth_summary["metrics"]

    plan_ready = [row for row in readiness_rows if row.get("readiness_status") == "plan_definition_ready"]
    missing_required = [row for row in missing_rows if row.get("status") == "missing"]
    pending_required = [row for row in missing_rows if row.get("status") == "pending"]
    explicit_go_missing = any(row.get("requirement_id") == "explicit_user_go" and row.get("status") == "missing" for row in missing_rows)
    owner_mapping_missing = any(row.get("requirement_id") == "owner_row_mapping" and row.get("status") == "missing" for row in missing_rows)

    route_rows = [
        {
            "route_option": "keep_dq_implementation_held",
            "decision": "select",
            "rationale": "10.38 authorization_decision=do_not_implement; explicit go is missing and owner row mappings are missing for plan-ready rows.",
        },
        {
            "route_option": "request_explicit_go_plus_owner_row_mappings",
            "decision": "select_as_required_input_path",
            "rationale": "This is the only path that can reopen implementation authorization without weakening the gate.",
        },
        {
            "route_option": "return_to_broader_strategy_review",
            "decision": "defer",
            "rationale": "A concrete DQ implementation package exists but is missing authorization inputs; broader strategy review is premature unless the user declines DQ implementation.",
        },
    ]
    request_rows = [
        {
            "request_id": "explicit_implementation_go",
            "required_input": "User explicitly says go for DQ implementation, not just planning.",
            "status": "missing" if explicit_go_missing else "present",
            "acceptance_condition": "Message must clearly authorize implementation of plan-ready DQ row mappings.",
        },
        {
            "request_id": "owner_row_level_mapping",
            "required_input": "Owner-approved before/after mapping for 64 plan-ready rows.",
            "status": "missing" if owner_mapping_missing else "present",
            "acceptance_condition": "Every row_id has old value, proposed_after_value, owner note, and rollback key.",
        },
        {
            "request_id": "rollback_manifest_recheck",
            "required_input": "Rollback manifest rechecked against the final owner mapping.",
            "status": "pending",
            "acceptance_condition": "Rollback table references exact row_id keys and old values for every edited row.",
        },
        {
            "request_id": "validation_boundary_recheck",
            "required_input": "Separate validation authorization after implementation freeze.",
            "status": "pending",
            "acceptance_condition": "Heldout/hard remains excluded from selection; any validation is approved separately.",
        },
        {
            "request_id": "anti_drift_diff_check",
            "required_input": "Diff check proving no GoalSearcher/ranking/threshold/feature whitelist/model training changes.",
            "status": "pending",
            "acceptance_condition": "Implementation diff is limited to explicitly authorized DQ mapping artifacts.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "implement_dq_row_mappings",
            "reason": "DQ implementation remains held; explicit go and owner row mappings are missing.",
            "allowed_after": "future explicit go plus complete owner row-level mapping package",
        },
        {
            "blocked_action": "return_to_validation_or_heldout",
            "reason": "No implementation has been authorized or frozen.",
            "allowed_after": "future implementation freeze and separate validation gate",
        },
        {
            "blocked_action": "train_or_reopen_s2",
            "reason": "Held checkpoint does not create accepted-OSS positive learning evidence.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS evidence",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "DQ held checkpoint cannot authorize online/search behavior changes.",
            "allowed_after": "separate post-DQ strategy review, if ever authorized",
        },
        {
            "blocked_action": "weaken_go_gate",
            "reason": "Default path without explicit go is do_not_implement.",
            "allowed_after": "never; explicit authorization is required",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "route_decision_csv": str(output_prefix.with_name(output_prefix.name + "_route_decision.csv")),
        "request_package_csv": str(output_prefix.with_name(output_prefix.name + "_request_package.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": auth_summary["stage"],
        "authorization_decision_from_10_38": auth_metrics["authorization_decision"],
        "held_decision": "keep_held",
        "request_explicit_go_plus_owner_mappings": True,
        "return_to_broader_strategy_now": False,
        "plan_ready_candidate_count": len(plan_ready),
        "plan_ready_distinct_row_count": auth_metrics["plan_ready_distinct_row_count"],
        "owner_after_values_missing": auth_metrics["owner_after_values_missing"],
        "missing_requirement_count": len(missing_required),
        "pending_requirement_count": len(pending_required),
        "authorization_check_count": len(auth_checks),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.39 DQ implementation held checkpoint",
        "read_only": True,
        "dq_implementation_held_checkpoint_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Keep DQ implementation held. The appropriate next path is to request explicit implementation go plus owner-approved row-level before/after mappings; "
            "returning to broader strategy review is deferred because a concrete implementation package exists but lacks authorization inputs."
        ),
        "anti_drift_conclusion": (
            "10.39 only decides held status and required authorization inputs. It does not edit data, taxonomy, rules, thresholds, GoalSearcher, feature whitelists, "
            "train or tune models, run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.40 DQ implementation explicit go/request package review",
            "goal": "Read-only review whether explicit implementation go and owner row-level mappings have been supplied; default remains do_not_implement.",
            "default": "keep held unless explicit go and complete owner row mapping package are present",
        },
    }

    _write_csv(Path(artifacts["route_decision_csv"]), route_rows, ["route_option", "decision", "rationale"])
    _write_csv(Path(artifacts["request_package_csv"]), request_rows, ["request_id", "required_input", "status", "acceptance_condition"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, route_rows, request_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
