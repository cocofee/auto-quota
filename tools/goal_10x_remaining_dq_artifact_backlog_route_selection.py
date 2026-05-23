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
DEFAULT_S2_CLOSURE = AGENT_STATE / "goal_10x_s2_lane_park_evidence_request_closure_summary.json"
DEFAULT_COVERAGE = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_coverage_summary.csv"
DEFAULT_LEARNABILITY = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_learnability_slices.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_remaining_dq_artifact_backlog_route_selection"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _rows_for_keys(rows: list[dict[str, str]], keys: set[str]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("bucket_key") in keys]


def _aggregate_lane(lane_id: str, label: str, rows: list[dict[str, str]], rationale: str) -> dict[str, Any]:
    support = sum(_to_int(row.get("count")) for row in rows)
    generated_rows = sum(
        _to_int(row.get("dominant_source_count"))
        for row in rows
        if row.get("dominant_source") == "global_repair_decision_table.csv"
    )
    source_counts = [_to_int(row.get("source_count")) for row in rows]
    max_source_count = max(source_counts) if source_counts else 0
    weighted_dominance = round(generated_rows / support, 6) if support else 0.0
    recommendation_text = " | ".join(row.get("recommendation_counts", "") for row in rows)
    examples = " | ".join(row.get("example_queries", "") for row in rows if row.get("example_queries"))
    if lane_id == "top1_family_coverage":
        actionability = 5
    elif lane_id == "query_family_empty_coverage":
        actionability = 3
    else:
        actionability = 4
    source_diversity_score = min(max_source_count, 5)
    source_penalty = round(weighted_dominance * 5, 3)
    support_score = min(support / 20.0, 5.0)
    score = round(support_score + actionability + source_diversity_score - source_penalty, 3)
    return {
        "lane_id": lane_id,
        "lane_label": label,
        "support_rows": support,
        "component_count": len(rows),
        "generated_dominant_rows": generated_rows,
        "weighted_generated_dominance": weighted_dominance,
        "max_source_count": max_source_count,
        "support_score": round(support_score, 3),
        "actionability_score": actionability,
        "source_diversity_score": source_diversity_score,
        "source_dominance_penalty": source_penalty,
        "route_score": score,
        "recommended_next_stage": (
            "10.31 top1_family coverage artifact audit"
            if lane_id == "top1_family_coverage"
            else "defer"
        ),
        "route_decision": "select" if lane_id == "top1_family_coverage" else "defer",
        "rationale": rationale,
        "recommendation_counts": recommendation_text,
        "example_queries": examples[:800],
    }


