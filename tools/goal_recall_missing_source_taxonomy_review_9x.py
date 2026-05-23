from __future__ import annotations

import argparse
import csv
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
DEFAULT_STAGE_9_29_ROWS = AGENT_STATE / "goal_recall_missing_high_support_bucket_9x_audit_rows.csv"
DEFAULT_STAGE_9_29_SUMMARY = AGENT_STATE / "goal_recall_missing_high_support_bucket_9x_audit_summary.json"
DEFAULT_SPLIT_SUMMARY = PROJECT_ROOT / "data" / "goal_search" / "splits_expanded" / "split_summary.json"
DEFAULT_DEV_SPLIT = PROJECT_ROOT / "data" / "goal_search" / "splits_expanded" / "dev.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_recall_missing_source_taxonomy_9x_review"

DOMINANT_SOURCE = "global_repair_decision_table.csv"
GLOBAL_REPAIR_SCHEMA_VERSION = "global_repair_decision.v2"
MIN_LEARNABLE_SUPPORT = 20
MIN_LEARNABLE_SOURCES = 2


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _dev_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _clean(row.get("source_file")),
        _clean(row.get("sample_id")),
        _clean(row.get("province")),
        _clean(row.get("query") or row.get("bill_name")),
    )


def _source_provenance_class(source_file: str) -> str:
    if source_file == DOMINANT_SOURCE:
        return "generated_global_repair_decision_table"
    return "non_global_eval_trace"


def _source_provenance_risk(source_file: str, dev_bucket: str) -> str:
    if source_file == DOMINANT_SOURCE:
        if dev_bucket in {"recall_miss", "rank_miss", "confidence_miss"}:
            return "derived_error_bucket_not_independent_source"
        return "derived_repair_table_mixed_bucket"
    return "trace_source_not_global_repair"


def _coverage_gap_class(row: dict[str, Any]) -> str:
    if _clean(row.get("top1_family")):
        return "top1_family_present"
    target = _clean(row.get("target_bucket"))
    relation = _clean(row.get("semantic_relation"))
    recommendation = _clean(row.get("stage_9_29_recommendation"))
    if recommendation == "top1_family_coverage_review":
        return "high_confidence_top1_family_coverage_gap"
    if "same_domain" in relation:
        return "probable_top1_family_coverage_gap"
    if target == "valve_taxonomy_reference":
        return "valve_label_or_taxonomy_mixture"
    if target == "empty_query_family_missing":
        return "query_family_empty_blocks_coverage_decision"
    return "ambiguous_top1_family_empty"


def _learnability_status(row: dict[str, Any]) -> tuple[str, str]:
    source_file = _clean(row.get("source_file"))
    recommendation = _clean(row.get("stage_9_29_recommendation"))
    audit_class = _clean(row.get("stage_9_29_audit_class"))
    target = _clean(row.get("target_bucket"))
    coverage = _coverage_gap_class(row)
    if source_file == DOMINANT_SOURCE:
        return "blocked_source_provenance", "global_repair_decision_table.csv is a generated repair-decision source"
    if coverage in {"high_confidence_top1_family_coverage_gap", "probable_top1_family_coverage_gap"}:
        return "taxonomy_coverage_not_recall_learning", "top1_family is empty; review taxonomy coverage before recall learning"
    if target == "empty_query_family_missing":
        return "blocked_query_taxonomy_empty", "query_family is empty; no stable family slice for learning"
    if audit_class in {"query_taxonomy_empty_not_rank_learning", "taxonomy_empty_label_backlog"}:
        return "blocked_taxonomy_or_label_backlog", "taxonomy or book labels are empty"
    if recommendation in {"taxonomy_and_label_coverage_review", "query_family_label_review"}:
        return "blocked_taxonomy_or_label_backlog", "recommendation is taxonomy/label review"
    return "manual_review_before_learning", "does not pass automatic read-only learnability screen"


