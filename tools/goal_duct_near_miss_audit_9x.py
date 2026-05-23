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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_after_electrical_box_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_duct_near_miss_9x_audit"


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


def _positive_text(value: str) -> str:
    return " || ".join(item.strip() for item in _clean(value).split(" || ") if item.strip())


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in text]


def _sizes(text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"(\d{2,5})\s*[*xX×]\s*(\d{2,5})", text):
        values.append(f"{match.group(1)}x{match.group(2)}")
    for match in re.finditer(r"(?:周长|直径|长边长|大边长)[^0-9≤<=]{0,16}(?:≤|<=|以内)?\s*(\d{2,5})", text):
        values.append(match.group(1))
    return values


def _perimeter_from_query(text: str) -> int | None:
    match = re.search(r"(\d{2,5})\s*[*xX×]\s*(\d{2,5})", text)
    if not match:
        return None
    return 2 * (int(match.group(1)) + int(match.group(2)))


def _subtype(text: str) -> str:
    if "防火阀" in text or "排烟阀" in text or "调节阀" in text:
        return "fire_damper"
    if "风口" in text or "散流器" in text or "百叶" in text:
        return "air_outlet"
    if "检修口" in text or "检修孔" in text:
        return "access_panel"
    if "风管" in text or "通风管道" in text:
        return "duct_body"
    return "other"


def _shape(text: str) -> str:
    if "圆形" in text:
        return "round"
    if "矩形" in text or "方形" in text:
        return "rectangular"
    return ""


def _action(text: str) -> str:
    actions = []
    if "制作" in text:
        actions.append("制作")
    if "安装" in text:
        actions.append("安装")
    if "调试" in text:
        actions.append("调试")
    return ",".join(actions)


def _material(text: str) -> str:
    values = _terms(text, ["不锈钢", "碳钢", "镀锌", "薄钢板", "铝合金", "玻璃钢"])
    return ",".join(values)


