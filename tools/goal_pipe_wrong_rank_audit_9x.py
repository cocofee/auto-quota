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

DEFAULT_GAP_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_FEATURES = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run" / "ltr_features_dev.jsonl"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_pipe_wrong_rank_9x_audit"

MATERIAL_TERMS = [
    "镀锌钢管",
    "焊接钢管",
    "无缝钢管",
    "碳钢",
    "钢管",
    "塑料",
    "PPR",
    "UPVC",
    "PVC",
    "铸铁",
    "球墨",
    "不锈钢",
    "复合",
    "金属骨架",
]
CONNECTION_TERMS = ["螺纹", "沟槽", "卡箍", "焊接", "电弧焊", "热熔", "电熔", "粘接", "胶圈", "承插", "法兰", "机械接口"]
SCENE_TERMS = ["室内", "室外", "给水", "排水", "雨水", "采暖", "空调", "消火栓", "喷淋", "燃气", "工业管道", "防腐", "保温", "刷油", "桥涵", "市政"]
PIPE_WORDS = ["管", "管道", "钢管", "塑料管", "管件", "管线", "套管", "风管"]
NON_PIPE_QUERY_TERMS = ["扩声", "服务器", "铁艺门", "紫外", "消毒器", "人防其他部件", "栏杆", "电视设备", "安检设备", "音频处理器"]


@dataclass
class FeatureGroup:
    group_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    positives: list[dict[str, Any]] = field(default_factory=list)
    top1: dict[str, Any] | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
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


def _load_groups(feature_path: Path, group_ids: set[str]) -> dict[str, FeatureGroup]:
    groups: dict[str, FeatureGroup] = {}
    for row in _iter_jsonl(feature_path):
        group_id = _clean(row.get("group_id"))
        if group_id not in group_ids:
            continue
        group = groups.setdefault(group_id, FeatureGroup(group_id=group_id))
        group.rows.append(row)
        if _to_int(row.get("candidate_rank")) == 1:
            group.top1 = row
        if _to_int(row.get("label")) > 0:
            group.positives.append(row)
    for group in groups.values():
        group.rows.sort(key=lambda item: (_to_int(item.get("candidate_rank")) or 999999, _clean(item.get("quota_id"))))
        group.positives.sort(key=lambda item: (_to_int(item.get("candidate_rank")) or 999999, _clean(item.get("quota_id"))))
        if not group.top1 and group.rows:
            group.top1 = group.rows[0]
    return groups


