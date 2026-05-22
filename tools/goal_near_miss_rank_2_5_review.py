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
DEFAULT_INPUT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_anchor_clean_gap_wrong_rank.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_near_miss_rank_2_5_review_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_near_miss_rank_2_5_review_summary.md"
DEFAULT_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_near_miss_rank_2_5_review_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_near_miss_rank_2_5_review_buckets.csv"

PARAM_EXACT_FEATURES = (
    "dn_exact",
    "cable_section_exact",
    "cable_cores_exact",
    "circuits_exact",
    "concrete_grade_exact",
    "thickness_exact",
    "width_height_exact",
)
PARAM_TIER_FEATURES = (
    "dn_tier_up",
    "cable_section_tier_up",
    "circuits_tier_up",
    "thickness_tier_up",
    "width_height_tier_match",
)
PARAM_QUERY_FEATURES = (
    "dn_query_present",
    "cable_section_query_present",
    "cable_cores_query_present",
    "circuits_query_present",
    "concrete_grade_query_present",
    "thickness_query_present",
    "width_height_query_present",
)

AUDIT_FAMILY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("electrical_box", ("配电箱", "控制箱", "开关柜", "配电柜", "箱变", "柜")),
    ("bridge", ("桥架", "线槽")),
    ("conduit", ("配管", "穿管", "线管", "电线管", "金属软管")),
    ("wire", ("配线", "穿线", "导线", "电线")),
    ("cable_head", ("电缆头", "终端头", "中间头")),
    ("cable", ("电缆",)),
    ("lamp", ("灯具", "灯带", "筒灯", "射灯", "灯", "照明")),
    ("switch", ("开关",)),
    ("socket", ("插座",)),
    ("valve", ("阀", "软接头", "补偿器", "过滤器", "倒流防止器", "真空破坏器")),
    ("support", ("支吊架", "支架", "吊架")),
    ("sleeve", ("套管",)),
    ("duct", ("风管", "风道", "风口", "柔性接口")),
    ("pipe", ("钢管", "塑料管", "管道", "给水管", "排水管", "镀锌管", "PE管", "PPR", "PVC", "HDPE", "管")),
    ("pump", ("水泵", "泵")),
    ("fan", ("风机",)),
    ("instrument", ("仪表", "压力表", "温度计", "传感器")),
    ("sanitary", ("洗脸盆", "坐便器", "小便器", "蹲便器", "地漏", "卫生器具")),
    ("concrete", ("混凝土", "砼")),
    ("rebar", ("钢筋",)),
    ("formwork", ("模板",)),
    ("earthwork", ("土方", "挖土", "回填", "余方弃置")),
    ("decoration_finish", ("墙面", "天棚", "吊顶", "饰面", "门", "窗", "踢脚", "涂料", "抹灰", "地面", "楼地面", "吸塑板", "铝塑板")),
    ("waterproof_joint", ("防水", "止水", "变形缝", "嵌缝")),
    ("tank", ("气压罐", "水箱", "罐")),
    ("fire_alarm", ("消防广播", "火灾报警", "对讲电话", "电话主机")),
)

SUBTYPE_TOKENS = (
    "落地",
    "悬挂",
    "嵌入",
    "明装",
    "暗装",
    "螺纹",
    "焊接",
    "法兰",
    "沟槽",
    "室内",
    "室外",
    "给水",
    "排水",
    "喷淋",
    "普通",
    "装饰",
    "轨道",
    "柔性",
    "刚性",
    "平开",
    "推拉",
    "人工",
    "机械",
    "三类土",
    "四类土",
    "厚",
    "宽",
    "高",
    "长",
    "半周长",
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


def _read_near_miss_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if _clean(row.get("status")) == "top80_present_but_wrong_rank" and _clean(row.get("rank_bucket")) == "rank_2_5"
        ]
    return rows


