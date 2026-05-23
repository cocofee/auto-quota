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
DEFAULT_KICKOFF_ROWS = AGENT_STATE / "goal_recall_missing_decomposition_9x_kickoff_rows.csv"
DEFAULT_KICKOFF_SUMMARY = AGENT_STATE / "goal_recall_missing_decomposition_9x_kickoff_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_recall_missing_high_support_bucket_9x_audit"

TARGET_BUCKETS = {
    ("<empty>", "query_family_empty"): "empty_query_family_missing",
    ("pipe", "top1_family_empty"): "pipe_top1_family_empty",
    ("valve", "top1_family_empty"): "valve_taxonomy_reference",
}
DOMINANT_SOURCE = "global_repair_decision_table.csv"

DOMAIN_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("valve", ("阀", "阀门", "蝶阀", "闸阀", "止回阀", "减压阀", "插板阀", "呼吸阀", "安全阀", "通气阀")),
    ("pipe", ("管道", "管件", "钢管", "塑料管", "无缝钢管", "碳钢管", "不锈钢管", "给水管", "水管", "套管", "管道防腐", "管道绝热")),
    ("lamp", ("灯", "照明", "路灯", "灯具", "疏散指示")),
    ("weak_current", ("扩声", "视频", "监控", "广播", "服务器", "网络", "信号线", "音频", "有线电视")),
    ("civil_earthwork", ("拆除", "基层", "垫层", "填方", "余方", "路面", "铣刨", "场地", "安砌", "园路", "桥检车")),
    ("civil_well_concrete", ("砌筑井", "混凝土井", "检查井", "井盖", "沟盖板", "混凝土", "泄水孔", "水泥")),
    ("decoration", ("装饰", "铝板", "玻璃", "前台", "瓷砖", "背景墙", "栏杆", "扶手", "门带套", "坐凳", "石座凳", "水磨石")),
    ("instrument", ("仪表", "流量", "指示器", "变送器", "测量", "水表")),
    ("hvac_fan_duct", ("风机", "风管", "空调", "通风", "防火阀", "排烟")),
    ("sanitary_water", ("水箱", "淋浴器", "小便", "坐便", "卫生间")),
    ("electrical", ("浪涌", "接地", "开关", "电源", "配线", "电缆", "蓄电池")),
]


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


def _infer_domain(text: str) -> str:
    text = _clean(text)
    if not text:
        return "empty_text"
    for domain, hints in DOMAIN_HINTS:
        if any(hint in text for hint in hints):
            return domain
    return "unknown"


def _target_key(row: dict[str, Any]) -> tuple[str, str]:
    return _clean(row.get("normalized_query_family")) or "<empty>", _clean(row.get("reason")) or "<empty>"


def _source_slice(row: dict[str, Any]) -> str:
    source = _clean(row.get("source_file"))
    if source == DOMINANT_SOURCE:
        return "dominant_global_repair_source"
    return "non_global_repair_source"


def _semantic_relation(row: dict[str, Any], query_domain: str, top1_domain: str) -> str:
    target = TARGET_BUCKETS[_target_key(row)]
    book_relation = _clean(row.get("book_relation"))
    if target == "empty_query_family_missing":
        if query_domain == "unknown":
            return "query_family_empty_unknown_query_semantics"
        if query_domain == top1_domain and book_relation == "same_book":
            return "query_family_empty_same_domain_same_book"
        if book_relation in {"both_books_empty", "top1_book_empty"}:
            return "query_family_empty_book_label_empty"
        if book_relation == "wrong_book":
            return "query_family_empty_wrong_book_candidate"
        return "query_family_empty_cross_or_ambiguous_domain"
    if target == "pipe_top1_family_empty":
        if top1_domain == "pipe" and book_relation == "same_book":
            return "pipe_same_domain_top1_taxonomy_empty"
        if top1_domain == "pipe" and book_relation == "both_books_empty":
            return "pipe_same_domain_book_label_empty"
        if top1_domain != "pipe":
            return "pipe_query_top1_non_pipe_absorption"
        return "pipe_same_domain_ambiguous"
    if target == "valve_taxonomy_reference":
        if top1_domain == "valve" and book_relation == "same_book":
            return "valve_same_domain_top1_taxonomy_empty"
        if query_domain != "valve" and top1_domain == "valve":
            return "valve_family_overbroad_query_label_issue"
        if book_relation == "both_books_empty":
            return "valve_book_label_empty"
        return "valve_taxonomy_ambiguous"
    return "unknown_relation"


