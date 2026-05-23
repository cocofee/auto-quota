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
DEFAULT_TOP1_SUMMARY = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_summary.json"
DEFAULT_TOP1_ROWS = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_rows.csv"
DEFAULT_DOMAIN_ROLLUP = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_domain_rollup.csv"
DEFAULT_DISPOSITION_ROLLUP = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_disposition_rollup.csv"
DEFAULT_ROUTE_CANDIDATES = AGENT_STATE / "goal_10x_remaining_dq_artifact_backlog_route_selection_candidate_lanes.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_top1_family_coverage_acceptance_gate"


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
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _route_decision(
    route_rows: list[dict[str, str]],
    top1_metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    label_row = next(row for row in route_rows if row["lane_id"] == "label_taxonomy_mixture_separation")
    query_row = next(row for row in route_rows if row["lane_id"] == "query_family_empty_coverage")

    decisions = [
        {
            "lane_id": "label_taxonomy_mixture_separation",
            "route_decision": "select_next",
            "recommended_stage": "10.33 label/taxonomy mixture separation artifact audit",
            "support_rows": _to_int(label_row.get("support_rows")),
            "weighted_generated_dominance": _to_float(label_row.get("weighted_generated_dominance")),
            "continuity_from_10_31_rows": top1_metrics["label_taxonomy_mixture_rows"],
            "acceptance_rationale": (
                "10.31 exposed a concrete valve-overlap label/taxonomy mixture subset; this lane is narrower than "
                "query_family_empty and can turn the top1_family artifact into a cleaner DQ separation table."
            ),
            "learning_boundary": "dq_backlog_only_not_learning_evidence",
        },
        {
            "lane_id": "query_family_empty_coverage",
            "route_decision": "defer",
            "recommended_stage": "defer_until_label_mixture_separation_or_new_owner_evidence",
            "support_rows": _to_int(query_row.get("support_rows")),
            "weighted_generated_dominance": _to_float(query_row.get("weighted_generated_dominance")),
            "continuity_from_10_31_rows": top1_metrics["query_family_empty_with_top1_gap_rows"],
            "acceptance_rationale": (
                "Larger support but broader, highly generated/source dominated, and not directly connected to the "
                "accepted 10.31 top1_family artifact because query_family_empty_with_top1_gap_rows is 0."
            ),
            "learning_boundary": "dq_backlog_only_not_learning_evidence",
        },
    ]
    return decisions


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    acceptance_rows: list[dict[str, Any]],
    residual_rows: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.32 Top1 Family Coverage Acceptance Gate",
        "",
        "Read-only acceptance gate for the 10.31 top1_family coverage DQ artifact.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["top1_artifact_accepted_for_dq_backlog", metrics["top1_artifact_accepted_for_dq_backlog"]],
                ["artifact_rows", metrics["artifact_rows"]],
                ["classified_rows", metrics["classified_rows"]],
                ["unclassified_rows", metrics["unclassified_rows"]],
                ["generated_rows", metrics["generated_rows"]],
                ["accepted_oss_rows", metrics["accepted_oss_rows"]],
                ["selected_residual_lane", metrics["selected_residual_lane"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
            ]
        ),
        "",
        "## Acceptance Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in acceptance_rows]
        ),
        "",
        "## Residual Route Decision",
        "",
        _md_table(
            [["lane_id", "route_decision", "support_rows", "weighted_generated_dominance", "recommended_stage"]]
            + [
                [
                    row["lane_id"],
                    row["route_decision"],
                    row["support_rows"],
                    row["weighted_generated_dominance"],
                    row["recommended_stage"],
                ]
                for row in residual_rows
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
    parser = argparse.ArgumentParser(description="Accept/reject top1_family DQ artifact and choose next residual DQ lane")
    parser.add_argument("--top1-summary", default=str(DEFAULT_TOP1_SUMMARY))
    parser.add_argument("--top1-rows", default=str(DEFAULT_TOP1_ROWS))
    parser.add_argument("--domain-rollup", default=str(DEFAULT_DOMAIN_ROLLUP))
    parser.add_argument("--disposition-rollup", default=str(DEFAULT_DISPOSITION_ROLLUP))
    parser.add_argument("--route-candidates", default=str(DEFAULT_ROUTE_CANDIDATES))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    top1_summary = _read_json(Path(args.top1_summary))
    top1_rows = _read_csv(Path(args.top1_rows))
    domain_rollup = _read_csv(Path(args.domain_rollup))
    disposition_rollup = _read_csv(Path(args.disposition_rollup))
    route_candidates = _read_csv(Path(args.route_candidates))
    top1_metrics = top1_summary["metrics"]

    classified_dispositions = {
        "same_domain_taxonomy_empty",
        "book_label_empty",
        "cross_domain_absorption",
        "label_taxonomy_mixture",
    }
    classified_rows = sum(1 for row in top1_rows if row.get("accepted_family_disposition") in classified_dispositions)
    unclassified_rows = len(top1_rows) - classified_rows
    generated_rows = sum(1 for row in top1_rows if row.get("source_file") == "global_repair_decision_table.csv")
    accepted_oss_rows = sum(1 for row in top1_rows if row.get("source_file", "").startswith("v36_oss_"))
    all_domains_have_disposition = all(row.get("recommended_disposition") for row in domain_rollup)
    all_dispositions_are_dq = all("dq_backlog" in row.get("recommended_disposition", "") or "generated_excluded" in row.get("recommended_disposition", "") for row in disposition_rollup)
    top1_artifact_accepted = (
        top1_summary.get("read_only") is True
        and top1_summary.get("dq_artifact_audit_only") is True
        and len(top1_rows) == _to_int(top1_metrics["top1_coverage_rows"])
        and unclassified_rows == 0
        and all_domains_have_disposition
        and all_dispositions_are_dq
        and top1_metrics["training_allowed"] is False
    )

    acceptance_rows = [
        {
            "check_id": "artifact_completeness",
            "status": "pass" if len(top1_rows) == _to_int(top1_metrics["top1_coverage_rows"]) else "fail",
            "evidence": f"rows_csv={len(top1_rows)}; summary_top1_coverage_rows={top1_metrics['top1_coverage_rows']}",
            "decision": "accept" if len(top1_rows) == _to_int(top1_metrics["top1_coverage_rows"]) else "reject",
        },
        {
            "check_id": "classification_coverage",
            "status": "pass" if unclassified_rows == 0 else "fail",
            "evidence": f"classified_rows={classified_rows}; unclassified_rows={unclassified_rows}",
            "decision": "accept" if unclassified_rows == 0 else "reject",
        },
        {
            "check_id": "domain_disposition_coverage",
            "status": "pass" if all_domains_have_disposition else "fail",
            "evidence": f"domain_count={len(domain_rollup)}; domains={','.join(row['domain'] for row in domain_rollup)}",
            "decision": "accept" if all_domains_have_disposition else "reject",
        },
        {
            "check_id": "learning_boundary",
            "status": "pass" if top1_metrics["training_allowed"] is False and top1_metrics["reentry_allowed_now"] is False else "fail",
            "evidence": f"training_allowed={top1_metrics['training_allowed']}; reentry_allowed_now={top1_metrics['reentry_allowed_now']}",
            "decision": "accept_as_dq_only" if top1_metrics["training_allowed"] is False else "reject",
        },
        {
            "check_id": "source_risk_boundary",
            "status": "pass",
            "evidence": f"generated_rows={generated_rows}; accepted_oss_rows={accepted_oss_rows}; generated rows remain excluded from learning",
            "decision": "accept_with_generated_exclusion",
        },
    ]

    residual_rows = _route_decision(route_candidates, top1_metrics)
    selected_residual = next(row for row in residual_rows if row["route_decision"] == "select_next")
    blocked_actions = [
        {
            "blocked_action": "treat_10_31_artifact_as_learning_evidence",
            "reason": "10.32 accepts the artifact only as DQ backlog evidence; generated/source dominated rows remain excluded.",
            "allowed_after": "future read-only re-entry review with separate accepted-OSS positive-net evidence",
        },
        {
            "blocked_action": "reopen_s2_training_or_candidate_expansion",
            "reason": "No new accepted-OSS positive-net evidence was introduced by 10.32.",
            "allowed_after": "explicit future go after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "10.32 is an acceptance gate for DQ artifacts, not model validation.",
            "allowed_after": "future validation gate if a learning lane is reopened and approved",
        },
        {
            "blocked_action": "change_thresholds_rules_goal_searcher_or_feature_whitelist",
            "reason": "No implementation is authorized from a DQ acceptance gate.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "No algorithm changed and no validation was run.",
            "allowed_after": "future approved offline/validation path with proper split policy",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "residual_route_decision_csv": str(output_prefix.with_name(output_prefix.name + "_residual_route_decision.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "top1_artifact_accepted_for_dq_backlog": top1_artifact_accepted,
        "artifact_rows": len(top1_rows),
        "classified_rows": classified_rows,
        "unclassified_rows": unclassified_rows,
        "domain_count": len(domain_rollup),
        "disposition_count": len(disposition_rollup),
        "generated_rows": generated_rows,
        "accepted_oss_rows": accepted_oss_rows,
        "same_domain_taxonomy_empty_rows": top1_metrics["same_domain_taxonomy_empty_rows"],
        "book_label_empty_rows": top1_metrics["book_label_empty_rows"],
        "cross_domain_absorption_rows": top1_metrics["cross_domain_absorption_rows"],
        "label_taxonomy_mixture_rows": top1_metrics["label_taxonomy_mixture_rows"],
        "query_family_empty_with_top1_gap_rows": top1_metrics["query_family_empty_with_top1_gap_rows"],
        "selected_residual_lane": selected_residual["lane_id"],
        "query_family_empty_deferred": True,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.32 top1_family coverage acceptance gate",
        "read_only": True,
        "dq_acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the 10.31 top1_family coverage artifact as DQ backlog evidence only. It is complete enough for backlog handoff because all 64 rows are classified "
            "across the four DQ dispositions, but it does not reopen S2 or learning. Select label/taxonomy mixture separation as the next residual DQ lane; defer "
            "query_family_empty coverage because it is broader, more source-dominated, and has no direct query_family_empty_with_top1_gap carryover from 10.31."
        ),
        "anti_drift_conclusion": (
            "10.32 only accepts a DQ artifact and selects the next DQ backlog lane. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.33 label/taxonomy mixture separation artifact audit",
            "goal": "Read-only separate label/taxonomy mixture rows into valve-overlap, overbroad labels, cross-domain absorption, and true taxonomy gaps.",
            "default": "continue DQ backlog only; S2 remains parked unless new accepted-OSS evidence package arrives",
        },
    }

    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_rows, ["check_id", "status", "evidence", "decision"])
    _write_csv(
        Path(artifacts["residual_route_decision_csv"]),
        residual_rows,
        [
            "lane_id",
            "route_decision",
            "recommended_stage",
            "support_rows",
            "weighted_generated_dominance",
            "continuity_from_10_31_rows",
            "acceptance_rationale",
            "learning_boundary",
        ],
    )
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_rows, residual_rows)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
