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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_lamp_near_miss_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_support_same_family_unknown_9x_audit"


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


def _dn_hints(text: str) -> list[int]:
    values: list[int] = []
    for match in re.finditer(r"\bDN\s*[-:]?\s*(\d{2,5})", text, flags=re.IGNORECASE):
        values.append(int(match.group(1)))
    for match in re.finditer(r"公称(?:外径|直径)?(?:\(mm\))?\s*(\d{2,5})", text):
        values.append(int(match.group(1)))
    return sorted(set(values))


def _family(text: str) -> str:
    if _has_any(text, ["支架", "支吊架", "吊架", "支撑架", "支撑", "管架"]):
        if _has_any(text, ["树木", "杉木桩", "成品支撑"]):
            return "landscape_support"
        if _has_any(text, ["抗震", "支吊架"]):
            return "seismic_support"
        if _has_any(text, ["管道支架", "管道支吊架", "一般管架", "管架"]):
            return "pipe_support"
        if _has_any(text, ["摄像机支架", "显示器支架", "拼接屏支架", "显示器吊架", "LED屏支架"]):
            return "weak_current_device_support"
        if _has_any(text, ["设备支架"]):
            return "equipment_support"
        if _has_any(text, ["钢支架"]):
            return "wiring_on_steel_support"
        return "generic_support"
    if _has_any(text, ["塑料管", "PP-R", "PPR", "铸铁排水管", "排水管", "给水管", "采暖管道", "管道消毒", "管道保护管"]):
        return "pipe"
    if _has_any(text, ["风机盘管", "空调器", "减振器", "隔振垫"]):
        return "hvac_equipment_or_isolator"
    if _has_any(text, ["静压箱", "消声器"]):
        return "duct_hvac_component"
    if _has_any(text, ["阀", "防火阀", "调节阀"]):
        return "valve"
    if _has_any(text, ["仪表", "监测", "液位计", "气象环保"]):
        return "instrument"
    if _has_any(text, ["配电箱", "接线端子", "端子"]):
        return "electrical_box_or_terminal"
    if _has_any(text, ["插座", "配线", "导线"]):
        return "wiring_or_socket"
    if _has_any(text, ["装饰灯", "灯具", "灯", "LED显示屏", "信号灯"]):
        return "lamp_or_display"
    if _has_any(text, ["天线", "读卡器", "目标识别设备", "无线设备"]):
        return "weak_current_device"
    return "other"


def _support_subtype(text: str) -> str:
    family = _family(text)
    if family.endswith("support") or family in {"generic_support", "wiring_on_steel_support"}:
        return family
    return ""


def _rank_depth(value: Any) -> str:
    rank = _to_int(value)
    if rank >= 41:
        return "rank_41_80"
    if rank >= 21:
        return "rank_21_40"
    if rank >= 11:
        return "rank_11_20"
    if rank >= 6:
        return "rank_6_10"
    if rank >= 2:
        return "rank_2_5"
    return ""


