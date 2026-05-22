from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REVIEW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_near_miss_rank_2_5_review_details.csv"
DEFAULT_GAP_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_wrong_rank.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_review_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_review_summary.md"
DEFAULT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_review_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_review_buckets.csv"

SCORE_FEATURES = (
    "current_score",
    "bm25_score",
    "field_score",
    "confidence",
    "token_overlap",
    "numeric_score",
    "domain_rule_score",
    "national_cluster_bonus",
    "unit_exact",
    "family_match",
    "material_match",
    "action_match",
    "connection_match",
    "install_method_match",
    "param_exact_count",
    "param_tier_up_count",
    "param_conflict_count",
    "reason_count",
)

PARAM_FEATURES = (
    "dn_exact",
    "dn_tier_up",
    "cable_section_exact",
    "cable_section_tier_up",
    "cable_cores_exact",
    "circuits_exact",
    "circuits_tier_up",
    "concrete_grade_exact",
    "thickness_exact",
    "thickness_tier_up",
    "width_height_exact",
    "width_height_tier_match",
)

QUERY_PARAM_FEATURES = (
    "dn_query_present",
    "cable_section_query_present",
    "cable_cores_query_present",
    "circuits_query_present",
    "concrete_grade_query_present",
    "thickness_query_present",
    "width_height_query_present",
)

DIFF_TERMS = (
    "成套",
    "配电箱",
    "控制箱",
    "开关柜",
    "落地",
    "悬挂",
    "嵌入",
    "明装",
    "暗装",
    "半周长",
    "管件",
    "沟槽",
    "卡箍",
    "螺纹",
    "焊接",
    "法兰",
    "柔性",
    "刚性",
    "防水",
    "防火",
    "人防",
    "预留",
    "单管",
    "门型",
    "侧向",
    "纵向",
    "抗震",
    "防水灯头",
    "座灯头",
    "普通灯具",
    "装饰灯",
    "Y型过滤器",
    "过滤器",
    "止回阀",
    "调节阀",
    "蝶阀",
    "闸阀",
    "球阀",
    "卡套",
    "仪表阀门",
    "压力表",
    "盘装",
    "就地",
    "风口",
    "百叶",
    "不锈钢",
    "铝合金",
    "普通土",
    "坚土",
    "平开",
    "推拉",
    "人工",
    "机械",
    "水喷淋",
    "空调",
    "冷热水",
    "给水",
    "排水",
    "支架",
    "套管",
    "风管",
)


@dataclass
class FeatureGroup:
    rows: list[dict[str, Any]] = field(default_factory=list)
    by_quota_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    positives: list[dict[str, Any]] = field(default_factory=list)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _candidate_rank(row: dict[str, Any] | None) -> int:
    if not row:
        return 999999
    return _int(row.get("candidate_rank") or row.get("base_rank") or row.get("row_index")) or 999999


def _load_review_rows(path: Path) -> list[dict[str, Any]]:
    return [
        row
        for row in _read_csv(path)
        if _clean(row.get("primary_category")) == "same_family_book_sorting"
        and _clean(row.get("gated_positive_rank")) == "2"
    ]


def _load_gap_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = _read_csv(path)
    return {(_clean(row.get("split")), _clean(row.get("group_id"))): row for row in rows}


def _load_feature_groups(data_dir: Path, split_to_group_ids: dict[str, set[str]]) -> dict[tuple[str, str], FeatureGroup]:
    groups: dict[tuple[str, str], FeatureGroup] = {}
    for split, group_ids in split_to_group_ids.items():
        if not group_ids:
            continue
        path = data_dir / f"ltr_features_{split}.jsonl"
        for row in _iter_jsonl(path):
            group_id = _clean(row.get("group_id"))
            if group_id not in group_ids:
                continue
            group = groups.setdefault((split, group_id), FeatureGroup())
            group.rows.append(row)
            quota_id = _clean(row.get("quota_id"))
            if quota_id and quota_id not in group.by_quota_id:
                group.by_quota_id[quota_id] = row
            if _int(row.get("label")) > 0:
                group.positives.append(row)

    for group in groups.values():
        group.rows.sort(key=_candidate_rank)
        group.positives.sort(key=_candidate_rank)
    return groups


