from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_ROWS = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review_rows.csv"
DEFAULT_REGISTRY = AGENT_STATE / "goal_10x_dq_source_provenance_bootstrap_audit_registry.csv"
DEFAULT_ROUTE_SELECTION = AGENT_STATE / "goal_10x_remaining_dq_artifact_backlog_route_selection_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_top1_family_coverage_artifact_audit"
TOP1_COVERAGE_CLASSES = {
    "probable_top1_family_coverage_gap",
    "high_confidence_top1_family_coverage_gap",
    "ambiguous_top1_family_empty",
}
FOCUS_DOMAINS = {"pipe", "valve", "lamp", "weak_current"}


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


def _domain(row: dict[str, str]) -> str:
    top1_domain = row.get("top1_domain") or "unknown"
    query_domain = row.get("query_domain") or "unknown"
    if top1_domain in FOCUS_DOMAINS:
        return top1_domain
    if query_domain in FOCUS_DOMAINS:
        return query_domain
    return "other"


def _disposition(row: dict[str, str]) -> tuple[str, str, str]:
    relation = row.get("semantic_relation", "")
    query_domain = row.get("query_domain", "")
    top1_domain = row.get("top1_domain", "")
    book_relation = row.get("book_relation", "")
    target_bucket = row.get("target_bucket", "")

    if relation == "pipe_query_top1_non_pipe_absorption":
        return (
            "cross_domain_absorption",
            "query and top1 domains diverge or top1 absorbs non-pipe object under pipe bucket",
            "taxonomy_cleanup",
        )
    if "book_label_empty" in relation or book_relation == "both_books_empty":
        return (
            "book_label_empty",
            "top1 family is empty because source book/label coverage is empty or under-specified",
            "taxonomy_cleanup",
        )
    if relation.startswith("valve_") and query_domain not in {"valve", top1_domain}:
        return (
            "label_taxonomy_mixture",
            "valve top1 coverage overlaps non-valve query domain, likely overbroad or mixed label",
            "taxonomy_cleanup",
        )
    if "top1_taxonomy_empty" in relation or relation == "query_family_empty_same_domain_same_book":
        return (
            "same_domain_taxonomy_empty",
            "query and top1 are same-domain but top1_family is empty and needs taxonomy coverage",
            "taxonomy_cleanup",
        )
    if target_bucket == "empty_query_family_missing":
        return (
            "query_family_empty_with_top1_coverage_gap",
            "query family is empty while top1 coverage also lacks stable family",
            "taxonomy_cleanup",
        )
    return (
        "needs_manual_dq_review",
        "top1_family coverage row does not match deterministic audit disposition",
        "evidence_only",
    )


def _source_family(row: dict[str, str], registry: dict[str, dict[str, str]]) -> str:
    source_file = row.get("source_file", "")
    if source_file in registry:
        return registry[source_file].get("source_family", "")
    if source_file == "global_repair_decision_table.csv":
        return "generated_repair_decision_table"
    if source_file.startswith("v36_oss_"):
        return "accepted_oss_unregistered"
    return "unknown_or_pipeline_trace"


def _learning_disposition(source_file: str, disposition: str) -> str:
    if source_file == "global_repair_decision_table.csv":
        return "taxonomy_cleanup_generated_excluded_from_learning"
    if disposition in {"same_domain_taxonomy_empty", "book_label_empty", "label_taxonomy_mixture", "cross_domain_absorption"}:
        return "taxonomy_cleanup_not_learning_evidence"
    return "evidence_only_pending_review"


def _acceptance_status(source_file: str, disposition: str) -> str:
    if source_file == "global_repair_decision_table.csv":
        return "accepted_for_dq_backlog_generated_excluded"
    if disposition == "needs_manual_dq_review":
        return "pending_manual_dq_review"
    return "accepted_for_dq_backlog_not_learning"