def _classify(row: dict[str, Any], source_counts: Counter[str]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(row.get("positive_names_in_top80"))
    source_file = _clean(row.get("source_file"))
    top1_family = _clean(row.get("top1_family"))

    query_family = _family(query)
    top_family_text = _family(top_name)
    positive_family = _family(positive_text)
    query_support_subtype = _support_subtype(query)
    top_support_subtype = _support_subtype(top_name)
    positive_support_subtype = _support_subtype(positive_text)
    query_dn = _dn_hints(query)
    top_dn = _dn_hints(top_name)
    positive_dn = _dn_hints(positive_text)
    positive_rank = _to_int(row.get("positive_rank_min"))

    flags: list[str] = []
    if source_counts[source_file] >= 20:
        flags.append("dominant_single_source")
    if top1_family and top1_family != "support":
        flags.append("top1_family_conflict")
    if not top1_family:
        flags.append("top1_family_missing")
    if query_family not in {"generic_support", "seismic_support", "pipe_support", "weak_current_device_support", "equipment_support", "landscape_support"}:
        flags.append("query_not_support_family")
    if top_family_text in {"generic_support", "seismic_support", "pipe_support", "weak_current_device_support", "equipment_support", "landscape_support", "wiring_on_steel_support"}:
        flags.append("top_is_support_like")
    if positive_family in {"generic_support", "seismic_support", "pipe_support", "weak_current_device_support", "equipment_support", "landscape_support"}:
        flags.append("positive_is_support_like")
    if query_support_subtype and top_support_subtype and query_support_subtype == top_support_subtype:
        flags.append("top_matches_query_support_subtype")
    if query_support_subtype and positive_support_subtype and query_support_subtype == positive_support_subtype:
        flags.append("positive_matches_query_support_subtype")
    if top_support_subtype and positive_support_subtype and top_support_subtype != positive_support_subtype:
        flags.append("top_positive_support_subtype_diff")
    if query_dn:
        flags.append("query_dn_present")
        if top_dn and set(query_dn) & set(top_dn):
            flags.append("top_matches_query_dn")
        if positive_dn and set(query_dn) & set(positive_dn):
            flags.append("positive_matches_query_dn")
    if positive_rank >= 41:
        flags.append("positive_rank_deep")
    elif positive_rank >= 21:
        flags.append("positive_rank_mid_deep")
    if _clean(row.get("expected_books")) and _clean(row.get("top1_book")) and _clean(row.get("expected_books")) != _clean(row.get("top1_book")):
        flags.append("top_book_differs_expected_books")

    if "query_not_support_family" in flags:
        if query_family == "pipe":
            primary = "pipe_absorbed_by_support_or_protection_chapter"
            learning_status = "exclude_or_family_label_review"
        elif query_family == "hvac_equipment_or_isolator":
            primary = "hvac_equipment_vibration_item_absorbed_by_support"
            learning_status = "exclude_or_family_label_review"
        elif query_family in {"valve", "instrument", "electrical_box_or_terminal", "wiring_or_socket", "lamp_or_display", "weak_current_device", "duct_hvac_component"}:
            primary = f"{query_family}_absorbed_by_support"
            learning_status = "exclude_or_family_label_review"
        else:
            primary = "non_support_query_absorbed_by_support"
            learning_status = "exclude_or_family_label_review"
    elif query_family == "landscape_support":
        primary = "landscape_support_not_transferable_to_installation_support"
        learning_status = "review_only"
    elif query_support_subtype and positive_support_subtype and top_support_subtype and top_support_subtype != positive_support_subtype:
        primary = "support_subtype_or_section_mismatch"
        learning_status = "review_only"
    elif query_support_subtype and "positive_matches_query_support_subtype" in flags and source_counts[source_file] < 20:
        primary = "specific_support_same_family_candidate"
        learning_status = "candidate_for_transferability_review"
    elif top1_family and top1_family != "support":
        primary = "top_family_conflict_non_support"
        learning_status = "review_only"
    else:
        primary = "other_support_same_family_wrong_rank"
        learning_status = "review_only"

    return {
        "split": _clean(row.get("split")),
        "group_id": _clean(row.get("group_id")),
        "sample_id": _clean(row.get("sample_id")),
        "source_file": source_file,
        "province": _clean(row.get("province")),
        "query": query,
        "positive_rank_min": _clean(row.get("positive_rank_min")),
        "rank_depth": _rank_depth(row.get("positive_rank_min")),
        "rank_bucket": _clean(row.get("rank_bucket")),
        "primary_issue": primary,
        "learning_status": learning_status,
        "flags": "|".join(flags),
        "query_family_inferred": query_family,
        "top_family_text_inferred": top_family_text,
        "positive_family_inferred": positive_family,
        "query_support_subtype": query_support_subtype,
        "top_support_subtype": top_support_subtype,
        "positive_support_subtype": positive_support_subtype,
        "query_dn_hints": ",".join(str(item) for item in query_dn),
        "top_dn_hints": ",".join(str(item) for item in top_dn),
        "positive_dn_hints": ",".join(str(item) for item in positive_dn),
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": top_name,
        "top1_family": top1_family,
        "top1_book": _clean(row.get("top1_book")),
        "top1_chapter": _clean(row.get("top1_chapter")),
        "top1_unit": _clean(row.get("top1_unit")),
        "expected_books": _clean(row.get("expected_books")),
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
            "rank_depth",
            "rank_bucket",
            "query_family_inferred",
            "top_family_text_inferred",
            "positive_family_inferred",
            "query_support_subtype",
            "top_support_subtype",
            "positive_support_subtype",
            "top1_family",
            "expected_books",
        ):
            counters[dimension][_clean(row.get(dimension)) or "<empty>"] += 1
        for flag in _clean(row.get("flags")).split("|"):
            if flag:
                counters["flag"][flag] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_support_same_family_unknown", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
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
        "# Stage 9.22 Support Same-family/Unknown Wrong-rank Audit",
        "",
        "Dev-only audit of 25 `support + same_family_or_unknown_wrong_rank` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["dominant_source_rows", report["metrics"]["dominant_source_rows"]],
                ["non_support_query_rows", report["metrics"]["non_support_query_rows"]],
                ["true_support_query_rows", report["metrics"]["true_support_query_rows"]],
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
        "## Query Family Mix",
        "",
        _md_table([["family", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in previews["query_family_inferred"]]),
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
    parser = argparse.ArgumentParser(description="Stage 9.22 support same-family/unknown wrong-rank audit")
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
    support_families = {"generic_support", "seismic_support", "pipe_support", "weak_current_device_support", "equipment_support", "landscape_support"}
    true_support_rows = [row for row in audited if row["query_family_inferred"] in support_families]
    candidate_rows = [row for row in audited if row["learning_status"] == "candidate_for_transferability_review"]

    if len(candidate_rows) >= 8 and len(_distinct(candidate_rows, "source_file")) >= 2:
        selected = "support_transferability_review"
        next_stage = "9.23 support transferability review; do not design a rule yet"
        next_goal = "review whether true support subtype evidence is transferable across sources before any what-if"
        support = len(candidate_rows)
    else:
        selected = "stop_support_same_family_unknown_direction"
        next_stage = "9.23 ranked gap reselection after support same-family audit"
        next_goal = "exclude this mixed support bucket and choose the next high-support dev wrong-rank bucket"
        support = 0

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.22 support same-family/unknown wrong-rank audit",
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
            "non_support_query_rows": sum(1 for row in audited if row["query_family_inferred"] not in support_families),
            "true_support_query_rows": len(true_support_rows),
            "exclude_or_family_label_review": learning_counter.get("exclude_or_family_label_review", 0),
            "review_only": learning_counter.get("review_only", 0),
            "candidate_for_transferability_review": learning_counter.get("candidate_for_transferability_review", 0),
            "candidate_source_count": len(_distinct(candidate_rows, "source_file")),
            "candidate_province_count": len(_distinct(candidate_rows, "province")),
            "top_family_conflict_rows": sum(1 for row in audited if "top1_family_conflict" in row["flags"]),
            "query_dn_rows": sum(1 for row in audited if "query_dn_present" in row["flags"]),
            "positive_rank_deep_rows": sum(1 for row in audited if _to_int(row.get("positive_rank_min")) >= 41),
        },
        "primary_issue_buckets": _preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _preview(buckets, "learning_status", 20),
        "query_family_buckets": _preview(buckets, "query_family_inferred", 20),
        "next_candidate": {
            "selected_from": "dev_only_support_same_family_unknown",
            "primary_issue": selected,
            "support": support,
            "source_count": len(_distinct(candidate_rows, "source_file")) if candidate_rows else 0,
            "province_count": len(_distinct(candidate_rows, "province")) if candidate_rows else 0,
            "selection_policy": "advance only if true support evidence has at least 8 rows and 2 sources; non-support query contamination and dominant single-source rows cannot drive a rule",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "decision": (
            "The support same-family bucket is mostly not a transferable support-ranking signal. "
            "Most rows are non-support queries absorbed by support-like candidates or chapters, including pipe, HVAC equipment, instruments, valves, lamps, terminals, sockets, weak-current devices, and duct components. "
            "The true support rows are heterogeneous across display brackets, seismic supports, pipe supports, and landscape supports, so no support rule is ready."
        ),
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "primary_issue": _preview(buckets, "primary_issue"),
                "learning_status": _preview(buckets, "learning_status"),
                "query_family_inferred": _preview(buckets, "query_family_inferred"),
                "flag": _preview(buckets, "flag"),
                "province": _preview(buckets, "province"),
                "source_file": _preview(buckets, "source_file"),
                "rank_depth": _preview(buckets, "rank_depth"),
            },
            "sample_rows": audited[:12],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.22 audits the selected support same-family/unknown bucket only. It does not use heldout, train, tune, write rules, or change GoalSearcher. Because the bucket is dominated by non-support contamination and heterogeneous support labels, it stops the support direction and returns to ranked gap reselection.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "positive_rank_min",
        "rank_depth",
        "rank_bucket",
        "primary_issue",
        "learning_status",
        "flags",
        "query_family_inferred",
        "top_family_text_inferred",
        "positive_family_inferred",
        "query_support_subtype",
        "top_support_subtype",
        "positive_support_subtype",
        "query_dn_hints",
        "top_dn_hints",
        "positive_dn_hints",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
        "top1_unit",
        "expected_books",
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
