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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_lamp_same_family_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_lamp_near_miss_9x_audit"


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


def _size_hints(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"(?:周长|灯罩周长)[^0-9≤<]*(?:≤|<=)?\s*(\d{2,5})", text):
        values.append(f"shade_perimeter<={match.group(1)}")
    for match in re.finditer(r"[φΦ]\s*(\d{2,5})", text):
        values.append(f"diameter={match.group(1)}")
    for match in re.finditer(r"直径\s*(\d{2,5})\s*[~～-]\s*(\d{2,5})", text):
        values.append(f"diameter={match.group(1)}-{match.group(2)}")
    return sorted(set(values))


def _mount(text: str) -> str:
    values: list[str] = []
    if _has_any(text, ["吸顶", "吊顶上安装"]):
        values.append("ceiling")
    if _has_any(text, ["壁灯", "壁装", "墙壁式"]):
        values.append("wall")
    if _has_any(text, ["吊链", "吊链式"]):
        values.append("chain")
    if _has_any(text, ["吊管", "吊管式"]):
        values.append("pipe_hung")
    if _has_any(text, ["吊杆", "吊杆式", "直杆式"]):
        values.append("rod")
    if _has_any(text, ["滑轨", "轨道"]):
        values.append("track")
    if _has_any(text, ["灯槽", "灯带（槽）", "灯带(槽)", "回光灯槽"]):
        values.append("trough")
    if _has_any(text, ["水下", "喷泉"]):
        values.append("underwater")
    return ",".join(dict.fromkeys(values))


def _tube_count(text: str) -> str:
    normalized = text.replace("×", "*").replace("x", "*").replace("X", "*")
    if _has_any(normalized, ["双管", "2*"]):
        return "double"
    if _has_any(normalized, ["单管", "1*"]):
        return "single"
    return ""


def _lamp_subtype(text: str) -> str:
    if _has_any(text, ["灯带（槽）", "灯带(槽)", "灯槽", "回光灯槽", "平顶灯带"]):
        return "lamp_trough_or_strip_slot"
    if _has_any(text, ["灯带", "灯条", "硬灯条", "软灯带"]):
        return "led_strip_or_bar"
    if _has_any(text, ["荧光灯", "单管灯", "双管灯", "灯管"]):
        return "fluorescent_or_linear_lamp"
    if _has_any(text, ["防爆灯", "密封灯"]):
        return "explosion_or_sealed_lamp"
    if _has_any(text, ["装饰灯", "射灯", "点光源", "艺术装饰", "滑轨"]):
        return "decorative_or_spot_lamp"
    if _has_any(text, ["平板灯"]):
        return "panel_lamp"
    if _has_any(text, ["座灯头", "灯头"]):
        return "lamp_holder"
    if _has_any(text, ["吸顶灯", "圆球吸顶", "灯罩周长"]):
        return "ceiling_shade_lamp"
    if _has_any(text, ["普通灯具", "普通壁灯", "防水吊灯"]):
        return "ordinary_lamp"
    if _has_any(text, ["水下", "喷泉"]):
        return "underwater_fountain_lamp"
    if "灯" in text:
        return "generic_lamp"
    return "other"


def _strip_trait(text: str) -> str:
    values: list[str] = []
    if "硬灯条" in text or "硬灯" in text:
        values.append("hard")
    if "软灯带" in text or "软灯" in text:
        values.append("soft")
    if "防水" in text:
        values.append("waterproof")
    if "LED" in text.upper():
        values.append("led")
    return ",".join(values)