def _join_stage_rows(stage_rows: list[dict[str, Any]], dev_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dev_by_key = {
        (
            _clean(row.get("source_file")),
            _clean(row.get("sample_id")),
            _clean(row.get("province")),
            _clean(row.get("bill_name")),
        ): row
        for row in dev_rows
    }
    joined: list[dict[str, Any]] = []
    for row in stage_rows:
        key = _dev_key(row)
        dev = dev_by_key.get(key, {})
        status, reason = _learnability_status(row)
        out = dict(row)
        out["dev_split_match"] = bool(dev)
        out["dev_split_bucket"] = (_clean(dev.get("bucket")) or "<empty>") if dev else "<missing>"
        out["dev_split_specialty"] = _clean(dev.get("specialty")) or "<empty>"
        out["dev_split_expected_ids"] = "|".join(dev.get("expected_ids") or []) if isinstance(dev.get("expected_ids"), list) else _clean(dev.get("expected_ids"))
        out["dev_split_source_file"] = _clean(dev.get("source_file")) or "<missing>"
        out["source_provenance_class"] = _source_provenance_class(_clean(row.get("source_file")))
        out["source_provenance_risk"] = _source_provenance_risk(_clean(row.get("source_file")), out["dev_split_bucket"])
        out["coverage_gap_class"] = _coverage_gap_class(row)
        out["learnability_status"] = status
        out["learnability_reason"] = reason
        joined.append(out)
    return joined


def _summarize(rows: list[dict[str, Any]], key_fields: tuple[str, ...], bucket_type: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(_clean(row.get(field)) or "<empty>" for field in key_fields)].append(row)
    out: list[dict[str, Any]] = []
    total = len(rows)
    for key, items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        target_buckets = Counter(_clean(row.get("target_bucket")) for row in items)
        split_buckets = Counter(_clean(row.get("dev_split_bucket")) for row in items)
        coverage = Counter(_clean(row.get("coverage_gap_class")) for row in items)
        learnability = Counter(_clean(row.get("learnability_status")) for row in items)
        recommendations = Counter(_clean(row.get("stage_9_29_recommendation")) for row in items)
        audit_classes = Counter(_clean(row.get("stage_9_29_audit_class")) for row in items)
        semantics = Counter(_clean(row.get("semantic_relation")) for row in items)
        top1_domains = Counter(_clean(row.get("top1_domain")) for row in items)
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        dominant_source, dominant_source_count = sources.most_common(1)[0] if sources else ("", 0)
        count = len(items)
        out.append(
            {
                "bucket_type": bucket_type,
                "bucket_key": " + ".join(key),
                "count": count,
                "rate_within_stage_9_30_targets": _rate(count, total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": dominant_source,
                "dominant_source_count": dominant_source_count,
                "dominant_source_rate": _rate(dominant_source_count, count),
                "top_target_bucket": target_buckets.most_common(1)[0][0] if target_buckets else "",
                "target_bucket_counts": " | ".join(f"{name}:{value}" for name, value in target_buckets.most_common()),
                "dev_split_bucket_counts": " | ".join(f"{name}:{value}" for name, value in split_buckets.most_common()),
                "coverage_gap_counts": " | ".join(f"{name}:{value}" for name, value in coverage.most_common()),
                "learnability_status_counts": " | ".join(f"{name}:{value}" for name, value in learnability.most_common()),
                "top_learnability_status": learnability.most_common(1)[0][0] if learnability else "",
                "top_learnability_count": learnability.most_common(1)[0][1] if learnability else 0,
                "recommendation_counts": " | ".join(f"{name}:{value}" for name, value in recommendations.most_common()),
                "audit_class_counts": " | ".join(f"{name}:{value}" for name, value in audit_classes.most_common()),
                "semantic_relation_counts": " | ".join(f"{name}:{value}" for name, value in semantics.most_common()),
                "top1_domain_counts": " | ".join(f"{name}:{value}" for name, value in top1_domains.most_common()),
                "example_queries": " | ".join(query for query, _ in queries.most_common(8)),
            }
        )
    out.sort(key=lambda row: int(row["count"]), reverse=True)
    return out


def _learnability_slices(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _summarize(rows, ("target_bucket", "coverage_gap_class", "learnability_status"), "learnability_slice")
    for row in grouped:
        support = int(row["count"])
        source_count = int(row["source_count"])
        status = _clean(row["top_learnability_status"])
        if status == "manual_review_before_learning" and support >= MIN_LEARNABLE_SUPPORT and source_count >= MIN_LEARNABLE_SOURCES:
            row["eligible_for_learning_after_9_30"] = "possible_manual_review"
        else:
            row["eligible_for_learning_after_9_30"] = "no"
        if support < MIN_LEARNABLE_SUPPORT:
            row["non_eligible_reason"] = "support_below_20"
        elif source_count < MIN_LEARNABLE_SOURCES:
            row["non_eligible_reason"] = "source_count_below_2"
        else:
            row["non_eligible_reason"] = status
    return grouped


def _overall_metrics(rows: list[dict[str, Any]], split_summary: dict[str, Any]) -> dict[str, Any]:
    sources = Counter(_clean(row.get("source_file")) for row in rows)
    provenance = Counter(_clean(row.get("source_provenance_class")) for row in rows)
    risks = Counter(_clean(row.get("source_provenance_risk")) for row in rows)
    coverage = Counter(_clean(row.get("coverage_gap_class")) for row in rows)
    learnability = Counter(_clean(row.get("learnability_status")) for row in rows)
    split_buckets = Counter(_clean(row.get("dev_split_bucket")) for row in rows)
    targets = Counter(_clean(row.get("target_bucket")) for row in rows)
    dev_stats = split_summary.get("split_stats", {}).get("dev", {})
    dev_source_files = dev_stats.get("source_files", {})
    dev_rows = int(dev_stats.get("rows") or 0)
    dev_global_rows = int(dev_source_files.get(DOMINANT_SOURCE) or 0)
    return {
        "target_rows": len(rows),
        "dev_split_matches": sum(1 for row in rows if row.get("dev_split_match")),
        "source_counts": dict(sources.most_common()),
        "source_provenance_class_counts": dict(provenance.most_common()),
        "source_provenance_risk_counts": dict(risks.most_common()),
        "coverage_gap_class_counts": dict(coverage.most_common()),
        "learnability_status_counts": dict(learnability.most_common()),
        "dev_split_bucket_counts": dict(split_buckets.most_common()),
        "target_bucket_counts": dict(targets.most_common()),
        "dev_split_global_source_rows": dev_global_rows,
        "dev_split_rows": dev_rows,
        "dev_split_global_source_rate": _rate(dev_global_rows, dev_rows),
        "global_repair_schema_version": GLOBAL_REPAIR_SCHEMA_VERSION,
        "learnable_slice_count": sum(1 for row in rows if row.get("learnability_status") == "manual_review_before_learning"),
    }


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
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
    provenance_summary: list[dict[str, Any]],
    coverage_summary: list[dict[str, Any]],
    learnability_slices: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 9.30 Recall-missing Source-provenance and Taxonomy Coverage Review",
        "",
        "Read-only review of `global_repair_decision_table.csv` provenance and `top1_family` coverage gaps for the stage 9.29 recall-missing target rows. This decides whether any sub-slice is eligible for learning without training, tuning, rule patches, ranking changes, heldout selection, or GoalSearcher changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", metrics["target_rows"]],
                ["dev_split_matches", metrics["dev_split_matches"]],
                ["generated_global_repair_rows", metrics["source_provenance_class_counts"].get("generated_global_repair_decision_table", 0)],
                ["non_global_eval_trace_rows", metrics["source_provenance_class_counts"].get("non_global_eval_trace", 0)],
                ["dev_split_global_source_rate", metrics["dev_split_global_source_rate"]],
                ["high_confidence_top1_family_coverage_gap", metrics["coverage_gap_class_counts"].get("high_confidence_top1_family_coverage_gap", 0)],
                ["probable_top1_family_coverage_gap", metrics["coverage_gap_class_counts"].get("probable_top1_family_coverage_gap", 0)],
                ["learnable_slice_count", metrics["learnable_slice_count"]],
            ]
        ),
        "",
        "## Provenance",
        "",
        _md_table(
            [["provenance", "count", "targets", "split_buckets", "coverage", "learnability", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["target_bucket_counts"],
                    row["dev_split_bucket_counts"],
                    row["coverage_gap_counts"],
                    row["learnability_status_counts"],
                    row["example_queries"],
                ]
                for row in provenance_summary
            ]
        ),
        "",
        "## Coverage Gaps",
        "",
        _md_table(
            [["coverage", "count", "sources", "targets", "top1_domains", "learnability", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["source_count"],
                    row["target_bucket_counts"],
                    row["top1_domain_counts"],
                    row["learnability_status_counts"],
                    row["example_queries"],
                ]
                for row in coverage_summary
            ]
        ),
        "",
        "## Learnability Slices",
        "",
        _md_table(
            [["slice", "count", "sources", "eligible", "reason", "split_buckets", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["source_count"],
                    row["eligible_for_learning_after_9_30"],
                    row["non_eligible_reason"],
                    row["dev_split_bucket_counts"],
                    row["example_queries"],
                ]
                for row in learnability_slices
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.30 recall-missing source provenance and taxonomy coverage review")
    parser.add_argument("--stage-9-29-rows", default=str(DEFAULT_STAGE_9_29_ROWS))
    parser.add_argument("--stage-9-29-summary", default=str(DEFAULT_STAGE_9_29_SUMMARY))
    parser.add_argument("--split-summary", default=str(DEFAULT_SPLIT_SUMMARY))
    parser.add_argument("--dev-split", default=str(DEFAULT_DEV_SPLIT))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    stage_rows = _read_csv(Path(args.stage_9_29_rows))
    stage_summary = _read_json(Path(args.stage_9_29_summary))
    split_summary = _read_json(Path(args.split_summary))
    dev_rows = _read_jsonl(Path(args.dev_split))
    joined_rows = _join_stage_rows(stage_rows, dev_rows)

    provenance_summary = _summarize(joined_rows, ("source_provenance_class", "source_provenance_risk"), "source_provenance")
    coverage_summary = _summarize(joined_rows, ("coverage_gap_class",), "coverage_gap")
    learnability = _learnability_slices(joined_rows)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "provenance_summary_csv": str(output_prefix.with_name(output_prefix.name + "_provenance_summary.csv")),
        "coverage_summary_csv": str(output_prefix.with_name(output_prefix.name + "_coverage_summary.csv")),
        "learnability_slices_csv": str(output_prefix.with_name(output_prefix.name + "_learnability_slices.csv")),
    }
    metrics = _overall_metrics(joined_rows, split_summary)
    report = {
        "stage": "Goal LTR v1 / stage 9.30 recall-missing source-provenance and taxonomy coverage review",
        "read_only": True,
        "eval_only": True,
        "dev_only_analysis": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "source_artifacts": {
            "stage_9_29_rows": str(Path(args.stage_9_29_rows)),
            "stage_9_29_summary": str(Path(args.stage_9_29_summary)),
            "split_summary": str(Path(args.split_summary)),
            "dev_split": str(Path(args.dev_split)),
            "global_repair_builder": str(PROJECT_ROOT / "tools" / "build_global_repair_decision.py"),
        },
        "metrics": metrics,
        "stage_9_29_decision": stage_summary.get("decision", ""),
        "provenance_summary": provenance_summary,
        "coverage_summary": coverage_summary,
        "learnability_slices": learnability,
        "decision": (
            "No recall-missing sub-slice is eligible for learning after this review. The dominant slice is a generated repair-decision source "
            "rather than an independent sample source, and all 161 global_repair target rows match dev split rows from that generated table. "
            "The remaining non-global rows are small and point to query/top1 taxonomy coverage or label backlog, not a transferable recall rule. "
            "Close the current recall-missing high-support bucket route and return to a broader no-eligible closure review."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.30 only reviews dev source provenance and taxonomy coverage for previously audited recall-missing rows. It does not train, "
            "tune, patch rules, change ranking, modify GoalSearcher, use heldout for selection, connect online, or relax any gate."
        ),
        "next_stage": {
            "stage": "9.31 recall-missing no-eligible learning-slice closure review",
            "goal": (
                "Read-only closure review of the recall-missing mining route after 9.30, deciding whether to stop 9.x mining or move to a separate "
                "taxonomy/data-quality backlog before any learning work."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation",
            ],
        },
    }

    row_fields = [
        "target_bucket",
        "source_provenance_class",
        "source_provenance_risk",
        "source_slice",
        "coverage_gap_class",
        "learnability_status",
        "learnability_reason",
        "dev_split_match",
        "dev_split_bucket",
        "dev_split_specialty",
        "dev_split_expected_ids",
        "query_domain",
        "top1_domain",
        "semantic_relation",
        "stage_9_29_audit_class",
        "stage_9_29_recommendation",
        "normalized_query_family",
        "book_relation",
        "taxonomy_signal",
        "source_file",
        "province",
        "query",
        "expected_ids",
        "expected_books",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_reasons",
    ]
    summary_fields = [
        "bucket_type",
        "bucket_key",
        "count",
        "rate_within_stage_9_30_targets",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "top_target_bucket",
        "target_bucket_counts",
        "dev_split_bucket_counts",
        "coverage_gap_counts",
        "learnability_status_counts",
        "top_learnability_status",
        "top_learnability_count",
        "recommendation_counts",
        "audit_class_counts",
        "semantic_relation_counts",
        "top1_domain_counts",
        "example_queries",
    ]
    learnability_fields = summary_fields + ["eligible_for_learning_after_9_30", "non_eligible_reason"]
    _write_csv(Path(artifacts["rows_csv"]), joined_rows, row_fields)
    _write_csv(Path(artifacts["provenance_summary_csv"]), provenance_summary, summary_fields)
    _write_csv(Path(artifacts["coverage_summary_csv"]), coverage_summary, summary_fields)
    _write_csv(Path(artifacts["learnability_slices_csv"]), learnability, learnability_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, provenance_summary, coverage_summary, learnability)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": metrics,
                "decision": report["decision"],
                "next_stage": report["next_stage"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
