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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_duct_fire_damper_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_valve_near_miss_9x_audit"


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


def _dimensions(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"(\d{2,5})\s*[*xX×]\s*(\d{2,5})", text):
        values.append(f"{match.group(1)}x{match.group(2)}")
    return values


def _perimeter_hints(text: str) -> list[int]:
    hints: list[int] = []
    for match in re.finditer(r"(\d{2,5})\s*[*xX×]\s*(\d{2,5})", text):
        hints.append(2 * (int(match.group(1)) + int(match.group(2))))
    return hints


def _size_hints(text: str) -> list[int]:
    hints: list[int] = []
    for match in re.finditer(r"\bDN\s*[-:]?\s*(\d{2,5})", text, flags=re.IGNORECASE):
        hints.append(int(match.group(1)))
    for match in re.finditer(r"\bD(?!40J)\s*[-:]?\s*(\d{3,5})", text, flags=re.IGNORECASE):
        hints.append(int(match.group(1)))
    for match in re.finditer(r"\bSMF\s*[-:]?\s*(\d{2})\b", text, flags=re.IGNORECASE):
        hints.append(int(match.group(1)) * 10)
    for match in re.finditer(r"直径\s*(?:\(mm\))?\s*(\d{2,5})", text):
        hints.append(int(match.group(1)))
    return sorted(set(hints))


def _tier_limits(text: str) -> list[int]:
    limits: list[int] = []
    for match in re.finditer(r"(?:≤|<=|以内)\s*(\d{2,5})", text):
        limits.append(int(match.group(1)))
    for match in re.finditer(r"(?:直径|周长)[^0-9]{0,10}(\d{2,5})\s*\(mm\)", text):
        limits.append(int(match.group(1)))
    return sorted(set(limits))


def _connection(text: str) -> str:
    values = []
    if "沟槽" in text:
        values.append("沟槽")
    if "焊接法兰" in text:
        values.append("焊接法兰")
    elif "法兰" in text:
        values.append("法兰")
    if "螺纹" in text:
        values.append("螺纹")
    if "对夹" in text:
        values.append("对夹")
    return ",".join(values)


def _material(text: str) -> str:
    return ",".join(_terms(text, ["不锈钢", "碳钢", "镀锌铁皮", "镀锌", "普通钢板", "玻璃钢", "复合型"]))


def _pressure(text: str) -> str:
    if "中压" in text:
        return "中压"
    if "低压" in text:
        return "低压"
    return ""


def _subtype(text: str) -> str:
    if _has_any(text, ["软接头", "软管", "补偿器", "伸缩器", "可曲挠"]):
        return "soft_joint_compensator"
    if _has_any(text, ["密闭阀", "SMF", "D40J"]):
        return "human_defense_sealed_valve"
    if "插板阀" in text:
        return "slide_gate_valve"
    if "末端试水" in text:
        return "terminal_test_valve"
    if "Y型过滤器" in text or "Y 型过滤器" in text or "过滤器" in text:
        return "y_filter"
    if "浮球阀" in text:
        return "float_valve"
    if "止回阀" in text:
        return "check_valve"
    if "自动排气" in text or "排气阀" in text:
        return "exhaust_valve"
    if "蝶阀" in text:
        return "butterfly_valve"
    if "闸阀" in text:
        return "gate_valve"
    if _has_any(text, ["防火阀", "排烟阀", "调节阀"]):
        return "duct_control_valve"
    if _has_any(text, ["保温盒", "保温托盘", "托盘"]):
        return "insulation_box_or_tray"
    if _has_any(text, ["法兰阀门", "螺纹阀门", "焊接法兰阀", "低压阀门", "中压阀门", "阀门安装", "阀安装"]):
        return "generic_connection_valve"
    if "风管" in text:
        return "duct_body"
    return "other"


