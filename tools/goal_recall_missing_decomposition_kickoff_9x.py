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
DEFAULT_TOP80_MISSING = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_top80_missing.csv"
DEFAULT_DECOMPOSITION_SUMMARY = AGENT_STATE / "goal_accuracy_gap_9x_decomposition_summary.json"
DEFAULT_STAGE_9_27_SUMMARY = AGENT_STATE / "goal_no_eligible_wrong_rank_closure_9x_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_recall_missing_decomposition_9x_kickoff"

MIN_SUPPORT = 20
MIN_PROVINCES = 3
MIN_SOURCES = 2
SOURCE_DOMINANCE_WARN = 0.90
SOURCE_DOMINANCE_HARD = 0.95


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_books(value: str) -> set[str]:
    out: set[str] = set()
    for chunk in value.replace("|", ",").replace(";", ",").replace("，", ",").split(","):
        item = chunk.strip()
        if item:
            out.add(item)
    return out


def _book_relation(row: dict[str, Any]) -> str:
    expected_books = _split_books(_clean(row.get("expected_books")))
    top1_book = _clean(row.get("top1_book"))
    if not expected_books and not top1_book:
        return "both_books_empty"
    if not expected_books:
        return "expected_book_empty"
    if not top1_book:
        return "top1_book_empty"
    if top1_book in expected_books:
        return "same_book"
    return "wrong_book"


def _taxonomy_signal(row: dict[str, Any]) -> str:
    query_family = _clean(row.get("query_family"))
    top1_family = _clean(row.get("top1_family"))
    if not query_family and not top1_family:
        return "query_and_top1_family_empty"
    if not query_family:
        return "query_family_empty"
    if not top1_family:
        return "top1_family_empty"
    if query_family == top1_family:
        return "same_family"
    return "family_conflict"


def _bucket_key(row: dict[str, Any]) -> tuple[str, str]:
    family = _clean(row.get("query_family")) or "<empty>"
    reason = _clean(row.get("reason")) or "<empty>"
    return family, reason