def _rollup(rows: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(str(row[field]) for field in key_fields)].append(row)
    result: list[dict[str, Any]] = []
    for key, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        out = {field: value for field, value in zip(key_fields, key)}
        out.update(
            {
                "support_rows": len(items),
                "source_count": len({item["source_file"] for item in items}),
                "generated_rows": sum(1 for item in items if item["source_file"] == "global_repair_decision_table.csv"),
                "accepted_oss_rows": sum(1 for item in items if str(item["source_file"]).startswith("v36_oss_")),
                "same_domain_taxonomy_empty_rows": sum(1 for item in items if item["accepted_family_disposition"] == "same_domain_taxonomy_empty"),
                "book_label_empty_rows": sum(1 for item in items if item["accepted_family_disposition"] == "book_label_empty"),
                "cross_domain_absorption_rows": sum(1 for item in items if item["accepted_family_disposition"] == "cross_domain_absorption"),
                "label_taxonomy_mixture_rows": sum(1 for item in items if item["accepted_family_disposition"] == "label_taxonomy_mixture"),
                "recommended_disposition": _recommended_disposition(items),
                "example_queries": " | ".join(item["query"] for item in items[:8]),
            }
        )
        result.append(out)
    return result


def _recommended_disposition(items: list[dict[str, Any]]) -> str:
    counter = Counter(item["accepted_family_disposition"] for item in items)
    top = counter.most_common(1)[0][0]
    if all(item["source_file"] == "global_repair_decision_table.csv" for item in items):
        return f"{top}; generated_excluded_from_learning"
    return f"{top}; dq_backlog_only"