def _classify(row: dict[str, Any]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(row.get("positive_names_in_top80"))
    combined = " ".join([query, top_name, positive_text])
    top_subtype = _subtype(top_name)
    positive_subtype = _subtype(positive_text)
    query_subtype = _subtype(query)
    top_action = _action(top_name)
    positive_action = _action(positive_text)
    query_action = _action(query)
    top_shape = _shape(top_name)
    positive_shape = _shape(positive_text)
    query_shape = _shape(query)
    query_perimeter = _perimeter_from_query(query)
    top_sizes = _sizes(top_name)
    positive_sizes = _sizes(positive_text)
    flags: list[str] = []

    if _clean(row.get("top1_family")) != "duct":
        flags.append("top1_family_missing_or_not_duct")
    if _has_any(combined, ["检修口", "检修孔"]):
        flags.append("access_panel_or_cross_domain")
    if query_subtype and top_subtype and positive_subtype and (top_subtype != positive_subtype or query_subtype != positive_subtype):
        flags.append("subtype_conflict")
    if query_perimeter is not None:
        flags.append("query_size_present")
    if set(top_sizes) != set(positive_sizes):
        flags.append("candidate_size_tier_diff")
    if query_shape and positive_shape and query_shape != positive_shape:
        flags.append("shape_conflict")
    if top_action != positive_action:
        flags.append("action_diff")
    if "防火阀" in query and "调试" in top_name and "安装" in positive_text:
        flags.append("fire_damper_debug_vs_install")
    if _material(top_name) != _material(positive_text):
        flags.append("material_or_joint_diff")
    if "超高" in query:
        flags.append("query_has_height_modifier")

    if "access_panel_or_cross_domain" in flags:
        primary = "false_positive_or_cross_domain"
        learning_status = "exclude_from_duct_learning"
    elif "fire_damper_debug_vs_install" in flags:
        primary = "fire_damper_debug_vs_install"
        learning_status = "same_province_review_only"
    elif query_perimeter is not None and "candidate_size_tier_diff" in flags:
        primary = "explicit_size_tier_evidence"
        learning_status = "candidate_for_future_what_if"
    elif positive_subtype == "air_outlet" and "candidate_size_tier_diff" in flags:
        primary = "air_outlet_perimeter_tier_no_query_evidence"
        learning_status = "review_only"
    elif positive_subtype == "duct_body" and "candidate_size_tier_diff" in flags:
        primary = "duct_body_size_tier_no_query_evidence"
        learning_status = "review_only"
    elif "action_diff" in flags and ("制作" in positive_action or "安装" in positive_action):
        primary = "fabrication_install_action_diff"
        learning_status = "review_only"
    elif "material_or_joint_diff" in flags:
        primary = "material_or_joint_subtype_diff"
        learning_status = "review_only"
    elif "subtype_conflict" in flags:
        primary = "subtype_conflict"
        learning_status = "review_only"
    else:
        primary = "other_duct_near_miss"
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
        "query_action": query_action,
        "top_action": top_action,
        "positive_action": positive_action,
        "query_shape": query_shape,
        "top_shape": top_shape,
        "positive_shape": positive_shape,
        "query_perimeter_hint": "" if query_perimeter is None else query_perimeter,
        "query_sizes": ",".join(_sizes(query)),
        "top_sizes": ",".join(top_sizes),
        "positive_sizes": ",".join(positive_sizes),
        "top_material": _material(top_name),
        "positive_material": _material(positive_text),
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": top_name,
        "top1_family": _clean(row.get("top1_family")),
        "top1_book": _clean(row.get("top1_book")),
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
            "query_subtype",
            "positive_subtype",
            "query_perimeter_hint",
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
            out.append({"scope": "dev_duct_near_miss", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


def _preview(buckets: list[dict[str, Any]], dimension: str, limit: int = 10) -> list[dict[str, Any]]:
    return [row for row in buckets if row["dimension"] == dimension][:limit]


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
        "# Stage 9.10 Duct Near-miss Audit",
        "",
        "Dev-only audit of 20 `duct + near_miss_rank_2_5` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["candidate_for_future_what_if", report["metrics"]["candidate_for_future_what_if"]],
                ["same_province_review_only", report["metrics"]["same_province_review_only"]],
                ["exclude_from_duct_learning", report["metrics"]["exclude_from_duct_learning"]],
                ["review_only", report["metrics"]["review_only"]],
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
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.10 dev-only duct near-miss audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    audited = [_classify(row) for row in source_rows]
    buckets = _bucket_rows(audited)
    primary_counter = Counter(row["primary_issue"] for row in audited)
    learning_counter = Counter(row["learning_status"] for row in audited)

    if primary_counter.get("fire_damper_debug_vs_install", 0) >= 5:
        selected = "fire_damper_debug_vs_install"
        next_stage = "9.11 duct fire-damper evidence review; do not design a rule yet"
        next_goal = "audit whether 防火阀 rows are a transferable action distinction or only a same-province/source artifact"
    elif primary_counter.get("explicit_size_tier_evidence", 0) >= 5:
        selected = "explicit_size_tier_evidence"
        next_stage = "9.11 duct explicit size evidence review"
        next_goal = "review whether explicit dimensions can safely drive duct/air-outlet tier comparison"
    else:
        selected = primary_counter.most_common(1)[0][0] if primary_counter else ""
        next_stage = "9.11 return to ranked gap table selection"
        next_goal = "if duct evidence remains fragmented, stop duct direction and choose the next high-support dev wrong-rank bucket"

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.10 duct near-miss audit",
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
            "candidate_for_future_what_if": learning_counter.get("candidate_for_future_what_if", 0),
            "same_province_review_only": learning_counter.get("same_province_review_only", 0),
            "exclude_from_duct_learning": learning_counter.get("exclude_from_duct_learning", 0),
            "review_only": learning_counter.get("review_only", 0),
            "query_size_present_rows": sum(1 for row in audited if _clean(row.get("query_perimeter_hint"))),
            "fire_damper_rows": sum(1 for row in audited if row["query_subtype"] == "fire_damper"),
            "air_outlet_rows": sum(1 for row in audited if row["query_subtype"] == "air_outlet"),
            "duct_body_rows": sum(1 for row in audited if row["query_subtype"] == "duct_body"),
        },
        "primary_issue_buckets": _preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _preview(buckets, "learning_status", 20),
        "next_candidate": {
            "selected_from": "dev_only_duct_near_miss",
            "primary_issue": selected,
            "support": primary_counter.get(selected, 0),
            "selection_policy": "prefer high-support evidence buckets for review, not direct duct rules; same-province artifacts and cross-domain labels cannot drive what-if",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "primary_issue": _preview(buckets, "primary_issue"),
                "learning_status": _preview(buckets, "learning_status"),
                "flag": _preview(buckets, "flag"),
                "province": _preview(buckets, "province"),
                "source_file": _preview(buckets, "source_file"),
            },
            "sample_rows": audited[:12],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.10 audits duct near-miss rows only. The largest clean-looking bucket is fire-damper debug-vs-install, but it is concentrated in one province/source and needs a review before any what-if. Other rows are fragmented across size tiers, subtype conflicts, and cross-domain labels.",
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
        "query_action",
        "top_action",
        "positive_action",
        "query_shape",
        "top_shape",
        "positive_shape",
        "query_perimeter_hint",
        "query_sizes",
        "top_sizes",
        "positive_sizes",
        "top_material",
        "positive_material",
        "top1_id",
        "top1_name",
        "top1_family",
        "top1_book",
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