def _book_key(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    book = _clean(row.get("quota_book"))
    if book:
        return book
    chapter = _clean(row.get("quota_chapter"))
    if "_" in chapter:
        return chapter.split("_", 1)[0].lstrip("0") or chapter.split("_", 1)[0]
    return ""


def _terms(text: str, terms: list[str]) -> list[str]:
    upper = text.upper()
    found: list[str] = []
    for term in terms:
        needle = term.upper()
        if needle in upper and term not in found:
            found.append(term)
    return found


def _pipe_like(text: str) -> bool:
    return bool(_terms(text, PIPE_WORDS))


def _numbers(text: str) -> list[int]:
    values: list[int] = []
    patterns = [
        r"(?:DN|DE|De|SC|Φ|φ)\s*([0-9]{1,4})",
        r"(?:公称直径|公称外径|管径|外径|直径)\D{0,8}([0-9]{1,4})",
        r"([0-9]{2,4})\s*mm",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if value not in values:
                values.append(value)
    return values


def _closest_gap(targets: list[int], values: list[int]) -> int | None:
    if not targets or not values:
        return None
    return min(abs(target - value) for target in targets for value in values)


def _flag_text(flags: list[str]) -> str:
    return "|".join(flags)


def _feature_int(row: dict[str, Any] | None, name: str) -> int:
    return _to_int(row.get(name)) if row else 0


def _classify(query: str, top: dict[str, Any] | None, positive: dict[str, Any] | None, rank: int) -> tuple[str, list[str], dict[str, Any]]:
    top_name = _clean(top.get("quota_name")) if top else ""
    positive_name = _clean(positive.get("quota_name")) if positive else ""
    full_top = f"{top_name} {_clean(top.get('quota_chapter')) if top else ''}"
    full_pos = f"{positive_name} {_clean(positive.get('quota_chapter')) if positive else ''}"
    query_nums = _numbers(query)
    top_nums = _numbers(full_top)
    pos_nums = _numbers(full_pos)
    top_gap = _closest_gap(query_nums, top_nums)
    pos_gap = _closest_gap(query_nums, pos_nums)
    top_book = _book_key(top)
    pos_book = _book_key(positive)
    query_material = _terms(query, MATERIAL_TERMS)
    top_material = _terms(full_top, MATERIAL_TERMS)
    pos_material = _terms(full_pos, MATERIAL_TERMS)
    query_conn = _terms(query, CONNECTION_TERMS)
    top_conn = _terms(full_top, CONNECTION_TERMS)
    pos_conn = _terms(full_pos, CONNECTION_TERMS)
    query_scene = _terms(query, SCENE_TERMS)
    top_scene = _terms(full_top, SCENE_TERMS)
    pos_scene = _terms(full_pos, SCENE_TERMS)
    flags: list[str] = []

    if not _pipe_like(query) or _terms(query, NON_PIPE_QUERY_TERMS) or (positive_name and not _pipe_like(positive_name) and not _pipe_like(query)):
        flags.append("pipe_family_false_positive_or_cross_domain")
    if top_book and pos_book and top_book != pos_book:
        flags.append("book_or_chapter_bias")
    if query_nums:
        if pos_gap is not None and (top_gap is None or pos_gap < top_gap):
            flags.append("param_tier_or_dn_gap")
    elif top_nums and pos_nums and set(top_nums) != set(pos_nums):
        flags.append("query_evidence_insufficient_for_dn")
    if query_conn:
        if any(term in pos_conn for term in query_conn) and not any(term in top_conn for term in query_conn):
            flags.append("connection_or_method_mismatch")
    elif pos_conn and top_conn and set(pos_conn) != set(top_conn):
        flags.append("connection_or_method_mismatch")
    if query_material:
        if any(term in pos_material for term in query_material) and not any(term in top_material for term in query_material):
            flags.append("material_mismatch")
    elif pos_material and top_material and set(pos_material) != set(top_material):
        flags.append("material_mismatch")
    if query_scene:
        if any(term in pos_scene for term in query_scene) and not any(term in top_scene for term in query_scene):
            flags.append("system_scene_mismatch")
    elif pos_scene and top_scene and set(pos_scene) != set(top_scene):
        flags.append("system_scene_mismatch")
    if _feature_int(positive, "param_exact_count") > _feature_int(top, "param_exact_count"):
        flags.append("positive_has_more_param_exact")
    if _feature_int(top, "book_conflict") and not _feature_int(positive, "book_conflict"):
        flags.append("top_has_book_conflict")
    if rank <= 5:
        flags.append("near_miss_rank_2_5")
    if not query_nums and not query_conn and len(query) <= 5 and not _terms(query, ["DN", "SC", "DE", "规格", "材质"]):
        flags.append("short_or_generic_query")

    priority = [
        "pipe_family_false_positive_or_cross_domain",
        "book_or_chapter_bias",
        "param_tier_or_dn_gap",
        "connection_or_method_mismatch",
        "material_mismatch",
        "system_scene_mismatch",
        "near_miss_rank_2_5",
        "query_evidence_insufficient_for_dn",
        "short_or_generic_query",
        "positive_has_more_param_exact",
    ]
    primary = next((flag for flag in priority if flag in flags), "other_pipe_wrong_rank")
    context = {
        "top_book_key": top_book,
        "positive_book_key": pos_book,
        "query_numbers": ",".join(str(item) for item in query_nums),
        "top_numbers": ",".join(str(item) for item in top_nums[:5]),
        "positive_numbers": ",".join(str(item) for item in pos_nums[:5]),
        "top_number_gap": "" if top_gap is None else top_gap,
        "positive_number_gap": "" if pos_gap is None else pos_gap,
        "query_material": ",".join(query_material),
        "top_material": ",".join(top_material),
        "positive_material": ",".join(pos_material),
        "query_connection": ",".join(query_conn),
        "top_connection": ",".join(top_conn),
        "positive_connection": ",".join(pos_conn),
        "query_scene": ",".join(query_scene),
        "top_scene": ",".join(top_scene),
        "positive_scene": ",".join(pos_scene),
    }
    return primary, flags, context


def _audit_rows(gap_rows: list[dict[str, Any]], groups: dict[str, FeatureGroup]) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for gap in gap_rows:
        group_id = _clean(gap.get("group_id"))
        group = groups.get(group_id)
        top = group.top1 if group else None
        positive = group.positives[0] if group and group.positives else None
        rank = _to_int(positive.get("candidate_rank") if positive else gap.get("positive_rank_min"))
        primary, flags, context = _classify(_clean(gap.get("query")), top, positive, rank)
        top_score = _to_float(top.get("current_score")) if top else 0.0
        pos_score = _to_float(positive.get("current_score")) if positive else 0.0
        row = {
            "split": "dev",
            "group_id": group_id,
            "sample_id": _clean(gap.get("sample_id")),
            "source_file": _clean(gap.get("source_file")),
            "province": _clean(gap.get("province")),
            "query": _clean(gap.get("query")),
            "rank_bucket": _clean(gap.get("rank_bucket")),
            "positive_rank": rank,
            "primary_issue": primary,
            "flags": _flag_text(flags),
            "top1_id": _clean(top.get("quota_id")) if top else _clean(gap.get("top1_id")),
            "top1_name": _clean(top.get("quota_name")) if top else _clean(gap.get("top1_name")),
            "top1_book": _book_key(top) or _clean(gap.get("top1_book")),
            "top1_chapter": _clean(top.get("quota_chapter")) if top else _clean(gap.get("top1_chapter")),
            "top1_score": round(top_score, 6),
            "positive_id": _clean(positive.get("quota_id")) if positive else _clean(gap.get("positive_ids_in_top80")).split("|")[0],
            "positive_name": _clean(positive.get("quota_name")) if positive else _clean(gap.get("positive_names_in_top80")),
            "positive_book": _book_key(positive),
            "positive_chapter": _clean(positive.get("quota_chapter")) if positive else "",
            "positive_score": round(pos_score, 6),
            "score_gap_top_minus_positive": round(top_score - pos_score, 6),
            "top_reasons": _clean(top.get("reasons")) if top else _clean(gap.get("top1_reasons")),
            "positive_reasons": _clean(positive.get("reasons")) if positive else "",
            "book_match": int((_book_key(top) or "") == (_book_key(positive) or "") and bool(_book_key(top))),
            "top_param_exact_count": _feature_int(top, "param_exact_count"),
            "positive_param_exact_count": _feature_int(positive, "param_exact_count"),
            "top_param_conflict_count": _feature_int(top, "param_conflict_count"),
            "positive_param_conflict_count": _feature_int(positive, "param_conflict_count"),
            "top_dn_exact": _feature_int(top, "dn_exact"),
            "positive_dn_exact": _feature_int(positive, "dn_exact"),
            "top_dn_tier_up": _feature_int(top, "dn_tier_up"),
            "positive_dn_tier_up": _feature_int(positive, "dn_tier_up"),
        }
        row.update(context)
        audited.append(row)
    return audited


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in ("primary_issue", "rank_bucket", "province", "source_file", "top1_book", "positive_book", "book_match"):
            counters[("all", dimension)][_clean(row.get(dimension)) or "<empty>"] += 1
        for flag in _clean(row.get("flags")).split("|"):
            if flag:
                counters[("all", "flag")][flag] += 1
    bucketed: list[dict[str, Any]] = []
    total = len(rows)
    for (_, dimension), counter in sorted(counters.items(), key=lambda item: item[0][1]):
        for key, count in counter.most_common():
            bucketed.append({"scope": "dev_pipe_wrong_rank", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return bucketed


def _clean_learning_count(rows: list[dict[str, Any]]) -> int:
    excluded = {"pipe_family_false_positive_or_cross_domain", "short_or_generic_query"}
    return sum(1 for row in rows if row["primary_issue"] not in excluded)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Stage 9.1 Dev-only Pipe Wrong-rank Audit",
        "",
        "Read-only audit of dev `pipe + top80_present_but_wrong_rank` samples. No training, tuning, rule patch, ranking change, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["clean_learning_candidate_rows", report["clean_learning_candidate_rows"]],
                ["clean_learning_candidate_rate", report["clean_learning_candidate_rate"]],
                ["selected_next_issue", report["next_candidate"]["primary_issue"]],
                ["selected_support", report["next_candidate"]["support"]],
                ["next_stage", report["next_candidate"]["next_stage"]],
            ]
        ),
        "",
        "## Primary Issue Buckets",
        "",
        _md_table([["issue", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in report["primary_issue_buckets"]]),
        "",
        "## Rank Buckets",
        "",
        _md_table([["rank_bucket", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in report["rank_buckets"]]),
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.1 dev-only pipe wrong-rank audit")
    parser.add_argument("--gap-csv", default=str(DEFAULT_GAP_CSV))
    parser.add_argument("--feature-path", default=str(DEFAULT_FEATURES))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    gap_rows = [
        row
        for row in _read_csv(Path(args.gap_csv))
        if _clean(row.get("split")) == "dev"
        and _clean(row.get("status")) == "top80_present_but_wrong_rank"
        and _clean(row.get("query_family")) == "pipe"
    ]
    group_ids = {_clean(row.get("group_id")) for row in gap_rows if _clean(row.get("group_id"))}
    groups = _load_groups(Path(args.feature_path), group_ids)
    audited_rows = _audit_rows(gap_rows, groups)
    buckets = _bucket_rows(audited_rows)
    primary_buckets = [row for row in buckets if row["dimension"] == "primary_issue"]
    rank_buckets = [row for row in buckets if row["dimension"] == "rank_bucket"]
    selected = next((row for row in primary_buckets if row["key"] not in {"pipe_family_false_positive_or_cross_domain", "short_or_generic_query"}), primary_buckets[0] if primary_buckets else {"key": "", "count": 0})
    clean_count = _clean_learning_count(audited_rows)
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.1 dev-only pipe wrong-rank audit",
        "read_only": True,
        "eval_only": True,
        "dev_only_selection": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "target_rows": len(audited_rows),
        "feature_group_missing": len(group_ids - set(groups)),
        "clean_learning_candidate_rows": clean_count,
        "clean_learning_candidate_rate": _rate(clean_count, len(audited_rows)),
        "primary_issue_buckets": primary_buckets,
        "rank_buckets": rank_buckets,
        "next_candidate": {
            "selected_from": "dev_only_pipe_wrong_rank",
            "primary_issue": selected["key"],
            "support": selected["count"],
            "selection_policy": "largest transferable primary_issue bucket after excluding false-positive/generic-query buckets; heldout not used",
            "next_stage": "9.2 design-review only for the selected transferable pipe issue; do not implement rules yet",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.1 only audits one dev bucket. It separates false pipe/cross-domain and weak-query cases from transferable pipe ranking issues, and does not train, tune, patch rules, or modify GoalSearcher.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "rank_bucket",
        "positive_rank",
        "primary_issue",
        "flags",
        "top1_id",
        "top1_name",
        "top1_book",
        "top1_chapter",
        "top1_score",
        "positive_id",
        "positive_name",
        "positive_book",
        "positive_chapter",
        "positive_score",
        "score_gap_top_minus_positive",
        "top_reasons",
        "positive_reasons",
        "book_match",
        "query_numbers",
        "top_numbers",
        "positive_numbers",
        "top_number_gap",
        "positive_number_gap",
        "query_material",
        "top_material",
        "positive_material",
        "query_connection",
        "top_connection",
        "positive_connection",
        "query_scene",
        "top_scene",
        "positive_scene",
        "top_param_exact_count",
        "positive_param_exact_count",
        "top_param_conflict_count",
        "positive_param_conflict_count",
        "top_dn_exact",
        "positive_dn_exact",
        "top_dn_tier_up",
        "positive_dn_tier_up",
    ]
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["rows_csv"]), audited_rows, row_fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, ["scope", "dimension", "key", "count", "rate"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "target_rows": report["target_rows"],
                    "clean_learning_candidate_rows": report["clean_learning_candidate_rows"],
                    "primary_issue_buckets": primary_buckets,
                    "rank_buckets": rank_buckets,
                    "next_candidate": report["next_candidate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
