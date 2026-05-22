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
DEFAULT_RANK2_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_review_details.csv"
DEFAULT_EVAL_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_feature_whatif_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_feature_whatif_summary.md"
DEFAULT_TARGET_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_feature_whatif_target_details.csv"
DEFAULT_HARM_DETAILS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_feature_whatif_harm_details.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_same_family_book_rank2_feature_whatif_buckets.csv"

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

PARAM_FEATURES_EXACT = (
    "dn_exact",
    "cable_section_exact",
    "cable_cores_exact",
    "circuits_exact",
    "concrete_grade_exact",
    "thickness_exact",
    "width_height_exact",
)
PARAM_FEATURES_TIER = (
    "dn_tier_up",
    "cable_section_tier_up",
    "circuits_tier_up",
    "thickness_tier_up",
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


@dataclass
class FeatureGroup:
    rows: list[dict[str, Any]] = field(default_factory=list)
    by_quota_id: dict[str, dict[str, Any]] = field(default_factory=dict)


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


def _name(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_name")) if row else ""


def _qid(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_id")) if row else ""


def _family(row: dict[str, Any] | None) -> str:
    return _clean(row.get("candidate_family")) if row else ""


def _book(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_book")) if row else ""


def _chapter(row: dict[str, Any] | None) -> str:
    return _clean(row.get("quota_chapter")) if row else ""


def _read_eval_details(details_dir: Path, splits: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in splits:
        path = details_dir / f"goal_anchor_clean_eval_details_{split}.jsonl"
        rows.extend(_iter_jsonl(path))
    return rows


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
    for group in groups.values():
        group.rows.sort(key=_candidate_rank)
    return groups


def _numbers(text: str) -> list[float]:
    return [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text or "")]


def _number_tokens(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def _specs(text: str) -> list[str]:
    patterns = [
        r"DN\s*\d+(?:\.\d+)?",
        r"\d+(?:\.\d+)?\s*[*×xX]\s*\d+(?:\.\d+)?(?:\s*[*×xX]\s*\d+(?:\.\d+)?)?",
        r"\d+(?:\.\d+)?\s*(?:mm|m|cm|kW|kV|A|mm2|㎡|m2)",
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


def _same_book(left: str, right: str) -> bool:
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


def _same_family_book(top: dict[str, Any] | None, challenger: dict[str, Any] | None) -> bool:
    return bool(
        top
        and challenger
        and _family(top)
        and _family(top) == _family(challenger)
        and _same_book(_book(top), _book(challenger))
    )


def _has_query_param(query: str) -> bool:
    return bool(_specs(query) or re.search(r"(?:DN|De|mm|毫米|半周长|周长|直径|宽|高|厚|长|规格|型号)\s*[：:]?\s*[≤<]?\s*\d", query or "", re.I))


def _numeric_match_score(query: str, candidate_name: str) -> float:
    query_numbers = _numbers(query)
    candidate_numbers = _numbers(candidate_name)
    if not query_numbers or not candidate_numbers:
        return 0.0
    score = 0.0
    for qnum in query_numbers:
        best = min(abs(cnum - qnum) / max(abs(qnum), 1.0) for cnum in candidate_numbers)
        if best <= 0.001:
            score += 2.0
        elif best <= 0.10:
            score += 1.0
        elif any(cnum >= qnum for cnum in candidate_numbers):
            score += 0.25
    return score


def _param_feature_score(row: dict[str, Any] | None, query: str) -> float:
    if not row:
        return 0.0
    exact = sum(_int(row.get(feature)) for feature in PARAM_FEATURES_EXACT)
    tier = sum(_int(row.get(feature)) for feature in PARAM_FEATURES_TIER)
    conflict = _int(row.get("param_conflict_count")) + _int(row.get("has_param_conflict_reason"))
    score = exact * 2.0 + tier + _float(row.get("numeric_score")) - conflict
    candidate_name = _name(row)
    score += _numeric_match_score(query, candidate_name)
    if _specs(query) and _numbers(candidate_name) and not _numbers(_clean(query)):
        score += 0.25
    if _specs(query) and _numbers(candidate_name):
        score += 0.25
    return score


def _subtype_scores(query: str, top: dict[str, Any] | None, challenger: dict[str, Any] | None) -> dict[str, Any]:
    query_terms = _term_hits(query)
    top_terms = _term_hits(_name(top))
    challenger_terms = _term_hits(_name(challenger))
    top_only = [term for term in top_terms if term not in challenger_terms]
    challenger_only = [term for term in challenger_terms if term not in top_terms]
    top_query_hits = [term for term in top_only if term in query_terms]
    challenger_query_hits = [term for term in challenger_only if term in query_terms]
    top_score = len(top_query_hits)
    challenger_score = len(challenger_query_hits)
    return {
        "subtype_top_score": top_score,
        "subtype_challenger_score": challenger_score,
        "subtype_favors_challenger": challenger_score > top_score,
        "query_terms": _join(query_terms),
        "top_only_terms": _join(top_only),
        "challenger_only_terms": _join(challenger_only),
        "query_hits_top_only": _join(top_query_hits),
        "query_hits_challenger_only": _join(challenger_query_hits),
    }


def _param_scores(query: str, top: dict[str, Any] | None, challenger: dict[str, Any] | None) -> dict[str, Any]:
    query_param = _has_query_param(query)
    top_score = _param_feature_score(top, query) if query_param else 0.0
    challenger_score = _param_feature_score(challenger, query) if query_param else 0.0
    if query_param and _specs(query) and _numbers(_name(challenger)) and not _numbers(_name(top)):
        challenger_score += 0.5
    if query_param and _specs(query) and _numbers(_name(top)) and not _numbers(_name(challenger)):
        top_score += 0.5
    return {
        "param_query_has_explicit": int(query_param),
        "param_top_score": round(top_score, 6),
        "param_challenger_score": round(challenger_score, 6),
        "param_favors_challenger": bool(query_param and challenger_score > top_score),
        "query_specs": _join(_specs(query)),
        "top_numbers": "|".join(_number_tokens(_name(top))),
        "challenger_numbers": "|".join(_number_tokens(_name(challenger))),
    }


def _variant_flags(subtype: dict[str, Any], param: dict[str, Any]) -> dict[str, bool]:
    a = bool(subtype["subtype_favors_challenger"])
    b = bool(param["param_favors_challenger"])
    return {
        "A_subtype_coverage": a,
        "B_param_tier_explicit": b,
        "A_plus_B": a or b,
    }


def _target_rows(
    rank2_rows: list[dict[str, Any]],
    groups: dict[tuple[str, str], FeatureGroup],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rank2_rows:
        split = _clean(row.get("split"))
        group_id = _clean(row.get("group_id"))
        group = groups.get((split, group_id))
        top = group.by_quota_id.get(_clean(row.get("top_id"))) if group else None
        challenger = group.by_quota_id.get(_clean(row.get("positive_id"))) if group else None
        query = _clean(row.get("query"))
        subtype = _subtype_scores(query, top, challenger)
        param = _param_scores(query, top, challenger)
        flags = _variant_flags(subtype, param)
        out.append(
            {
                "dataset": "target_rank2",
                "split": split,
                "group_id": group_id,
                "sample_id": _clean(row.get("sample_id")),
                "query_family": _clean(row.get("query_family")),
                "expected_books": _clean(row.get("expected_books")),
                "query": query,
                "top_id": _qid(top) or _clean(row.get("top_id")),
                "top_name": _name(top) or _clean(row.get("top_name")),
                "challenger_id": _qid(challenger) or _clean(row.get("positive_id")),
                "challenger_name": _name(challenger) or _clean(row.get("positive_name")),
                "challenger_is_positive": 1,
                "same_family_book": int(_same_family_book(top, challenger)),
                "top_chapter": _chapter(top),
                "challenger_chapter": _chapter(challenger),
                "A_subtype_rescue": int(flags["A_subtype_coverage"]),
                "B_param_rescue": int(flags["B_param_tier_explicit"]),
                "A_plus_B_rescue": int(flags["A_plus_B"]),
                **subtype,
                **param,
            }
        )
    return out


def _control_rows(
    eval_rows: list[dict[str, Any]],
    groups: dict[tuple[str, str], FeatureGroup],
    *,
    max_candidate_rank: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for detail in eval_rows:
        if not bool(detail.get("gated_hit1")):
            continue
        split = _clean(detail.get("split"))
        group_id = _clean(detail.get("group_id"))
        group = groups.get((split, group_id))
        if not group:
            continue
        top = group.by_quota_id.get(_clean(detail.get("gated_top_id")))
        if not top or _int(top.get("label")) <= 0:
            continue
        query = _clean(detail.get("query"))
        for candidate in group.rows:
            if _qid(candidate) == _qid(top):
                continue
            if _candidate_rank(candidate) > max_candidate_rank:
                continue
            if _int(candidate.get("label")) > 0:
                continue
            if not _same_family_book(top, candidate):
                continue
            subtype = _subtype_scores(query, top, candidate)
            param = _param_scores(query, top, candidate)
            flags = _variant_flags(subtype, param)
            if not any(flags.values()):
                continue
            out.append(
                {
                    "dataset": "control_top1_hit",
                    "split": split,
                    "group_id": group_id,
                    "sample_id": _clean(detail.get("sample_id")),
                    "query_family": _clean(top.get("query_family")),
                    "expected_books": _book(top),
                    "query": query,
                    "top_id": _qid(top),
                    "top_name": _name(top),
                    "challenger_id": _qid(candidate),
                    "challenger_name": _name(candidate),
                    "challenger_rank": _candidate_rank(candidate),
                    "challenger_is_positive": 0,
                    "same_family_book": 1,
                    "top_chapter": _chapter(top),
                    "challenger_chapter": _chapter(candidate),
                    "A_subtype_harm": int(flags["A_subtype_coverage"]),
                    "B_param_harm": int(flags["B_param_tier_explicit"]),
                    "A_plus_B_harm": int(flags["A_plus_B"]),
                    **subtype,
                    **param,
                }
            )
    return out


def _summarize_variant(
    *,
    variant: str,
    target_rows: list[dict[str, Any]],
    harm_rows: list[dict[str, Any]],
    control_group_count: int,
) -> dict[str, Any]:
    target_key = {
        "A_subtype_coverage": "A_subtype_rescue",
        "B_param_tier_explicit": "B_param_rescue",
        "A_plus_B": "A_plus_B_rescue",
    }[variant]
    harm_key = {
        "A_subtype_coverage": "A_subtype_harm",
        "B_param_tier_explicit": "B_param_harm",
        "A_plus_B": "A_plus_B_harm",
    }[variant]
    rescued = [row for row in target_rows if _int(row.get(target_key)) > 0]
    harm = [row for row in harm_rows if _int(row.get(harm_key)) > 0]
    harm_groups = {(_clean(row.get("split")), _clean(row.get("group_id"))) for row in harm}
    return {
        "variant": variant,
        "target_groups": len(target_rows),
        "max_rescue": len(rescued),
        "max_rescue_rate": _rate(len(rescued), len(target_rows)),
        "control_top1_hit_groups": control_group_count,
        "potential_harm_groups": len(harm_groups),
        "potential_harm_candidates": len(harm),
        "potential_harm_group_rate": _rate(len(harm_groups), control_group_count),
        "upper_bound_net_vs_control_groups": len(rescued) - len(harm_groups),
        "rescue_by_family": _top_items(Counter(_clean(row.get("query_family")) or "<empty>" for row in rescued), 12),
        "harm_by_family": _top_items(Counter(_clean(row.get("query_family")) or "<empty>" for row in harm), 12),
    }


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _bucket_rows(target_rows: list[dict[str, Any]], harm_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    definitions = [
        ("target", target_rows, ("A_subtype_rescue", "B_param_rescue", "A_plus_B_rescue")),
        ("control_harm", harm_rows, ("A_subtype_harm", "B_param_harm", "A_plus_B_harm")),
    ]
    for dataset, source_rows, flag_keys in definitions:
        for flag in flag_keys:
            selected = [row for row in source_rows if _int(row.get(flag)) > 0]
            for dimension in ("query_family", "expected_books", "top_chapter", "challenger_chapter"):
                counter = Counter(_clean(row.get(dimension)) or "<empty>" for row in selected)
                for key, count in counter.most_common():
                    rows.append(
                        {
                            "dataset": dataset,
                            "flag": flag,
                            "dimension": dimension,
                            "key": key,
                            "count": count,
                            "rate_within_flag": _rate(count, len(selected)),
                        }
                    )
    return rows


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


def _sample_rows(rows: list[dict[str, Any]], flag: str, limit: int) -> list[list[object]]:
    selected = [row for row in rows if _int(row.get(flag)) > 0]
    selected.sort(key=lambda row: (_clean(row.get("query_family")), _clean(row.get("query"))))
    return [
        [
            row["query_family"],
            row["query"],
            f"{row['challenger_id']} {row['challenger_name']}",
            f"{row['top_id']} {row['top_name']}",
            row.get("query_hits_challenger_only") or row.get("challenger_numbers"),
            row.get("query_hits_top_only") or row.get("top_numbers"),
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], target_rows: list[dict[str, Any]], harm_rows: list[dict[str, Any]], sample_limit: int) -> None:
    lines = [
        "# Goal Same-Family Same-Book Rank2 Feature What-If",
        "",
        "Stage 4.2 read-only feature draft. It simulates only two candidate features on the 43 same-family same-book rank2 misses: A subtype-term coverage difference, and B explicit parameter/tier difference. No search code, model, or ranking config is changed.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                [
                    "variant",
                    "max_rescue",
                    "rescue_rate",
                    "potential_harm_groups",
                    "harm_rate",
                    "harm_candidates",
                    "net_upper_bound",
                ],
                *[
                    [
                        item["variant"],
                        item["max_rescue"],
                        item["max_rescue_rate"],
                        item["potential_harm_groups"],
                        item["potential_harm_group_rate"],
                        item["potential_harm_candidates"],
                        item["upper_bound_net_vs_control_groups"],
                    ]
                    for item in report["variants"]
                ],
            ]
        ),
        "",
        "## Rescue Families",
        "",
    ]
    for item in report["variants"]:
        lines.extend([f"### {item['variant']}", "", _md_table(_counter_table(item["rescue_by_family"])), ""])

    lines.extend(["## Harm Families", ""])
    for item in report["variants"]:
        lines.extend([f"### {item['variant']}", "", _md_table(_counter_table(item["harm_by_family"])), ""])

    lines.extend(
        [
            "## Target Samples",
            "",
            "A subtype-term coverage:",
            "",
            _md_table([["family", "query", "challenger_positive", "current_top", "challenger_evidence", "top_evidence"]] + _sample_rows(target_rows, "A_subtype_rescue", sample_limit)),
            "",
            "B explicit parameter/tier:",
            "",
            _md_table([["family", "query", "challenger_positive", "current_top", "challenger_evidence", "top_evidence"]] + _sample_rows(target_rows, "B_param_rescue", sample_limit)),
            "",
            "## Potential Harm Samples",
            "",
            "A subtype-term coverage:",
            "",
            _md_table([["family", "query", "wrong_challenger", "current_correct_top", "challenger_evidence", "top_evidence"]] + _sample_rows(harm_rows, "A_subtype_harm", sample_limit)),
            "",
            "B explicit parameter/tier:",
            "",
            _md_table([["family", "query", "wrong_challenger", "current_correct_top", "challenger_evidence", "top_evidence"]] + _sample_rows(harm_rows, "B_param_harm", sample_limit)),
            "",
            "## Artifacts",
            "",
            _md_table(
                [
                    ["artifact", "path"],
                    ["target_details_csv", report["target_details_csv"]],
                    ["harm_details_csv", report["harm_details_csv"]],
                    ["buckets_csv", report["buckets_csv"]],
                ]
            ),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline what-if for two same-family/book rank2 feature drafts")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--rank2-details", default=str(DEFAULT_RANK2_DETAILS))
    parser.add_argument("--eval-details-dir", default=str(DEFAULT_EVAL_DETAILS_DIR))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--control-max-candidate-rank", type=int, default=5)
    parser.add_argument("--sample-limit", type=int, default=8)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--target-details-csv", default=str(DEFAULT_TARGET_DETAILS_CSV))
    parser.add_argument("--harm-details-csv", default=str(DEFAULT_HARM_DETAILS_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    rank2_rows = _read_csv(Path(args.rank2_details))
    eval_rows = _read_eval_details(Path(args.eval_details_dir), args.splits)

    split_to_group_ids: dict[str, set[str]] = defaultdict(set)
    for row in rank2_rows:
        split_to_group_ids[_clean(row.get("split"))].add(_clean(row.get("group_id")))
    control_details = [row for row in eval_rows if bool(row.get("gated_hit1"))]
    for row in control_details:
        split_to_group_ids[_clean(row.get("split"))].add(_clean(row.get("group_id")))

    groups = _load_feature_groups(Path(args.data_dir), split_to_group_ids)
    target_rows = _target_rows(rank2_rows, groups)
    harm_rows = _control_rows(
        control_details,
        groups,
        max_candidate_rank=args.control_max_candidate_rank,
    )

    control_group_count = len({(_clean(row.get("split")), _clean(row.get("group_id"))) for row in control_details})
    variants = [
        _summarize_variant(
            variant=variant,
            target_rows=target_rows,
            harm_rows=harm_rows,
            control_group_count=control_group_count,
        )
        for variant in ("A_subtype_coverage", "B_param_tier_explicit", "A_plus_B")
    ]

    target_fields = [
        "dataset",
        "split",
        "group_id",
        "sample_id",
        "query_family",
        "expected_books",
        "query",
        "top_id",
        "top_name",
        "challenger_id",
        "challenger_name",
        "A_subtype_rescue",
        "B_param_rescue",
        "A_plus_B_rescue",
        "subtype_top_score",
        "subtype_challenger_score",
        "param_top_score",
        "param_challenger_score",
        "param_query_has_explicit",
        "query_terms",
        "query_specs",
        "top_only_terms",
        "challenger_only_terms",
        "query_hits_top_only",
        "query_hits_challenger_only",
        "top_numbers",
        "challenger_numbers",
        "top_chapter",
        "challenger_chapter",
    ]
    harm_fields = [
        "dataset",
        "split",
        "group_id",
        "sample_id",
        "query_family",
        "expected_books",
        "query",
        "top_id",
        "top_name",
        "challenger_id",
        "challenger_name",
        "challenger_rank",
        "A_subtype_harm",
        "B_param_harm",
        "A_plus_B_harm",
        "subtype_top_score",
        "subtype_challenger_score",
        "param_top_score",
        "param_challenger_score",
        "param_query_has_explicit",
        "query_terms",
        "query_specs",
        "top_only_terms",
        "challenger_only_terms",
        "query_hits_top_only",
        "query_hits_challenger_only",
        "top_numbers",
        "challenger_numbers",
        "top_chapter",
        "challenger_chapter",
    ]
    _write_csv(Path(args.target_details_csv), target_rows, target_fields)
    _write_csv(Path(args.harm_details_csv), harm_rows, harm_fields)
    _write_csv(
        Path(args.buckets_csv),
        _bucket_rows(target_rows, harm_rows),
        ["dataset", "flag", "dimension", "key", "count", "rate_within_flag"],
    )

    report = {
        "stage": "Goal LTR v1 / stage 4.2 same-family/book rank2 feature what-if",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "feature_drafts": [
            "A_subtype_coverage: candidate-only subtype terms covered by query more than current top",
            "B_param_tier_explicit: explicit query parameter/tier evidence favors challenger",
        ],
        "data_dir": args.data_dir,
        "rank2_details": args.rank2_details,
        "eval_details_dir": args.eval_details_dir,
        "splits": args.splits,
        "control_max_candidate_rank": args.control_max_candidate_rank,
        "target_groups": len(target_rows),
        "control_top1_hit_groups": control_group_count,
        "feature_group_count": len(groups),
        "variants": variants,
        "target_details_csv": args.target_details_csv,
        "harm_details_csv": args.harm_details_csv,
        "buckets_csv": args.buckets_csv,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, target_rows, harm_rows, args.sample_limit)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "target_groups": report["target_groups"],
                    "control_top1_hit_groups": report["control_top1_hit_groups"],
                    "variants": variants,
                },
                "artifacts": {
                    "report_json": str(report_json),
                    "report_md": args.report_md,
                    "target_details_csv": args.target_details_csv,
                    "harm_details_csv": args.harm_details_csv,
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