def _load_feature_groups(data_dir: Path, split_to_group_ids: dict[str, set[str]]) -> dict[tuple[str, str], FeatureGroup]:
    result: dict[tuple[str, str], FeatureGroup] = {}
    for split, group_ids in split_to_group_ids.items():
        if not group_ids:
            continue
        path = data_dir / f"ltr_features_{split}.jsonl"
        for row in _iter_jsonl(path):
            group_id = _clean(row.get("group_id"))
            if group_id not in group_ids:
                continue
            key = (split, group_id)
            group = result.setdefault(key, FeatureGroup())
            group.rows.append(row)
            quota_id = _clean(row.get("quota_id"))
            if quota_id and quota_id not in group.by_quota_id:
                group.by_quota_id[quota_id] = row
            if _int(row.get("label")) > 0:
                group.positives.append(row)
    for group in result.values():
        group.rows.sort(key=_candidate_rank)
        group.positives.sort(key=_candidate_rank)
    return result


def _book_matches(left: str, right: str) -> bool:
    left = _clean(left).upper()
    right = _clean(right).upper()
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith("C") and left[1:].isdigit() and right == left[1:]:
        return True
    if right.startswith("C") and right[1:].isdigit() and left == right[1:]:
        return True
    return False


def _split_values(value: str, sep: str = ",") -> list[str]:
    return [_clean(item) for item in _clean(value).split(sep) if _clean(item)]


def _same_book(row: dict[str, Any], top_row: dict[str, Any] | None, positive_row: dict[str, Any] | None) -> bool:
    top_book = _clean(top_row.get("quota_book")) if top_row else _clean(row.get("gated_top_book"))
    positive_book = _clean(positive_row.get("quota_book")) if positive_row else ""
    expected_books = _split_values(_clean(row.get("expected_books")))
    if positive_book and _book_matches(positive_book, top_book):
        return True
    return any(_book_matches(expected_book, top_book) for expected_book in expected_books)


def _same_family(row: dict[str, Any], top_row: dict[str, Any] | None, positive_row: dict[str, Any] | None) -> bool:
    top_family = _clean(top_row.get("candidate_family")) if top_row else _clean(row.get("gated_top_family"))
    positive_family = _clean(positive_row.get("candidate_family")) if positive_row else ""
    expected_families = _split_values(_clean(row.get("expected_families")))
    if top_family and positive_family and top_family == positive_family:
        return True
    return bool(top_family and top_family in expected_families)


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def _has_param_digit(text: str) -> bool:
    text = text or ""
    if re.search(r"\d+\s*[*×xX]\s*\d+", text):
        return True
    if re.search(r"(?:DN|De|Φ|φ|mm|毫米|cm|厘米|m\b|米|kV|A以下|以内|厚|宽|高|长|直径|管径|周长|半周长)\s*[≤<]?\s*\d", text, re.I):
        return True
    if re.search(r"\d+\s*(?:mm|毫米|cm|厘米|m\b|米|kV|A|以内|以下)", text, re.I):
        return True
    return False


def _audit_family_hint(query: str) -> str:
    query_upper = query.upper()
    hits: list[str] = []
    for family, tokens in AUDIT_FAMILY_HINTS:
        for token in tokens:
            token_cmp = token.upper() if re.search(r"[A-Za-z]", token) else token
            if token_cmp in query_upper:
                hits.append(family)
                break
    return "|".join(dict.fromkeys(hits))