def _audit_class(row: dict[str, Any], semantic_relation: str) -> tuple[str, str]:
    target = TARGET_BUCKETS[_target_key(row)]
    source_slice = _source_slice(row)
    book_relation = _clean(row.get("book_relation"))
    if source_slice == "dominant_global_repair_source" and target in {"empty_query_family_missing", "pipe_top1_family_empty"}:
        return "source_dominated_artifact", "do_not_learn_until_source_provenance_review"
    if target == "empty_query_family_missing":
        if book_relation in {"both_books_empty", "top1_book_empty"}:
            return "taxonomy_empty_label_backlog", "taxonomy_and_label_coverage_review"
        if semantic_relation == "query_family_empty_wrong_book_candidate":
            return "possible_true_recall_but_query_taxonomy_empty", "manual_review_after_taxonomy_fill"
        return "query_taxonomy_empty_not_rank_learning", "taxonomy_and_label_coverage_review"
    if target == "pipe_top1_family_empty":
        if semantic_relation.startswith("pipe_same_domain"):
            return "top1_taxonomy_empty_same_domain", "top1_family_coverage_review"
        return "possible_true_recall_or_family_absorption", "manual_review_before_recall_learning"
    if target == "valve_taxonomy_reference":
        if semantic_relation == "valve_same_domain_top1_taxonomy_empty":
            return "taxonomy_reference_same_domain", "top1_family_coverage_review"
        if semantic_relation == "valve_family_overbroad_query_label_issue":
            return "taxonomy_reference_label_mixture", "query_family_label_review"
        return "taxonomy_reference_book_label_empty", "taxonomy_and_label_coverage_review"
    return "manual_review", "manual_review"


def _annotate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        key = _target_key(row)
        if key not in TARGET_BUCKETS:
            continue
        query_domain = _infer_domain(_clean(row.get("query")))
        top1_domain = _infer_domain(_clean(row.get("top1_name")))
        relation = _semantic_relation(row, query_domain, top1_domain)
        audit_class, recommendation = _audit_class(row, relation)
        out = dict(row)
        out["target_bucket"] = TARGET_BUCKETS[key]
        out["query_domain"] = query_domain
        out["top1_domain"] = top1_domain
        out["semantic_relation"] = relation
        out["source_slice"] = _source_slice(row)
        out["stage_9_29_audit_class"] = audit_class
        out["stage_9_29_recommendation"] = recommendation
        selected.append(out)
    return selected


def _summarize_group(rows: list[dict[str, Any]], key_fields: tuple[str, ...], bucket_type: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(_clean(row.get(field)) or "<empty>" for field in key_fields)].append(row)
    out: list[dict[str, Any]] = []
    total = len(rows)
    for key, items in grouped.items():
        sources = Counter(_clean(row.get("source_file")) for row in items if _clean(row.get("source_file")))
        provinces = Counter(_clean(row.get("province")) for row in items if _clean(row.get("province")))
        audit_classes = Counter(_clean(row.get("stage_9_29_audit_class")) for row in items)
        recommendations = Counter(_clean(row.get("stage_9_29_recommendation")) for row in items)
        book_relations = Counter(_clean(row.get("book_relation")) for row in items)
        query_domains = Counter(_clean(row.get("query_domain")) for row in items)
        top1_domains = Counter(_clean(row.get("top1_domain")) for row in items)
        semantic_relations = Counter(_clean(row.get("semantic_relation")) for row in items)
        source_slices = Counter(_clean(row.get("source_slice")) for row in items)
        queries = Counter(_clean(row.get("query")) for row in items if _clean(row.get("query")))
        dominant_source, dominant_count = sources.most_common(1)[0] if sources else ("", 0)
        out.append(
            {
                "bucket_type": bucket_type,
                "bucket_key": " + ".join(key),
                "count": len(items),
                "rate_within_stage_9_29_targets": _rate(len(items), total),
                "province_count": len(provinces),
                "source_count": len(sources),
                "dominant_source": dominant_source,
                "dominant_source_count": dominant_count,
                "dominant_source_rate": _rate(dominant_count, len(items)),
                "top_audit_class": audit_classes.most_common(1)[0][0] if audit_classes else "",
                "top_audit_class_count": audit_classes.most_common(1)[0][1] if audit_classes else 0,
                "top_recommendation": recommendations.most_common(1)[0][0] if recommendations else "",
                "top_recommendation_count": recommendations.most_common(1)[0][1] if recommendations else 0,
                "source_slice_counts": " | ".join(f"{name}:{value}" for name, value in source_slices.most_common()),
                "book_relation_counts": " | ".join(f"{name}:{value}" for name, value in book_relations.most_common()),
                "query_domain_counts": " | ".join(f"{name}:{value}" for name, value in query_domains.most_common()),
                "top1_domain_counts": " | ".join(f"{name}:{value}" for name, value in top1_domains.most_common()),
                "semantic_relation_counts": " | ".join(f"{name}:{value}" for name, value in semantic_relations.most_common()),
                "example_queries": " | ".join(query for query, _ in queries.most_common(8)),
            }
        )
    out.sort(key=lambda row: int(row["count"]), reverse=True)
    return out