def _best_positive(group: FeatureGroup | None, positive_id: str) -> dict[str, Any] | None:
    if not group:
        return None
    if positive_id and positive_id in group.by_quota_id:
        row = group.by_quota_id[positive_id]
        if _int(row.get("label")) > 0:
            return row
    return group.positives[0] if group.positives else None


def _name(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_name")) if row else ""


def _qid(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_id")) if row else ""


def _chapter(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_chapter")) if row else ""


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def _specs(text: str) -> list[str]:
    patterns = [
        r"DN\s*\d+(?:\.\d+)?",
        r"\d+(?:\.\d+)?\s*[*×xX]\s*\d+(?:\.\d+)?(?:\s*[*×xX]\s*\d+(?:\.\d+)?)?",
        r"\d+(?:\.\d+)?\s*(?:mm|m|cm|kW|kV|A)",
    ]
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(re.findall(pattern, text or "", flags=re.I))
    return list(dict.fromkeys(hit.replace(" ", "") for hit in hits))


def _term_hits(text: str) -> list[str]:
    text = text or ""
    hits = [term for term in DIFF_TERMS if term and term in text]
    hits.extend(_specs(text))
    return list(dict.fromkeys(hits))


def _join(values: list[str]) -> str:
    return "|".join(value for value in values if value)


def _feature_delta(top_row: dict[str, Any] | None, positive_row: dict[str, Any] | None, feature: str) -> float:
    return _float(positive_row.get(feature) if positive_row else 0) - _float(top_row.get(feature) if top_row else 0)


def _feature_winners(top_row: dict[str, Any] | None, positive_row: dict[str, Any] | None) -> tuple[list[str], list[str], list[str]]:
    positive_better: list[str] = []
    top_better: list[str] = []
    equal: list[str] = []
    for feature in SCORE_FEATURES:
        delta = _feature_delta(top_row, positive_row, feature)
        if delta > 1e-6:
            positive_better.append(feature)
        elif delta < -1e-6:
            top_better.append(feature)
        else:
            equal.append(feature)
    return positive_better, top_better, equal


def _param_score(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    explicit = sum(_int(row.get(feature)) for feature in PARAM_FEATURES)
    return explicit + _float(row.get("numeric_score")) + _int(row.get("param_exact_count")) + 0.5 * _int(row.get("param_tier_up_count"))


def _query_has_param(row: dict[str, Any] | None, query: str) -> bool:
    if any(_int(row.get(feature)) > 0 for feature in QUERY_PARAM_FEATURES) if row else False:
        return True
    return bool(_specs(query) or re.search(r"(DN|De|mm|毫米|半周长|周长|直径|宽|高|厚|长)\s*[≤<]?\s*\d", query or "", re.I))


def _overlap_count(query_terms: list[str], candidate_terms: list[str]) -> int:
    return len(set(query_terms) & set(candidate_terms))


def _classify(
    *,
    review_row: dict[str, Any],
    gap_row: dict[str, Any],
    top_row: dict[str, Any] | None,
    positive_row: dict[str, Any] | None,
) -> dict[str, Any]:
    query = _clean(review_row.get("query"))
    top_name = _name(top_row) or _clean(review_row.get("gated_top"))
    positive_name = _name(positive_row) or _clean(review_row.get("positive_name"))
    query_terms = _term_hits(query)
    top_terms = _term_hits(top_name)
    positive_terms = _term_hits(positive_name)
    top_only_terms = [term for term in top_terms if term not in positive_terms]
    positive_only_terms = [term for term in positive_terms if term not in top_terms]
    query_hits_top_only = [term for term in top_only_terms if term in query_terms]
    query_hits_positive_only = [term for term in positive_only_terms if term in query_terms]
    query_top_overlap = _overlap_count(query_terms, top_terms)
    query_positive_overlap = _overlap_count(query_terms, positive_terms)

    positive_better, top_better, equal_features = _feature_winners(top_row, positive_row)
    top_param_score = _param_score(top_row)
    positive_param_score = _param_score(positive_row)
    query_param = _query_has_param(top_row, query) or _query_has_param(positive_row, query)
    chapter_same = bool(_chapter(top_row) and _chapter(top_row) == _chapter(positive_row))
    raw_ltr_top_id = _clean(gap_row.get("raw_ltr_top_id"))
    positive_id = _qid(positive_row) or _clean(review_row.get("positive_id"))
    gated_top_id = _clean(review_row.get("gated_top_id"))

    if raw_ltr_top_id and raw_ltr_top_id == positive_id and gated_top_id != positive_id:
        diagnosis = "safety_gate_blocked_correct_ltr"
    elif query_hits_positive_only and query_hits_top_only:
        diagnosis = "conflicting_query_terms_or_label_ambiguous"
    elif query_hits_positive_only and not query_hits_top_only:
        diagnosis = "missing_or_weak_subtype_feature"
    elif query_param and positive_param_score > top_param_score:
        diagnosis = "parameter_signal_not_strong_enough"
    elif query_param and (positive_only_terms or top_only_terms or _numbers(top_name) != _numbers(positive_name)):
        diagnosis = "parameter_or_tier_feature_missing"
    elif len(positive_better) >= 3 and _feature_delta(top_row, positive_row, "current_score") < 0:
        diagnosis = "existing_features_not_weighted_enough"
    elif query_hits_top_only and not query_hits_positive_only:
        diagnosis = "top_surface_match_stronger_or_label_ambiguous"
    elif (positive_only_terms or top_only_terms) and not query_hits_positive_only and not query_hits_top_only:
        diagnosis = "subtype_diff_not_in_query_or_label_specific"
    elif not chapter_same and not query_param:
        diagnosis = "chapter_bias_or_section_prior_needed"
    elif not query_terms and top_terms != positive_terms:
        diagnosis = "query_lacks_discriminating_terms"
    else:
        diagnosis = "low_discrimination_same_family_book"

    current_delta = _feature_delta(top_row, positive_row, "current_score")
    score_direction = "positive_higher" if current_delta > 1e-6 else "top_higher" if current_delta < -1e-6 else "tie"
    top_rank = _candidate_rank(top_row)
    positive_rank = _candidate_rank(positive_row)
    rank_gap = positive_rank - top_rank if top_rank < 999999 and positive_rank < 999999 else ""

    return {
        "diagnosis": diagnosis,
        "score_direction": score_direction,
        "feature_positive_better": _join(positive_better),
        "feature_top_better": _join(top_better),
        "feature_equal_count": len(equal_features),
        "query_terms": _join(query_terms),
        "top_terms": _join(top_terms),
        "positive_terms": _join(positive_terms),
        "top_only_terms": _join(top_only_terms),
        "positive_only_terms": _join(positive_only_terms),
        "query_hits_top_only": _join(query_hits_top_only),
        "query_hits_positive_only": _join(query_hits_positive_only),
        "query_top_term_overlap": query_top_overlap,
        "query_positive_term_overlap": query_positive_overlap,
        "query_has_param": int(query_param),
        "top_numbers": _join(_numbers(top_name)),
        "positive_numbers": _join(_numbers(positive_name)),
        "query_specs": _join(_specs(query)),
        "top_param_score": round(top_param_score, 6),
        "positive_param_score": round(positive_param_score, 6),
        "chapter_same": int(chapter_same),
        "top_rank": top_rank if top_rank < 999999 else "",
        "positive_rank": positive_rank if positive_rank < 999999 else "",
        "rank_gap": rank_gap,
        "current_score_delta_positive_minus_top": round(current_delta, 6),
        "raw_ltr_top_id": raw_ltr_top_id,
        "raw_ltr_top": _clean(gap_row.get("raw_ltr_top")),
        "raw_ltr_was_positive": int(bool(raw_ltr_top_id and raw_ltr_top_id == positive_id)),
    }


def _review_rows(
    rows: list[dict[str, Any]],
    gaps: dict[tuple[str, str], dict[str, Any]],
    groups: dict[tuple[str, str], FeatureGroup],
) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for row in rows:
        split = _clean(row.get("split"))
        group_id = _clean(row.get("group_id"))
        group = groups.get((split, group_id))
        top_row = group.by_quota_id.get(_clean(row.get("gated_top_id"))) if group else None
        positive_row = _best_positive(group, _clean(row.get("positive_id")))
        gap_row = gaps.get((split, group_id), {})
        diagnosis = _classify(review_row=row, gap_row=gap_row, top_row=top_row, positive_row=positive_row)
        feature_deltas = {f"delta_{feature}": round(_feature_delta(top_row, positive_row, feature), 6) for feature in SCORE_FEATURES}
        reviewed.append(
            {
                "diagnosis": diagnosis["diagnosis"],
                "split": split,
                "group_id": group_id,
                "sample_id": _clean(row.get("sample_id")),
                "province": _clean(row.get("province")),
                "query_family": _clean(row.get("query_family")),
                "audit_family_hint": _clean(row.get("audit_family_hint")),
                "expected_books": _clean(row.get("expected_books")),
                "query": _clean(row.get("query")),
                "top_id": _clean(row.get("gated_top_id")),
                "top_name": _name(top_row) or _clean(row.get("gated_top")),
                "top_chapter": _chapter(top_row),
                "top_reasons": _clean(top_row.get("reasons")) if top_row else "",
                "positive_id": _qid(positive_row) or _clean(row.get("positive_id")),
                "positive_name": _name(positive_row) or _clean(row.get("positive_name")),
                "positive_chapter": _chapter(positive_row),
                "positive_reasons": _clean(positive_row.get("reasons")) if positive_row else "",
                "gate_reason": _clean(row.get("gate_reason")),
                "score_margin": _clean(row.get("score_margin")),
                **diagnosis,
                **feature_deltas,
            }
        )
    return reviewed


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _bucket_key(value: Any) -> str:
    return _clean(value) or "<empty>"


def _summarize(reviewed: list[dict[str, Any]], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    bucket_rows: list[dict[str, Any]] = []
    for row in reviewed:
        counters["diagnosis"][_bucket_key(row.get("diagnosis"))] += 1
        counters["query_family"][_bucket_key(row.get("query_family"))] += 1
        counters["audit_family_hint"][_bucket_key(row.get("audit_family_hint"))] += 1
        counters["expected_books"][_bucket_key(row.get("expected_books"))] += 1
        counters["chapter_same"][_bucket_key(row.get("chapter_same"))] += 1
        counters["score_direction"][_bucket_key(row.get("score_direction"))] += 1
        counters["raw_ltr_was_positive"][_bucket_key(row.get("raw_ltr_was_positive"))] += 1
        counters[f"diagnosis_family:{_bucket_key(row.get('diagnosis'))}"][_bucket_key(row.get("query_family"))] += 1
        counters[f"diagnosis_book:{_bucket_key(row.get('diagnosis'))}"][_bucket_key(row.get("expected_books"))] += 1

    total = len(reviewed)
    for dimension, counter in counters.items():
        for key, count in counter.most_common():
            bucket_rows.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})

    summary = {
        "rows": total,
        "by_diagnosis": _top_items(counters["diagnosis"], top_limit),
        "by_query_family": _top_items(counters["query_family"], top_limit),
        "by_audit_family_hint": _top_items(counters["audit_family_hint"], top_limit),
        "by_expected_books": _top_items(counters["expected_books"], top_limit),
        "by_score_direction": _top_items(counters["score_direction"], top_limit),
        "by_chapter_same": _top_items(counters["chapter_same"], top_limit),
        "raw_ltr_was_positive": _top_items(counters["raw_ltr_was_positive"], top_limit),
    }
    return summary, bucket_rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]]) -> list[list[object]]:
    return [["key", "count"], *[[item["key"], item["count"]] for item in items]]