def _write_markdown(path: Path, report: dict[str, Any], domain_rows: list[dict[str, Any]], disposition_rows: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 10.31 Top1 Family Coverage Artifact Audit",
        "",
        "Read-only DQ artifact audit for top1_family coverage. This is not learning evidence.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["top1_coverage_rows", metrics["top1_coverage_rows"]],
                ["focus_domain_count", metrics["focus_domain_count"]],
                ["same_domain_taxonomy_empty_rows", metrics["same_domain_taxonomy_empty_rows"]],
                ["book_label_empty_rows", metrics["book_label_empty_rows"]],
                ["cross_domain_absorption_rows", metrics["cross_domain_absorption_rows"]],
                ["label_taxonomy_mixture_rows", metrics["label_taxonomy_mixture_rows"]],
                ["reentry_allowed_now", metrics["reentry_allowed_now"]],
            ]
        ),
        "",
        "## Domain Rollup",
        "",
        _md_table(
            [["domain", "support_rows", "source_count", "generated_rows", "accepted_oss_rows", "recommended_disposition"]]
            + [
                [
                    row["domain"],
                    row["support_rows"],
                    row["source_count"],
                    row["generated_rows"],
                    row["accepted_oss_rows"],
                    row["recommended_disposition"],
                ]
                for row in domain_rows
            ]
        ),
        "",
        "## Disposition Rollup",
        "",
        _md_table(
            [["accepted_family_disposition", "support_rows", "source_count", "generated_rows", "accepted_oss_rows"]]
            + [
                [
                    row["accepted_family_disposition"],
                    row["support_rows"],
                    row["source_count"],
                    row["generated_rows"],
                    row["accepted_oss_rows"],
                ]
                for row in disposition_rows
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
    parser = argparse.ArgumentParser(description="Audit top1_family coverage artifact without learning execution")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--route-selection", default=str(DEFAULT_ROUTE_SELECTION))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    registry_rows = _read_csv(Path(args.registry))
    route_selection = _read_json(Path(args.route_selection))
    registry = {row["source_file"]: row for row in registry_rows}

    target_rows = [row for row in source_rows if row.get("coverage_gap_class") in TOP1_COVERAGE_CLASSES]
    audit_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(target_rows, start=1):
        disposition, reason, learning_type = _disposition(row)
        source_file = row.get("source_file", "")
        payload = "|".join(
            [
                row.get("target_bucket", ""),
                row.get("source_file", ""),
                row.get("province", ""),
                row.get("query", ""),
                row.get("top1_id", ""),
                row.get("top1_name", ""),
            ]
        )
        audit_rows.append(
            {
                "row_id": f"top1cov_{idx:04d}_{_sha(payload)[:10]}",
                "query_family": row.get("normalized_query_family", ""),
                "top1_family": row.get("top1_family", ""),
                "coverage_issue": row.get("coverage_gap_class", ""),
                "accepted_family_disposition": disposition,
                "disposition_reason": reason,
                "domain": _domain(row),
                "query_domain": row.get("query_domain", ""),
                "top1_domain": row.get("top1_domain", ""),
                "semantic_relation": row.get("semantic_relation", ""),
                "book_relation": row.get("book_relation", ""),
                "taxonomy_signal": row.get("taxonomy_signal", ""),
                "source_file": source_file,
                "source_family": _source_family(row, registry),
                "provenance_hash": registry.get(source_file, {}).get("provenance_hash", _sha(source_file) if source_file else ""),
                "learning_disposition": _learning_disposition(source_file, disposition),
                "acceptance_status": _acceptance_status(source_file, disposition),
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "expected_ids": row.get("expected_ids", ""),
                "top1_id": row.get("top1_id", ""),
                "top1_name": row.get("top1_name", ""),
                "top1_book": row.get("top1_book", ""),
                "top1_reasons": row.get("top1_reasons", ""),
            }
        )

    domain_rollup = _rollup(audit_rows, ["domain"])
    disposition_rollup = _rollup(audit_rows, ["accepted_family_disposition"])
    source_rollup = _rollup(audit_rows, ["source_file", "source_family"])
    blocked_actions = [
        {
            "blocked_action": "treat_top1_coverage_rows_as_learning_evidence",
            "reason": "10.31 produces DQ taxonomy coverage dispositions only; generated rows remain excluded and accepted OSS rows are taxonomy cleanup.",
            "allowed_after": "future re-entry review with separate accepted-OSS positive learning evidence",
        },
        {
            "blocked_action": "train_tune_or_expand_candidates",
            "reason": "Top1 family coverage audit is read-only DQ work.",
            "allowed_after": "explicit future execution authorization after re-entry gates pass",
        },
        {
            "blocked_action": "run_heldout_or_hard_validation_or_selection",
            "reason": "No validation candidate exists and this DQ audit does not select model policy.",
            "allowed_after": "future validation gate after learning re-entry, if ever reached",
        },
        {
            "blocked_action": "change_goal_searcher_rules_thresholds_or_feature_whitelist",
            "reason": "No implementation authorization exists.",
            "allowed_after": "post-validation implementation review, if ever reached",
        },
        {
            "blocked_action": "claim_accuracy_gain",
            "reason": "No algorithm was changed and no validation was run.",
            "allowed_after": "future approved offline/validation path with proper split policy",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "audit_rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "domain_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_domain_rollup.csv")),
        "disposition_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_disposition_rollup.csv")),
        "source_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_source_rollup.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "selected_route": route_selection["metrics"]["selected_lane"],
        "top1_coverage_rows": len(audit_rows),
        "focus_domain_count": len({row["domain"] for row in audit_rows}),
        "pipe_rows": sum(1 for row in audit_rows if row["domain"] == "pipe"),
        "valve_rows": sum(1 for row in audit_rows if row["domain"] == "valve"),
        "lamp_rows": sum(1 for row in audit_rows if row["domain"] == "lamp"),
        "weak_current_rows": sum(1 for row in audit_rows if row["domain"] == "weak_current"),
        "other_rows": sum(1 for row in audit_rows if row["domain"] == "other"),
        "same_domain_taxonomy_empty_rows": sum(1 for row in audit_rows if row["accepted_family_disposition"] == "same_domain_taxonomy_empty"),
        "book_label_empty_rows": sum(1 for row in audit_rows if row["accepted_family_disposition"] == "book_label_empty"),
        "cross_domain_absorption_rows": sum(1 for row in audit_rows if row["accepted_family_disposition"] == "cross_domain_absorption"),
        "label_taxonomy_mixture_rows": sum(1 for row in audit_rows if row["accepted_family_disposition"] == "label_taxonomy_mixture"),
        "query_family_empty_with_top1_gap_rows": sum(1 for row in audit_rows if row["accepted_family_disposition"] == "query_family_empty_with_top1_coverage_gap"),
        "generated_rows": sum(1 for row in audit_rows if row["source_file"] == "global_repair_decision_table.csv"),
        "accepted_oss_rows": sum(1 for row in audit_rows if str(row["source_file"]).startswith("v36_oss_")),
        "reentry_allowed_now": False,
        "training_allowed": False,
        "heldout_selection_allowed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / 10.31 top1_family coverage artifact audit",
        "read_only": True,
        "dq_artifact_audit_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Top1_family coverage artifact is built and dispositioned as DQ backlog, not learning evidence. The dominant dispositions are same-domain taxonomy-empty, "
            "book-label-empty, and cross-domain absorption; label/taxonomy mixture is present only as a smaller valve-overlap subset. S2 remains parked."
        ),
        "anti_drift_conclusion": (
            "10.31 only audits top1_family coverage rows. It does not train, tune, expand candidates, run heldout/hard validation or selection, change thresholds or rules, "
            "modify GoalSearcher, edit feature whitelists, or treat DQ backlog rows as learning evidence."
        ),
        "next_stage": {
            "stage": "10.32 top1_family coverage acceptance gate",
            "goal": "Read-only decide whether the top1_family coverage artifact is acceptable as DQ backlog evidence and which residual lane comes next.",
            "default": "continue DQ backlog only; S2 remains parked unless new accepted-OSS evidence package arrives",
        },
    }

    audit_fields = [
        "row_id", "query_family", "top1_family", "coverage_issue", "accepted_family_disposition",
        "disposition_reason", "domain", "query_domain", "top1_domain", "semantic_relation",
        "book_relation", "taxonomy_signal", "source_file", "source_family", "provenance_hash",
        "learning_disposition", "acceptance_status", "province", "query", "expected_ids",
        "top1_id", "top1_name", "top1_book", "top1_reasons",
    ]
    rollup_fields = [
        "domain", "support_rows", "source_count", "generated_rows", "accepted_oss_rows",
        "same_domain_taxonomy_empty_rows", "book_label_empty_rows", "cross_domain_absorption_rows",
        "label_taxonomy_mixture_rows", "recommended_disposition", "example_queries",
    ]
    disposition_fields = [
        "accepted_family_disposition", "support_rows", "source_count", "generated_rows", "accepted_oss_rows",
        "same_domain_taxonomy_empty_rows", "book_label_empty_rows", "cross_domain_absorption_rows",
        "label_taxonomy_mixture_rows", "recommended_disposition", "example_queries",
    ]
    source_fields = [
        "source_file", "source_family", "support_rows", "source_count", "generated_rows", "accepted_oss_rows",
        "same_domain_taxonomy_empty_rows", "book_label_empty_rows", "cross_domain_absorption_rows",
        "label_taxonomy_mixture_rows", "recommended_disposition", "example_queries",
    ]
    _write_csv(Path(artifacts["audit_rows_csv"]), audit_rows, audit_fields)
    _write_csv(Path(artifacts["domain_rollup_csv"]), domain_rollup, rollup_fields)
    _write_csv(Path(artifacts["disposition_rollup_csv"]), disposition_rollup, disposition_fields)
    _write_csv(Path(artifacts["source_rollup_csv"]), source_rollup, source_fields)
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, domain_rollup, disposition_rollup)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
