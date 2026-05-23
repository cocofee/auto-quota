from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_ROWS = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_rows.csv"
DEFAULT_ACCEPTANCE_GATE = AGENT_STATE / "goal_10x_top1_family_coverage_acceptance_gate_summary.json"
DEFAULT_REGISTRY = AGENT_STATE / "goal_10x_dq_source_provenance_bootstrap_audit_registry.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_label_taxonomy_mixture_separation_artifact_audit"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _source_family(source_file: str, registry: dict[str, dict[str, str]]) -> str:
    if source_file in registry:
        return registry[source_file].get("source_family", "")
    if source_file == "global_repair_decision_table.csv":
        return "generated_repair_decision_table"
    if source_file.startswith("v36_oss_"):
        return "accepted_oss_unregistered"
    return "unknown_or_pipeline_trace"


def _is_valve_like_text(value: str) -> bool:
    valve_terms = ("阀", "过滤器", "除污器", "减压", "水锤消除器")
    return any(term in value for term in valve_terms)


def _separation(row: dict[str, str]) -> tuple[str, str, str]:
    query = row.get("query", "")
    top1_name = row.get("top1_name", "")
    query_domain = row.get("query_domain", "")
    top1_domain = row.get("top1_domain", "")
    relation = row.get("semantic_relation", "")
    book_relation = row.get("book_relation", "")
    top1_reasons = row.get("top1_reasons", "")

    query_valve_like = _is_valve_like_text(query)
    top1_valve_like = _is_valve_like_text(top1_name) or "family:valve" in top1_reasons

    if query_valve_like and (top1_valve_like or relation == "valve_taxonomy_ambiguous"):
        return (
            "valve_overlap",
            "query/top1 are valve-adjacent, but the taxonomy family is empty or ambiguous",
            "create valve-adjacent taxonomy mapping review; do not learn from generated source",
        )
    if relation == "valve_family_overbroad_query_label_issue" or (
        row.get("normalized_query_family") == "valve" and query_domain not in {"valve"} and not query_valve_like
    ):
        return (
            "overbroad_labels",
            "query is labeled as valve but reads as instrument, sanitary, container, or unknown non-valve object",
            "split query label from valve family before any learning re-entry",
        )
    if top1_domain not in {"", "unknown", "valve", query_domain} or "family conflict" in top1_reasons:
        return (
            "cross_domain_absorption",
            "valve label or lexical match pulls a civil/pipe/duct/sanitary target across domains",
            "separate cross-domain absorption from taxonomy coverage work",
        )
    if book_relation == "both_books_empty" or row.get("taxonomy_signal") == "top1_family_empty":
        return (
            "true_taxonomy_gaps",
            "row remains a family/book coverage gap after label and cross-domain cases are separated",
            "backfill taxonomy/book labels after owner acceptance",
        )
    return (
        "needs_manual_dq_review",
        "row does not match deterministic label/taxonomy mixture separation",
        "manual owner review before acceptance",
    )