def _sample_rows(reviewed: list[dict[str, Any]], diagnosis: str, limit: int) -> list[list[object]]:
    rows = [row for row in reviewed if row["diagnosis"] == diagnosis]
    rows.sort(key=lambda row: (_clean(row.get("query_family")), _clean(row.get("query"))))
    return [
        [
            row["query_family"],
            row["query"],
            f"{row['positive_id']} {row['positive_name']}",
            f"{row['top_id']} {row['top_name']}",
            row["query_hits_positive_only"] or row["positive_only_terms"],
            row["query_hits_top_only"] or row["top_only_terms"],
        ]
        for row in rows[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], reviewed: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Same-Family Same-Book Rank2 Review",
        "",
        "Stage 4.1 read-only audit. It only reviews `same_family_book_sorting` rows where the correct candidate is rank 2. No tuning, no ranking change, no search integration.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["review_csv", report["review_csv"]],
                ["gap_csv", report["gap_csv"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Diagnosis",
        "",
        _md_table(_counter_table(summary["by_diagnosis"])),
        "",
        "## Buckets",
        "",
        "Query family:",
        "",
        _md_table(_counter_table(summary["by_query_family"])),
        "",
        "Expected book:",
        "",
        _md_table(_counter_table(summary["by_expected_books"])),
        "",
        "Score direction (positive minus top current score):",
        "",
        _md_table(_counter_table(summary["by_score_direction"])),
        "",
        "Same chapter:",
        "",
        _md_table(_counter_table(summary["by_chapter_same"])),
        "",
        "Raw LTR already picked positive:",
        "",
        _md_table(_counter_table(summary["raw_ltr_was_positive"])),
        "",
        "## Samples",
        "",
    ]
    for item in summary["by_diagnosis"][:6]:
        diagnosis = item["key"]
        lines.extend(
            [
                f"### {diagnosis}",
                "",
                _md_table(
                    [["family", "query", "positive", "top1", "positive_terms", "top_terms"]]
                    + _sample_rows(reviewed, diagnosis, sample_limit)
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Artifacts",
            "",
            _md_table(
                [
                    ["artifact", "path"],
                    ["details_csv", report["details_csv"]],
                    ["buckets_csv", report["buckets_csv"]],
                ]
            ),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review same-family same-book rank2 near misses without tuning")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--review-csv", default=str(DEFAULT_REVIEW_CSV))
    parser.add_argument("--gap-csv", default=str(DEFAULT_GAP_CSV))
    parser.add_argument("--top-limit", type=int, default=20)
    parser.add_argument("--sample-limit", type=int, default=6)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-csv", default=str(DEFAULT_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    review_rows = _load_review_rows(Path(args.review_csv))
    split_to_group_ids: dict[str, set[str]] = defaultdict(set)
    for row in review_rows:
        split_to_group_ids[_clean(row.get("split"))].add(_clean(row.get("group_id")))

    gaps = _load_gap_index(Path(args.gap_csv))
    groups = _load_feature_groups(Path(args.data_dir), split_to_group_ids)
    reviewed = _review_rows(review_rows, gaps, groups)
    summary, bucket_rows = _summarize(reviewed, args.top_limit)

    detail_fields = [
        "diagnosis",
        "score_direction",
        "split",
        "group_id",
        "sample_id",
        "province",
        "query_family",
        "audit_family_hint",
        "expected_books",
        "query",
        "positive_id",
        "positive_name",
        "positive_chapter",
        "top_id",
        "top_name",
        "top_chapter",
        "chapter_same",
        "raw_ltr_top_id",
        "raw_ltr_top",
        "raw_ltr_was_positive",
        "gate_reason",
        "score_margin",
        "query_terms",
        "positive_terms",
        "top_terms",
        "positive_only_terms",
        "top_only_terms",
        "query_hits_positive_only",
        "query_hits_top_only",
        "query_has_param",
        "query_specs",
        "positive_numbers",
        "top_numbers",
        "positive_param_score",
        "top_param_score",
        "feature_positive_better",
        "feature_top_better",
        "feature_equal_count",
        "positive_reasons",
        "top_reasons",
        "current_score_delta_positive_minus_top",
    ] + [f"delta_{feature}" for feature in SCORE_FEATURES]

    _write_csv(Path(args.details_csv), reviewed, detail_fields)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate"])

    report = {
        "stage": "Goal LTR v1 / stage 4.1 same-family same-book rank2 review",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": args.data_dir,
        "review_csv": args.review_csv,
        "gap_csv": args.gap_csv,
        "details_csv": args.details_csv,
        "buckets_csv": args.buckets_csv,
        "feature_group_count": len(groups),
        "summary": summary,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, reviewed, args.sample_limit)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "rows": summary["rows"],
                    "by_diagnosis": summary["by_diagnosis"],
                },
                "artifacts": {
                    "report_json": str(report_json),
                    "report_md": args.report_md,
                    "details_csv": args.details_csv,
                    "buckets_csv": args.buckets_csv,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
