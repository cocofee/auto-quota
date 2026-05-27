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
DEFAULT_PREV_SUMMARY = AGENT_STATE / "goal_10x_dq_implementation_held_checkpoint_summary.json"
DEFAULT_READINESS = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_candidate_readiness.csv"
DEFAULT_EXACT_MAPPING = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_exact_row_mapping.csv"
DEFAULT_GO_REQUIREMENTS = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate_explicit_go_requirements.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_implementation_explicit_go_request_package_review"


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
    review_rows: list[dict[str, Any]],
    blocked_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.40 DQ Implementation Explicit Go / Request Package Review",
        "",
        "Read-only review of the explicit implementation go signal and the owner row-level mapping package.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["explicit_go_present", metrics["explicit_go_present"]],
                ["owner_row_mapping_present", metrics["owner_row_mapping_present"]],
                ["plan_ready_candidate_count", metrics["plan_ready_candidate_count"]],
                ["plan_ready_distinct_row_count", metrics["plan_ready_distinct_row_count"]],
                ["owner_after_values_missing", metrics["owner_after_values_missing"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Request Package Review",
        "",
        _md_table(
            [["request_id", "status", "evidence", "decision"]]
            + [[row["request_id"], row["status"], row["evidence"], row["decision"]] for row in review_rows]
        ),
        "",
        "## Blocked Actions",
        "",
        _md_table(
            [["blocked_action", "reason", "allowed_after"]]
            + [[row["blocked_action"], row["reason"], row["allowed_after"]] for row in blocked_rows]
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
    parser = argparse.ArgumentParser(description="Read-only 10.40 DQ implementation explicit go/request package review")
    parser.add_argument("--prev-summary", default=str(DEFAULT_PREV_SUMMARY))
    parser.add_argument("--readiness", default=str(DEFAULT_READINESS))
    parser.add_argument("--exact-mapping", default=str(DEFAULT_EXACT_MAPPING))
    parser.add_argument("--go-requirements", default=str(DEFAULT_GO_REQUIREMENTS))
    parser.add_argument("--explicit-go", action="store_true", help="Set only when the user explicitly authorizes DQ implementation.")
    parser.add_argument("--go-evidence", default="", help="Short auditable description of the explicit go signal.")
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    prev_summary = _read_json(Path(args.prev_summary))
    readiness = _read_csv(Path(args.readiness))
    exact_mapping = _read_csv(Path(args.exact_mapping))
    go_requirements = _read_csv(Path(args.go_requirements))

    ready_candidates = [row for row in readiness if row.get("readiness_status") == "plan_definition_ready"]
    ready_mapping = [row for row in exact_mapping if row.get("readiness_status") == "plan_definition_ready"]
    ready_row_ids = sorted({row["row_id"] for row in ready_mapping})
    owner_values_missing = sum(1 for row in ready_mapping if row.get("proposed_after_value") == "TBD_owner_mapping_required")
    owner_mapping_present = owner_values_missing == 0 and bool(ready_mapping)
    implementation_allowed = bool(args.explicit_go) and owner_mapping_present

    explicit_go_evidence = args.go_evidence or "explicit_go_flag=true"
    review_rows = [
        {
            "request_id": "explicit_implementation_go",
            "status": "present" if args.explicit_go else "missing",
            "evidence": explicit_go_evidence if args.explicit_go else "no explicit DQ implementation go in this review",
            "decision": "satisfies_go_requirement" if args.explicit_go else "block_implementation",
        },
        {
            "request_id": "owner_row_level_mapping",
            "status": "present" if owner_mapping_present else "missing",
            "evidence": f"plan_ready_rows={len(ready_mapping)}; proposed_after_value_missing={owner_values_missing}",
            "decision": "satisfies_mapping_requirement" if owner_mapping_present else "block_implementation",
        },
        {
            "request_id": "rollback_manifest_recheck",
            "status": "pending" if implementation_allowed else "blocked_by_owner_mapping",
            "evidence": "rollback must be rechecked against final owner mapping before edits",
            "decision": "future_recheck_required",
        },
        {
            "request_id": "validation_boundary_recheck",
            "status": "pending" if implementation_allowed else "blocked_by_owner_mapping",
            "evidence": "validation requires separate authorization after implementation freeze",
            "decision": "keep_heldout_hard_excluded_from_selection",
        },
        {
            "request_id": "anti_drift_diff_check",
            "status": "pending" if implementation_allowed else "blocked_by_owner_mapping",
            "evidence": "future diff must exclude GoalSearcher, ranking, thresholds, feature whitelist, and model training",
            "decision": "future_diff_check_required",
        },
    ]

    blocked_rows = [
        {
            "blocked_action": "implement_dq_row_mappings",
            "reason": "Explicit go is present, but owner-approved row-level before/after mapping is still missing for plan-ready rows.",
            "allowed_after": "complete owner mapping for every plan-ready row plus rollback recheck",
        },
        {
            "blocked_action": "fill_owner_after_values_without_owner",
            "reason": "Proposed after-values must be owner-approved; the agent cannot infer or synthesize them.",
            "allowed_after": "owner supplies proposed_after_value, owner_note, and rollback key for each row_id",
        },
        {
            "blocked_action": "run_validation_or_heldout",
            "reason": "No implementation freeze exists; heldout/hard cannot be used for selection.",
            "allowed_after": "future implementation freeze and separate validation authorization",
        },
        {
            "blocked_action": "train_or_reopen_learning_lane",
            "reason": "A DQ request package is not accepted-OSS learning evidence.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS evidence",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "10.40 request review cannot authorize search, ranking, threshold, rule, or feature whitelist changes.",
            "allowed_after": "separate post-DQ strategy review, if ever authorized",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "request_package_review_csv": str(output_prefix.with_name(output_prefix.name + "_request_package_review.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": prev_summary["stage"],
        "explicit_go_present": bool(args.explicit_go),
        "owner_row_mapping_present": owner_mapping_present,
        "plan_ready_candidate_count": len(ready_candidates),
        "plan_ready_mapping_rows": len(ready_mapping),
        "plan_ready_distinct_row_count": len(ready_row_ids),
        "owner_after_values_missing": owner_values_missing,
        "requirements_checked": len(go_requirements),
        "implementation_allowed": implementation_allowed,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "reentry_allowed_now": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    decision = (
        "Accept the explicit implementation go signal as present, but keep DQ implementation held because the owner-approved "
        f"row-level before/after mapping package is still missing for {owner_values_missing} plan-ready rows. "
        "No DQ edits, validation, training, heldout/hard selection, GoalSearcher changes, ranking changes, threshold changes, "
        "rule changes, or feature whitelist changes are allowed from this stage."
    )
    report = {
        "stage": "Goal LTR v1 / 10.40 DQ implementation explicit go/request package review",
        "read_only": True,
        "dq_implementation_request_package_review_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": decision,
        "anti_drift_conclusion": (
            "10.40 records explicit go/request package status only. It does not edit data, taxonomy, rules, thresholds, "
            "GoalSearcher, feature whitelists, train or tune models, run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "blocked pending owner row-level mapping package",
            "goal": "Collect owner-approved before/after mappings for all 64 plan-ready rows, then rerun the authorization gate.",
            "default": "keep held until complete owner mapping, rollback recheck, anti-drift diff check, and separate validation authorization are present",
        },
    }

    _write_csv(Path(artifacts["request_package_review_csv"]), review_rows, ["request_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_rows, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, review_rows, blocked_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