def _write_markdown(path: Path, report: dict[str, Any], route_rows: list[dict[str, Any]], selected: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.30 Remaining DQ artifact backlog route selection",
        "",
        "Read-only route selection after S2 was formally parked.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["s2_lane_parked", metrics["s2_lane_parked"]],
                ["candidate_lane_count", metrics["candidate_lane_count"]],
                ["selected_lane", metrics["selected_lane"]],
                ["selected_support_rows", metrics["selected_support_rows"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
                ["training_allowed", metrics["training_allowed"]],
            ]
        ),
        "",
        "## Candidate Lanes",
        "",
        _md_table(
            [["lane_id", "support_rows", "weighted_generated_dominance", "route_score", "route_decision"]]
            + [
                [
                    row["lane_id"],
                    row["support_rows"],
                    row["weighted_generated_dominance"],
                    row["route_score"],
                    row["route_decision"],
                ]
                for row in route_rows
            ]
        ),
        "",
        "## Selected Lane",
        "",
        _md_table(
            [
                ["field", "value"],
                ["lane_id", selected["lane_id"]],
                ["recommended_next_stage", selected["recommended_next_stage"]],
                ["rationale", selected["rationale"]],
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
    parser = argparse.ArgumentParser(description="Select next remaining DQ artifact backlog route without learning execution")
    parser.add_argument("--s2-closure", default=str(DEFAULT_S2_CLOSURE))
    parser.add_argument("--coverage-summary", default=str(DEFAULT_COVERAGE))
    parser.add_argument("--learnability-slices", default=str(DEFAULT_LEARNABILITY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s2_closure = _read_json(Path(args.s2_closure))
    coverage_rows = _read_csv(Path(args.coverage_summary))
    learnability_rows = _read_csv(Path(args.learnability_slices))

    query_rows = _rows_for_keys(coverage_rows, {"query_family_empty_blocks_coverage_decision"})
    top1_rows = _rows_for_keys(
        coverage_rows,
        {"probable_top1_family_coverage_gap", "high_confidence_top1_family_coverage_gap", "ambiguous_top1_family_empty"},
    )
    mixture_rows = _rows_for_keys(coverage_rows, {"valve_label_or_taxonomy_mixture"})

    route_rows = [
        _aggregate_lane(
            "query_family_empty_coverage",
            "query_family_empty coverage",
            query_rows,
            "Largest support, but most rows are generated-source dominated and many examples have unknown/cross-domain query semantics; useful after a narrower coverage lane establishes accepted taxonomy decisions.",
        ),
        _aggregate_lane(
            "top1_family_coverage",
            "top1_family coverage",
            top1_rows,
            "Best next DQ lane because it is structured around same-domain top1_family coverage gaps, includes pipe/valve cases, and has the clearest audit output contract without reopening S2.",
        ),
        _aggregate_lane(
            "label_taxonomy_mixture_separation",
            "label/taxonomy mixture separation",
            mixture_rows,
            "Important but narrower valve-focused lane; defer until top1_family coverage separates true empty-family coverage gaps from overbroad label mixtures.",
        ),
    ]
    route_rows = sorted(route_rows, key=lambda row: (row["route_decision"] != "select", -row["route_score"]))
    selected = next(row for row in route_rows if row["route_decision"] == "select")

    evidence_contract = [
        {
            "artifact": "top1_family_coverage_audit",
            "required_fields": "row_id, query_family, top1_family, coverage_issue, accepted_family_disposition, domain, source_file, source_family, provenance_hash, learning_disposition, acceptance_status",
            "acceptance_check": "Separate same-domain taxonomy-empty from book-label-empty and cross-domain absorption; do not count generated rows as learning evidence.",
        },
        {
            "artifact": "top1_family_domain_rollup",
            "required_fields": "domain, support_rows, source_count, generated_dominance, accepted_oss_rows, recommended_disposition",
            "acceptance_check": "Pipe/valve/lamp/weak-current rows must be dispositioned before any future learning re-entry.",
        },
        {
            "artifact": "blocked_learning_boundary",
            "required_fields": "blocked_action, reason, allowed_after",
            "acceptance_check": "Keep training, validation, GoalSearcher changes, feature whitelist edits, and S2 re-entry blocked.",
        },
    ]
    route_boundary = [
        {
            "boundary": "s2_lane",
            "status": "parked",
            "rule": "Do not reopen S2 without a new accepted-OSS positive-net evidence package.",
        },
        {
            "boundary": "heldout_hard",
            "status": "forbidden_for_selection",
            "rule": "Do not use heldout/hard to choose DQ route or validate S2.",
        },
        {
            "boundary": "dq_backlog_rows",
            "status": "not_learning_evidence",
            "rule": "DQ rows can be classified and accepted, but cannot become rank/recall learning evidence until re-entry gates pass.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "reopen_s2_or_execute_training",
            "reason": "10.30 only selects a DQ route after S2 closure; no accepted-OSS positive evidence exists.",
            "allowed_after": "future accepted-OSS evidence package and explicit re-entry review",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "DQ route selection is read-only and must not use heldout/hard for selection.",
            "allowed_after": "future validation gate after learning re-entry, if ever reached",
        },
        {
            "blocked_action": "change_thresholds_rules_goal_searcher_or_feature_whitelist",
            "reason": "DQ route selection is not implementation.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "treat_dq_backlog_as_learning_evidence",
            "reason": "Selected route produces DQ acceptance artifacts only.",
            "allowed_after": "future read-only re-entry review accepts artifacts and separate learning evidence",
        },
        {
            "blocked_action": "claim_top1_gain",
            "reason": "No algorithm changed and no validation was run.",
            "allowed_after": "future approved offline/validation path with proper split policy",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_lanes_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_lanes.csv")),
        "selected_route_csv": str(output_prefix.with_name(output_prefix.name + "_selected_route.csv")),
        "evidence_contract_csv": str(output_prefix.with_name(output_prefix.name + "_evidence_contract.csv")),
        "route_boundary_csv": str(output_prefix.with_name(output_prefix.name + "_route_boundary.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "s2_lane_parked": bool(s2_closure["metrics"]["s2_lane_parked"]),
        "candidate_lane_count": len(route_rows),
        "selected_lane": selected["lane_id"],
        "selected_support_rows": selected["support_rows"],
        "selected_weighted_generated_dominance": selected["weighted_generated_dominance"],
        "selected_route_score": selected["route_score"],
        "learnability_slice_count": len(learnability_rows),
        "eligible_learning_slice_count": sum(1 for row in learnability_rows if row.get("eligible_for_learning_after_9_30") == "yes"),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.30 remaining DQ artifact backlog route selection",
        "read_only": True,
        "dq_route_selection_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Select top1_family coverage as the next remaining DQ artifact route. Query_family_empty has larger support but is broader and more generated/source dominated; "
            "label/taxonomy mixture is narrower and should follow once top1_family coverage separates same-domain empty-family gaps from overbroad labels. S2 remains parked."
        ),
        "anti_drift_conclusion": (
            "10.30 only selects the next DQ backlog route. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.31 top1_family coverage artifact audit",
            "goal": "Read-only build/inspect the top1_family coverage artifact for pipe/valve/lamp/weak-current/other domains and disposition taxonomy-empty vs label-mixture rows.",
            "default": "continue DQ backlog only; S2 remains parked unless new accepted-OSS evidence package arrives",
        },
    }

    _write_csv(Path(artifacts["candidate_lanes_csv"]), route_rows, [
        "lane_id", "lane_label", "support_rows", "component_count", "generated_dominant_rows",
        "weighted_generated_dominance", "max_source_count", "support_score", "actionability_score",
        "source_diversity_score", "source_dominance_penalty", "route_score",
        "recommended_next_stage", "route_decision", "rationale", "recommendation_counts", "example_queries",
    ])
    _write_csv(Path(artifacts["selected_route_csv"]), [selected], [
        "lane_id", "lane_label", "support_rows", "weighted_generated_dominance", "route_score",
        "recommended_next_stage", "route_decision", "rationale",
    ])
    _write_csv(Path(artifacts["evidence_contract_csv"]), evidence_contract, ["artifact", "required_fields", "acceptance_check"])
    _write_csv(Path(artifacts["route_boundary_csv"]), route_boundary, ["boundary", "status", "rule"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, route_rows, selected)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