def _overview(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_slices = Counter(_clean(row.get("source_slice")) for row in rows)
    audit_classes = Counter(_clean(row.get("stage_9_29_audit_class")) for row in rows)
    recommendations = Counter(_clean(row.get("stage_9_29_recommendation")) for row in rows)
    targets = Counter(_clean(row.get("target_bucket")) for row in rows)
    semantics = Counter(_clean(row.get("semantic_relation")) for row in rows)
    domains = Counter(_clean(row.get("query_domain")) for row in rows)
    top1_domains = Counter(_clean(row.get("top1_domain")) for row in rows)
    books = Counter(_clean(row.get("book_relation")) for row in rows)
    return {
        "target_rows": len(rows),
        "target_bucket_counts": dict(targets.most_common()),
        "source_slice_counts": dict(source_slices.most_common()),
        "audit_class_counts": dict(audit_classes.most_common()),
        "recommendation_counts": dict(recommendations.most_common()),
        "semantic_relation_counts": dict(semantics.most_common()),
        "query_domain_counts": dict(domains.most_common()),
        "top1_domain_counts": dict(top1_domains.most_common()),
        "book_relation_counts": dict(books.most_common()),
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
    bucket_summary: list[dict[str, Any]],
    issue_summary: list[dict[str, Any]],
    source_slice_summary: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    lines = [
        "# Stage 9.29 Recall-missing High-support Bucket Audit",
        "",
        "Read-only audit of the largest recall-missing buckets selected from stage 9.28. The audit focuses on `<empty> + query_family_empty`, `pipe + top1_family_empty`, and the comparison bucket `valve + top1_family_empty`, separating true missing-recall candidates from taxonomy-empty labels and source-dominated artifacts.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", metrics["target_rows"]],
                ["empty_query_family_missing", metrics["target_bucket_counts"].get("empty_query_family_missing", 0)],
                ["pipe_top1_family_empty", metrics["target_bucket_counts"].get("pipe_top1_family_empty", 0)],
                ["valve_taxonomy_reference", metrics["target_bucket_counts"].get("valve_taxonomy_reference", 0)],
                ["dominant_global_repair_source", metrics["source_slice_counts"].get("dominant_global_repair_source", 0)],
                ["non_global_repair_source", metrics["source_slice_counts"].get("non_global_repair_source", 0)],
                ["source_dominated_artifact", metrics["audit_class_counts"].get("source_dominated_artifact", 0)],
                ["taxonomy_reference_same_domain", metrics["audit_class_counts"].get("taxonomy_reference_same_domain", 0)],
                ["top1_taxonomy_empty_same_domain", metrics["audit_class_counts"].get("top1_taxonomy_empty_same_domain", 0)],
            ]
        ),
        "",
        "## Target Buckets",
        "",
        _md_table(
            [["bucket", "count", "sources", "dom_source_rate", "top_class", "recommendation", "book_relations", "semantic_relations", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["source_count"],
                    row["dominant_source_rate"],
                    row["top_audit_class"],
                    row["top_recommendation"],
                    row["book_relation_counts"],
                    row["semantic_relation_counts"],
                    row["example_queries"],
                ]
                for row in bucket_summary
            ]
        ),
        "",
        "## Issue Split",
        "",
        _md_table(
            [["issue", "recommendation", "count", "source_slice", "query_domains", "top1_domains", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["top_recommendation"],
                    row["count"],
                    row["source_slice_counts"],
                    row["query_domain_counts"],
                    row["top1_domain_counts"],
                    row["example_queries"],
                ]
                for row in issue_summary
            ]
        ),
        "",
        "## Source Slice",
        "",
        _md_table(
            [["slice", "count", "top_class", "book_relations", "target_mix", "examples"]]
            + [
                [
                    row["bucket_key"],
                    row["count"],
                    row["top_audit_class"],
                    row["book_relation_counts"],
                    row["semantic_relation_counts"],
                    row["example_queries"],
                ]
                for row in source_slice_summary
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
    parser = argparse.ArgumentParser(description="Stage 9.29 recall-missing high-support bucket audit")
    parser.add_argument("--kickoff-rows", default=str(DEFAULT_KICKOFF_ROWS))
    parser.add_argument("--kickoff-summary", default=str(DEFAULT_KICKOFF_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    kickoff_rows = _read_csv(Path(args.kickoff_rows))
    kickoff_summary = _read_json(Path(args.kickoff_summary))
    audited_rows = _annotate(kickoff_rows)
    bucket_summary = _summarize_group(audited_rows, ("target_bucket",), "target_bucket")
    issue_summary = _summarize_group(audited_rows, ("stage_9_29_audit_class",), "audit_class")
    source_slice_summary = _summarize_group(audited_rows, ("source_slice",), "source_slice")
    semantic_summary = _summarize_group(audited_rows, ("target_bucket", "semantic_relation"), "target_semantic_relation")

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "bucket_summary_csv": str(output_prefix.with_name(output_prefix.name + "_bucket_summary.csv")),
        "issue_summary_csv": str(output_prefix.with_name(output_prefix.name + "_issue_summary.csv")),
        "source_slice_summary_csv": str(output_prefix.with_name(output_prefix.name + "_source_slice_summary.csv")),
        "semantic_summary_csv": str(output_prefix.with_name(output_prefix.name + "_semantic_summary.csv")),
    }
    metrics = _overview(audited_rows)
    report = {
        "stage": "Goal LTR v1 / stage 9.29 recall-missing high-support bucket audit",
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
            "stage_9_28_rows": str(Path(args.kickoff_rows)),
            "stage_9_28_summary": str(Path(args.kickoff_summary)),
        },
        "metrics": metrics,
        "kickoff_context": {
            "stage_9_28_decision": kickoff_summary.get("decision", ""),
            "dev_top80_missing_rows": kickoff_summary.get("metrics", {}).get("recall_missing_overview", {}).get("dev_top80_missing_rows"),
        },
        "target_bucket_summary": bucket_summary,
        "issue_summary": issue_summary,
        "source_slice_summary": source_slice_summary,
        "semantic_summary": semantic_summary,
        "decision": (
            "Do not promote the audited high-support recall-missing buckets into recall learning yet. The two largest buckets are dominated by "
            "global_repair_decision_table.csv: <empty> + query_family_empty has 97/104 rows from that source and pipe + top1_family_empty has "
            "37/40. The valve comparison bucket is less source-dominated, but its strongest signal is top1_family taxonomy coverage rather than "
            "a transferable recall rule. The next step should be a read-only source-provenance and taxonomy-coverage review, not training or rule patches."
        ),
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": (
            "Stage 9.29 only audits selected dev recall-missing buckets from stage 9.28. It does not train, tune, patch rules, change ranking, "
            "modify GoalSearcher, use heldout for selection, connect online, or relax any gate."
        ),
        "next_stage": {
            "stage": "9.30 recall-missing source-provenance and taxonomy coverage review",
            "goal": (
                "Read-only review of the 9.29 source-dominated and taxonomy-coverage findings, focusing on global_repair_decision_table.csv provenance "
                "and top1_family coverage gaps before deciding whether any recall-missing slice is eligible for learning."
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
        "source_slice",
        "query_domain",
        "top1_domain",
        "semantic_relation",
        "stage_9_29_audit_class",
        "stage_9_29_recommendation",
        "normalized_query_family",
        "book_relation",
        "taxonomy_signal",
        "bucket_count",
        "bucket_source_count",
        "bucket_dominant_source",
        "bucket_dominant_source_rate",
        "source_shape",
        "audit_bucket_shape",
        "primary_issue",
        "next_action",
        "split",
        "status",
        "reason",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_books",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_score",
        "top1_reasons",
        "top80_rows",
    ]
    summary_fields = [
        "bucket_type",
        "bucket_key",
        "count",
        "rate_within_stage_9_29_targets",
        "province_count",
        "source_count",
        "dominant_source",
        "dominant_source_count",
        "dominant_source_rate",
        "top_audit_class",
        "top_audit_class_count",
        "top_recommendation",
        "top_recommendation_count",
        "source_slice_counts",
        "book_relation_counts",
        "query_domain_counts",
        "top1_domain_counts",
        "semantic_relation_counts",
        "example_queries",
    ]
    _write_csv(Path(artifacts["rows_csv"]), audited_rows, row_fields)
    _write_csv(Path(artifacts["bucket_summary_csv"]), bucket_summary, summary_fields)
    _write_csv(Path(artifacts["issue_summary_csv"]), issue_summary, summary_fields)
    _write_csv(Path(artifacts["source_slice_summary_csv"]), source_slice_summary, summary_fields)
    _write_csv(Path(artifacts["semantic_summary_csv"]), semantic_summary, summary_fields)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, bucket_summary, issue_summary, source_slice_summary)

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
