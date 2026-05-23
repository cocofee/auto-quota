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
DEFAULT_OWNER_GATE = AGENT_STATE / "goal_10x_dq_fix_owner_acceptance_gate_summary.json"
DEFAULT_SEEDS = AGENT_STATE / "goal_10x_dq_fix_owner_acceptance_gate_future_implementation_plan_seeds.csv"
DEFAULT_ACCEPTANCE = AGENT_STATE / "goal_10x_dq_fix_owner_acceptance_gate_candidate_acceptance.csv"
DEFAULT_TOP1_ROWS = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_dq_implementation_plan_definition_gate"


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


def _candidate_rows(candidate_id: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if candidate_id == "dqfix_top1_same_domain_taxonomy_empty_backfill":
        return [row for row in rows if row.get("accepted_family_disposition") == "same_domain_taxonomy_empty"]
    prefix = "dqfix_top1_"
    if candidate_id.startswith(prefix):
        domain = candidate_id[len(prefix) :]
        if domain == "weak_current":
            return [row for row in rows if row.get("domain") == "weak_current"]
        if domain in {"pipe", "valve", "other", "lamp"}:
            return [row for row in rows if row.get("domain") == domain]
    return []


def _readiness(seed: dict[str, str], mapped_rows: list[dict[str, str]]) -> tuple[str, str, str]:
    candidate_id = seed["candidate_id"]
    if candidate_id == "dqfix_top1_same_domain_taxonomy_empty_backfill":
        return (
            "not_specific_enough",
            "scope_level_overlap",
            "Umbrella same-domain candidate overlaps domain-level seeds; it needs row-level deduplication and owner mapping before an implementation plan.",
        )
    if not mapped_rows:
        return (
            "not_specific_enough",
            "no_exact_rows",
            "No exact row mapping found for this candidate.",
        )
    if all(row.get("source_file") == "global_repair_decision_table.csv" for row in mapped_rows):
        return (
            "not_specific_enough",
            "generated_only",
            "Candidate maps only to generated-source rows; hold until non-generated owner evidence exists.",
        )
    return (
        "plan_definition_ready",
        "exact_rows_available",
        "Candidate has exact row mapping from the accepted top1_family coverage artifact; future implementation still needs explicit go.",
    )


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    readiness_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    go_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.37 DQ Implementation Plan Definition Gate",
        "",
        "Read-only gate for future DQ implementation plan readiness. No implementation is performed here.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["seed_count", metrics["seed_count"]],
                ["plan_definition_ready_count", metrics["plan_definition_ready_count"]],
                ["not_specific_enough_count", metrics["not_specific_enough_count"]],
                ["distinct_exact_row_count", metrics["distinct_exact_row_count"]],
                ["implementation_allowed", metrics["implementation_allowed"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Candidate Readiness",
        "",
        _md_table(
            [["candidate_id", "readiness_status", "gate_status", "mapped_row_count", "distinct_row_count"]]
            + [
                [
                    row["candidate_id"],
                    row["readiness_status"],
                    row["gate_status"],
                    row["mapped_row_count"],
                    row["distinct_row_count"],
                ]
                for row in readiness_rows
            ]
        ),
        "",
        "## Validation Boundary",
        "",
        _md_table(
            [["boundary_id", "allowed", "rule"]]
            + [[row["boundary_id"], row["allowed"], row["rule"]] for row in validation_rows]
        ),
        "",
        "## Explicit Go Requirements",
        "",
        _md_table(
            [["requirement_id", "required_before", "pass_condition"]]
            + [[row["requirement_id"], row["required_before"], row["pass_condition"]] for row in go_rows]
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
    parser = argparse.ArgumentParser(description="Read-only DQ implementation plan definition gate")
    parser.add_argument("--owner-gate", default=str(DEFAULT_OWNER_GATE))
    parser.add_argument("--seeds", default=str(DEFAULT_SEEDS))
    parser.add_argument("--acceptance", default=str(DEFAULT_ACCEPTANCE))
    parser.add_argument("--top1-rows", default=str(DEFAULT_TOP1_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    owner_gate = _read_json(Path(args.owner_gate))
    seeds = _read_csv(Path(args.seeds))
    acceptance = _read_csv(Path(args.acceptance))
    top1_rows = _read_csv(Path(args.top1_rows))
    acceptance_by_id = {row["candidate_id"]: row for row in acceptance}

    readiness_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for seed in seeds:
        candidate_id = seed["candidate_id"]
        matched = _candidate_rows(candidate_id, top1_rows)
        readiness_status, gate_status, rationale = _readiness(seed, matched)
        distinct_row_ids = sorted({row["row_id"] for row in matched})
        readiness_rows.append(
            {
                "candidate_id": candidate_id,
                "fix_family": seed["fix_family"],
                "readiness_status": readiness_status,
                "gate_status": gate_status,
                "mapped_row_count": len(matched),
                "distinct_row_count": len(distinct_row_ids),
                "source_risk": seed["source_risk"],
                "readiness_rationale": rationale,
                "required_before_implementation": seed["required_before_implementation"],
                "allowed_now": "no",
                "implementation_boundary": "future_plan_definition_only_no_data_edits",
            }
        )
        for row in matched:
            mapping_rows.append(
                {
                    "candidate_id": candidate_id,
                    "readiness_status": readiness_status,
                    "row_id": row["row_id"],
                    "domain": row["domain"],
                    "accepted_family_disposition": row["accepted_family_disposition"],
                    "source_file": row["source_file"],
                    "source_family": row["source_family"],
                    "province": row["province"],
                    "query": row["query"],
                    "expected_ids": row["expected_ids"],
                    "top1_id": row["top1_id"],
                    "top1_name": row["top1_name"],
                    "current_top1_family": row["top1_family"],
                    "proposed_change_type": "owner_to_define_taxonomy_or_book_label_delta",
                    "proposed_after_value": "TBD_owner_mapping_required",
                    "rollback_key": f"{candidate_id}:{row['row_id']}",
                }
            )

    ready_candidates = [row for row in readiness_rows if row["readiness_status"] == "plan_definition_ready"]
    not_ready_candidates = [row for row in readiness_rows if row["readiness_status"] != "plan_definition_ready"]
    ready_row_ids = {row["row_id"] for row in mapping_rows if row["readiness_status"] == "plan_definition_ready"}
    all_row_ids = {row["row_id"] for row in mapping_rows}
    overlap_count = len(mapping_rows) - len(all_row_ids)

    rollback_rows = [
        {
            "rollback_id": "rollback_manifest_required",
            "scope": "all future DQ implementation candidates",
            "required_artifact": "before/after mapping with row_id, old value, new value, source_file, provenance_hash, and owner approval",
            "rollback_action": "restore all old values by row_id and rerun only read-only consistency checks",
        },
        {
            "rollback_id": "generated_source_guard",
            "scope": "generated-source rows",
            "required_artifact": "generated exclusion list carried into implementation plan",
            "rollback_action": "remove generated-derived changes unless owner supplies non-generated row-level evidence",
        },
        {
            "rollback_id": "no_goal_searcher_change",
            "scope": "online/search behavior",
            "required_artifact": "diff proves no GoalSearcher/ranking/rule/threshold/feature whitelist edits",
            "rollback_action": "revert any non-DQ file changes before validation",
        },
    ]
    validation_rows = [
        {
            "boundary_id": "selection_split",
            "allowed": "dev_or_oof_only_for_preflight",
            "rule": "Future implementation planning may define dev/OOF consistency checks only; heldout/hard cannot be used to choose fixes.",
        },
        {
            "boundary_id": "heldout_hard",
            "allowed": "not_allowed_for_selection",
            "rule": "Heldout/hard may not be run or used until a separate validation gate explicitly authorizes it after implementation freeze.",
        },
        {
            "boundary_id": "online",
            "allowed": "not_allowed",
            "rule": "No online, GoalSearcher, threshold, ranking, or feature whitelist changes from this gate.",
        },
        {
            "boundary_id": "learning",
            "allowed": "not_allowed",
            "rule": "DQ row mappings are not learning evidence and do not reopen S2.",
        },
    ]
    go_rows = [
        {
            "requirement_id": "explicit_user_go",
            "required_before": "any DQ implementation file edit",
            "pass_condition": "user explicitly says go for implementation, not just planning",
        },
        {
            "requirement_id": "owner_row_mapping",
            "required_before": "implementation plan execution",
            "pass_condition": "every ready candidate row has owner-approved before/after values",
        },
        {
            "requirement_id": "rollback_manifest",
            "required_before": "implementation plan execution",
            "pass_condition": "rollback table is complete and references exact row_id keys",
        },
        {
            "requirement_id": "validation_boundary_acceptance",
            "required_before": "any validation run",
            "pass_condition": "validation scope is approved separately and heldout/hard is not used for selection",
        },
        {
            "requirement_id": "anti_drift_diff_check",
            "required_before": "implementation plan execution",
            "pass_condition": "planned diff excludes GoalSearcher, ranking, thresholds, feature whitelist, and model training code",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "implement_dq_plan_now",
            "reason": "10.37 only defines plan readiness, exact row mapping requirements, rollback, validation boundary, and go requirements.",
            "allowed_after": "future explicit implementation go with owner-approved before/after row mapping",
        },
        {
            "blocked_action": "fill_proposed_after_values_without_owner",
            "reason": "10.37 maps rows but leaves proposed_after_value as owner-required.",
            "allowed_after": "owner-approved row-level taxonomy/book-label mapping",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No implemented and frozen DQ candidate exists; heldout/hard cannot be used for selection.",
            "allowed_after": "future validation gate after explicit implementation authorization and freeze",
        },
        {
            "blocked_action": "train_or_reopen_s2",
            "reason": "DQ implementation planning does not provide accepted-OSS positive learning evidence.",
            "allowed_after": "future learning re-entry review with separate accepted-OSS evidence",
        },
        {
            "blocked_action": "change_goal_searcher_thresholds_rules_or_feature_whitelist",
            "reason": "Implementation plan definition excludes online/search behavior changes.",
            "allowed_after": "separate post-DQ strategy review, if ever authorized",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_readiness_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_readiness.csv")),
        "exact_row_mapping_csv": str(output_prefix.with_name(output_prefix.name + "_exact_row_mapping.csv")),
        "rollback_plan_csv": str(output_prefix.with_name(output_prefix.name + "_rollback_plan.csv")),
        "validation_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_validation_boundary.csv")),
        "explicit_go_requirements_csv": str(output_prefix.with_name(output_prefix.name + "_explicit_go_requirements.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": owner_gate["stage"],
        "seed_count": len(seeds),
        "candidate_acceptance_count": len(acceptance_by_id),
        "plan_definition_ready_count": len(ready_candidates),
        "not_specific_enough_count": len(not_ready_candidates),
        "exact_row_mapping_rows": len(mapping_rows),
        "distinct_exact_row_count": len(all_row_ids),
        "distinct_ready_row_count": len(ready_row_ids),
        "overlap_mapping_count": overlap_count,
        "rollback_requirement_count": len(rollback_rows),
        "validation_boundary_count": len(validation_rows),
        "explicit_go_requirement_count": len(go_rows),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.37 DQ implementation plan definition gate",
        "read_only": True,
        "dq_implementation_plan_definition_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Five accepted top1-family domain seeds are specific enough for future implementation plan definition because they have exact row mappings. "
            "The same-domain taxonomy-empty umbrella seed is not specific enough yet because it overlaps the domain seeds and needs owner row-level deduplication. "
            "This gate defines exact row mapping, rollback, validation boundary, and explicit go requirements only; it does not implement any DQ fix."
        ),
        "anti_drift_conclusion": (
            "10.37 only defines future DQ implementation plan readiness. It does not edit data, taxonomy, rules, thresholds, GoalSearcher, feature whitelists, "
            "train or tune models, run heldout/hard validation or selection, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.38 DQ implementation authorization go/no-go",
            "goal": "Read-only collect explicit go/no-go for implementing the plan-ready DQ row mappings; default without explicit go is do_not_implement.",
            "default": "do_not_implement unless user explicitly authorizes DQ implementation; no training, no validation, and S2 remains parked",
        },
    }

    readiness_fields = [
        "candidate_id",
        "fix_family",
        "readiness_status",
        "gate_status",
        "mapped_row_count",
        "distinct_row_count",
        "source_risk",
        "readiness_rationale",
        "required_before_implementation",
        "allowed_now",
        "implementation_boundary",
    ]
    mapping_fields = [
        "candidate_id",
        "readiness_status",
        "row_id",
        "domain",
        "accepted_family_disposition",
        "source_file",
        "source_family",
        "province",
        "query",
        "expected_ids",
        "top1_id",
        "top1_name",
        "current_top1_family",
        "proposed_change_type",
        "proposed_after_value",
        "rollback_key",
    ]
    _write_csv(Path(artifacts["candidate_readiness_csv"]), readiness_rows, readiness_fields)
    _write_csv(Path(artifacts["exact_row_mapping_csv"]), mapping_rows, mapping_fields)
    _write_csv(Path(artifacts["rollback_plan_csv"]), rollback_rows, ["rollback_id", "scope", "required_artifact", "rollback_action"])
    _write_csv(Path(artifacts["validation_boundary_csv"]), validation_rows, ["boundary_id", "allowed", "rule"])
    _write_csv(Path(artifacts["explicit_go_requirements_csv"]), go_rows, ["requirement_id", "required_before", "pass_condition"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, readiness_rows, validation_rows, go_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
