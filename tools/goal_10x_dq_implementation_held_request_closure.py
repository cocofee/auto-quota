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
DEFAULT_REQUEST_REVIEW = AGENT_STATE / "goal_10x_dq_implementation_explicit_go_request_package_review_summary.json"
DEFAULT_PACKAGE_REVIEW = AGENT_STATE / "goal_10x_dq_implementation_explicit_go_request_package_review_package_review.csv"
DEFAULT_REQUEST_PACKAGE = AGENT_STATE / "goal_10x_dq_implementation_held_checkpoint_request_package.csv"
DEFAULT_READINESS = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_candidate_readiness.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_implementation_held_request_closure"


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
    closure_rows: list[dict[str, Any]],
    resume_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.41 DQ Implementation Held/Request Closure",
        "",
        "Read-only closure for the DQ implementation held/request loop.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["closure_decision", metrics["closure_decision"]],
                ["request_package_decision_from_10_40", metrics["request_package_decision_from_10_40"]],
                ["plan_ready_candidate_count", metrics["plan_ready_candidate_count"]],
                ["owner_after_values_missing", metrics["owner_after_values_missing"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
            ]
        ),
        "",
        "## Closure Decision",
        "",
        _md_table(
            [["closure_item", "status", "evidence", "decision"]]
            + [[row["closure_item"], row["status"], row["evidence"], row["decision"]] for row in closure_rows]
        ),
        "",
        "## Resume Requirements",
        "",
        _md_table(
            [["requirement_id", "required_input", "status", "acceptance_condition"]]
            + [
                [row["requirement_id"], row["required_input"], row["status"], row["acceptance_condition"]]
                for row in resume_rows
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
    parser = argparse.ArgumentParser(description="Close DQ implementation held/request loop without implementation")
    parser.add_argument("--request-review", default=str(DEFAULT_REQUEST_REVIEW))
    parser.add_argument("--package-review", default=str(DEFAULT_PACKAGE_REVIEW))
    parser.add_argument("--request-package", default=str(DEFAULT_REQUEST_PACKAGE))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    request_review = _read_json(Path(args.request_review))
    package_review = _read_csv(Path(args.package_review))
    request_package = _read_csv(Path(args.request_package))
    readiness = _read_csv(Path(args.readiness))
    metrics_10_40 = request_review["metrics"]

    plan_ready = [row for row in readiness if row.get("readiness_status") == "plan_definition_ready"]
    not_ready = [row for row in readiness if row.get("readiness_status") != "plan_definition_ready"]
    blocked_package_items = [row for row in package_review if row.get("review_decision") == "block"]
    pending_package_items = [row for row in package_review if row.get("review_decision", "").startswith("pending")]

    closure_rows = [
        {
            "closure_item": "implementation_authorization",
            "status": "closed_as_held",
            "evidence": f"request_package_decision={metrics_10_40['request_package_decision']}; explicit_go_present={metrics_10_40['explicit_go_present']}",
            "decision": "pause_keep_held",
        },
        {
            "closure_item": "owner_row_mapping",
            "status": "missing",
            "evidence": f"owner_mapping_complete={metrics_10_40['owner_mapping_complete']}; owner_after_values_missing={metrics_10_40['owner_after_values_missing']}",
            "decision": "request_owner_mapping_before_any_implementation",
        },
        {
            "closure_item": "plan_ready_candidates",
            "status": "parked",
            "evidence": f"plan_ready_candidate_count={len(plan_ready)}; not_ready_candidate_count={len(not_ready)}",
            "decision": "keep_plan_ready_candidates_as_future_seeds",
        },
        {
            "closure_item": "broader_strategy_return",
            "status": "deferred",
            "evidence": "concrete DQ implementation package exists but lacks authorization inputs",
            "decision": "do_not_return_to_strategy_until_user_declines_or_package_is_supplied",
        },
    ]
    resume_rows = [
        {
            "requirement_id": "explicit_implementation_go",
            "required_input": "User explicitly authorizes implementation of plan-ready DQ row mappings.",
            "status": "missing",
            "acceptance_condition": "Message clearly says go for implementation, not just planning or review.",
        },
        {
            "requirement_id": "owner_row_level_mapping",
            "required_input": "Owner-approved before/after values for all 64 plan-ready rows.",
            "status": "missing",
            "acceptance_condition": "Every row_id has old value, proposed_after_value, owner note, and rollback key.",
        },
        {
            "requirement_id": "rollback_manifest_recheck",
            "required_input": "Rollback manifest rechecked after owner mapping package is complete.",
            "status": "pending",
            "acceptance_condition": "Rollback table references exact row_id keys and old values for every edited row.",
        },
        {
            "requirement_id": "validation_boundary_recheck",
            "required_input": "Separate validation authorization after implementation freeze.",
            "status": "pending",
            "acceptance_condition": "Heldout/hard remains excluded from selection; validation is approved separately.",
        },
        {
            "requirement_id": "anti_drift_diff_check",
            "required_input": "Diff check proving implementation scope excludes GoalSearcher/ranking/threshold/feature whitelist/model training changes.",
            "status": "pending",
            "acceptance_condition": "Only explicitly authorized DQ mapping artifacts are changed.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "implement_dq_row_mappings",
            "reason": "Held/request loop is closed as paused; explicit go and owner mappings are still missing.",
            "allowed_after": "future complete request package review",
        },
        {
            "blocked_action": "continue_auto_advancing_dq_implementation",
            "reason": "No further implementation gate should advance without new inputs.",
            "allowed_after": "explicit go plus owner mapping package, or user directs return to strategy",
        },
        {
            "blocked_action": "run_validation_or_heldout",
            "reason": "No implementation exists to validate.",
            "allowed_after": "future implementation freeze and separate validation gate",
        },
        {
            "blocked_action": "train_or_reopen_s2",
            "reason": "DQ request closure does not create learning evidence.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS evidence",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "Online/search behavior is outside the held/request closure.",
            "allowed_after": "separate post-DQ strategy review, if ever authorized",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "closure_decision_csv": str(output_prefix.with_name(output_prefix.name + "_closure_decision.csv")),
        "resume_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_resume_requirements.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": request_review["stage"],
        "closure_decision": "pause_keep_held",
        "request_package_decision_from_10_40": metrics_10_40["request_package_decision"],
        "blocked_package_item_count": len(blocked_package_items),
        "pending_package_item_count": len(pending_package_items),
        "plan_ready_candidate_count": len(plan_ready),
        "not_ready_candidate_count": len(not_ready),
        "owner_after_values_missing": metrics_10_40["owner_after_values_missing"],
        "resume_requirement_count": len(resume_rows),
        "request_item_count": len(request_package),
        "return_to_broader_strategy_now": False,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.41 DQ implementation held/request closure",
        "read_only": True,
        "dq_implementation_held_request_closure_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Close the DQ implementation held/request loop as pause_keep_held. No explicit implementation go or complete owner row mapping package was supplied, "
            "so the five plan-ready candidates remain parked and no DQ implementation, validation, or learning re-entry is allowed."
        ),
        "anti_drift_conclusion": (
            "10.41 only closes the held/request loop. It does not edit data, taxonomy, rules, thresholds, GoalSearcher, feature whitelists, train or tune models, "
            "run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "pause awaiting explicit DQ implementation package or user direction",
            "goal": "Stop auto-advancing DQ implementation until explicit go plus owner row mappings are supplied, or the user directs a return to broader strategy review.",
            "default": "pause_keep_held",
        },
    }

    _write_csv(Path(artifacts["closure_decision_csv"]), closure_rows, ["closure_item", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["resume_requirements_csv"]), resume_rows, ["requirement_id", "required_input", "status", "acceptance_condition"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, closure_rows, resume_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
