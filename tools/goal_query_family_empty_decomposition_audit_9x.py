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
DEFAULT_EMPTY_ROWS = AGENT_STATE / "goal_no_eligible_gate_review_9x_query_family_empty_rows.csv"
DEFAULT_EMPTY_DECOMPOSITION = AGENT_STATE / "goal_no_eligible_gate_review_9x_query_family_empty_decomposition.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_query_family_empty_decomposition_9x_audit"

MIN_SUPPORT = 20
MIN_PROVINCES = 3
MIN_SOURCES = 2

ALLOWED_TOP1_FAMILIES_BY_SUBBUCKET = {
    "electrical_lighting": {"electrical_box", "lamp", "cable", "switch"},
    "fire_sanitary_water": {"pipe", "sanitary", "valve", "instrument", "support"},
    "hvac_duct_fan_noise": {"duct", "fan", "pipe", "valve", "support"},
    "top1_family_pipe": {"pipe"},
    "top1_family_instrument": {"instrument"},
    "weak_current_display_broadcast": {"cable", "electrical_box", "instrument", "switch"},
}

LABEL_MISMATCH_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...], str]] = [
    ("water_meter_expected_valve_or_sanitary", ("水表",), ("阀", "隔油器"), "水表 query 被阀/隔油器 expected/top1 吸走"),
    ("soft_joint_expected_valve", ("软接头", "橡胶接头", "可曲挠"), ("法兰阀", "阀安装"), "软接头 query 的 positive 是阀/法兰阀"),
    ("planting_expected_earthwork_transport", ("栽植乔木", "栽植灌木", "栽植色带"), ("淤泥", "流砂", "运输", "材料上山"), "栽植 query 的 positive/top1 混入运输或土石方"),
    ("cover_plate_expected_pipe", ("沟盖板",), ("塑料管", "分水栓"), "沟盖板 query 的 positive/top1 混入管道/给水"),
    ("fuse_top1_pipe", ("熔断器",), ("pipe", "管式"), "熔断器 query 被 pipe family/top1 吸走"),
    ("level_gauge_top1_equalizing_ring", ("水位标尺",), ("均压环",), "水位标尺 query 被均压环 top1 吸走"),
    ("display_processor_measurement", ("LED屏", "处理器"), ("亮度均匀性", "测量指标"), "显示处理器 query 被测量项吸走"),
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _top1_book_relation(row: dict[str, Any]) -> str:
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


def _detect_label_mismatch(row: dict[str, Any]) -> tuple[str, str]:
    query = _clean(row.get("query"))
    evidence = " ".join(
        [
            _clean(row.get("positive_names_in_top80")),
            _clean(row.get("top1_name")),
            _clean(row.get("top1_family")),
        ]
    )
    for issue, query_terms, evidence_terms, explanation in LABEL_MISMATCH_RULES:
        if any(term in query for term in query_terms) and any(term in evidence for term in evidence_terms):
            return issue, explanation
    return "", ""


def _family_relation(row: dict[str, Any]) -> str:
    subbucket = _clean(row.get("inferred_empty_subbucket"))
    top1_family = _clean(row.get("top1_family"))
    if not _clean(row.get("query_family")) and not top1_family:
        return "query_family_empty_top1_family_empty"
    if not _clean(row.get("query_family")) and top1_family:
        allowed = ALLOWED_TOP1_FAMILIES_BY_SUBBUCKET.get(subbucket)
        if allowed is None:
            return "query_family_empty_top1_family_present_unmapped"
        if top1_family in allowed:
            return "query_family_empty_top1_family_coherent"
        return "query_family_empty_top1_family_conflict"
    return "unexpected_non_empty_query_family"


def _rank_depth(row: dict[str, Any]) -> str:
    rank = _to_int(row.get("positive_rank_min"))
    if rank <= 0:
        return "rank_unknown"
    if rank <= 5:
        return "near_miss_rank_2_5"
    if rank <= 10:
        return "rank_6_10"
    if rank <= 20:
        return "rank_11_20"
    if rank <= 40:
        return "rank_21_40"
    return "rank_41_80"


def _source_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_clean(row.get("inferred_empty_subbucket"))].append(row)
    stats: dict[str, dict[str, Any]] = {}
    for bucket, items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        dominant_source, dominant_count = sources.most_common(1)[0] if sources else ("", 0)
        stats[bucket] = {
            "count": len(items),
            "source_count": len(sources),
            "province_count": len(provinces),
            "dominant_source": dominant_source,
            "dominant_source_count": dominant_count,
            "dominant_source_rate": _rate(dominant_count, len(items)),
        }
    return stats


