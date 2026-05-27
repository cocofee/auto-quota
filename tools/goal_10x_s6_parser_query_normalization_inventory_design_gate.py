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
DEFAULT_1060_SUMMARY = AGENT_STATE / "goal_10x_s7_diagnostic_implications_next_lane_selection_summary.json"
DEFAULT_1060_SELECTED = AGENT_STATE / "goal_10x_s7_diagnostic_implications_next_lane_selection_selected_next_lane.csv"
DEFAULT_QFE_SUMMARY = AGENT_STATE / "goal_query_family_empty_decomposition_9x_audit_summary.json"
DEFAULT_QFE_ROWS = AGENT_STATE / "goal_query_family_empty_decomposition_9x_audit_rows.csv"
DEFAULT_QFE_SUBBUCKETS = AGENT_STATE / "goal_query_family_empty_decomposition_9x_audit_subbuckets.csv"
DEFAULT_TOP1_SUMMARY = AGENT_STATE / "goal_10x_top1_family_coverage_acceptance_gate_summary.json"
DEFAULT_TOP1_ROWS = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit_rows.csv"
DEFAULT_MIXTURE_SUMMARY = AGENT_STATE / "goal_10x_label_taxonomy_mixture_acceptance_gate_summary.json"
DEFAULT_S7_RANK = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_rank_position_distribution.csv"
DEFAULT_S7_POOL = AGENT_STATE / "goal_10x_s7_diagnostic_artifact_definition_candidate_pool_boundary.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s6_parser_query_normalization_inventory_design_gate"


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


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _sum_count(rows: list[dict[str, str]], field: str = "count", **filters: str) -> int:
    total = 0
    for row in rows:
        if all(row.get(key) == value for key, value in filters.items()):
            total += _int(row.get(field))
    return total


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
    gate_checks: list[dict[str, Any]],
    inventory_axes: list[dict[str, Any]],
    artifact_plan: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.61 S6 Parser/Query Normalization Inventory Design Gate",
        "",
        "Read-only gate for deciding whether S6 is concrete enough for a future inventory artifact.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_lane", metrics["selected_lane"]],
                ["query_family_empty_rows", metrics["query_family_empty_rows"]],
                ["qfe_top1_family_empty_rows", metrics["qfe_top1_family_empty_rows"]],
                ["qfe_source_dominated_rows", metrics["qfe_source_dominated_rows"]],
                ["top1_artifact_rows", metrics["top1_artifact_rows"]],
                ["gate_pass_count", metrics["gate_pass_count"]],
                ["s6_design_gate_decision", metrics["s6_design_gate_decision"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["check_id", "status", "evidence", "decision"]]
            + [[row["check_id"], row["status"], row["evidence"], row["decision"]] for row in gate_checks]
        ),
        "",
        "## Inventory Axes",
        "",
        _md_table(
            [["axis_id", "purpose", "required_inputs", "forbidden_use"]]
            + [[row["axis_id"], row["purpose"], row["required_inputs"], row["forbidden_use"]] for row in inventory_axes]
        ),
        "",
        "## Artifact Plan",
        "",
        _md_table(
            [["artifact", "contents", "acceptance_check"]]
            + [[row["artifact"], row["contents"], row["acceptance_check"]] for row in artifact_plan]
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
    parser = argparse.ArgumentParser(description="S6 parser/query normalization inventory design gate")
    parser.add_argument("--summary-1060", default=str(DEFAULT_1060_SUMMARY))
    parser.add_argument("--selected-1060", default=str(DEFAULT_1060_SELECTED))
    parser.add_argument("--qfe-summary", default=str(DEFAULT_QFE_SUMMARY))
    parser.add_argument("--qfe-rows", default=str(DEFAULT_QFE_ROWS))
    parser.add_argument("--qfe-subbuckets", default=str(DEFAULT_QFE_SUBBUCKETS))
    parser.add_argument("--top1-summary", default=str(DEFAULT_TOP1_SUMMARY))
    parser.add_argument("--top1-rows", default=str(DEFAULT_TOP1_ROWS))
    parser.add_argument("--mixture-summary", default=str(DEFAULT_MIXTURE_SUMMARY))
    parser.add_argument("--s7-rank", default=str(DEFAULT_S7_RANK))
    parser.add_argument("--s7-pool", default=str(DEFAULT_S7_POOL))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    summary_1060 = _read_json(Path(args.summary_1060))
    selected_1060 = _read_csv(Path(args.selected_1060))
    qfe_summary = _read_json(Path(args.qfe_summary))
    qfe_rows = _read_csv(Path(args.qfe_rows))
    qfe_subbuckets = _read_csv(Path(args.qfe_subbuckets))
    top1_summary = _read_json(Path(args.top1_summary))
    top1_rows = _read_csv(Path(args.top1_rows))
    mixture_summary = _read_json(Path(args.mixture_summary))
    s7_rank = _read_csv(Path(args.s7_rank))
    s7_pool = _read_csv(Path(args.s7_pool))

    m1060 = summary_1060["metrics"]
    mqfe = qfe_summary["metrics"]
    mtop1 = top1_summary["metrics"]
    mmix = mixture_summary["metrics"]
    selected_lane = selected_1060[0].get("selected_next_lane", "") if selected_1060 else ""

    qfe_top1_family_empty_from_rows = sum(1 for row in qfe_rows if not row.get("top1_family"))
    qfe_parser_unrecognized_candidates = sum(
        _int(row.get("taxonomy_empty_rows"))
        for row in qfe_subbuckets
        if row.get("top_primary_issue") in {"taxonomy_empty_same_domain_rank_gap", "taxonomy_missing_but_near_miss"}
    )
    qfe_source_or_generated_artifact_rows = sum(
        _int(row.get("source_dominated_rows"))
        for row in qfe_subbuckets
        if _float(row.get("dominant_source_rate")) >= 0.8
    )
    qfe_label_or_expected_rows = _sum_count(qfe_subbuckets, "label_or_expected_mismatch_rows")
    qfe_potential_fix_candidate_subbuckets = sum(
        1
        for row in qfe_subbuckets
        if row.get("top_learning_status") == "taxonomy_audit_candidate_not_rank_rule"
        and _float(row.get("dominant_source_rate")) < 0.9
    )
    s7_dev_rank_qfe = _sum_count(
        s7_rank,
        field="count",
        split="dev",
        reason="query_family_empty",
    )
    s7_pool_qfe = _sum_count(
        s7_pool,
        field="groups",
        source="9x_gap_rows",
        split="dev",
        reason="query_family_empty",
    )

    input_manifest = [
        {
            "input_id": "10.60_selected_s6_lane",
            "path": str(Path(args.selected_1060)),
            "rows": len(selected_1060),
            "use": "Confirm S6 is the selected next non-execution lane.",
            "status": "available" if selected_lane == "S6_parser_query_normalization_inventory" else "wrong_lane",
        },
        {
            "input_id": "9.25_query_family_empty_rows",
            "path": str(Path(args.qfe_rows)),
            "rows": len(qfe_rows),
            "use": "Row-level query text, query_family, top1_family, source, book relation, and prior issue labels.",
            "status": "available" if qfe_rows else "missing",
        },
        {
            "input_id": "9.25_query_family_empty_subbuckets",
            "path": str(Path(args.qfe_subbuckets)),
            "rows": len(qfe_subbuckets),
            "use": "Subbucket-level source dominance, taxonomy-empty, label mismatch, and representative query evidence.",
            "status": "available" if qfe_subbuckets else "missing",
        },
        {
            "input_id": "10.32_top1_family_coverage_acceptance",
            "path": str(Path(args.top1_summary)),
            "rows": _int(mtop1.get("artifact_rows")),
            "use": "Accepted DQ backlog boundary for top1_family coverage rows.",
            "status": "accepted" if mtop1.get("top1_artifact_accepted_for_dq_backlog") else "not_accepted",
        },
        {
            "input_id": "10.31_top1_family_coverage_rows",
            "path": str(Path(args.top1_rows)),
            "rows": len(top1_rows),
            "use": "Row-level top1 family coverage disposition and source-family fields.",
            "status": "available" if top1_rows else "missing",
        },
        {
            "input_id": "10.34_label_taxonomy_mixture_acceptance",
            "path": str(Path(args.mixture_summary)),
            "rows": _int(mmix.get("mixture_rows")),
            "use": "Known label/taxonomy mixture separation boundary, including generated-source dominance.",
            "status": "accepted" if mmix.get("mixture_artifact_accepted_for_dq_backlog") else "not_accepted",
        },
        {
            "input_id": "10.58_s7_rank_position_distribution",
            "path": str(Path(args.s7_rank)),
            "rows": len(s7_rank),
            "use": "Rank-position confirmation for query_family_empty wrong-rank slices.",
            "status": "available" if s7_rank else "missing",
        },
        {
            "input_id": "10.58_s7_candidate_pool_boundary",
            "path": str(Path(args.s7_pool)),
            "rows": len(s7_pool),
            "use": "top80_present/top80_missing boundary for query/top1 family empty slices.",
            "status": "available" if s7_pool else "missing",
        },
    ]

    inventory_axes = [
        {
            "axis_id": "query_family_empty_parser_unrecognized",
            "purpose": "Identify query text/subbuckets whose family is empty despite same-domain or near-miss signals.",
            "required_inputs": "query, query_family, inferred_empty_subbucket, matched_hint, top_primary_issue, family_relation",
            "forbidden_use": "Do not add parser rules or map families in this stage.",
        },
        {
            "axis_id": "top1_family_empty_taxonomy_coverage_gap",
            "purpose": "Separate top1_family_empty from top1_family_present hints and accepted top1 coverage dispositions.",
            "required_inputs": "top1_family, top1_name, top1_reasons, accepted_family_disposition, taxonomy_signal",
            "forbidden_use": "Do not edit taxonomy labels or quota metadata.",
        },
        {
            "axis_id": "query_text_normalization_gap",
            "purpose": "Flag text patterns such as specs, model numbers, punctuation, bracketed attributes, and noisy mixed terms that may hide family cues.",
            "required_inputs": "query, matched_hint, inferred_empty_subbucket, label_mismatch_explanation",
            "forbidden_use": "Do not change text_normalizer or parser behavior.",
        },
        {
            "axis_id": "source_generated_artifact_boundary",
            "purpose": "Keep global_repair_decision_table/generated or source-dominated rows separate from accepted OSS evidence.",
            "required_inputs": "source_file, source_pattern, dominant_source_rate, source_family, provenance_hash",
            "forbidden_use": "Do not treat generated/source-dominated rows as learning evidence.",
        },
        {
            "axis_id": "future_fix_candidate_readiness",
            "purpose": "Mark only owner-reviewable parser/taxonomy candidates, with evidence and risk, for a later implementation plan.",
            "required_inputs": "primary_issue, learning_status, recommendation, province_count, source_count, accepted DQ disposition",
            "forbidden_use": "Do not implement fixes, write rules, or claim Top1 gain.",
        },
    ]

    artifact_plan = [
        {
            "artifact": "s6_inventory_input_manifest.csv",
            "contents": "Input files, row counts, evidence role, and read-only status.",
            "acceptance_check": "All required existing artifacts are available and no new evidence source is required.",
        },
        {
            "artifact": "s6_failure_mode_inventory.csv",
            "contents": "Subbucket-level classification into parser-unrecognized, taxonomy-coverage, normalization, source/generated artifact, label/expected mismatch, and possible future fix candidate.",
            "acceptance_check": "Each query_family_empty subbucket has one primary failure mode and an explicit forbidden-use boundary.",
        },
        {
            "artifact": "s6_candidate_fix_readiness.csv",
            "contents": "Only candidate parser/taxonomy fixes that are owner-reviewable later, including evidence, source risk, and acceptance criteria.",
            "acceptance_check": "No candidate is marked implementation-ready without future explicit go and row/mapping review.",
        },
        {
            "artifact": "s6_blocked_learning_actions.csv",
            "contents": "Training, ranking, parser edits, taxonomy edits, heldout/hard selection, and GoalSearcher changes that remain blocked.",
            "acceptance_check": "The inventory cannot be used as learning re-entry or algorithm authorization.",
        },
    ]

    gate_checks = [
        {
            "check_id": "S6_SELECTED_BY_10_60",
            "status": "pass" if selected_lane == "S6_parser_query_normalization_inventory" else "fail",
            "evidence": f"selected_next_lane={selected_lane}",
            "decision": "S6 is the selected next non-execution lane.",
        },
        {
            "check_id": "QUERY_FAMILY_EMPTY_INPUTS_AVAILABLE",
            "status": "pass" if qfe_rows and qfe_subbuckets and _int(mqfe.get("query_family_empty_rows")) > 0 else "fail",
            "evidence": f"query_family_empty_rows={mqfe.get('query_family_empty_rows')}; subbucket_count={len(qfe_subbuckets)}",
            "decision": "Existing 9.25 rows and subbuckets are enough for inventory design.",
        },
        {
            "check_id": "TOP1_FAMILY_COVERAGE_BOUNDARY_AVAILABLE",
            "status": "pass" if mtop1.get("top1_artifact_accepted_for_dq_backlog") and top1_rows else "fail",
            "evidence": f"top1_artifact_accepted={mtop1.get('top1_artifact_accepted_for_dq_backlog')}; top1_rows={len(top1_rows)}",
            "decision": "Accepted top1 coverage DQ boundary can separate top1 taxonomy gaps.",
        },
        {
            "check_id": "NORMALIZATION_EVIDENCE_PRESENT",
            "status": "pass" if qfe_rows and {"query", "matched_hint", "label_mismatch_explanation"}.issubset(qfe_rows[0].keys()) else "fail",
            "evidence": "qfe row fields include query, matched_hint, label_mismatch_explanation",
            "decision": "S6 can inspect query text patterns without changing parser/normalizer code.",
        },
        {
            "check_id": "SOURCE_ARTIFACT_BOUNDARY_VISIBLE",
            "status": "pass" if _int(mqfe.get("source_dominated_rows")) > 0 and _int(mmix.get("query_family_empty_support_rows")) > 0 else "fail",
            "evidence": f"qfe_source_dominated_rows={mqfe.get('source_dominated_rows')}; mixture_qfe_generated_dominance={mmix.get('query_family_empty_generated_dominance')}",
            "decision": "S6 can keep generated/source-dominated artifacts out of learning claims.",
        },
        {
            "check_id": "S7_DIAGNOSTIC_CONFIRMATION_AVAILABLE",
            "status": "pass" if s7_dev_rank_qfe > 0 and s7_pool_qfe > 0 else "fail",
            "evidence": f"s7_dev_rank_qfe_count={s7_dev_rank_qfe}; s7_pool_qfe_groups={s7_pool_qfe}",
            "decision": "S7 confirms query_family_empty is visible in rank-position and pool-boundary diagnostics.",
        },
        {
            "check_id": "NON_EXECUTION_BOUNDARY",
            "status": "pass",
            "evidence": "training_allowed=false; implementation_allowed=false; parser_edit_allowed=false; taxonomy_edit_allowed=false; heldout_selection_allowed=false",
            "decision": "10.61 may only pass to read-only inventory artifact definition.",
        },
    ]
    gate_fail_count = sum(1 for row in gate_checks if row["status"] != "pass")

    blocked_actions = [
        {
            "blocked_action": "edit_parser_or_text_normalizer",
            "reason": "10.61 is a design gate only and does not authorize parser behavior changes.",
            "allowed_after": "future implementation plan with explicit go and reviewed mappings",
        },
        {
            "blocked_action": "edit_taxonomy_or_family_labels",
            "reason": "Top1/query family gaps are DQ evidence, not immediate metadata edits.",
            "allowed_after": "future DQ/parser implementation plan with owner acceptance",
        },
        {
            "blocked_action": "train_or_tune_from_s6_inventory",
            "reason": "S6 inventory is not learning evidence and remains source/taxonomy separated.",
            "allowed_after": "future lane-specific re-entry review with accepted non-generated evidence",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "10.61 uses existing dev/OOF and 9.x artifacts only.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_top1_gain",
            "reason": "Design/inventory artifacts explain failure modes only.",
            "allowed_after": "future validated algorithm or DQ implementation result with full loss audit",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "input_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_input_manifest.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "inventory_axes_csv": str(output_prefix.with_name(output_prefix.name + "_inventory_axes.csv")),
        "artifact_plan_csv": str(output_prefix.with_name(output_prefix.name + "_artifact_plan.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": summary_1060["stage"],
        "selected_lane": selected_lane,
        "query_family_empty_rows": _int(mqfe.get("query_family_empty_rows")),
        "query_family_empty_subbucket_count": len(qfe_subbuckets),
        "qfe_top1_family_empty_rows": _int(mqfe.get("top1_family_empty_rows")),
        "qfe_top1_family_empty_rows_from_rows": qfe_top1_family_empty_from_rows,
        "qfe_top1_family_present_rows": _int(mqfe.get("top1_family_present_rows")),
        "qfe_source_dominated_rows": _int(mqfe.get("source_dominated_rows")),
        "qfe_source_dominated_artifact_rows_from_subbuckets": qfe_source_or_generated_artifact_rows,
        "qfe_label_or_expected_mismatch_rows": qfe_label_or_expected_rows,
        "qfe_parser_unrecognized_candidate_rows": qfe_parser_unrecognized_candidates,
        "qfe_potential_fix_candidate_subbuckets": qfe_potential_fix_candidate_subbuckets,
        "top1_artifact_rows": _int(mtop1.get("artifact_rows")),
        "top1_same_domain_taxonomy_empty_rows": _int(mtop1.get("same_domain_taxonomy_empty_rows")),
        "top1_book_label_empty_rows": _int(mtop1.get("book_label_empty_rows")),
        "top1_cross_domain_absorption_rows": _int(mtop1.get("cross_domain_absorption_rows")),
        "top1_label_taxonomy_mixture_rows": _int(mtop1.get("label_taxonomy_mixture_rows")),
        "mixture_rows": _int(mmix.get("mixture_rows")),
        "mixture_query_family_empty_support_rows": _int(mmix.get("query_family_empty_support_rows")),
        "mixture_query_family_empty_generated_dominance": _float(mmix.get("query_family_empty_generated_dominance")),
        "s7_dev_rank_query_family_empty_count": s7_dev_rank_qfe,
        "s7_pool_query_family_empty_groups": s7_pool_qfe,
        "inventory_axis_count": len(inventory_axes),
        "planned_artifact_count": len(artifact_plan),
        "gate_pass_count": len(gate_checks) - gate_fail_count,
        "gate_fail_count": gate_fail_count,
        "s6_design_gate_decision": "pass_to_read_only_inventory_artifact_definition" if gate_fail_count == 0 else "hold_until_inputs_complete",
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "parser_edit_allowed": False,
        "taxonomy_edit_allowed": False,
        "goal_searcher_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.61 S6 parser/query normalization inventory design gate",
        "read_only": True,
        "inventory_design_gate_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Pass S6 to read-only inventory artifact definition. Existing 9.25 query_family_empty rows, 10.31/10.32 top1_family coverage artifacts, "
            "10.34 label/taxonomy mixture boundary, and 10.58/10.60 S7 diagnostics are sufficient to inventory parser-unrecognized, top1 taxonomy coverage, "
            "query normalization, source/generated artifact, and future fix-candidate readiness modes. This does not authorize parser edits, taxonomy edits, training, ranking changes, or GoalSearcher changes."
        ),
        "anti_drift_conclusion": (
            "10.61 only checks whether S6 is concrete enough for future read-only inventory artifact definition. It does not train, tune, expand candidate matrices, "
            "run heldout/hard selection, change thresholds or rules, modify GoalSearcher, edit parser/normalizer/taxonomy logic, edit feature whitelists, implement DQ fixes, or claim Top1 gain."
        ),
        "next_stage": {
            "stage": "10.62 S6 parser/query normalization inventory artifact definition",
            "goal": "Read-only build the S6 inventory artifacts that classify query_family_empty/top1_family_empty rows into parser, taxonomy, normalization, source/generated artifact, label mismatch, and future fix-candidate buckets.",
            "default": "inventory artifact definition only; no parser edit, taxonomy edit, training, implementation, or heldout/hard selection",
        },
    }

    _write_csv(Path(artifacts["input_manifest_csv"]), input_manifest, ["input_id", "path", "rows", "use", "status"])
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["check_id", "status", "evidence", "decision"])
    _write_csv(Path(artifacts["inventory_axes_csv"]), inventory_axes, ["axis_id", "purpose", "required_inputs", "forbidden_use"])
    _write_csv(Path(artifacts["artifact_plan_csv"]), artifact_plan, ["artifact", "contents", "acceptance_check"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_checks, inventory_axes, artifact_plan)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
