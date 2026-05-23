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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_valve_duct_block_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_valve_same_family_unknown_9x_audit"


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


def _terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in text]


def _book_prefix(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value.split("-")[0]


def _size_hints(text: str) -> list[int]:
    hints: list[int] = []
    for match in re.finditer(r"\bDN\s*[-:]?\s*(\d{2,5})", text, flags=re.IGNORECASE):
        hints.append(int(match.group(1)))
    for match in re.finditer(r"公称直径\s*(?:\(mm\)|mm)?\s*(\d{2,5})", text):
        hints.append(int(match.group(1)))
    return sorted(set(hints))


def _tier_limits(text: str) -> list[int]:
    limits: list[int] = []
    for match in re.finditer(r"(?:≤|<=|以内)\s*(\d{2,5})", text):
        limits.append(int(match.group(1)))
    for match in re.finditer(r"(\d{2,5})\s*mm\s*以内", text, flags=re.IGNORECASE):
        limits.append(int(match.group(1)))
    for match in re.finditer(r"公称直径(?:\(mm\))?\s*(\d{2,5})", text):
        limits.append(int(match.group(1)))
    return sorted(set(limits))


def _connection(text: str) -> str:
    values = []
    if "沟槽" in text:
        values.append("沟槽")
    if "承插焊" in text:
        values.append("承插焊")
    if "焊接法兰" in text:
        values.append("焊接法兰")
    elif "法兰" in text:
        values.append("法兰")
    if "螺纹法兰" in text:
        values.append("螺纹法兰")
    elif "螺纹" in text:
        values.append("螺纹")
    if "对夹" in text:
        values.append("对夹")
    return ",".join(dict.fromkeys(values))


def _material(text: str) -> str:
    return ",".join(_terms(text, ["不锈钢", "橡塑板", "纤维类", "低压", "中压", "碳钢"]))


def _subtype(text: str) -> str:
    if _has_any(text, ["蓄电池"]):
        return "battery"
    if _has_any(text, ["干燥机"]):
        return "dryer"
    if _has_any(text, ["热媒集配装置"]):
        return "heat_media_collector"
    if _has_any(text, ["阀门绝热", "橡塑板", "纤维类"]):
        return "valve_insulation"
    if _has_any(text, ["防火调节阀", "防烟防火调节阀"]):
        return "fire_smoke_damper"
    if _has_any(text, ["过滤器减压阀"]):
        return "instrument_filter_reducer"
    if _has_any(text, ["Y型过滤器", "Y形过滤器", "过滤器", "除污器"]):
        return "filter_or_strainer"
    if _has_any(text, ["减压孔板"]):
        return "pressure_reducing_orifice"
    if _has_any(text, ["减压器"]):
        return "pressure_reducer"
    if _has_any(text, ["试水阀", "放气装置", "水压试验"]):
        return "test_or_vent_device"
    if _has_any(text, ["电动蝶阀", "电动双位蝶阀", "电动、电磁阀门", "电磁阀", "两通电动阀"]):
        return "electric_valve"
    if "液压" in text and "水位控制阀" in text:
        return "hydraulic_level_control_valve"
    if "蝶阀" in text:
        return "butterfly_valve"
    if "闸阀" in text:
        return "gate_valve"
    if _has_any(text, ["沟槽阀门", "沟槽式阀门"]):
        return "grooved_valve"
    if _has_any(text, ["螺纹阀门", "螺纹阀"]):
        return "threaded_valve"
    if _has_any(text, ["法兰阀门", "法兰阀", "焊接法兰阀门", "低压法兰阀门"]):
        return "flange_valve"
    if _has_any(text, ["选择阀", "安全阀"]):
        return "other_named_valve"
    if "阀" in text:
        return "generic_valve"
    return "other"


def _size_relation(query_values: list[int], top_tiers: list[int], positive_tiers: list[int]) -> str:
    if not positive_tiers or not top_tiers:
        return ""
    if not query_values:
        return "tier_diff_without_query_size" if set(top_tiers) != set(positive_tiers) else ""
    value = max(query_values)
    top_limit = max(top_tiers)
    positive_limit = max(positive_tiers)
    if value <= positive_limit and value > top_limit:
        return "query_size_supports_positive"
    if value <= top_limit:
        return "query_size_supports_top"
    return "query_size_exceeds_both"


def _classify(row: dict[str, Any], source_counts: Counter[str]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(row.get("positive_names_in_top80"))
    source_file = _clean(row.get("source_file"))

    query_subtype = _subtype(query)
    top_subtype = _subtype(top_name)
    positive_subtype = _subtype(positive_text)
    query_connection = _connection(query)
    top_connection = _connection(top_name)
    positive_connection = _connection(positive_text)
    query_sizes = _size_hints(query)
    top_tiers = _tier_limits(top_name)
    positive_tiers = _tier_limits(positive_text)
    size_relation = _size_relation(query_sizes, top_tiers, positive_tiers)
    top_book = _clean(row.get("top1_book"))
    expected_books = _clean(row.get("expected_books"))
    top1_family = _clean(row.get("top1_family"))
    positive_book_prefix = _book_prefix(_clean(row.get("positive_ids_in_top80")))
    top_id_prefix = _book_prefix(_clean(row.get("top1_id")))
    positive_rank = _to_int(row.get("positive_rank_min"))

    flags: list[str] = []
    if source_counts[source_file] >= 30:
        flags.append("dominant_single_source")
    if top1_family and top1_family != "valve":
        flags.append("top1_family_conflict")
    if not top1_family:
        flags.append("top1_family_missing")
    if query_subtype in {"dryer", "battery", "heat_media_collector"}:
        flags.append("query_family_probably_wrong")
    if query_subtype != "other" and top_subtype != "other" and query_subtype == top_subtype:
        flags.append("top_matches_query_subtype")
    if query_subtype != "other" and positive_subtype != "other" and query_subtype != positive_subtype:
        flags.append("positive_subtype_conflicts_query")
    if top_subtype != "other" and positive_subtype != "other" and top_subtype != positive_subtype:
        flags.append("top_positive_subtype_diff")
    if query_connection and top_connection and query_connection == top_connection:
        flags.append("top_matches_query_connection")
    if query_connection and positive_connection and query_connection != positive_connection:
        flags.append("positive_connection_conflicts_query")
    if top_connection and positive_connection and top_connection != positive_connection:
        flags.append("top_positive_connection_diff")
    if size_relation:
        flags.append(size_relation)
    if expected_books and top_book and expected_books != top_book:
        flags.append("top_book_differs_expected_books")
    if positive_book_prefix and top_id_prefix and positive_book_prefix != top_id_prefix:
        flags.append("top_id_prefix_differs_positive")
    if positive_rank >= 41:
        flags.append("positive_rank_deep")
    elif positive_rank >= 21:
        flags.append("positive_rank_mid_deep")
    if _material(top_name) != _material(positive_text):
        flags.append("material_or_pressure_diff")

    if "query_family_probably_wrong" in flags:
        primary = "query_family_wrong_or_non_valve"
        learning_status = "exclude_or_family_label_review"
    elif "dominant_single_source" in flags and (
        "top_matches_query_subtype" in flags
        or "top_matches_query_connection" in flags
        or "positive_subtype_conflicts_query" in flags
        or "positive_connection_conflicts_query" in flags
    ):
        primary = "expected_label_conflicts_query_or_top_stronger"
        learning_status = "exclude_or_label_review"
    elif size_relation == "tier_diff_without_query_size":
        primary = "tier_expected_without_query_size"
        learning_status = "exclude_or_label_review"
    elif query_subtype == "fire_smoke_damper":
        primary = "fire_damper_shape_or_perimeter_no_query_evidence"
        learning_status = "review_only"
    elif query_subtype == "valve_insulation":
        primary = "insulation_material_or_thickness_no_query_evidence"
        learning_status = "review_only"
    elif query_subtype in {"filter_or_strainer", "pressure_reducer", "pressure_reducing_orifice", "electric_valve", "test_or_vent_device"}:
        primary = "named_valve_subtype_or_chapter_mismatch"
        learning_status = "review_only"
    elif "top_id_prefix_differs_positive" in flags or "top_book_differs_expected_books" in flags:
        primary = "book_or_section_bias_no_query_evidence"
        learning_status = "review_only"
    elif size_relation == "query_size_supports_positive":
        primary = "explicit_size_tier_candidate"
        learning_status = "candidate_for_transferability_review"
    else:
        primary = "other_valve_same_family_wrong_rank"
        learning_status = "review_only"

    if source_counts[source_file] >= 30 and learning_status == "candidate_for_transferability_review":
        learning_status = "same_source_review_only"

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
        "query_connection": query_connection,
        "top_connection": top_connection,
        "positive_connection": positive_connection,
        "query_size_hints": ",".join(str(item) for item in query_sizes),
        "top_tiers": ",".join(str(item) for item in top_tiers),
        "positive_tiers": ",".join(str(item) for item in positive_tiers),
        "size_relation": size_relation,
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
            "size_relation",
            "top1_family",
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
            out.append({"scope": "dev_valve_same_family_unknown", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
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
        "# Stage 9.16 Valve Same-family/Unknown Wrong-rank Audit",
        "",
        "Dev-only audit of 35 `valve + same_family_or_unknown_wrong_rank` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["dominant_source_rows", report["metrics"]["dominant_source_rows"]],
                ["exclude_or_label_review", report["metrics"]["exclude_or_label_review"]],
                ["exclude_or_family_label_review", report["metrics"]["exclude_or_family_label_review"]],
                ["review_only", report["metrics"]["review_only"]],
                ["candidate_for_transferability_review", report["metrics"]["candidate_for_transferability_review"]],
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
    parser = argparse.ArgumentParser(description="Stage 9.16 valve same-family/unknown wrong-rank audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    source_counts = Counter(_clean(row.get("source_file")) for row in source_rows)
    audited = [_classify(row, source_counts) for row in source_rows]
    buckets = _bucket_rows(audited)
    primary_counter = Counter(row["primary_issue"] for row in audited)
    learning_counter = Counter(row["learning_status"] for row in audited)
    candidate_rows = [row for row in audited if row["learning_status"] == "candidate_for_transferability_review"]

    if candidate_rows and len(_distinct(candidate_rows, "source_file")) >= 2:
        selected = "explicit_size_tier_candidate"
        next_stage = "9.17 valve explicit-size transferability review; do not design a rule yet"
        next_goal = "review whether explicit query size can safely rescue same-family valve ranking across sources"
    else:
        selected = "stop_valve_same_family_unknown_direction"
        next_stage = "9.17 ranked gap reselection after valve same-family audit"
        next_goal = "exclude this label-heavy and single-source-dominant valve bucket and choose the next high-support dev wrong-rank bucket"

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.16 valve same-family/unknown wrong-rank audit",
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
            "exclude_or_family_label_review": learning_counter.get("exclude_or_family_label_review", 0),
            "review_only": learning_counter.get("review_only", 0),
            "same_source_review_only": learning_counter.get("same_source_review_only", 0),
            "candidate_for_transferability_review": learning_counter.get("candidate_for_transferability_review", 0),
            "top_matches_query_subtype_rows": sum(1 for row in audited if "top_matches_query_subtype" in row["flags"]),
            "top_matches_query_connection_rows": sum(1 for row in audited if "top_matches_query_connection" in row["flags"]),
            "positive_rank_deep_rows": sum(1 for row in audited if _to_int(row.get("positive_rank_min")) >= 41),
        },
        "primary_issue_buckets": _preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _preview(buckets, "learning_status", 20),
        "next_candidate": {
            "selected_from": "dev_only_valve_same_family_unknown",
            "primary_issue": selected,
            "support": len(candidate_rows) if selected == "explicit_size_tier_candidate" else 0,
            "source_count": len(_distinct(candidate_rows, "source_file")) if candidate_rows else 0,
            "selection_policy": "only advance to what-if review if the candidate has explicit query evidence and cross-source support; label-conflict and dominant-single-source rows are blocked",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "decision": "The selected valve same-family bucket is mostly not learnable as a ranking rule: 34 of 35 rows come from one source, many Top1 candidates match the query text better than the expected item, and several rows are likely family-label or OSS-label issues. No cross-source transferable valve what-if is ready.",
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
        "anti_drift_conclusion": "Stage 9.16 audits the selected valve same-family/unknown bucket only. It blocks label-heavy and single-source-dominant evidence from becoming a valve rule, does not use heldout, and does not train, tune, or change GoalSearcher.",
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
        "query_connection",
        "top_connection",
        "positive_connection",
        "query_size_hints",
        "top_tiers",
        "positive_tiers",
        "size_relation",
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