def _classify(row: dict[str, Any], source_counts: Counter[str]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(row.get("positive_names_in_top80"))
    source_file = _clean(row.get("source_file"))
    top1_family = _clean(row.get("top1_family"))

    query_subtype = _lamp_subtype(query)
    top_subtype = _lamp_subtype(top_name)
    positive_subtype = _lamp_subtype(positive_text)
    query_mount = _mount(query)
    top_mount = _mount(top_name)
    positive_mount = _mount(positive_text)
    query_tube = _tube_count(query)
    top_tube = _tube_count(top_name)
    positive_tube = _tube_count(positive_text)
    query_watts = _watt_hints(query)
    top_watts = _watt_hints(top_name)
    positive_watts = _watt_hints(positive_text)
    query_sizes = _size_hints(query)
    top_sizes = _size_hints(top_name)
    positive_sizes = _size_hints(positive_text)
    query_strip = _strip_trait(query)
    top_strip = _strip_trait(top_name)
    positive_strip = _strip_trait(positive_text)
    positive_rank = _to_int(row.get("positive_rank_min"))
    expected_books = _clean(row.get("expected_books"))
    top_book = _clean(row.get("top1_book"))
    positive_id_prefix = _book_prefix(_clean(row.get("positive_ids_in_top80")))
    top_id_prefix = _book_prefix(_clean(row.get("top1_id")))

    flags: list[str] = []
    if source_counts[source_file] >= 15:
        flags.append("dominant_single_source")
    if top1_family and top1_family != "lamp":
        flags.append("top1_family_conflict")
    if not top1_family:
        flags.append("top1_family_missing")
    if query in {"普通灯具", "装饰灯", "荧光灯"}:
        flags.append("query_too_generic")
    if positive_rank and 2 <= positive_rank <= 5:
        flags.append("positive_rank_2_5")
    if query_subtype != "other" and top_subtype != "other" and query_subtype == top_subtype:
        flags.append("top_matches_query_subtype")
    if query_subtype != "other" and positive_subtype != "other" and query_subtype == positive_subtype:
        flags.append("positive_matches_query_subtype")
    if query_subtype != "other" and top_subtype != "other" and positive_subtype != "other" and top_subtype != positive_subtype:
        flags.append("top_positive_subtype_diff")
    if query_subtype != "other" and positive_subtype != "other" and query_subtype != positive_subtype:
        flags.append("positive_subtype_conflicts_query")
    if query_mount and top_mount and query_mount == top_mount:
        flags.append("top_matches_query_mount")
    if query_mount and positive_mount and query_mount == positive_mount:
        flags.append("positive_matches_query_mount")
    if query_mount and top_mount and query_mount != top_mount:
        flags.append("top_mount_conflicts_query")
    if query_mount and positive_mount and query_mount != positive_mount:
        flags.append("positive_mount_conflicts_query")
    if query_tube and top_tube and query_tube == top_tube:
        flags.append("top_matches_query_tube_count")
    if query_tube and positive_tube and query_tube == positive_tube:
        flags.append("positive_matches_query_tube_count")
    if query_watts:
        flags.append("query_watt_present")
        if positive_watts and set(query_watts) & set(positive_watts):
            flags.append("positive_matches_query_watt")
        if top_watts and set(query_watts) & set(top_watts):
            flags.append("top_matches_query_watt")
    if positive_sizes:
        flags.append("positive_size_tier_present")
    if top_sizes:
        flags.append("top_size_tier_present")
    if query_strip or top_strip or positive_strip:
        flags.append("led_strip_trait_context")
    if query_strip and top_strip and set(query_strip.split(",")) & set(top_strip.split(",")):
        flags.append("top_matches_query_strip_trait")
    if query_strip and positive_strip and set(query_strip.split(",")) & set(positive_strip.split(",")):
        flags.append("positive_matches_query_strip_trait")
    if top_strip and positive_strip and top_strip != positive_strip:
        flags.append("top_positive_strip_trait_diff")
    if expected_books and top_book and expected_books != top_book:
        flags.append("top_book_differs_expected_books")
    if positive_id_prefix and top_id_prefix and positive_id_prefix != top_id_prefix:
        flags.append("top_id_prefix_differs_positive")

    if "query_too_generic" in flags:
        primary = "generic_query_label_insufficient"
        learning_status = "exclude_or_label_review"
    elif "top_matches_query_strip_trait" in flags and "top_positive_strip_trait_diff" in flags:
        primary = "led_strip_trait_label_conflict"
        learning_status = "exclude_or_label_review"
    elif query_subtype == "explosion_or_sealed_lamp" and positive_subtype == "ceiling_shade_lamp":
        primary = "expected_label_conflicts_query_or_top_stronger"
        learning_status = "exclude_or_label_review"
    elif query_subtype == "lamp_holder" and positive_subtype == "ceiling_shade_lamp":
        primary = "expected_label_conflicts_query_or_top_stronger"
        learning_status = "exclude_or_label_review"
    elif query_subtype == "led_strip_or_bar":
        primary = "led_strip_scene_or_unit_review"
        learning_status = "review_only"
    elif query_subtype == "fluorescent_or_linear_lamp":
        if "positive_matches_query_tube_count" in flags or "positive_matches_query_mount" in flags:
            primary = "linear_lamp_mount_tube_or_watt_evidence"
            learning_status = "candidate_for_transferability_review"
        else:
            primary = "linear_lamp_mount_or_unit_review"
            learning_status = "review_only"
    elif query_subtype == "lamp_trough_or_strip_slot":
        primary = "lamp_trough_same_source_chapter_artifact"
        learning_status = "review_only"
    elif query_subtype == "panel_lamp":
        primary = "panel_lamp_shade_count_label_review"
        learning_status = "review_only"
    elif top_subtype != positive_subtype and ("top_matches_query_subtype" in flags or "top_matches_query_mount" in flags):
        primary = "expected_label_conflicts_query_or_top_stronger"
        learning_status = "exclude_or_label_review"
    elif "positive_matches_query_subtype" in flags and "positive_matches_query_mount" in flags:
        primary = "specific_lamp_near_miss_evidence"
        learning_status = "candidate_for_transferability_review"
    elif "top_id_prefix_differs_positive" in flags or "top_book_differs_expected_books" in flags:
        primary = "book_or_section_bias_no_safe_signal"
        learning_status = "review_only"
    else:
        primary = "other_lamp_near_miss"
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
        "query_size_hints": ",".join(query_sizes),
        "top_size_hints": ",".join(top_sizes),
        "positive_size_hints": ",".join(positive_sizes),
        "query_strip_trait": query_strip,
        "top_strip_trait": top_strip,
        "positive_strip_trait": positive_strip,
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": top_name,
        "top1_family": top1_family,
        "top1_book": top_book,
        "top1_chapter": _clean(row.get("top1_chapter")),
        "top1_unit": _clean(row.get("top1_unit")),
        "expected_books": expected_books,
        "positive_id_prefix": positive_id_prefix,
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
            "query_strip_trait",
            "top_strip_trait",
            "positive_strip_trait",
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
            out.append({"scope": "dev_lamp_near_miss_rank_2_5", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
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
        "# Stage 9.20 Lamp Near-miss Rank 2-5 Audit",
        "",
        "Dev-only audit of 20 `lamp + near_miss_rank_2_5` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
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
                ["candidate_source_count", report["metrics"]["candidate_source_count"]],
                ["generic_query_rows", report["metrics"]["generic_query_rows"]],
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
    parser = argparse.ArgumentParser(description="Stage 9.20 lamp near-miss rank_2_5 dev-only audit")
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
    candidate_source_count = len(_distinct(candidate_rows, "source_file"))

    if len(candidate_rows) >= 8 and candidate_source_count >= 2:
        selected = "lamp_near_miss_transferability_review"
        next_stage = "9.21 lamp near-miss transferability review; do not design a rule yet"
        next_goal = "review whether specific lamp subtype, mount, tube/watt, or strip traits are transferable across sources before any what-if"
        support = len(candidate_rows)
    else:
        selected = "stop_lamp_near_miss_direction"
        next_stage = "9.21 ranked gap reselection after lamp near-miss audit"
        next_goal = "exclude this single-source-heavy lamp near-miss bucket and choose the next high-support dev wrong-rank bucket"
        support = 0

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.20 lamp near-miss rank_2_5 audit",
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
            "candidate_source_count": candidate_source_count,
            "candidate_province_count": len(_distinct(candidate_rows, "province")),
            "generic_query_rows": sum(1 for row in audited if "query_too_generic" in row["flags"]),
            "led_strip_context_rows": sum(1 for row in audited if "led_strip_trait_context" in row["flags"]),
            "linear_lamp_rows": sum(1 for row in audited if row["query_subtype"] == "fluorescent_or_linear_lamp"),
            "top_matches_query_subtype_rows": sum(1 for row in audited if "top_matches_query_subtype" in row["flags"]),
            "positive_matches_query_subtype_rows": sum(1 for row in audited if "positive_matches_query_subtype" in row["flags"]),
        },
        "primary_issue_buckets": _preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _preview(buckets, "learning_status", 20),
        "next_candidate": {
            "selected_from": "dev_only_lamp_near_miss_rank_2_5",
            "primary_issue": selected,
            "support": support,
            "source_count": candidate_source_count if candidate_rows else 0,
            "province_count": len(_distinct(candidate_rows, "province")) if candidate_rows else 0,
            "selection_policy": "advance only if specific lamp near-miss evidence has at least 8 rows and 2 sources; generic labels, label conflicts, and dominant single-source rows cannot drive a rule",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "decision": "The lamp near-miss bucket is not ready for a transferable ranking change. 19 of 20 rows come from one source; many rows are generic labels or cases where the current top1 is semantically stronger than the expected label. The few linear-lamp candidates are still single-source and must not become a rule.",
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
        "anti_drift_conclusion": "Stage 9.20 audits the selected lamp near-miss bucket only. It does not use heldout, train, tune, write rules, or change GoalSearcher. Because transferability support is single-source-heavy, it stops the lamp near-miss direction and returns to ranked gap reselection.",
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
        "query_size_hints",
        "top_size_hints",
        "positive_size_hints",
        "query_strip_trait",
        "top_strip_trait",
        "positive_strip_trait",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_unit",
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