def _size_relation(query_values: list[int], query_perimeters: list[int], top_tiers: list[int], positive_tiers: list[int]) -> str:
    if not positive_tiers or not top_tiers:
        return ""
    top_limit = max(top_tiers)
    positive_limit = max(positive_tiers)
    values = query_values + query_perimeters
    if not values:
        return "tier_diff_without_query_size"
    value = max(values)
    if value <= positive_limit and value > top_limit:
        return "query_size_supports_positive"
    if value <= top_limit:
        return "query_size_supports_top"
    return "query_size_exceeds_both"


def _classify(row: dict[str, Any]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(row.get("positive_names_in_top80"))
    combined = " ".join([query, top_name, positive_text])

    query_subtype = _subtype(query)
    top_subtype = _subtype(top_name)
    positive_subtype = _subtype(positive_text)
    query_size = _size_hints(query)
    query_perimeters = _perimeter_hints(query)
    top_tiers = _tier_limits(top_name)
    positive_tiers = _tier_limits(positive_text)
    size_relation = _size_relation(query_size, query_perimeters, top_tiers, positive_tiers)
    query_connection = _connection(query)
    top_connection = _connection(top_name)
    positive_connection = _connection(positive_text)
    top_material = _material(top_name)
    positive_material = _material(positive_text)
    top_pressure = _pressure(top_name)
    positive_pressure = _pressure(positive_text)
    top_family = _clean(row.get("top1_family"))
    expected_books = _clean(row.get("expected_books"))
    top_book = _clean(row.get("top1_book"))

    flags: list[str] = []
    if top_family and top_family != "valve":
        flags.append("top1_family_conflict")
    if "风管" in top_name or "风管" in positive_text or "人防" in positive_text or "人防" in top_name:
        flags.append("duct_taxonomy_context")
    if query_size:
        flags.append("query_dn_or_model_size_present")
    if query_perimeters:
        flags.append("query_dimension_perimeter_present")
    if top_tiers != positive_tiers:
        flags.append("candidate_tier_diff")
    if size_relation:
        flags.append(size_relation)
    if query_subtype != "other" and top_subtype != "other" and query_subtype != top_subtype:
        flags.append("query_top_subtype_diff")
    if query_subtype != "other" and positive_subtype != "other" and query_subtype != positive_subtype:
        flags.append("query_positive_subtype_diff")
    if top_subtype != "other" and positive_subtype != "other" and top_subtype != positive_subtype:
        flags.append("top_positive_subtype_diff")
    if query_connection and top_connection and query_connection != top_connection:
        flags.append("query_top_connection_diff")
    if query_connection and positive_connection and query_connection != positive_connection:
        flags.append("query_positive_connection_diff")
    if top_pressure != positive_pressure:
        flags.append("pressure_tier_diff")
    if top_material != positive_material:
        flags.append("material_tier_diff")
    if query_subtype == top_subtype and positive_subtype == "generic_connection_valve":
        flags.append("top_semantically_stronger_than_expected")
    if query_connection and top_connection == query_connection and positive_connection and positive_connection != query_connection:
        flags.append("expected_connection_conflicts_query")

    if query_subtype == "soft_joint_compensator" or positive_subtype == "soft_joint_compensator":
        primary = "soft_joint_compensator_review"
        learning_status = "review_only"
    elif query_subtype == "insulation_box_or_tray":
        primary = "insulation_material_label_insufficient"
        learning_status = "exclude_or_label_review"
    elif "top_semantically_stronger_than_expected" in flags or "expected_connection_conflicts_query" in flags:
        primary = "expected_label_conflicts_query_or_top_stronger"
        learning_status = "exclude_or_label_review"
    elif query_subtype in {"human_defense_sealed_valve", "slide_gate_valve", "exhaust_valve"} and "duct_taxonomy_context" in flags:
        if size_relation == "query_size_supports_positive":
            primary = "valve_duct_family_explicit_size_tier"
            learning_status = "candidate_for_transferability_review"
        elif "candidate_tier_diff" in flags:
            primary = "valve_duct_family_no_or_weak_size_evidence"
            learning_status = "review_only"
        else:
            primary = "valve_duct_family_taxonomy_gap"
            learning_status = "review_only"
    elif query_subtype == "check_valve" and "duct_taxonomy_context" in flags:
        if size_relation == "query_size_supports_positive":
            primary = "wind_duct_check_valve_explicit_size_tier"
            learning_status = "candidate_for_transferability_review"
        else:
            primary = "wind_duct_check_valve_taxonomy_gap"
            learning_status = "review_only"
    elif size_relation == "query_size_supports_positive":
        primary = "explicit_dn_or_size_tier"
        learning_status = "candidate_for_future_what_if"
    elif "pressure_tier_diff" in flags:
        primary = "pressure_tier_no_query_evidence"
        learning_status = "exclude_or_label_review"
    elif "material_tier_diff" in flags and not query_size:
        primary = "material_tier_no_query_evidence"
        learning_status = "exclude_or_label_review"
    elif query_subtype != "other" and (query_subtype != top_subtype or query_subtype != positive_subtype):
        primary = "valve_subtype_or_connection_conflict"
        learning_status = "review_only"
    elif size_relation == "tier_diff_without_query_size":
        primary = "dn_or_tier_no_query_evidence"
        learning_status = "exclude_or_label_review"
    else:
        primary = "other_valve_near_miss"
        learning_status = "review_only"

    return {
        "split": _clean(row.get("split")),
        "group_id": _clean(row.get("group_id")),
        "sample_id": _clean(row.get("sample_id")),
        "source_file": _clean(row.get("source_file")),
        "province": _clean(row.get("province")),
        "query": query,
        "positive_rank_min": _clean(row.get("positive_rank_min")),
        "primary_issue": primary,
        "learning_status": learning_status,
        "flags": "|".join(flags),
        "query_subtype": query_subtype,
        "top_subtype": top_subtype,
        "positive_subtype": positive_subtype,
        "query_connection": query_connection,
        "top_connection": top_connection,
        "positive_connection": positive_connection,
        "query_size_hints": ",".join(str(item) for item in query_size),
        "query_dimensions": ",".join(_dimensions(query)),
        "query_perimeter_hints": ",".join(str(item) for item in query_perimeters),
        "top_tiers": ",".join(str(item) for item in top_tiers),
        "positive_tiers": ",".join(str(item) for item in positive_tiers),
        "size_relation": size_relation,
        "top_material": top_material,
        "positive_material": positive_material,
        "top_pressure": top_pressure,
        "positive_pressure": positive_pressure,
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": top_name,
        "top1_family": top_family,
        "top1_book": top_book,
        "top1_chapter": _clean(row.get("top1_chapter")),
        "expected_books": expected_books,
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
            out.append({"scope": "dev_valve_near_miss", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
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
        "# Stage 9.13 Valve Near-miss Audit",
        "",
        "Dev-only audit of 24 `valve + near_miss_rank_2_5` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["candidate_for_transferability_review", report["metrics"]["candidate_for_transferability_review"]],
                ["exclude_or_label_review", report["metrics"]["exclude_or_label_review"]],
                ["review_only", report["metrics"]["review_only"]],
                ["soft_joint_compensator_rows", report["metrics"]["soft_joint_compensator_rows"]],
                ["top_family_conflict_rows", report["metrics"]["top_family_conflict_rows"]],
                ["query_size_evidence_rows", report["metrics"]["query_size_evidence_rows"]],
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
    parser = argparse.ArgumentParser(description="Stage 9.13 dev-only valve near-miss audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    audited = [_classify(row) for row in source_rows]
    buckets = _bucket_rows(audited)
    primary_counter = Counter(row["primary_issue"] for row in audited)
    learning_counter = Counter(row["learning_status"] for row in audited)
    size_candidate_rows = [
        row
        for row in audited
        if row["primary_issue"] in {"valve_duct_family_explicit_size_tier", "wind_duct_check_valve_explicit_size_tier"}
    ]
    province_count = len(_distinct(size_candidate_rows, "province"))
    source_count = len(_distinct(size_candidate_rows, "source_file"))

    if len(size_candidate_rows) >= 8:
        selected = "valve_duct_family_size_tier_transferability_review"
        next_stage = "9.14 valve/duct family size-tier transferability review; do not design a rule yet"
        next_goal = "audit whether valve-ish queries that land in duct/human-defense/wind-duct chapters form a transferable family-compatibility + size-tier pattern, or only province/source artifacts"
    elif primary_counter:
        selected = primary_counter.most_common(1)[0][0]
        next_stage = "9.14 return to ranked gap table selection"
        next_goal = "if valve evidence is fragmented or label-heavy, stop valve direction and choose the next high-support dev wrong-rank bucket"
    else:
        selected = ""
        next_stage = "9.14 return to ranked gap table selection"
        next_goal = "no valve audit rows were available"

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.13 valve near-miss audit",
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
            "candidate_for_transferability_review": learning_counter.get("candidate_for_transferability_review", 0),
            "candidate_for_future_what_if": learning_counter.get("candidate_for_future_what_if", 0),
            "exclude_or_label_review": learning_counter.get("exclude_or_label_review", 0),
            "review_only": learning_counter.get("review_only", 0),
            "soft_joint_compensator_rows": primary_counter.get("soft_joint_compensator_review", 0),
            "top_family_conflict_rows": sum(1 for row in audited if row["top1_family"] and row["top1_family"] != "valve"),
            "duct_taxonomy_context_rows": sum(1 for row in audited if "duct_taxonomy_context" in row["flags"]),
            "query_size_evidence_rows": sum(1 for row in audited if row["query_size_hints"] or row["query_perimeter_hints"]),
            "size_candidate_rows": len(size_candidate_rows),
            "size_candidate_province_count": province_count,
            "size_candidate_source_count": source_count,
        },
        "primary_issue_buckets": _preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _preview(buckets, "learning_status", 20),
        "next_candidate": {
            "selected_from": "dev_only_valve_near_miss",
            "primary_issue": selected,
            "support": len(size_candidate_rows) if selected == "valve_duct_family_size_tier_transferability_review" else primary_counter.get(selected, 0),
            "province_count": province_count if selected == "valve_duct_family_size_tier_transferability_review" else "",
            "source_count": source_count if selected == "valve_duct_family_size_tier_transferability_review" else "",
            "selection_policy": "prefer transferable, cross-row evidence for family compatibility and size-tier review; label-conflict rows and soft-joint-empty buckets cannot drive a rule",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "decision": "The valve near-miss bucket is not a clean valve-rule bucket. soft_joint_compensator has zero rows. The strongest usable signal is valve/duct taxonomy interaction with explicit size or perimeter evidence, but it is still only an audit candidate and must be reviewed for transferability before any what-if.",
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "primary_issue": _preview(buckets, "primary_issue"),
                "learning_status": _preview(buckets, "learning_status"),
                "flag": _preview(buckets, "flag"),
                "province": _preview(buckets, "province"),
                "source_file": _preview(buckets, "source_file"),
                "query_subtype": _preview(buckets, "query_subtype"),
                "top1_family": _preview(buckets, "top1_family"),
            },
            "sample_rows": audited[:12],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.13 audits valve near-miss rows only. It explicitly rejects a soft-joint/compensator patch because support is zero, and it treats label-conflict rows as review/exclusion rather than learnable rules. The only possible next direction is a generic valve/duct family-compatibility plus size-tier transferability review, not a valve-specific rule.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "positive_rank_min",
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
        "query_dimensions",
        "query_perimeter_hints",
        "top_tiers",
        "positive_tiers",
        "size_relation",
        "top_material",
        "positive_material",
        "top_pressure",
        "positive_pressure",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
        "top1_chapter",
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