def _group_stats(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> dict[tuple[str, ...], dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple((_clean(row.get(field)) or "<empty>") for field in key_fields)
        grouped[key].append(row)

    out: dict[tuple[str, ...], dict[str, Any]] = {}
    for key, items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        dominant_source, dominant_count = sources.most_common(1)[0] if sources else ("", 0)
        out[key] = {
            "count": len(items),
            "province_count": len(provinces),
            "source_count": len(sources),
            "dominant_source": dominant_source,
            "dominant_source_count": dominant_count,
            "dominant_source_rate": _rate(dominant_count, len(items)),
        }
    return out


def _source_shape(stats: dict[str, Any]) -> str:
    if stats["source_count"] <= 1:
        return "single_source"
    if stats["dominant_source_rate"] >= SOURCE_DOMINANCE_HARD:
        return "dominant_source_ge_95pct"
    if stats["dominant_source_rate"] >= SOURCE_DOMINANCE_WARN:
        return "dominant_source_ge_90pct"
    return "source_diverse"


def _audit_bucket_shape(stats: dict[str, Any]) -> str:
    if stats["count"] >= MIN_SUPPORT and stats["province_count"] >= MIN_PROVINCES and stats["source_count"] >= MIN_SOURCES:
        if stats["dominant_source_rate"] >= SOURCE_DOMINANCE_WARN:
            return "high_support_diverse_but_source_dominated"
        return "high_support_diverse"
    if stats["count"] >= MIN_SUPPORT and stats["source_count"] <= 1:
        return "high_support_single_source"
    return "small_or_fragmented"


def _primary_issue(row: dict[str, Any], bucket_stats: dict[str, Any]) -> tuple[str, str]:
    reason = _clean(row.get("reason"))
    query_family = _clean(row.get("query_family"))
    book_relation = _book_relation(row)
    taxonomy_signal = _taxonomy_signal(row)
    source_shape = _source_shape(bucket_stats)

    if source_shape == "single_source":
        return "single_source_artifact", "bucket has only one source_file; keep out of transferable recall learning until source provenance is reviewed"
    if source_shape in {"dominant_source_ge_95pct", "dominant_source_ge_90pct"}:
        return "source_dominated_artifact", "bucket passes diversity only nominally; one source dominates the rows"
    if reason == "query_family_empty" or not query_family:
        return "query_taxonomy_empty", "query_family is empty, so recall failure cannot be attributed to a stable object family yet"
    if taxonomy_signal == "top1_family_empty" and book_relation == "same_book":
        return "top1_taxonomy_empty_same_book", "expected book and top1 book agree, but top1 family is empty and positive is missing from top80"
    if book_relation == "wrong_book":
        return "true_recall_wrong_book_candidate", "positive is missing from top80 and top1 book is outside expected_books"
    if book_relation in {"expected_book_empty", "both_books_empty"}:
        return "taxonomy_or_label_empty_book", "expected_books or top1_book are empty, so label/taxonomy coverage must be reviewed before learning"
    return "true_recall_same_domain_candidate", "positive is missing from top80 with a non-empty query family; candidate for recall analysis after taxonomy/source checks"


def _next_action(primary_issue: str) -> str:
    if primary_issue in {"single_source_artifact", "source_dominated_artifact"}:
        return "source_provenance_review_before_recall_learning"
    if primary_issue in {"query_taxonomy_empty", "top1_taxonomy_empty_same_book", "taxonomy_or_label_empty_book"}:
        return "taxonomy_label_coverage_review"
    if primary_issue in {"true_recall_wrong_book_candidate", "true_recall_same_domain_candidate"}:
        return "candidate_for_recall_missing_audit"
    return "manual_review"


def _annotate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bucket_stats_map = _group_stats(rows, ("query_family", "reason"))
    annotated: list[dict[str, Any]] = []
    for row in rows:
        key = tuple("" if item == "<empty>" else item for item in _bucket_key(row))
        # group_stats uses <empty> for blank fields, so use the stored key directly.
        stats = bucket_stats_map[_bucket_key(row)]
        issue, explanation = _primary_issue(row, stats)
        out = dict(row)
        out["normalized_query_family"] = _clean(row.get("query_family")) or "<empty>"
        out["book_relation"] = _book_relation(row)
        out["taxonomy_signal"] = _taxonomy_signal(row)
        out["bucket_count"] = stats["count"]
        out["bucket_province_count"] = stats["province_count"]
        out["bucket_source_count"] = stats["source_count"]
        out["bucket_dominant_source"] = stats["dominant_source"]
        out["bucket_dominant_source_rate"] = stats["dominant_source_rate"]
        out["source_shape"] = _source_shape(stats)
        out["audit_bucket_shape"] = _audit_bucket_shape(stats)
        out["primary_issue"] = issue
        out["primary_explanation"] = explanation
        out["next_action"] = _next_action(issue)
        annotated.append(out)
    return annotated


def _bucket_rows(rows: list[dict[str, Any]], key_fields: tuple[str, ...], bucket_type: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple((_clean(row.get(field)) or "<empty>") for field in key_fields)
        grouped[key].append(row)

    out: list[dict[str, Any]] = []
    total = len(rows)
    for key, items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        reasons = Counter(_clean(row.get("reason")) or "<empty>" for row in items)
        families = Counter(_clean(row.get("normalized_query_family")) or "<empty>" for row in items)
        issues = Counter(_clean(row.get("primary_issue")) for row in items)
        actions = Counter(_clean(row.get("next_action")) for row in items)
        book_relations = Counter(_clean(row.get("book_relation")) for row in items)
        taxonomy = Counter(_clean(row.get("taxonomy_signal")) for row in items)
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        dominant_source, dominant_source_count = sources.most_common(1)[0] if sources else ("", 0)
        count = len(items)
        stats = {
            "count": count,
            "province_count": len(provinces),
            "source_count": len(sources),
            "dominant_source": dominant_source,
            "dominant_source_count": dominant_source_count,
            "dominant_source_rate": _rate(dominant_source_count, count),
        }
        out.append(
            {
                "bucket_type": bucket_type,
                "bucket_key": " + ".join(key),
                "count": count,
                "rate_within_dev_top80_missing": _rate(count, total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": dominant_source,
                "dominant_source_count": dominant_source_count,
                "dominant_source_rate": stats["dominant_source_rate"],
                "source_shape": _source_shape(stats),
                "audit_bucket_shape": _audit_bucket_shape(stats),
                "top_reason": reasons.most_common(1)[0][0] if reasons else "",
                "top_reason_count": reasons.most_common(1)[0][1] if reasons else 0,
                "top_query_family": families.most_common(1)[0][0] if families else "",
                "top_query_family_count": families.most_common(1)[0][1] if families else 0,
                "top_primary_issue": issues.most_common(1)[0][0] if issues else "",
                "top_primary_issue_count": issues.most_common(1)[0][1] if issues else 0,
                "top_next_action": actions.most_common(1)[0][0] if actions else "",
                "top_next_action_count": actions.most_common(1)[0][1] if actions else 0,
                "book_relation_counts": " | ".join(f"{name}:{value}" for name, value in book_relations.most_common()),
                "taxonomy_signal_counts": " | ".join(f"{name}:{value}" for name, value in taxonomy.most_common()),
                "example_queries": " | ".join(query for query, _ in queries.most_common(8)),
            }
        )
    out.sort(key=lambda row: int(row["count"]), reverse=True)
    return out


def _issue_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(_clean(row.get("primary_issue")), _clean(row.get("next_action")))].append(row)

    out: list[dict[str, Any]] = []
    total = len(rows)
    for (issue, action), items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        families = Counter(_clean(row.get("normalized_query_family")) for row in items)
        reasons = Counter(_clean(row.get("reason")) for row in items)
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        dominant_source, dominant_source_count = sources.most_common(1)[0] if sources else ("", 0)
        out.append(
            {
                "primary_issue": issue,
                "next_action": action,
                "count": len(items),
                "rate_within_dev_top80_missing": _rate(len(items), total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": dominant_source,
                "dominant_source_count": dominant_source_count,
                "dominant_source_rate": _rate(dominant_source_count, len(items)),
                "top_query_family": families.most_common(1)[0][0] if families else "",
                "top_query_family_count": families.most_common(1)[0][1] if families else 0,
                "top_reason": reasons.most_common(1)[0][0] if reasons else "",
                "top_reason_count": reasons.most_common(1)[0][1] if reasons else 0,
                "example_queries": " | ".join(query for query, _ in queries.most_common(10)),
            }
        )
    out.sort(key=lambda row: int(row["count"]), reverse=True)
    return out


def _overview(rows: list[dict[str, Any]], annotated: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter(_clean(row.get("reason")) for row in rows)
    families = Counter(_clean(row.get("query_family")) or "<empty>" for row in rows)
    sources = Counter(_clean(row.get("source_file")) for row in rows if _clean(row.get("source_file")))
    provinces = Counter(_clean(row.get("province")) for row in rows if _clean(row.get("province")))
    issue_counts = Counter(_clean(row.get("primary_issue")) for row in annotated)
    action_counts = Counter(_clean(row.get("next_action")) for row in annotated)
    book_counts = Counter(_clean(row.get("book_relation")) for row in annotated)
    taxonomy_counts = Counter(_clean(row.get("taxonomy_signal")) for row in annotated)
    source_shape_counts = Counter(_clean(row.get("source_shape")) for row in annotated)
    dominant_source, dominant_source_count = sources.most_common(1)[0] if sources else ("", 0)
    return {
        "dev_top80_missing_rows": len(rows),
        "reason_counts": dict(reasons.most_common()),
        "query_family_counts": dict(families.most_common(20)),
        "province_count": len(provinces),
        "source_count": len(sources),
        "dominant_source": dominant_source,
        "dominant_source_count": dominant_source_count,
        "dominant_source_rate": _rate(dominant_source_count, len(rows)),
        "all_top1_family_empty": all(not _clean(row.get("top1_family")) for row in rows),
        "primary_issue_counts": dict(issue_counts.most_common()),
        "next_action_counts": dict(action_counts.most_common()),
        "book_relation_counts": dict(book_counts.most_common()),
        "taxonomy_signal_counts": dict(taxonomy_counts.most_common()),
        "source_shape_counts": dict(source_shape_counts.most_common()),
    }


def _dev_decomposition_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    dev = next(item for item in summary["splits"] if item["split"] == "dev")
    return {
        "dev_groups": dev["groups"],
        "dev_baseline_top1_hit": dev["baseline_top1_hit"],
        "dev_baseline_top1_rate": dev["baseline_top1_rate"],
        "dev_top80_missing": dev["top80_missing"],
        "dev_top80_missing_rate": dev["top80_missing_rate"],
        "dev_wrong_rank": dev["top80_present_but_wrong_rank"],
        "dev_wrong_rank_rate": dev["top80_present_but_wrong_rank_rate"],
        "dev_top80_recall_rate": dev["top80_recall_rate"],
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
    family_reason_buckets: list[dict[str, Any]],
    source_buckets: list[dict[str, Any]],
    province_buckets: list[dict[str, Any]],
    issue_buckets: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]["recall_missing_overview"]
    lines = [
        "# Stage 9.28 Recall-missing Decomposition Kickoff",
        "",
        "Read-only decomposition of dev `top80_missing` rows. This starts from reason/query_family/source/province buckets and separates true recall-failure candidates from empty taxonomy labels and source-dominated artifacts. It does not train, tune, patch rules, change ranking, use heldout for selection, or modify GoalSearcher.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["dev_top80_missing_rows", metrics["dev_top80_missing_rows"]],
                ["reason_top1_family_empty", metrics["reason_counts"].get("top1_family_empty", 0)],
                ["reason_query_family_empty", metrics["reason_counts"].get("query_family_empty", 0)],
                ["reason_top1_wrong_book", metrics["reason_counts"].get("top1_wrong_book", 0)],
                ["all_top1_family_empty", metrics["all_top1_family_empty"]],
                ["dominant_source", metrics["dominant_source"]],
                ["dominant_source_rate", metrics["dominant_source_rate"]],
            ]
        ),
        "",
        "## Issue Split",
        "",
        _md_table(
            [["issue", "next_action", "count", "sources", "dom_source_rate", "top_family", "top_reason", "examples"]]
            + [
                [
                    row["primary_issue"],
                    row["next_action"],
                    row["count"],
                    row["source_count"],
                    row["dominant_source_rate"],
                    row["top_query_family"],
                    row["top_reason"],
                    row["example_queries"],
                ]
                for row in issue_buckets
            ]
        ),
        "",
        "## Top Reason + Family Buckets",
        "",
        _md_table(
            [["bucket", "count", "provinces", "sources", "dom_source_rate", "shape", "top_issue", "next_action", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["province_count"],
                    row["source_count"],
                    row["dominant_source_rate"],
                    row["audit_bucket_shape"],
                    row["top_primary_issue"],
                    row["top_next_action"],
                    row["example_queries"],
                ]
                for row in family_reason_buckets[:12]
            ]
        ),
        "",
        "## Source Buckets",
        "",
        _md_table(
            [["source", "count", "provinces", "top_family", "top_reason", "top_issue", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["province_count"],
                    row["top_query_family"],
                    row["top_reason"],
                    row["top_primary_issue"],
                    row["example_queries"],
                ]
                for row in source_buckets
            ]
        ),
        "",
        "## Province Buckets",
        "",
        _md_table(
            [["province", "count", "sources", "top_family", "top_reason", "top_issue", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["source_count"],
                    row["top_query_family"],
                    row["top_reason"],
                    row["top_primary_issue"],
                    row["example_queries"],
                ]
                for row in province_buckets[:12]
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
    parser = argparse.ArgumentParser(description="Stage 9.28 recall-missing decomposition kickoff")
    parser.add_argument("--top80-missing", default=str(DEFAULT_TOP80_MISSING))
    parser.add_argument("--decomposition-summary", default=str(DEFAULT_DECOMPOSITION_SUMMARY))
    parser.add_argument("--stage-9-27-summary", default=str(DEFAULT_STAGE_9_27_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    top80_missing = [
        row
        for row in _read_csv(Path(args.top80_missing))
        if _clean(row.get("split")) == "dev" and _clean(row.get("status")) == "top80_missing"
    ]
    decomposition_summary = _read_json(Path(args.decomposition_summary))
    stage_9_27_summary = _read_json(Path(args.stage_9_27_summary))

    annotated_rows = _annotate_rows(top80_missing)
    family_reason_buckets = _bucket_rows(annotated_rows, ("normalized_query_family", "reason"), "query_family_reason")
    reason_buckets = _bucket_rows(annotated_rows, ("reason",), "reason")
    source_buckets = _bucket_rows(annotated_rows, ("source_file",), "source")
    province_buckets = _bucket_rows(annotated_rows, ("province",), "province")
    issue_buckets = _issue_buckets(annotated_rows)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "family_reason_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_family_reason_buckets.csv")),
        "reason_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_reason_buckets.csv")),
        "source_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_source_buckets.csv")),
        "province_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_province_buckets.csv")),
        "issue_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_issue_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.28 recall-missing decomposition kickoff",
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
            "top80_missing_rows": str(Path(args.top80_missing)),
            "decomposition_summary": str(Path(args.decomposition_summary)),
            "stage_9_27_summary": str(Path(args.stage_9_27_summary)),
        },
        "metrics": {
            "dev_decomposition": _dev_decomposition_metrics(decomposition_summary),
            "stage_9_27_decision": stage_9_27_summary.get("decision", ""),
            "recall_missing_overview": _overview(top80_missing, annotated_rows),
        },
        "top_issue_buckets": issue_buckets,
        "top_family_reason_buckets": family_reason_buckets[:16],
        "top_source_buckets": source_buckets,
        "top_province_buckets": province_buckets[:16],
        "decision": (
            "Do not start recall learning from these rows yet. The kickoff decomposition shows that every dev top80_missing row has "
            "an empty top1_family, and the lane is heavily dominated by global_repair_decision_table.csv. Treat the immediate next step "
            "as a focused read-only audit of high-support, nominally diverse family/reason buckets, beginning with query_family_empty and "
            "pipe/top1_family_empty, while keeping single-source and source-dominated slices out of transferable learning."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.28 only decomposes dev top80_missing rows. It does not train, tune, patch rules, change ranking, modify GoalSearcher, "
            "use heldout for selection, connect online, or relax the wrong-rank gate."
        ),
        "next_stage": {
            "stage": "9.29 recall-missing high-support bucket audit",
            "goal": (
                "Read-only audit the largest recall-missing buckets from 9.28, prioritizing <empty> + query_family_empty and "
                "pipe + top1_family_empty, to separate true missing-recall patterns from taxonomy-empty labels and source-dominated artifacts."
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
        "normalized_query_family",
        "book_relation",
        "taxonomy_signal",
        "bucket_count",
        "bucket_province_count",
        "bucket_source_count",
        "bucket_dominant_source",
        "bucket_dominant_source_rate",
        "source_shape",
        "audit_bucket_shape",
        "primary_issue",
        "primary_explanation",
        "next_action",
        "split",
        "status",
        "reason",
        "rank_bucket",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_local_ids",
        "expected_not_local_ids",
        "expected_families",
        "expected_books",
        "expected_names",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_unit",
        "top1_score",
        "top1_reasons",
        "top80_rows",
    ]
    bucket_fields = [
        "bucket_type",
        "bucket_key",
        "count",
        "rate_within_dev_top80_missing",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "source_shape",
        "audit_bucket_shape",
        "top_reason",
        "top_reason_count",
        "top_query_family",
        "top_query_family_count",
        "top_primary_issue",
        "top_primary_issue_count",
        "top_next_action",
        "top_next_action_count",
        "book_relation_counts",
        "taxonomy_signal_counts",
        "example_queries",
    ]
    issue_fields = [
        "primary_issue",
        "next_action",
        "count",
        "rate_within_dev_top80_missing",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "top_query_family",
        "top_query_family_count",
        "top_reason",
        "top_reason_count",
        "example_queries",
    ]
    _write_csv(Path(artifacts["rows_csv"]), annotated_rows, row_fields)
    _write_csv(Path(artifacts["family_reason_buckets_csv"]), family_reason_buckets, bucket_fields)
    _write_csv(Path(artifacts["reason_buckets_csv"]), reason_buckets, bucket_fields)
    _write_csv(Path(artifacts["source_buckets_csv"]), source_buckets, bucket_fields)
    _write_csv(Path(artifacts["province_buckets_csv"]), province_buckets, bucket_fields)
    _write_csv(Path(artifacts["issue_buckets_csv"]), issue_buckets, issue_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(
        Path(artifacts["summary_md"]),
        report,
        family_reason_buckets,
        source_buckets,
        province_buckets,
        issue_buckets,
    )

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": report["metrics"]["recall_missing_overview"],
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
