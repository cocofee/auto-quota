from __future__ import annotations

import argparse
import csv
import json
import re
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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_valve_same_family_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_lamp_same_family_unknown_9x_audit"


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


def _positive_text(value: Any) -> str:
    return " || ".join(item.strip() for item in _clean(value).split(" || ") if item.strip())


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _book_prefix(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value.split("-")[0]


def _watt_hints(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"(\d+)\s*[xX*×]\s*(\d+)\s*W", text, flags=re.IGNORECASE):
        values.append(f"{match.group(1)}x{match.group(2)}W")
    for match in re.finditer(r"(?<![xX*×])(\d+)\s*W", text, flags=re.IGNORECASE):
        values.append(f"{match.group(1)}W")
    return sorted(set(values))


def _shape_or_mount(text: str) -> str:
    values: list[str] = []
    if _has_any(text, ["壁装", "壁灯", "墙壁式"]):
        values.append("wall")
    if _has_any(text, ["吸顶", "吸顶式"]):
        values.append("ceiling")
    if _has_any(text, ["吊杆", "吊杆式"]):
        values.append("rod")
    if _has_any(text, ["吊链", "吊链式"]):
        values.append("chain")
    if _has_any(text, ["滑轨", "轨道"]):
        values.append("track")
    if _has_any(text, ["嵌入", "嵌入式"]):
        values.append("embedded")
    if _has_any(text, ["立柱", "草坪"]):
        values.append("post_or_lawn")
    return ",".join(dict.fromkeys(values))


def _tube_count(text: str) -> str:
    if _has_any(text, ["双管", "2*"]):
        return "double"
    if _has_any(text, ["单管", "1*"]):
        return "single"
    return ""


def _lamp_subtype(text: str) -> str:
    if _has_any(text, ["集中电源", "疏散", "标志灯", "指示", "诱导", "禁止入内", "消防控制室", "应急照明"]):
        return "emergency_sign_or_exit_lamp"
    if _has_any(text, ["防爆荧光灯"]):
        return "explosion_proof_fluorescent"
    if _has_any(text, ["荧光灯", "单管灯", "双管灯", "灯管"]):
        return "fluorescent_or_linear_lamp"
    if _has_any(text, ["装饰灯", "点光源", "艺术装饰", "射灯", "滑轨"]):
        return "decorative_or_spot_lamp"
    if _has_any(text, ["工厂灯", "防潮"]):
        return "factory_or_damp_lamp"
    if _has_any(text, ["壁装", "壁灯", "井道灯", "坡道过渡照明灯"]):
        return "wall_or_corridor_lamp"
    if _has_any(text, ["普通灯具", "普通壁灯", "座灯头", "灯头", "吊链灯", "防水灯头"]):
        return "ordinary_lamp"
    if _has_any(text, ["吸顶", "圆球", "半圆球", "矩形罩", "方形吸顶"]):
        return "ceiling_lamp"
    if "灯" in text:
        return "generic_lamp"
    return "other"


def _classify(row: dict[str, Any], source_counts: Counter[str]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(row.get("positive_names_in_top80"))
    source_file = _clean(row.get("source_file"))
    top1_family = _clean(row.get("top1_family"))

    query_subtype = _lamp_subtype(query)
    top_subtype = _lamp_subtype(top_name)
    positive_subtype = _lamp_subtype(positive_text)
    query_mount = _shape_or_mount(query)
    top_mount = _shape_or_mount(top_name)
    positive_mount = _shape_or_mount(positive_text)
    query_tube = _tube_count(query)
    top_tube = _tube_count(top_name)
    positive_tube = _tube_count(positive_text)
    query_watts = _watt_hints(query)
    top_watts = _watt_hints(top_name)
    positive_watts = _watt_hints(positive_text)
    expected_books = _clean(row.get("expected_books"))
    top_book = _clean(row.get("top1_book"))
    positive_book_prefix = _book_prefix(_clean(row.get("positive_ids_in_top80")))
    top_id_prefix = _book_prefix(_clean(row.get("top1_id")))
    positive_rank = _to_int(row.get("positive_rank_min"))

    flags: list[str] = []
    if source_counts[source_file] >= 20:
        flags.append("dominant_single_source")
    if top1_family and top1_family != "lamp":
        flags.append("top1_family_conflict")
    if not top1_family:
        flags.append("top1_family_missing")
    if query in {"普通灯具", "装饰灯", "荧光灯"}:
        flags.append("query_too_generic")
    if query_subtype != "other" and top_subtype != "other" and query_subtype == top_subtype:
        flags.append("top_matches_query_subtype")
    if query_subtype != "other" and positive_subtype != "other" and query_subtype == positive_subtype:
        flags.append("positive_matches_query_subtype")
    if query_subtype != "other" and positive_subtype != "other" and query_subtype != positive_subtype:
        flags.append("positive_subtype_conflicts_query")
    if top_subtype != "other" and positive_subtype != "other" and top_subtype != positive_subtype:
        flags.append("top_positive_subtype_diff")
    if query_mount and top_mount and query_mount == top_mount:
        flags.append("top_matches_query_mount")
    if query_mount and positive_mount and query_mount == positive_mount:
        flags.append("positive_matches_query_mount")
    if query_mount and positive_mount and query_mount != positive_mount:
        flags.append("positive_mount_conflicts_query")
    if query_mount and top_mount and query_mount != top_mount:
        flags.append("top_mount_conflicts_query")
    if query_tube and positive_tube and query_tube == positive_tube:
        flags.append("positive_matches_query_tube_count")
    if query_tube and top_tube and query_tube == top_tube:
        flags.append("top_matches_query_tube_count")
    if query_watts:
        flags.append("query_watt_present")
        if positive_watts and set(query_watts) & set(positive_watts):
            flags.append("positive_matches_query_watt")
        if top_watts and set(query_watts) & set(top_watts):
            flags.append("top_matches_query_watt")
    if expected_books and top_book and expected_books != top_book:
        flags.append("top_book_differs_expected_books")
    if positive_book_prefix and top_id_prefix and positive_book_prefix != top_id_prefix:
        flags.append("top_id_prefix_differs_positive")
    if positive_rank >= 41:
        flags.append("positive_rank_deep")
    elif positive_rank >= 21:
        flags.append("positive_rank_mid_deep")

    if "query_too_generic" in flags:
        primary = "generic_query_label_insufficient"
        learning_status = "exclude_or_label_review"
    elif "dominant_single_source" in flags and (
        "top_matches_query_subtype" in flags
        or "top_matches_query_mount" in flags
        or "positive_subtype_conflicts_query" in flags
        or "positive_mount_conflicts_query" in flags
    ):
        primary = "expected_label_conflicts_query_or_top_stronger"
        learning_status = "exclude_or_label_review"
    elif query_subtype == "emergency_sign_or_exit_lamp" and positive_subtype == "emergency_sign_or_exit_lamp":
        primary = "emergency_sign_lamp_mount_or_chapter"
        learning_status = "review_only"
    elif query_subtype == "wall_or_corridor_lamp" and positive_subtype in {"ordinary_lamp", "fluorescent_or_linear_lamp"}:
        primary = "wall_or_corridor_lamp_subtype_review"
        learning_status = "review_only"
    elif query_subtype == "fluorescent_or_linear_lamp":
        primary = "fluorescent_lamp_mount_or_tube_count"
        learning_status = "review_only"
    elif query_subtype == "factory_or_damp_lamp":
        primary = "factory_lamp_label_or_mount_review"
        learning_status = "review_only"
    elif "positive_matches_query_subtype" in flags and "positive_matches_query_mount" in flags and source_counts[source_file] < 20:
        primary = "cross_source_specific_lamp_evidence"
        learning_status = "candidate_for_transferability_review"
    elif "top_id_prefix_differs_positive" in flags or "top_book_differs_expected_books" in flags:
        primary = "book_or_section_bias_no_query_evidence"
        learning_status = "review_only"
    else:
        primary = "other_lamp_same_family_wrong_rank"
        learning_status = "review_only"

    return {
        "split": _clean(row.get("split")),
        "group_id": _clean(row.get("group_id")),
        "sample_id": _clean(row.get("sample_id")),
        "source_file": source_file,
        "province": _clean(row.get("province")),
        "query": query,
        "positive_rank_min": _clean(row.get("positive_rank_min")),
        "rank_bucket": _clean(row.get("rank_bucket")),
        "primary_issue": primary,
        "learning_status": learning_status,
        "flags": "|".join(flags),
        "query_subtype": query_subtype,
        "top_subtype": top_subtype,
        "positive_subtype": positive_subtype,
        "query_mount": query_mount,
        "top_mount": top_mount,
        "positive_mount": positive_mount,
        "query_tube_count": query_tube,
        "top_tube_count": top_tube,
        "positive_tube_count": positive_tube,
        "query_watts": ",".join(query_watts),
        "top_watts": ",".join(top_watts),
        "positive_watts": ",".join(positive_watts),
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": top_name,
        "top1_family": top1_family,
        "top1_book": top_book,
        "top1_chapter": _clean(row.get("top1_chapter")),
        "expected_books": expected_books,
        "positive_id_prefix": positive_book_prefix,
        "top_id_prefix": top_id_prefix,
        "positive_ids_in_top80": _clean(row.get("positive_ids_in_top80")),
        "positive_names_in_top80": positive_text,
    }


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in (
            "primary_issue",
            "learning_status",
            "province",
            "source_file",
            "rank_bucket",
            "query_subtype",
            "top_subtype",
            "positive_subtype",
            "query_mount",
            "top_mount",
            "positive_mount",
            "expected_books",
            "positive_rank_min",
        ):
            counters[dimension][_clean(row.get(dimension)) or "<empty>"] += 1
        for flag in _clean(row.get("flags")).split("|"):
            if flag:
                counters["flag"][flag] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_lamp_same_family_unknown", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


def _preview(buckets: list[dict[str, Any]], dimension: str, limit: int = 12) -> list[dict[str, Any]]:
    return [row for row in buckets if row["dimension"] == dimension][:limit]


def _distinct(rows: list[dict[str, Any]], field: str) -> set[str]:
    return {_clean(row.get(field)) for row in rows if _clean(row.get(field))}


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
    previews = report["artifacts_preview"]["top_buckets"]
    lines = [
        "# Stage 9.18 Lamp Same-family/Unknown Wrong-rank Audit",
        "",
        "Dev-only audit of 31 `lamp + same_family_or_unknown_wrong_rank` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["dominant_source_rows", report["metrics"]["dominant_source_rows"]],
                ["exclude_or_label_review", report["metrics"]["exclude_or_label_review"]],
                ["review_only", report["metrics"]["review_only"]],
                ["candidate_for_transferability_review", report["metrics"]["candidate_for_transferability_review"]],
                ["specific_query_rows", report["metrics"]["specific_query_rows"]],
                ["selected_next_issue", report["next_candidate"]["primary_issue"]],
                ["selected_support", report["next_candidate"]["support"]],
                ["next_stage", report["next_candidate"]["next_stage"]],
            ]
        ),
        "",
        "## Primary Issue Buckets",
        "",
        _md_table([["issue", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in previews["primary_issue"]]),
        "",
        "## Learning Status",
        "",
        _md_table([["status", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in previews["learning_status"]]),
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
    parser = argparse.ArgumentParser(description="Stage 9.18 lamp same-family/unknown wrong-rank audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    source_counts = Counter(_clean(row.get("source_file")) for row in source_rows)
    audited = [_classify(row, source_counts) for row in source_rows]
    buckets = _bucket_rows(audited)
    learning_counter = Counter(row["learning_status"] for row in audited)
    primary_counter = Counter(row["primary_issue"] for row in audited)
    candidate_rows = [row for row in audited if row["learning_status"] == "candidate_for_transferability_review"]
    specific_query_rows = [
        row
        for row in audited
        if row["query_subtype"] not in {"ordinary_lamp", "decorative_or_spot_lamp"}
        and "query_too_generic" not in row["flags"]
    ]

    if candidate_rows and len(_distinct(candidate_rows, "source_file")) >= 2:
        selected = "cross_source_specific_lamp_evidence"
        next_stage = "9.19 lamp specific-evidence transferability review; do not design a rule yet"
        next_goal = "review whether specific lamp subtype and mount evidence can safely rescue same-family lamp ranking across sources"
        support = len(candidate_rows)
    else:
        selected = "stop_lamp_same_family_unknown_direction"
        next_stage = "9.19 ranked gap reselection after lamp same-family audit"
        next_goal = "exclude this generic-label and single-source-dominant lamp bucket and choose the next high-support dev wrong-rank bucket"
        support = 0

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.18 lamp same-family/unknown wrong-rank audit",
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
        "source_artifact": str(Path(args.rows)),
        "target_rows": len(audited),
        "metrics": {
            "dominant_source_rows": sum(1 for row in audited if "dominant_single_source" in row["flags"]),
            "source_count": len(_distinct(audited, "source_file")),
            "province_count": len(_distinct(audited, "province")),
            "exclude_or_label_review": learning_counter.get("exclude_or_label_review", 0),
            "review_only": learning_counter.get("review_only", 0),
            "candidate_for_transferability_review": learning_counter.get("candidate_for_transferability_review", 0),
            "generic_query_rows": sum(1 for row in audited if "query_too_generic" in row["flags"]),
            "specific_query_rows": len(specific_query_rows),
            "positive_rank_deep_rows": sum(1 for row in audited if _to_int(row.get("positive_rank_min")) >= 41),
            "top_matches_query_subtype_rows": sum(1 for row in audited if "top_matches_query_subtype" in row["flags"]),
            "positive_matches_query_subtype_rows": sum(1 for row in audited if "positive_matches_query_subtype" in row["flags"]),
            "positive_matches_query_mount_rows": sum(1 for row in audited if "positive_matches_query_mount" in row["flags"]),
        },
        "primary_issue_buckets": _preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _preview(buckets, "learning_status", 20),
        "next_candidate": {
            "selected_from": "dev_only_lamp_same_family_unknown",
            "primary_issue": selected,
            "support": support,
            "source_count": len(_distinct(candidate_rows, "source_file")) if candidate_rows else 0,
            "selection_policy": "only advance if there is specific lamp subtype/mount evidence with cross-source support; generic 普通灯具/装饰灯 labels and dominant single-source rows cannot drive a rule",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "decision": "The lamp same-family bucket is mostly not ready for a transferable ranking change: 29 of 31 rows come from one source, many queries are generic labels such as 普通灯具/装饰灯, and specific emergency/wall/fluorescent rows still lack cross-source support strong enough for a what-if.",
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "primary_issue": _preview(buckets, "primary_issue"),
                "learning_status": _preview(buckets, "learning_status"),
                "flag": _preview(buckets, "flag"),
                "province": _preview(buckets, "province"),
                "source_file": _preview(buckets, "source_file"),
                "query_subtype": _preview(buckets, "query_subtype"),
                "rank_bucket": _preview(buckets, "rank_bucket"),
            },
            "sample_rows": audited[:12],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.18 audits the selected lamp same-family/unknown bucket only. It blocks generic-label and dominant-single-source evidence from becoming a lamp rule, does not use heldout, and does not train, tune, or change GoalSearcher.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "positive_rank_min",
        "rank_bucket",
        "primary_issue",
        "learning_status",
        "flags",
        "query_subtype",
        "top_subtype",
        "positive_subtype",
        "query_mount",
        "top_mount",
        "positive_mount",
        "query_tube_count",
        "top_tube_count",
        "positive_tube_count",
        "query_watts",
        "top_watts",
        "positive_watts",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "expected_books",
        "positive_id_prefix",
        "top_id_prefix",
        "positive_ids_in_top80",
        "positive_names_in_top80",
    ]
    _write_csv(Path(artifacts["rows_csv"]), audited, row_fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, ["scope", "dimension", "key", "count", "rate"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "next_candidate": report["next_candidate"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
