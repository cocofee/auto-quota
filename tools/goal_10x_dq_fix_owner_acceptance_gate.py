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
DEFAULT_SCOPE_SUMMARY = AGENT_STATE / "goal_10x_dq_fix_planning_scope_definition_summary.json"
DEFAULT_MANIFEST = AGENT_STATE / "goal_10x_dq_fix_planning_scope_definition_candidate_manifest.csv"
DEFAULT_CHECKS = AGENT_STATE / "goal_10x_dq_fix_planning_scope_definition_owner_acceptance_checks.csv"
DEFAULT_BOUNDARY = AGENT_STATE / "goal_10x_dq_fix_planning_scope_definition_implementation_boundary.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_fix_owner_acceptance_gate"


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
        if value == "":
            return 0
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _acceptance(row: dict[str, str]) -> tuple[str, str, str, str]:
    candidate_id = row["candidate_id"]
    source_risk = row["source_risk"]
    source_artifact = row["source_artifact"]
    support = _to_int(row["support_rows"])
    accepted_oss = _to_int(row.get("accepted_oss_rows"))

    if source_risk == "high_generated_source_risk":
        return (
            "accepted_for_backlog_not_implementation",
            "hold_until_non_generated_owner_evidence",
            "High generated-source risk and no accepted OSS rows; keep as backlog evidence but do not move into implementation plan yet.",
            "new owner evidence with non-generated provenance, exact rows, and explicit generated exclusion",
        )
    if "same_domain_taxonomy_empty_backfill" in candidate_id:
        return (
            "accepted_for_future_implementation_plan",
            "scope_level_candidate",
            "Largest same-domain taxonomy-empty disposition; acceptable as a planning umbrella if owner later maps concrete rows.",
            "owner row-level mapping table before implementation",
        )
    if source_artifact.startswith("10.31") and support >= 4:
        return (
            "accepted_for_future_implementation_plan",
            "row_level_candidate",
            f"Top1 coverage candidate has support={support} and accepted_oss_rows={accepted_oss}; still requires owner row-level approval.",
            "owner approval of candidate rows and before/after taxonomy labels",
        )
    return (
        "accepted_for_backlog_not_implementation",
        "low_support_or_unclear_scope",
        "Candidate remains useful backlog evidence but is not strong enough for implementation planning without more owner detail.",
        "owner clarification and row-level examples",
    )


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    acceptance_rows: list[dict[str, Any]],
    implementation_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.36 DQ Fix Owner Acceptance Gate",
        "",
        "Read-only owner acceptance gate for DQ fix candidates. No fixes are implemented here.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", metrics["candidate_count"]],
                ["accepted_for_future_implementation_plan_count", metrics["accepted_for_future_implementation_plan_count"]],
                ["accepted_for_backlog_not_implementation_count", metrics["accepted_for_backlog_not_implementation_count"]],
                ["held_high_generated_source_risk_count", metrics["held_high_generated_source_risk_count"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Candidate Acceptance",
        "",
        _md_table(
            [["candidate_id", "acceptance_status", "gate_status", "support_rows", "source_risk"]]
            + [
                [
                    row["candidate_id"],
                    row["acceptance_status"],
                    row["gate_status"],
                    row["support_rows"],
                    row["source_risk"],
                ]
                for row in acceptance_rows
            ]
        ),
        "",
        "## Future Implementation Plan Seeds",
        "",
        _md_table(
            [["candidate_id", "plan_status", "required_before_implementation"]]
            + [[row["candidate_id"], row["plan_status"], row["required_before_implementation"]] for row in implementation_rows]
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
    parser = argparse.ArgumentParser(description="Read-only owner acceptance gate for DQ fix candidate manifest")
    parser.add_argument("--scope-summary", default=str(DEFAULT_SCOPE_SUMMARY))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--checks", default=str(DEFAULT_CHECKS))
    parser.add_argument("--boundary", default=str(DEFAULT_BOUNDARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    scope_summary = _read_json(Path(args.scope_summary))
    manifest = _read_csv(Path(args.manifest))
    checks = _read_csv(Path(args.checks))
    boundaries = _read_csv(Path(args.boundary))

    acceptance_rows: list[dict[str, Any]] = []
    implementation_rows: list[dict[str, Any]] = []
    for row in manifest:
        status, gate_status, rationale, required_before_impl = _acceptance(row)
        accepted = status == "accepted_for_future_implementation_plan"
        acceptance_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "fix_family": row["fix_family"],
                "source_artifact": row["source_artifact"],
                "support_rows": row["support_rows"],
                "generated_rows": row["generated_rows"],
                "accepted_oss_rows": row["accepted_oss_rows"],
                "source_risk": row["source_risk"],
                "acceptance_status": status,
                "gate_status": gate_status,
                "acceptance_rationale": rationale,
                "required_before_implementation": required_before_impl,
                "implementation_boundary": row["implementation_boundary"],
                "learning_boundary": row["learning_boundary"],
            }
        )
        if accepted:
            implementation_rows.append(
                {
                    "candidate_id": row["candidate_id"],
                    "fix_family": row["fix_family"],
                    "plan_status": "seed_future_implementation_plan",
                    "support_rows": row["support_rows"],
                    "source_risk": row["source_risk"],
                    "required_before_implementation": required_before_impl,
                    "allowed_now": "no",
                    "reason_implementation_not_allowed_now": "10.36 is owner acceptance gate only; future implementation plan still needs explicit authorization.",
                }
            )

    blocked_actions = [
        {
            "blocked_action": "implement_accepted_candidates_now",
            "reason": "10.36 can accept candidates for a future implementation plan but does not authorize data/taxonomy/rule/code edits.",
            "allowed_after": "future implementation plan definition and explicit user/owner go",
        },
        {
            "blocked_action": "use_accepted_dq_candidates_as_learning_evidence",
            "reason": "Accepted DQ candidates are not accepted-OSS positive-net learning evidence.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS positive-net evidence",
        },
        {
            "blocked_action": "train_or_expand_s2",
            "reason": "No S2 re-entry evidence is introduced by DQ owner acceptance.",
            "allowed_after": "explicit future go after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No implemented fix exists to validate and heldout/hard cannot be used for selection at this gate.",
            "allowed_after": "future validation gate after an implementation candidate is explicitly authorized",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "Online/search behavior changes are outside the owner acceptance gate.",
            "allowed_after": "post-implementation review, if ever reached",
        },
    ]

    accepted_count = sum(1 for row in acceptance_rows if row["acceptance_status"] == "accepted_for_future_implementation_plan")
    backlog_count = len(acceptance_rows) - accepted_count
    high_risk_hold_count = sum(1 for row in acceptance_rows if row["gate_status"] == "hold_until_non_generated_owner_evidence")
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_acceptance_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_acceptance.csv")),
        "future_implementation_plan_seeds_csv": str(output_prefix.with_name(output_prefix.name + "_future_implementation_plan_seeds.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": scope_summary["stage"],
        "candidate_count": len(manifest),
        "owner_acceptance_check_count": len(checks),
        "implementation_boundary_count": len(boundaries),
        "accepted_for_future_implementation_plan_count": accepted_count,
        "accepted_for_backlog_not_implementation_count": backlog_count,
        "future_implementation_plan_seed_count": len(implementation_rows),
        "held_high_generated_source_risk_count": high_risk_hold_count,
        "accepted_top1_candidate_count": sum(1 for row in implementation_rows if row["candidate_id"].startswith("dqfix_top1_")),
        "accepted_label_mixture_candidate_count": sum(1 for row in implementation_rows if row["candidate_id"].startswith("dqfix_labelmix_")),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.36 DQ fix owner acceptance gate",
        "read_only": True,
        "dq_owner_acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept six top1-family DQ fix candidates as seeds for a future implementation plan, while holding the three label/taxonomy mixture candidates as backlog-only "
            "because they are high generated-source risk. This gate still does not implement any fix, train, validate, or reopen S2."
        ),
        "anti_drift_conclusion": (
            "10.36 only reviews owner acceptance status for DQ fix candidates. It does not edit data, taxonomy, rules, thresholds, GoalSearcher, feature whitelists, "
            "train or tune models, run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.37 DQ implementation plan definition gate",
            "goal": "Read-only define whether the accepted 10.36 candidates are specific enough for a future implementation plan, including exact row mapping, rollback, validation boundary, and explicit go requirements.",
            "default": "plan definition only; no data edits, no rules, no training, no validation, and S2 remains parked",
        },
    }

    acceptance_fields = [
        "candidate_id",
        "fix_family",
        "source_artifact",
        "support_rows",
        "generated_rows",
        "accepted_oss_rows",
        "source_risk",
        "acceptance_status",
        "gate_status",
        "acceptance_rationale",
        "required_before_implementation",
        "implementation_boundary",
        "learning_boundary",
    ]
    seed_fields = [
        "candidate_id",
        "fix_family",
        "plan_status",
        "support_rows",
        "source_risk",
        "required_before_implementation",
        "allowed_now",
        "reason_implementation_not_allowed_now",
    ]
    _write_csv(Path(artifacts["candidate_acceptance_csv"]), acceptance_rows, acceptance_fields)
    _write_csv(Path(artifacts["future_implementation_plan_seeds_csv"]), implementation_rows, seed_fields)
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_rows, implementation_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