def _rollup(rows: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(str(row.get(field, "")) for field in key_fields)].append(row)
    result: list[dict[str, Any]] = []
    for key, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        out = {field: value for field, value in zip(key_fields, key)}
        out.update(
            {
                "support_rows": len(items),
                "province_count": len({item["province"] for item in items}),
                "source_count": len({item["source_file"] for item in items}),
                "generated_rows": sum(1 for item in items if item["source_file"] == "global_repair_decision_table.csv"),
                "accepted_oss_rows": sum(1 for item in items if str(item["source_file"]).startswith("v36_oss_")),
                "valve_overlap_rows": sum(1 for item in items if item["separation_class"] == "valve_overlap"),
                "overbroad_label_rows": sum(1 for item in items if item["separation_class"] == "overbroad_labels"),
                "cross_domain_absorption_rows": sum(1 for item in items if item["separation_class"] == "cross_domain_absorption"),
                "true_taxonomy_gap_rows": sum(1 for item in items if item["separation_class"] == "true_taxonomy_gaps"),
                "needs_manual_review_rows": sum(1 for item in items if item["separation_class"] == "needs_manual_dq_review"),
                "example_queries": " | ".join(item["query"] for item in items[:8]),
            }
        )
        result.append(out)
    return result


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    class_rollup: list[dict[str, Any]],
    domain_rollup: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.33 Label/Taxonomy Mixture Separation Artifact Audit",
        "",
        "Read-only DQ artifact audit for label/taxonomy mixture rows. This is not learning evidence.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["mixture_rows", metrics["mixture_rows"]],
                ["valve_overlap_rows", metrics["valve_overlap_rows"]],
                ["overbroad_label_rows", metrics["overbroad_label_rows"]],
                ["cross_domain_absorption_rows", metrics["cross_domain_absorption_rows"]],
                ["true_taxonomy_gap_rows", metrics["true_taxonomy_gap_rows"]],
                ["generated_rows", metrics["generated_rows"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
            ]
        ),
        "",
        "## Separation Rollup",
        "",
        _md_table(
            [["separation_class", "support_rows", "province_count", "generated_rows", "example_queries"]]
            + [
                [
                    row["separation_class"],
                    row["support_rows"],
                    row["province_count"],
                    row["generated_rows"],
                    row["example_queries"],
                ]
                for row in class_rollup
            ]
        ),
        "",
        "## Domain Rollup",
        "",
        _md_table(
            [["query_domain", "top1_domain", "support_rows", "valve_overlap_rows", "overbroad_label_rows", "cross_domain_absorption_rows", "true_taxonomy_gap_rows"]]
            + [
                [
                    row["query_domain"],
                    row["top1_domain"],
                    row["support_rows"],
                    row["valve_overlap_rows"],
                    row["overbroad_label_rows"],
                    row["cross_domain_absorption_rows"],
                    row["true_taxonomy_gap_rows"],
                ]
                for row in domain_rollup
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
    parser = argparse.ArgumentParser(description="Audit label/taxonomy mixture separation without learning execution")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--acceptance-gate", default=str(DEFAULT_ACCEPTANCE_GATE))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    acceptance_gate = _read_json(Path(args.acceptance_gate))
    registry_rows = _read_csv(Path(args.registry))
    registry = {row["source_file"]: row for row in registry_rows}

    target_rows = [row for row in source_rows if row.get("coverage_gap_class") == "valve_label_or_taxonomy_mixture"]
    audit_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(target_rows, start=1):
        separation_class, reason, recommended_owner_action = _separation(row)
        source_file = row.get("source_file", "")
        payload = "|".join(
            [
                row.get("coverage_gap_class", ""),
                row.get("source_file", ""),
                row.get("province", ""),
                row.get("query", ""),
                row.get("top1_id", ""),
                row.get("top1_name", ""),
            ]
        )
        audit_rows.append(
            {
                "row_id": f"labelmix_{idx:04d}_{_sha(payload)[:10]}",
                "separation_class": separation_class,
                "separation_reason": reason,
                "recommended_owner_action": recommended_owner_action,
                "coverage_gap_class": row.get("coverage_gap_class", ""),
                "normalized_query_family": row.get("normalized_query_family", ""),
                "query_domain": row.get("query_domain", ""),
                "top1_domain": row.get("top1_domain", ""),
                "semantic_relation": row.get("semantic_relation", ""),
                "book_relation": row.get("book_relation", ""),
                "taxonomy_signal": row.get("taxonomy_signal", ""),
                "source_file": source_file,
                "source_family": _source_family(source_file, registry),
                "provenance_hash": registry.get(source_file, {}).get("provenance_hash", _sha(source_file) if source_file else ""),
                "learning_disposition": "dq_backlog_generated_excluded_from_learning"
                if source_file == "global_repair_decision_table.csv"
                else "dq_backlog_not_learning_evidence",
                "acceptance_status": "accepted_for_dq_backlog_generated_excluded"
                if source_file == "global_repair_decision_table.csv"
                else "accepted_for_dq_backlog_not_learning",
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "expected_ids": row.get("expected_ids", ""),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "top1_family": row.get("top1_family", ""),
                "top1_book": row.get("top1_book", ""),
                "top1_reasons": row.get("top1_reasons", ""),
            }
        )

    class_rollup = _rollup(audit_rows, ["separation_class"])
    domain_rollup = _rollup(audit_rows, ["query_domain", "top1_domain"])
    source_rollup = _rollup(audit_rows, ["source_file", "source_family"])
    blocked_actions = [
        {
            "blocked_action": "treat_label_taxonomy_mixture_as_learning_evidence",
            "reason": "10.33 separates generated-source DQ issues only; all target rows remain excluded from learning.",
            "allowed_after": "future re-entry review with accepted non-generated positive evidence",
        },
        {
            "blocked_action": "write_taxonomy_rules_or_goal_searcher_changes",
            "reason": "10.33 is artifact audit only, not implementation.",
            "allowed_after": "future owner-accepted DQ fix plan and explicit implementation authorization",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "No learning lane is reopened by label/taxonomy mixture separation.",
            "allowed_after": "explicit future go after learning re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No model or policy candidate is selected here.",
            "allowed_after": "future validation gate if a learning or DQ-fix candidate is approved",
        },
        {
            "blocked_action": "claim_accuracy_gain",
            "reason": "No algorithm changed and no validation was run.",
            "allowed_after": "future approved offline/validation path with proper split policy",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "separation_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_separation_rollup.csv")),
        "domain_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_domain_rollup.csv")),
        "source_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_source_rollup.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "selected_lane_from_10_32": acceptance_gate["metrics"]["selected_residual_lane"],
        "mixture_rows": len(audit_rows),
        "classified_rows": sum(1 for row in audit_rows if row["separation_class"] != "needs_manual_dq_review"),
        "needs_manual_review_rows": sum(1 for row in audit_rows if row["separation_class"] == "needs_manual_dq_review"),
        "valve_overlap_rows": sum(1 for row in audit_rows if row["separation_class"] == "valve_overlap"),
        "overbroad_label_rows": sum(1 for row in audit_rows if row["separation_class"] == "overbroad_labels"),
        "cross_domain_absorption_rows": sum(1 for row in audit_rows if row["separation_class"] == "cross_domain_absorption"),
        "true_taxonomy_gap_rows": sum(1 for row in audit_rows if row["separation_class"] == "true_taxonomy_gaps"),
        "generated_rows": sum(1 for row in audit_rows if row["source_file"] == "global_repair_decision_table.csv"),
        "accepted_oss_rows": sum(1 for row in audit_rows if str(row["source_file"]).startswith("v36_oss_")),
        "province_count": len({row["province"] for row in audit_rows}),
        "source_count": len({row["source_file"] for row in audit_rows}),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.33 label/taxonomy mixture separation artifact audit",
        "read_only": True,
        "dq_artifact_audit_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Label/taxonomy mixture rows are separated into valve-overlap, overbroad labels, cross-domain absorption, and true taxonomy gaps as DQ backlog only. "
            "The lane is fully generated-source dominated, so it cannot reopen S2, cannot become learning evidence, and should feed a future owner-accepted DQ fix plan instead."
        ),
        "anti_drift_conclusion": (
            "10.33 only audits label/taxonomy mixture rows. It does not train, tune, expand candidates, run heldout/hard validation or selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.34 label/taxonomy mixture acceptance gate",
            "goal": "Read-only decide whether the 10.33 separation artifact is acceptable as DQ backlog evidence and whether the remaining route should move to query_family_empty coverage or DQ-fix planning.",
            "default": "continue DQ backlog only; S2 remains parked unless new accepted-OSS evidence package arrives",
        },
    }

    row_fields = [
        "row_id",
        "separation_class",
        "separation_reason",
        "recommended_owner_action",
        "coverage_gap_class",
        "normalized_query_family",
        "query_domain",
        "top1_domain",
        "semantic_relation",
        "book_relation",
        "taxonomy_signal",
        "source_file",
        "source_family",
        "provenance_hash",
        "learning_disposition",
        "acceptance_status",
        "province",
        "query",
        "expected_ids",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_reasons",
    ]
    rollup_fields = [
        "separation_class",
        "support_rows",
        "province_count",
        "source_count",
        "generated_rows",
        "accepted_oss_rows",
        "valve_overlap_rows",
        "overbroad_label_rows",
        "cross_domain_absorption_rows",
        "true_taxonomy_gap_rows",
        "needs_manual_review_rows",
        "example_queries",
    ]
    domain_fields = [
        "query_domain",
        "top1_domain",
        "support_rows",
        "province_count",
        "source_count",
        "generated_rows",
        "accepted_oss_rows",
        "valve_overlap_rows",
        "overbroad_label_rows",
        "cross_domain_absorption_rows",
        "true_taxonomy_gap_rows",
        "needs_manual_review_rows",
        "example_queries",
    ]
    source_fields = [
        "source_file",
        "source_family",
        "support_rows",
        "province_count",
        "source_count",
        "generated_rows",
        "accepted_oss_rows",
        "valve_overlap_rows",
        "overbroad_label_rows",
        "cross_domain_absorption_rows",
        "true_taxonomy_gap_rows",
        "needs_manual_review_rows",
        "example_queries",
    ]
    _write_csv(Path(artifacts["rows_csv"]), audit_rows, row_fields)
    _write_csv(Path(artifacts["separation_rollup_csv"]), class_rollup, rollup_fields)
    _write_csv(Path(artifacts["domain_rollup_csv"]), domain_rollup, domain_fields)
    _write_csv(Path(artifacts["source_rollup_csv"]), source_rollup, source_fields)
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, class_rollup, domain_rollup)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
