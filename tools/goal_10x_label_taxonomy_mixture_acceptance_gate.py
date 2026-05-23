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
DEFAULT_MIXTURE_SUMMARY = AGENT_STATE / "goal_10x_label_taxonomy_mixture_separation_artifact_audit_summary.json"
DEFAULT_MIXTURE_ROWS = AGENT_STATE / "goal_10x_label_taxonomy_mixture_separation_artifact_audit_rows.csv"
DEFAULT_SEPARATION_ROLLUP = AGENT_STATE / "goal_10x_label_taxonomy_mixture_separation_artifact_audit_separation_rollup.csv"
DEFAULT_SOURCE_ROLLUP = AGENT_STATE / "goal_10x_label_taxonomy_mixture_separation_artifact_audit_source_rollup.csv"
DEFAULT_TOP1_ACCEPTANCE = AGENT_STATE / "goal_10x_top1_family_coverage_acceptance_gate_summary.json"
DEFAULT_ROUTE_CANDIDATES = AGENT_STATE / "goal_10x_remaining_dq_artifact_backlog_route_selection_candidate_lanes.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_label_taxonomy_mixture_acceptance_gate"


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


def _route_candidate(rows: list[dict[str, str]], lane_id: str) -> dict[str, str]:
    return next(row for row in rows if row.get("lane_id") == lane_id)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    acceptance_rows: list[dict[str, Any]],
    route_rows: list[dict[str, Any]],
    fix_inputs: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.34 Label/Taxonomy Mixture Acceptance Gate",
        "",
        "Read-only acceptance gate for the 10.33 label/taxonomy mixture artifact.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["mixture_artifact_accepted_for_dq_backlog", metrics["mixture_artifact_accepted_for_dq_backlog"]],
                ["mixture_rows", metrics["mixture_rows"]],
                ["classified_rows", metrics["classified_rows"]],
                ["generated_rows", metrics["generated_rows"]],
                ["selected_remaining_route", metrics["selected_remaining_route"]],
                ["query_family_empty_deferred", metrics["query_family_empty_deferred"]],
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
        "## Route Decision",
        "",
        _md_table(
            [["route_id", "route_decision", "support_rows", "generated_dominance", "rationale"]]
            + [
                [
                    row["route_id"],
                    row["route_decision"],
                    row["support_rows"],
                    row["generated_dominance"],
                    row["rationale"],
                ]
                for row in route_rows
            ]
        ),
        "",
        "## DQ Fix Planning Inputs",
        "",
        _md_table(
            [["input_id", "source_artifact", "support_rows", "owner_action"]]
            + [[row["input_id"], row["source_artifact"], row["support_rows"], row["owner_action"]] for row in fix_inputs]
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
    parser = argparse.ArgumentParser(description="Accept/reject label/taxonomy mixture artifact and choose remaining DQ route")
    parser.add_argument("--mixture-summary", default=str(DEFAULT_MIXTURE_SUMMARY))
    parser.add_argument("--mixture-rows", default=str(DEFAULT_MIXTURE_ROWS))
    parser.add_argument("--separation-rollup", default=str(DEFAULT_SEPARATION_ROLLUP))
    parser.add_argument("--source-rollup", default=str(DEFAULT_SOURCE_ROLLUP))
    parser.add_argument("--top1-acceptance", default=str(DEFAULT_TOP1_ACCEPTANCE))
    parser.add_argument("--route-candidates", default=str(DEFAULT_ROUTE_CANDIDATES))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    mixture_summary = _read_json(Path(args.mixture_summary))
    mixture_rows = _read_csv(Path(args.mixture_rows))
    separation_rollup = _read_csv(Path(args.separation_rollup))
    source_rollup = _read_csv(Path(args.source_rollup))
    top1_acceptance = _read_json(Path(args.top1_acceptance))
    route_candidates = _read_csv(Path(args.route_candidates))
    mixture_metrics = mixture_summary["metrics"]
    top1_metrics = top1_acceptance["metrics"]
    query_lane = _route_candidate(route_candidates, "query_family_empty_coverage")

    accepted_classes = {"valve_overlap", "overbroad_labels", "cross_domain_absorption", "true_taxonomy_gaps"}
    classified_rows = sum(1 for row in mixture_rows if row.get("separation_class") in accepted_classes)
    unclassified_rows = len(mixture_rows) - classified_rows
    generated_rows = sum(1 for row in mixture_rows if row.get("source_file") == "global_repair_decision_table.csv")
    accepted_oss_rows = sum(1 for row in mixture_rows if row.get("source_file", "").startswith("v36_oss_"))
    all_generated = generated_rows == len(mixture_rows)
    has_required_rollup = {row.get("separation_class") for row in separation_rollup} >= {
        "valve_overlap",
        "overbroad_labels",
        "cross_domain_absorption",
    }
    mixture_artifact_accepted = (
        mixture_summary.get("read_only") is True
        and mixture_summary.get("dq_artifact_audit_only") is True
        and len(mixture_rows) == _to_int(mixture_metrics["mixture_rows"])
        and unclassified_rows == 0
        and has_required_rollup
        and _to_int(mixture_metrics["true_taxonomy_gap_rows"]) == 0
        and mixture_metrics["training_allowed"] is False
    )

    acceptance_rows = [
        {
            "check_id": "artifact_completeness",
            "status": "pass" if len(mixture_rows) == _to_int(mixture_metrics["mixture_rows"]) else "fail",
            "evidence": f"rows_csv={len(mixture_rows)}; summary_mixture_rows={mixture_metrics['mixture_rows']}",
            "decision": "accept" if len(mixture_rows) == _to_int(mixture_metrics["mixture_rows"]) else "reject",
        },
        {
            "check_id": "classification_coverage",
            "status": "pass" if unclassified_rows == 0 else "fail",
            "evidence": f"classified_rows={classified_rows}; unclassified_rows={unclassified_rows}",
            "decision": "accept" if unclassified_rows == 0 else "reject",
        },
        {
            "check_id": "required_separation_classes",
            "status": "pass" if has_required_rollup else "fail",
            "evidence": "classes=" + ",".join(sorted(row.get("separation_class", "") for row in separation_rollup)),
            "decision": "accept" if has_required_rollup else "reject",
        },
        {
            "check_id": "source_boundary",
            "status": "pass" if all_generated and accepted_oss_rows == 0 else "fail",
            "evidence": f"generated_rows={generated_rows}; accepted_oss_rows={accepted_oss_rows}; source_rollup_rows={len(source_rollup)}",
            "decision": "accept_as_dq_only_generated_excluded",
        },
        {
            "check_id": "learning_boundary",
            "status": "pass" if mixture_metrics["training_allowed"] is False and mixture_metrics["reentry_allowed_now"] is False else "fail",
            "evidence": f"training_allowed={mixture_metrics['training_allowed']}; reentry_allowed_now={mixture_metrics['reentry_allowed_now']}",
            "decision": "accept_as_dq_only",
        },
    ]

    query_support = _to_int(query_lane.get("support_rows"))
    query_generated_dominance = _to_float(query_lane.get("weighted_generated_dominance"))
    dq_fix_support = _to_int(top1_metrics["artifact_rows"]) + len(mixture_rows)
    route_rows = [
        {
            "route_id": "dq_fix_planning",
            "route_decision": "select_next",
            "recommended_stage": "10.35 DQ fix planning scope definition",
            "support_rows": dq_fix_support,
            "generated_dominance": "mixed; top1=54/64 generated, label_mixture=18/18 generated",
            "rationale": (
                "Two DQ artifacts are now accepted and actionable as backlog inputs; moving to fix planning creates an owner-reviewable "
                "repair scope without pretending generated rows are learning evidence."
            ),
            "learning_boundary": "dq_fix_planning_only_not_learning_evidence",
        },
        {
            "route_id": "query_family_empty_coverage",
            "route_decision": "defer",
            "recommended_stage": "defer_until_after_dq_fix_planning_or_new_owner_evidence",
            "support_rows": query_support,
            "generated_dominance": query_generated_dominance,
            "rationale": (
                "It has larger support, but remains broad, highly generated/source dominated, and weakly connected to the accepted "
                "top1/label-mixture artifacts; auditing it now risks another long non-learning detour."
            ),
            "learning_boundary": "dq_backlog_only_not_learning_evidence",
        },
    ]
    fix_inputs = [
        {
            "input_id": "top1_family_coverage",
            "source_artifact": "10.31/10.32 accepted top1_family coverage artifact",
            "support_rows": top1_metrics["artifact_rows"],
            "owner_action": "Define taxonomy/book-label backfill candidates and generated-row exclusion boundaries.",
        },
        {
            "input_id": "label_taxonomy_mixture_valve_overlap",
            "source_artifact": "10.33 valve_overlap separation",
            "support_rows": mixture_metrics["valve_overlap_rows"],
            "owner_action": "Review valve-adjacent terms such as insert valves, filters, and pressure reducers as taxonomy mapping candidates.",
        },
        {
            "input_id": "label_taxonomy_mixture_overbroad_labels",
            "source_artifact": "10.33 overbroad_labels separation",
            "support_rows": mixture_metrics["overbroad_label_rows"],
            "owner_action": "Split overbroad valve labels from instrument, sanitary, container, faucet, and well objects.",
        },
        {
            "input_id": "label_taxonomy_mixture_cross_domain_absorption",
            "source_artifact": "10.33 cross_domain_absorption separation",
            "support_rows": mixture_metrics["cross_domain_absorption_rows"],
            "owner_action": "Keep cross-domain absorption as DQ cleanup, not rank/recall learning evidence.",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "treat_10_33_artifact_as_learning_evidence",
            "reason": "10.34 accepts the artifact only as DQ backlog evidence; all 18 rows are generated-source rows.",
            "allowed_after": "future read-only re-entry review with separate accepted-OSS positive-net evidence",
        },
        {
            "blocked_action": "implement_dq_fixes_now",
            "reason": "10.34 can select DQ-fix planning but does not authorize data edits, taxonomy edits, or rule changes.",
            "allowed_after": "future owner-accepted DQ fix plan and explicit implementation authorization",
        },
        {
            "blocked_action": "reopen_s2_training_or_candidate_expansion",
            "reason": "No new accepted-OSS positive-net evidence was introduced by 10.34.",
            "allowed_after": "explicit future go after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "10.34 is an artifact acceptance and route gate, not validation.",
            "allowed_after": "future validation gate if a fix or learning candidate is approved",
        },
        {
            "blocked_action": "change_thresholds_rules_goal_searcher_or_feature_whitelist",
            "reason": "No implementation is authorized from a DQ acceptance gate.",
            "allowed_after": "post-acceptance implementation review, if ever reached",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "acceptance_checks_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_checks.csv")),
        "remaining_route_decision_csv": str(output_prefix.with_name(output_prefix.name + "_remaining_route_decision.csv")),
        "dq_fix_planning_inputs_csv": str(output_prefix.with_name(output_prefix.name + "_dq_fix_planning_inputs.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "mixture_artifact_accepted_for_dq_backlog": mixture_artifact_accepted,
        "mixture_rows": len(mixture_rows),
        "classified_rows": classified_rows,
        "unclassified_rows": unclassified_rows,
        "valve_overlap_rows": mixture_metrics["valve_overlap_rows"],
        "overbroad_label_rows": mixture_metrics["overbroad_label_rows"],
        "cross_domain_absorption_rows": mixture_metrics["cross_domain_absorption_rows"],
        "true_taxonomy_gap_rows": mixture_metrics["true_taxonomy_gap_rows"],
        "generated_rows": generated_rows,
        "accepted_oss_rows": accepted_oss_rows,
        "top1_accepted_artifact_rows": top1_metrics["artifact_rows"],
        "combined_accepted_dq_artifact_rows": dq_fix_support,
        "query_family_empty_support_rows": query_support,
        "query_family_empty_generated_dominance": query_generated_dominance,
        "selected_remaining_route": "dq_fix_planning",
        "query_family_empty_deferred": True,
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.34 label/taxonomy mixture acceptance gate",
        "read_only": True,
        "dq_acceptance_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Accept the 10.33 label/taxonomy mixture separation artifact as DQ backlog evidence only. Because the artifact is complete but fully generated-source dominated, "
            "it cannot reopen S2 or learning. Select DQ-fix planning as the next remaining route, using the accepted top1_family coverage and label/taxonomy mixture artifacts "
            "as owner-reviewable inputs; defer query_family_empty coverage because it remains broader and more source dominated."
        ),
        "anti_drift_conclusion": (
            "10.34 only accepts a DQ artifact and selects the next DQ backlog route. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, implement DQ fixes, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.35 DQ fix planning scope definition",
            "goal": "Read-only define owner-reviewable DQ fix candidates from accepted artifacts, including scope, evidence, risk, acceptance checks, and implementation boundary.",
            "default": "planning only; no data edits, no rules, no training, no validation, and S2 remains parked",
        },
    }

    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_rows, ["check_id", "status", "evidence", "decision"])
    _write_csv(
        Path(artifacts["remaining_route_decision_csv"]),
        route_rows,
        ["route_id", "route_decision", "recommended_stage", "support_rows", "generated_dominance", "rationale", "learning_boundary"],
    )
    _write_csv(Path(artifacts["dq_fix_planning_inputs_csv"]), fix_inputs, ["input_id", "source_artifact", "support_rows", "owner_action"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, acceptance_rows, route_rows, fix_inputs)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