def _source_pattern(row: dict[str, Any], stats: dict[str, Any]) -> str:
    source_file = _clean(row.get("source_file"))
    if stats["source_count"] <= 1:
        return "single_source_subbucket"
    if stats["dominant_source_rate"] >= 0.9 and source_file == stats["dominant_source"]:
        return "dominant_source_ge_90pct"
    if stats["dominant_source_rate"] >= 0.8 and source_file == stats["dominant_source"]:
        return "dominant_source_ge_80pct"
    return "source_diverse_row"


def _primary_issue(row: dict[str, Any], stats: dict[str, Any]) -> tuple[str, str]:
    label_issue, label_explanation = _detect_label_mismatch(row)
    family_relation = _family_relation(row)
    book_relation = _top1_book_relation(row)
    source_pattern = _source_pattern(row, stats)
    rank_depth = _rank_depth(row)

    if label_issue:
        return "label_or_expected_mismatch", label_explanation
    if book_relation == "wrong_book":
        return "true_cross_domain_or_wrong_book_rank_gap", "top1_book 与 expected_books 不一致"
    if family_relation == "query_family_empty_top1_family_conflict":
        return "top1_family_absorption_conflict", "query_family 为空但 top1_family 指向不相容族"
    if source_pattern in {"single_source_subbucket", "dominant_source_ge_90pct"} and family_relation == "query_family_empty_top1_family_empty":
        return "source_dominated_taxonomy_empty", "子桶强单源且 query/top1 family 均为空"
    if family_relation == "query_family_empty_top1_family_empty" and book_relation in {"same_book", "expected_book_empty", "both_books_empty"}:
        return "taxonomy_empty_same_domain_rank_gap", "同书册或缺书册信息下的空 family 错排"
    if family_relation == "query_family_empty_top1_family_coherent" and rank_depth in {"near_miss_rank_2_5", "rank_6_10"}:
        return "taxonomy_missing_but_near_miss", "query_family 为空但 top1_family 可提供族线索，positive 排名较近"
    if family_relation == "query_family_empty_top1_family_present_unmapped":
        return "taxonomy_missing_with_top1_family_hint", "query_family 为空但 top1_family 有线索"
    return "manual_review_empty_family", "空 family 混合现象，需要人工小样本复核"


def _learning_status(row: dict[str, Any]) -> str:
    primary_issue = _clean(row.get("primary_issue"))
    source_pattern = _clean(row.get("source_pattern"))
    if primary_issue == "label_or_expected_mismatch":
        return "exclude_label_or_expected_review"
    if primary_issue == "source_dominated_taxonomy_empty":
        return "exclude_source_dominated_taxonomy_backlog"
    if source_pattern in {"single_source_subbucket", "dominant_source_ge_90pct"}:
        return "blocked_by_source_dominance"
    if primary_issue in {"true_cross_domain_or_wrong_book_rank_gap", "taxonomy_empty_same_domain_rank_gap", "taxonomy_missing_but_near_miss"}:
        return "taxonomy_audit_candidate_not_rank_rule"
    return "manual_review_before_learning"


def _annotate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats = _source_stats(rows)
    annotated: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        bucket_stats = stats[_clean(row.get("inferred_empty_subbucket"))]
        out["top1_book_relation"] = _top1_book_relation(row)
        out["family_relation"] = _family_relation(row)
        out["rank_depth"] = _rank_depth(row)
        out["source_pattern"] = _source_pattern(row, bucket_stats)
        label_issue, label_explanation = _detect_label_mismatch(row)
        out["label_mismatch_rule"] = label_issue
        out["label_mismatch_explanation"] = label_explanation
        primary_issue, primary_explanation = _primary_issue(row, bucket_stats)
        out["primary_issue"] = primary_issue
        out["primary_explanation"] = primary_explanation
        out["learning_status"] = _learning_status(out)
        out["subbucket_source_count"] = bucket_stats["source_count"]
        out["subbucket_dominant_source"] = bucket_stats["dominant_source"]
        out["subbucket_dominant_source_rate"] = bucket_stats["dominant_source_rate"]
        annotated.append(out)
    return annotated