def _param_score(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    exact = sum(_int(row.get(feature)) for feature in PARAM_EXACT_FEATURES)
    tier = sum(_int(row.get(feature)) for feature in PARAM_TIER_FEATURES)
    return exact * 2.0 + tier + _float(row.get("numeric_score"))


def _query_param_feature_count(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    return sum(_int(row.get(feature)) for feature in PARAM_QUERY_FEATURES)


def _param_conflict_count(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    return _int(row.get("param_conflict_count")) + _int(row.get("has_param_conflict_reason"))


def _candidate_numeric_diff(top_name: str, positive_name: str) -> bool:
    top_numbers = set(_numbers(top_name))
    positive_numbers = set(_numbers(positive_name))
    return bool(top_numbers and positive_numbers and top_numbers != positive_numbers)


def _candidate_subtype_diff_unrequested(query: str, top_name: str, positive_name: str) -> bool:
    if not top_name or not positive_name:
        return False
    query_text = query or ""
    top_only = [token for token in SUBTYPE_TOKENS if token in top_name and token not in positive_name and token not in query_text]
    positive_only = [token for token in SUBTYPE_TOKENS if token in positive_name and token not in top_name and token not in query_text]
    return bool(top_only or positive_only)


def _name(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_name")) if row else ""


def _qid(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_id")) if row else ""


def _score(row: dict[str, Any] | None) -> float:
    return _float(row.get("current_score")) if row else 0.0


def _best_positive(group: FeatureGroup | None, preferred_ids: str) -> dict[str, Any] | None:
    if not group:
        return None
    ids = [_clean(item) for item in preferred_ids.split("|") if _clean(item)]
    for qid in ids:
        row = group.by_quota_id.get(qid)
        if row and _int(row.get("label")) > 0:
            return row
    return group.positives[0] if group.positives else None


def _classify(row: dict[str, Any], top_row: dict[str, Any] | None, positive_row: dict[str, Any] | None) -> dict[str, Any]:
    query = _clean(row.get("query"))
    query_family = _clean(row.get("query_family"))
    family_hint = _audit_family_hint(query)
    top_name = _name(top_row) or _clean(row.get("gated_top"))
    positive_name = _name(positive_row) or _clean(row.get("positive_names_in_top80")) or _clean(row.get("expected_names"))
    same_family = _same_family(row, top_row, positive_row)
    same_book = _same_book(row, top_row, positive_row)
    has_param_digit = _has_param_digit(query)
    numeric_diff = _candidate_numeric_diff(top_name, positive_name)
    subtype_diff = _candidate_subtype_diff_unrequested(query, top_name, positive_name)
    top_param_score = _param_score(top_row)
    positive_param_score = _param_score(positive_row)
    top_param_conflict = _param_conflict_count(top_row)
    positive_param_conflict = _param_conflict_count(positive_row)
    query_param_features = max(_query_param_feature_count(top_row), _query_param_feature_count(positive_row))
    param_better = positive_param_score > top_param_score or top_param_conflict > positive_param_conflict
    query_family_empty = not query_family

    tags: list[str] = []
    if query_family_empty:
        tags.append("query_family_empty")
    if family_hint:
        tags.append(f"family_hint:{family_hint}")
    if same_family:
        tags.append("same_family")
    if same_book:
        tags.append("same_book")
    if has_param_digit:
        tags.append("query_has_param")
    if numeric_diff:
        tags.append("candidate_numeric_diff")
    if subtype_diff:
        tags.append("candidate_subtype_diff_unrequested")
    if param_better:
        tags.append("positive_param_signal_better")

    if (not has_param_digit and not query_param_features) and (numeric_diff or subtype_diff) and (same_family or same_book):
        category = "oss_label_too_specific_or_ambiguous"
    elif query_family_empty and family_hint:
        category = "query_family_empty_but_clear"
    elif has_param_digit and (numeric_diff or param_better):
        category = "param_tier_near_miss"
    elif same_family and same_book:
        category = "same_family_book_sorting"
    elif same_family:
        category = "same_family_cross_book_sorting"
    elif same_book:
        category = "same_book_family_or_subtype_mismatch"
    elif query_family_empty:
        category = "query_family_empty_unclear_or_non_install"
    else:
        category = "other_near_miss"

    evidence = []
    if family_hint:
        evidence.append(f"hint={family_hint}")
    if same_family:
        evidence.append("same_family")
    if same_book:
        evidence.append("same_book")
    if numeric_diff:
        evidence.append("candidate_numbers_differ")
    if subtype_diff:
        evidence.append("candidate_subtype_diff_not_in_query")
    if param_better:
        evidence.append(f"positive_param>{top_param_score:g}->{positive_param_score:g}")
    if top_param_conflict > positive_param_conflict:
        evidence.append(f"top_param_conflict>{positive_param_conflict}")

    return {
        "primary_category": category,
        "secondary_tags": "|".join(tags),
        "audit_family_hint": family_hint,
        "same_family": int(same_family),
        "same_book": int(same_book),
        "has_param_digit": int(has_param_digit),
        "candidate_numeric_diff": int(numeric_diff),
        "candidate_subtype_diff_unrequested": int(subtype_diff),
        "query_param_feature_count": query_param_features,
        "top_param_score": round(top_param_score, 6),
        "positive_param_score": round(positive_param_score, 6),
        "top_param_conflict": top_param_conflict,
        "positive_param_conflict": positive_param_conflict,
        "evidence": "; ".join(evidence),
    }


def _review_rows(rows: list[dict[str, Any]], groups: dict[tuple[str, str], FeatureGroup]) -> list[dict[str, Any]]:
    reviewed: list[dict[str, Any]] = []
    for row in rows:
        split = _clean(row.get("split"))
        group_id = _clean(row.get("group_id"))
        group = groups.get((split, group_id))
        top_row = group.by_quota_id.get(_clean(row.get("gated_top_id"))) if group else None
        positive_row = _best_positive(group, _clean(row.get("positive_ids_in_top80")))
        classified = _classify(row, top_row, positive_row)
        reviewed.append(
            {
                **row,
                **classified,
                "positive_id": _qid(positive_row),
                "positive_name": _name(positive_row),
                "positive_family": _clean(positive_row.get("candidate_family")) if positive_row else "",
                "positive_book": _clean(positive_row.get("quota_book")) if positive_row else "",
                "positive_chapter": _clean(positive_row.get("quota_chapter")) if positive_row else "",
                "positive_score": round(_score(positive_row), 6),
                "gated_top_family_from_features": _clean(top_row.get("candidate_family")) if top_row else "",
                "gated_top_book_from_features": _clean(top_row.get("quota_book")) if top_row else "",
                "gated_top_score_from_features": round(_score(top_row), 6),
            }
        )
    return reviewed


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _bucket_key(value: Any) -> str:
    return _clean(value) or "<empty>"


def _summarize(reviewed: list[dict[str, Any]], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_split: dict[str, Counter[str]] = defaultdict(Counter)
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    bucket_rows: list[dict[str, Any]] = []

    for row in reviewed:
        split = _clean(row.get("split"))
        category = _clean(row.get("primary_category"))
        by_split[split][category] += 1
        counters["category"][category] += 1
        for dimension, field in (
            ("reason", "reason"),
            ("query_family", "query_family"),
            ("audit_family_hint", "audit_family_hint"),
            ("expected_family", "expected_families"),
            ("expected_book", "expected_books"),
            ("province", "province"),
            ("positive_rank", "gated_positive_rank"),
            ("category_reason", "reason"),
        ):
            key = _bucket_key(row.get(field))
            bucket_name = f"{dimension}"
            counters[bucket_name][key] += 1
            if dimension == "category_reason":
                counters[f"category_reason:{category}"][key] += 1
        counters[f"category_query_family:{category}"][_bucket_key(row.get("query_family"))] += 1
        counters[f"category_hint:{category}"][_bucket_key(row.get("audit_family_hint"))] += 1
        counters[f"category_book:{category}"][_bucket_key(row.get("expected_books"))] += 1

    total = len(reviewed)
    for dimension, counter in counters.items():
        for key, count in counter.most_common():
            bucket_rows.append(
                {
                    "dimension": dimension,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )

    summary = {
        "rows": total,
        "by_category": _top_items(counters["category"], top_limit),
        "by_split_category": {
            split: [{"key": key, "count": count} for key, count in counter.most_common()]
            for split, counter in sorted(by_split.items())
        },
        "by_reason": _top_items(counters["reason"], top_limit),
        "by_query_family": _top_items(counters["query_family"], top_limit),
        "by_audit_family_hint": _top_items(counters["audit_family_hint"], top_limit),
        "by_expected_family": _top_items(counters["expected_family"], top_limit),
        "by_expected_book": _top_items(counters["expected_book"], top_limit),
        "by_positive_rank": _top_items(counters["positive_rank"], top_limit),
        "by_province": _top_items(counters["province"], top_limit),
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


def _sample_rows(reviewed: list[dict[str, Any]], category: str, limit: int) -> list[list[object]]:
    rows = [row for row in reviewed if row["primary_category"] == category]
    rows.sort(key=lambda row: (_clean(row.get("split")), _int(row.get("gated_positive_rank")), _clean(row.get("query"))))
    return [
        [
            row["split"],
            row["gated_positive_rank"],
            row["query"],
            row["positive_id"] + " " + row["positive_name"],
            row["gated_top"],
            row["evidence"],
        ]
        for row in rows[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], reviewed: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Near Miss Rank 2-5 Review",
        "",
        "Stage 4.0 read-only audit. It only reviews anchor-clean `rank_2_5` near misses from Stage 3.9 and does not tune rules, train a model, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["near_miss_rows", summary["rows"]],
                ["input_csv", report["input_csv"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Primary Categories",
        "",
        _md_table(_counter_table(summary["by_category"])),
        "",
        "## Split Categories",
        "",
    ]
    for split, items in summary["by_split_category"].items():
        lines.extend([f"### {split}", "", _md_table(_counter_table(items)), ""])

    lines.extend(
        [
            "## Buckets",
            "",
            "Reason:",
            "",
            _md_table(_counter_table(summary["by_reason"])),
            "",
            "Query family:",
            "",
            _md_table(_counter_table(summary["by_query_family"])),
            "",
            "Audit family hint:",
            "",
            _md_table(_counter_table(summary["by_audit_family_hint"])),
            "",
            "Expected book:",
            "",
            _md_table(_counter_table(summary["by_expected_book"])),
            "",
            "Positive rank:",
            "",
            _md_table(_counter_table(summary["by_positive_rank"])),
            "",
            "## Samples",
            "",
        ]
    )
    for item in summary["by_category"][:6]:
        category = item["key"]
        lines.extend(
            [
                f"### {category}",
                "",
                _md_table([["split", "rank", "query", "positive", "gated_top", "evidence"]] + _sample_rows(reviewed, category, sample_limit)),
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
    parser = argparse.ArgumentParser(description="Review anchor-clean rank 2-5 near misses without tuning")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--top-limit", type=int, default=20)
    parser.add_argument("--sample-limit", type=int, default=6)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-csv", default=str(DEFAULT_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    near_miss_rows = _read_near_miss_rows(Path(args.input_csv))
    split_to_group_ids: dict[str, set[str]] = defaultdict(set)
    for row in near_miss_rows:
        split_to_group_ids[_clean(row.get("split"))].add(_clean(row.get("group_id")))

    groups = _load_feature_groups(Path(args.data_dir), split_to_group_ids)
    reviewed = _review_rows(near_miss_rows, groups)
    summary, bucket_rows = _summarize(reviewed, args.top_limit)

    details_fields = [
        "primary_category",
        "secondary_tags",
        "evidence",
        "audit_family_hint",
        "split",
        "reason",
        "rank_bucket",
        "gated_positive_rank",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "expected_ids",
        "expected_families",
        "expected_books",
        "expected_names",
        "positive_id",
        "positive_name",
        "positive_family",
        "positive_book",
        "positive_chapter",
        "positive_score",
        "gated_top_id",
        "gated_top",
        "gated_top_family",
        "gated_top_book",
        "gated_top_chapter",
        "gated_top_score",
        "same_family",
        "same_book",
        "has_param_digit",
        "candidate_numeric_diff",
        "candidate_subtype_diff_unrequested",
        "query_param_feature_count",
        "top_param_score",
        "positive_param_score",
        "top_param_conflict",
        "positive_param_conflict",
        "gate_reason",
        "score_margin",
    ]
    _write_csv(Path(args.details_csv), reviewed, details_fields)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate"])

    report = {
        "stage": "Goal LTR v1 / stage 4.0 rank_2_5 near miss review",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "data_dir": args.data_dir,
        "input_csv": args.input_csv,
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
                    "by_category": summary["by_category"],
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
