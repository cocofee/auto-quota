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
DEFAULT_PLAN_SUMMARY = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_summary.json"
DEFAULT_READINESS = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_candidate_readiness.csv"
DEFAULT_EXACT_MAPPING = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_exact_row_mapping.csv"
DEFAULT_GO_REQUIREMENTS = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_explicit_go_requirements.csv"
DEFAULT_VALIDATION_BOUNDARY = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_validation_boundary.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_implementation_authorization_go_no_go"


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
    auth_rows: list[dict[str, Any]],
    missing_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.38 DQ Implementation Authorization Go/No-Go",
        "",
        "Read-only authorization gate for plan-ready DQ row mappings. No implementation is performed here.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["authorization_decision", metrics["authorization_decision"]],
                ["explicit_go_present", metrics["explicit_go_present"]],
                ["plan_ready_candidate_count", metrics["plan_ready_candidate_count"]],
                ["plan_ready_distinct_row_count", metrics["plan_ready_distinct_row_count"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Authorization Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in auth_rows]
        ),
        "",
        "## Missing Before Go",
        "",
        _md_table(
            [["requirement_id", "required_before", "status", "missing_detail"]]
            + [
                [row["requirement_id"], row["required_before"], row["status"], row["missing_detail"]]
                for row in missing_rows
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
    parser = argparse.ArgumentParser(description="Read-only DQ implementation authorization go/no-go gate")
    parser.add_argument("--plan-summary", default=str(DEFAULT_PLAN_SUMMARY))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--exact-mapping", default=str(DEFAULT_EXACT_MAPPING))
    parser.add_argument("--go-requirements", default=str(DEFAULT_GO_REQUIREMENTS))
    parser.add_argument("--validation-boundary", default=str(DEFAULT_VALIDATION_BOUNDARY))
    parser.add_argument("--explicit-go", action="store_true", help="Only set when the user explicitly authorizes DQ implementation.")
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    plan_summary = _read_json(Path(args.plan_summary))
    readiness = _read_csv(Path(args.readiness))
    exact_mapping = _read_csv(Path(args.exact_mapping))
    go_requirements = _read_csv(Path(args.go_requirements))
    validation_boundary = _read_csv(Path(args.validation_boundary))

    ready_candidates = [row for row in readiness if row.get("readiness_status") == "plan_definition_ready"]
    not_ready_candidates = [row for row in readiness if row.get("readiness_status") != "plan_definition_ready"]
    ready_mapping = [row for row in exact_mapping if row.get("readiness_status") == "plan_definition_ready"]
    ready_row_ids = sorted({row["row_id"] for row in ready_mapping})
    owner_values_missing = sum(1 for row in ready_mapping if row.get("proposed_after_value") == "TBD_owner_mapping_required")
    heldout_blocked = any(row.get("boundary_id") == "heldout_hard" and row.get("allowed") == "not_allowed_for_selection" for row in validation_boundary)

    authorization_decision = "implement_authorized" if args.explicit_go and owner_values_missing == 0 else "do_not_implement"
    implementation_allowed = authorization_decision == "implement_authorized"
    auth_rows = [
        {
            "check_id": "explicit_user_go",
            "status": "pass" if args.explicit_go else "fail",
            "evidence": "explicit_go_flag=true" if args.explicit_go else "no explicit implementation go was provided",
            "decision": "allow_next_check" if args.explicit_go else "block_implementation",
        },
        {
            "check_id": "plan_ready_candidates",
            "status": "pass" if ready_candidates else "fail",
            "evidence": f"plan_ready_candidate_count={len(ready_candidates)}; not_ready_candidate_count={len(not_ready_candidates)}",
            "decision": "allow_next_check" if ready_candidates else "block_implementation",
        },
        {
            "check_id": "owner_after_values",
            "status": "pass" if owner_values_missing == 0 else "fail",
            "evidence": f"ready_mapping_rows={len(ready_mapping)}; proposed_after_value_missing={owner_values_missing}",
            "decision": "allow_next_check" if owner_values_missing == 0 else "block_implementation",
        },
        {
            "check_id": "validation_boundary",
            "status": "pass" if heldout_blocked else "fail",
            "evidence": "heldout_hard remains not_allowed_for_selection" if heldout_blocked else "heldout boundary missing",
            "decision": "keep_read_only_boundary",
        },
        {
            "check_id": "anti_drift_boundary",
            "status": "pass",
            "evidence": "GoalSearcher/ranking/threshold/feature whitelist/model training changes remain blocked",
            "decision": "keep_blocked",
        },
    ]
    missing_rows = []
    for row in go_requirements:
        req_id = row["requirement_id"]
        if req_id == "explicit_user_go":
            status = "satisfied" if args.explicit_go else "missing"
            detail = "user did not explicitly authorize implementation"
        elif req_id == "owner_row_mapping":
            status = "satisfied" if owner_values_missing == 0 else "missing"
            detail = f"{owner_values_missing} plan-ready rows still have TBD_owner_mapping_required"
        else:
            status = "pending"
            detail = "must be rechecked in a future implementation authorization package"
        missing_rows.append(
            {
                "requirement_id": req_id,
                "required_before": row["required_before"],
                "status": status,
                "missing_detail": detail,
                "pass_condition": row["pass_condition"],
            }
        )

    blocked_actions = [
        {
            "blocked_action": "implement_dq_row_mappings",
            "reason": "No explicit implementation go was provided and owner after-values remain TBD.",
            "allowed_after": "future explicit go plus owner-approved before/after row mapping",
        },
        {
            "blocked_action": "fill_owner_after_values",
            "reason": "10.38 is an authorization gate, not owner taxonomy mapping.",
            "allowed_after": "owner supplies row-level proposed_after_value mapping",
        },
        {
            "blocked_action": "run_validation",
            "reason": "Implementation is not authorized, so validation is premature.",
            "allowed_after": "future implementation freeze and separate validation gate",
        },
        {
            "blocked_action": "train_or_reopen_s2",
            "reason": "DQ implementation authorization is not learning evidence.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS evidence",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "DQ row mapping implementation, even if later authorized, must not change online/search behavior from this gate.",
            "allowed_after": "separate post-DQ strategy review, if ever authorized",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "authorization_checks_csv": str(output_prefix.with_name(output_prefix.name + "_authorization_checks.csv")),
        "missing_before_go_csv": str(output_prefix.with_name(output_prefix.name + "_missing_before_go.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": plan_summary["stage"],
        "authorization_decision": authorization_decision,
        "explicit_go_present": bool(args.explicit_go),
        "plan_ready_candidate_count": len(ready_candidates),
        "not_ready_candidate_count": len(not_ready_candidates),
        "plan_ready_mapping_rows": len(ready_mapping),
        "plan_ready_distinct_row_count": len(ready_row_ids),
        "owner_after_values_missing": owner_values_missing,
        "missing_requirement_count": sum(1 for row in missing_rows if row["status"] == "missing"),
        "pending_requirement_count": sum(1 for row in missing_rows if row["status"] == "pending"),
        "heldout_selection_allowed": False,
        "implementation_allowed": implementation_allowed,
        "training_allowed": False,
        "reentry_allowed_now": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.38 DQ implementation authorization go/no-go",
        "read_only": True,
        "dq_implementation_authorization_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "No explicit DQ implementation go was provided, and plan-ready rows still require owner-approved proposed_after_value mappings. "
            "Therefore the authorization decision is do_not_implement. The five plan-ready candidates remain parked for a future explicit go package."
        ),
        "anti_drift_conclusion": (
            "10.38 only records DQ implementation go/no-go authorization. It does not edit data, taxonomy, rules, thresholds, GoalSearcher, feature whitelists, "
            "train or tune models, run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.39 DQ implementation held checkpoint",
            "goal": "Read-only decide whether to keep DQ implementation held, request explicit go plus owner row mappings, or return to broader strategy review.",
            "default": "keep held unless explicit implementation go and owner row-level mappings are supplied",
        },
    }

    _write_csv(Path(artifacts["authorization_checks_csv"]), auth_rows, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["missing_before_go_csv"]), missing_rows, ["requirement_id", "required_before", "status", "missing_detail", "pass_condition"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, auth_rows, missing_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