def _summarize_subbuckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_clean(row.get("inferred_empty_subbucket"))].append(row)
    out: list[dict[str, Any]] = []
    total = len(rows)
    for bucket, items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        primary = Counter(_clean(row.get("primary_issue")) for row in items)
        statuses = Counter(_clean(row.get("learning_status")) for row in items)
        family_relations = Counter(_clean(row.get("family_relation")) for row in items)
        book_relations = Counter(_clean(row.get("top1_book_relation")) for row in items)
        dominant_source, dominant_count = sources.most_common(1)[0] if sources else ("", 0)
        count = len(items)
        gate_shape = count >= MIN_SUPPORT and len(provinces) >= MIN_PROVINCES and len(sources) >= MIN_SOURCES
        source_dominated_rows = sum(
            1 for row in items if _clean(row.get("source_pattern")) in {"single_source_subbucket", "dominant_source_ge_90pct", "dominant_source_ge_80pct"}
        )
        taxonomy_empty_rows = sum(1 for row in items if _clean(row.get("family_relation")) == "query_family_empty_top1_family_empty")
        label_rows = sum(1 for row in items if _clean(row.get("label_mismatch_rule")))
        wrong_book_rows = sum(1 for row in items if _clean(row.get("top1_book_relation")) == "wrong_book")
        recommendation = _subbucket_recommendation(
            count=count,
            gate_shape=gate_shape,
            source_count=len(sources),
            dominant_source_rate=_rate(dominant_count, count),
            taxonomy_empty_rows=taxonomy_empty_rows,
            label_rows=label_rows,
            wrong_book_rows=wrong_book_rows,
            top_status=statuses.most_common(1)[0][0] if statuses else "",
        )
        out.append(
            {
                "inferred_empty_subbucket": bucket,
                "count": count,
                "rate_within_query_family_empty": _rate(count, total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": dominant_source,
                "dominant_source_count": dominant_count,
                "dominant_source_rate": _rate(dominant_count, count),
                "source_dominated_rows": source_dominated_rows,
                "taxonomy_empty_rows": taxonomy_empty_rows,
                "top1_family_present_rows": count - taxonomy_empty_rows,
                "label_or_expected_mismatch_rows": label_rows,
                "wrong_book_rows": wrong_book_rows,
                "same_book_rows": sum(1 for row in items if _clean(row.get("top1_book_relation")) == "same_book"),
                "gate_shape_if_split": "passes_support_province_source" if gate_shape else "does_not_pass_support_province_source",
                "top_primary_issue": primary.most_common(1)[0][0] if primary else "",
                "top_primary_issue_count": primary.most_common(1)[0][1] if primary else 0,
                "top_learning_status": statuses.most_common(1)[0][0] if statuses else "",
                "top_learning_status_count": statuses.most_common(1)[0][1] if statuses else 0,
                "family_relation_counts": " | ".join(f"{name}:{value}" for name, value in family_relations.most_common()),
                "book_relation_counts": " | ".join(f"{name}:{value}" for name, value in book_relations.most_common()),
                "top_queries": " | ".join(query for query, _ in queries.most_common(8)),
                "recommendation": recommendation,
            }
        )
    out.sort(key=lambda row: int(row["count"]), reverse=True)
    return out


def _subbucket_recommendation(
    *,
    count: int,
    gate_shape: bool,
    source_count: int,
    dominant_source_rate: float,
    taxonomy_empty_rows: int,
    label_rows: int,
    wrong_book_rows: int,
    top_status: str,
) -> str:
    if source_count == 1 or dominant_source_rate >= 0.9:
        return "stop_as_rank_bucket_source_dominated; keep as taxonomy/data-quality backlog"
    if label_rows >= max(2, count // 4):
        return "stop_as_rank_bucket_label_or_expected_review_first"
    if gate_shape and taxonomy_empty_rows >= count // 2:
        return "audit_taxonomy_coverage_before_any_rank_learning"
    if gate_shape and wrong_book_rows >= max(2, count // 3):
        return "possible_cross_domain_gap_but_needs_label_and_source_review"
    if top_status == "taxonomy_audit_candidate_not_rank_rule":
        return "taxonomy_audit_candidate_not_rank_rule"
    return "manual_review_before_reselection"


def _issue_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                _clean(row.get("primary_issue")),
                _clean(row.get("learning_status")),
                _clean(row.get("inferred_empty_subbucket")),
            )
        ].append(row)
    out: list[dict[str, Any]] = []
    total = len(rows)
    for (issue, status, subbucket), items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        out.append(
            {
                "primary_issue": issue,
                "learning_status": status,
                "inferred_empty_subbucket": subbucket,
                "count": len(items),
                "rate_within_query_family_empty": _rate(len(items), total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "top_source": sources.most_common(1)[0][0] if sources else "",
                "top_source_count": sources.most_common(1)[0][1] if sources else 0,
                "top_queries": " | ".join(query for query, _ in queries.most_common(6)),
            }
        )
    out.sort(key=lambda row: (int(row["count"]), row["primary_issue"]), reverse=True)
    return out


def _summary_metrics(rows: list[dict[str, Any]], subbuckets: list[dict[str, Any]]) -> dict[str, Any]:
    status_counter = Counter(_clean(row.get("learning_status")) for row in rows)
    issue_counter = Counter(_clean(row.get("primary_issue")) for row in rows)
    source_counter = Counter(_clean(row.get("source_pattern")) for row in rows)
    family_counter = Counter(_clean(row.get("family_relation")) for row in rows)
    book_counter = Counter(_clean(row.get("top1_book_relation")) for row in rows)
    return {
        "query_family_empty_rows": len(rows),
        "subbucket_count": len(subbuckets),
        "subbuckets_passing_support_province_source_shape": sum(
            1 for row in subbuckets if row["gate_shape_if_split"] == "passes_support_province_source"
        ),
        "single_source_subbuckets": sum(1 for row in subbuckets if int(row["source_count"]) == 1),
        "source_dominated_subbuckets_ge_90pct": sum(1 for row in subbuckets if float(row["dominant_source_rate"]) >= 0.9),
        "top1_family_empty_rows": family_counter.get("query_family_empty_top1_family_empty", 0),
        "top1_family_present_rows": len(rows) - family_counter.get("query_family_empty_top1_family_empty", 0),
        "wrong_book_rows": book_counter.get("wrong_book", 0),
        "same_book_rows": book_counter.get("same_book", 0),
        "expected_book_empty_rows": book_counter.get("expected_book_empty", 0) + book_counter.get("both_books_empty", 0),
        "label_or_expected_mismatch_rows": issue_counter.get("label_or_expected_mismatch", 0),
        "source_dominated_rows": sum(
            source_counter.get(name, 0)
            for name in ("single_source_subbucket", "dominant_source_ge_90pct", "dominant_source_ge_80pct")
        ),
        "learning_status_counts": dict(status_counter.most_common()),
        "primary_issue_counts": dict(issue_counter.most_common()),
        "source_pattern_counts": dict(source_counter.most_common()),
        "family_relation_counts": dict(family_counter.most_common()),
        "top1_book_relation_counts": dict(book_counter.most_common()),
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


def _write_markdown(path: Path, report: dict[str, Any], subbuckets: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 9.25 Query-family-empty Decomposition Audit",
        "",
        "Read-only audit of the blocked `<empty> + query_family_empty` bucket. This separates source dominance, empty taxonomy, wrong-book/cross-domain ranking gaps, and label/expected mismatches without training, tuning, rule patches, ranking changes, heldout selection, or GoalSearcher changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["query_family_empty_rows", metrics["query_family_empty_rows"]],
                ["subbucket_count", metrics["subbucket_count"]],
                ["subbuckets_passing_support_province_source_shape", metrics["subbuckets_passing_support_province_source_shape"]],
                ["single_source_subbuckets", metrics["single_source_subbuckets"]],
                ["source_dominated_subbuckets_ge_90pct", metrics["source_dominated_subbuckets_ge_90pct"]],
                ["source_dominated_rows", metrics["source_dominated_rows"]],
                ["top1_family_empty_rows", metrics["top1_family_empty_rows"]],
                ["top1_family_present_rows", metrics["top1_family_present_rows"]],
                ["wrong_book_rows", metrics["wrong_book_rows"]],
                ["label_or_expected_mismatch_rows", metrics["label_or_expected_mismatch_rows"]],
            ]
        ),
        "",
        "## Subbucket Audit",
        "",
        _md_table(
            [["subbucket", "count", "sources", "dom_source_rate", "taxonomy_empty", "label_mismatch", "wrong_book", "top_status", "recommendation"]]
            + [
                [
                    row["inferred_empty_subbucket"],
                    row["count"],
                    row["source_count"],
                    row["dominant_source_rate"],
                    row["taxonomy_empty_rows"],
                    row["label_or_expected_mismatch_rows"],
                    row["wrong_book_rows"],
                    row["top_learning_status"],
                    row["recommendation"],
                ]
                for row in subbuckets
            ]
        ),
        "",
        "## Largest Issue Buckets",
        "",
        _md_table(
            [["issue", "status", "subbucket", "count", "sources", "top_queries"]]
            + [
                [
                    row["primary_issue"],
                    row["learning_status"],
                    row["inferred_empty_subbucket"],
                    row["count"],
                    row["source_count"],
                    row["top_queries"],
                ]
                for row in issues[:14]
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
    parser = argparse.ArgumentParser(description="Stage 9.25 query_family_empty decomposition audit")
    parser.add_argument("--empty-rows", default=str(DEFAULT_EMPTY_ROWS))
    parser.add_argument("--empty-decomposition", default=str(DEFAULT_EMPTY_DECOMPOSITION))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.empty_rows))
    annotated_rows = _annotate_rows(source_rows)
    subbuckets = _summarize_subbuckets(annotated_rows)
    issue_buckets = _issue_buckets(annotated_rows)

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "subbuckets_csv": str(output_prefix.with_name(output_prefix.name + "_subbuckets.csv")),
        "issue_buckets_csv": str(output_prefix.with_name(output_prefix.name + "_issue_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.25 query_family_empty decomposition audit",
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
            "query_family_empty_rows": str(Path(args.empty_rows)),
            "query_family_empty_decomposition": str(Path(args.empty_decomposition)),
        },
        "metrics": _summary_metrics(annotated_rows, subbuckets),
        "top_subbuckets": subbuckets[:10],
        "top_issue_buckets": issue_buckets[:12],
        "decision": (
            "Stop treating <empty> + query_family_empty as a rank-learning bucket. The audit shows a taxonomy/data-quality backlog dominated by "
            "empty family labels and source concentration: most large subbuckets are single-source or >=90% dominated by global_repair_decision_table.csv. "
            "The few diversified subbuckets still need taxonomy/label review before any learning; do not relax the gate or write ranking rules from this bucket."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.25 only audits the already-blocked dev query_family_empty rows. It does not train, tune, change ranking, patch rules, "
            "modify GoalSearcher, use heldout for selection, or connect online."
        ),
        "next_stage": {
            "stage": "9.26 ranked gap reselection after query_family_empty audit",
            "goal": (
                "Return to the dev wrong-rank table, exclude the audited blocked query_family_empty rows, and verify whether any remaining high-support "
                "bucket passes the existing support/province/source gate."
            ),
            "prohibited": [
                "training",
                "tuning",
                "rule patches",
                "GoalSearcher changes",
                "heldout threshold selection",
                "online integration",
                "gate relaxation without explicit review",
            ],
        },
    }

    row_fields = [
        "inferred_empty_subbucket",
        "matched_hint",
        "top1_book_relation",
        "family_relation",
        "rank_depth",
        "source_pattern",
        "label_mismatch_rule",
        "label_mismatch_explanation",
        "primary_issue",
        "primary_explanation",
        "learning_status",
        "subbucket_source_count",
        "subbucket_dominant_source",
        "subbucket_dominant_source_rate",
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
        "expected_books",
        "positive_ids_in_top80",
        "positive_names_in_top80",
        "positive_ranks",
        "positive_rank_min",
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
    subbucket_fields = [
        "inferred_empty_subbucket",
        "count",
        "rate_within_query_family_empty",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "source_dominated_rows",
        "taxonomy_empty_rows",
        "top1_family_present_rows",
        "label_or_expected_mismatch_rows",
        "wrong_book_rows",
        "same_book_rows",
        "gate_shape_if_split",
        "top_primary_issue",
        "top_primary_issue_count",
        "top_learning_status",
        "top_learning_status_count",
        "family_relation_counts",
        "book_relation_counts",
        "top_queries",
        "recommendation",
    ]
    issue_fields = [
        "primary_issue",
        "learning_status",
        "inferred_empty_subbucket",
        "count",
        "rate_within_query_family_empty",
        "province_count",
        "source_count",
        "top_source",
        "top_source_count",
        "top_queries",
    ]
    _write_csv(Path(artifacts["rows_csv"]), annotated_rows, row_fields)
    _write_csv(Path(artifacts["subbuckets_csv"]), subbuckets, subbucket_fields)
    _write_csv(Path(artifacts["issue_buckets_csv"]), issue_buckets, issue_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, subbuckets, issue_buckets)

    print(
        json.dumps(
            {
                "summary": artifacts["summary_json"],
                "metrics": report["metrics"],
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
