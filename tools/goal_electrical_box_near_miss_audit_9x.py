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

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_ranked_gap_reselection_9x_selected_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_electrical_box_near_miss_9x_audit"

BOX_TERMS = ["配电箱", "控制箱", "箱"]
SUBTYPE_TERMS = ["配电箱", "控制箱", "模块箱", "端子箱", "远程控制器", "区域报警", "报警控制箱", "成套配电箱"]
METHOD_TERMS = ["落地", "悬挂", "嵌入", "明装", "暗装", "墙上", "柱上", "移位"]
CROSS_DOMAIN_TERMS = ["热泵", "灭藻", "水箱自洁器", "设备重量", "出水管径"]


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


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _found_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in text]


def _positive_text(value: str) -> str:
    return " || ".join(item.strip() for item in _clean(value).split(" || ") if item.strip())


def _box_code_tail(query: str) -> str:
    value = query
    for term in ("配电箱", "控制箱", "箱"):
        value = value.replace(term, " ")
    value = re.sub(r"规格型号\s*[:：]?", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" -_、，,;；")


def _query_evidence(query: str) -> dict[str, Any]:
    tail = _box_code_tail(query)
    has_dimension = bool(re.search(r"\d+\s*[*xX×]\s*\d+", query))
    has_circuit = "回路" in query
    has_method = bool(_found_terms(query, METHOD_TERMS))
    has_box_word = _has_any(query, BOX_TERMS)
    has_code = bool(tail and re.search(r"[A-Za-z0-9]", tail)) and not has_dimension
    is_generic = has_box_word and not has_code and not has_dimension and not has_circuit and not has_method and len(tail) == 0
    return {
        "query_tail": tail,
        "query_has_box_word": int(has_box_word),
        "query_has_code": int(has_code),
        "query_has_dimension": int(has_dimension),
        "query_has_circuit": int(has_circuit),
        "query_has_method": int(has_method),
        "query_is_generic_box": int(is_generic),
    }


def _methods(text: str) -> list[str]:
    found = _found_terms(text, METHOD_TERMS)
    if "悬挂" in text or "嵌入" in text:
        # Many quota names use "悬挂、嵌入式" as one tier.
        found = [term for term in found if term not in {"悬挂", "嵌入"}]
        found.append("悬挂/嵌入")
    if "墙上" in text or "柱上" in text:
        found = [term for term in found if term not in {"墙上", "柱上"}]
        found.append("墙上/柱上")
    return sorted(set(found))


def _subtypes(text: str) -> list[str]:
    found = _found_terms(text, SUBTYPE_TERMS)
    if "成套配电箱" in found:
        found = [term for term in found if term != "配电箱"]
    return sorted(set(found))


def _half_perimeter_values(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"半周长[^\d]{0,12}(\d+(?:\.\d+)?)", text):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    return values


def _circuit_values(text: str) -> list[int]:
    values: list[int] = []
    for match in re.finditer(r"回路以内\)?\s*(\d+)", text):
        try:
            values.append(int(match.group(1)))
        except ValueError:
            continue
    return values


def _dimension_hint(query: str) -> float | None:
    match = re.search(r"(\d{2,4})\s*[*xX×]\s*(\d{2,4})", query)
    if not match:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    # Audit-only hint: box half-perimeter tiers often use width + height in meters.
    return round((width + height) / 1000, 3)


def _value_text(values: list[Any]) -> str:
    return ",".join(str(item).rstrip("0").rstrip(".") if isinstance(item, float) else str(item) for item in values)


def _classify(row: dict[str, Any]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    top_name = _clean(row.get("top1_name"))
    positive_text = _positive_text(_clean(row.get("positive_names_in_top80")))
    combined = " ".join([query, top_name, positive_text])
    evidence = _query_evidence(query)

    top_methods = _methods(top_name)
    positive_methods = _methods(positive_text)
    top_subtypes = _subtypes(top_name)
    positive_subtypes = _subtypes(positive_text)
    top_half = _half_perimeter_values(top_name)
    positive_half = _half_perimeter_values(positive_text)
    top_circuits = _circuit_values(top_name)
    positive_circuits = _circuit_values(positive_text)
    dim_hint = _dimension_hint(query)
    flags: list[str] = []

    top_family = _clean(row.get("top1_family"))
    if top_family and top_family != "electrical_box":
        flags.append("top1_family_not_electrical_box")
    if not evidence["query_has_box_word"] or _has_any(combined, CROSS_DOMAIN_TERMS):
        flags.append("cross_domain_or_false_family_signal")
    if set(top_methods) != set(positive_methods):
        flags.append("install_method_diff")
    if set(top_subtypes) != set(positive_subtypes):
        flags.append("subtype_diff")
    if top_half or positive_half:
        flags.append("half_perimeter_present")
    if top_half and positive_half and set(top_half) != set(positive_half):
        flags.append("half_perimeter_tier_diff")
    if top_circuits or positive_circuits:
        flags.append("circuit_tier_present")
    if top_circuits and positive_circuits and set(top_circuits) != set(positive_circuits):
        flags.append("circuit_tier_diff")
    if dim_hint is not None:
        flags.append("query_dimension_present")
    if evidence["query_is_generic_box"]:
        flags.append("generic_box_query")
    if evidence["query_has_code"]:
        flags.append("box_code_without_structured_size")

    top_book = _clean(row.get("top1_book"))
    expected_books = _clean(row.get("expected_books"))
    if top_book and expected_books and top_book not in {item.strip() for item in expected_books.split("|")}:
        flags.append("book_or_chapter_bias")

    if "cross_domain_or_false_family_signal" in flags:
        primary = "false_positive_or_cross_domain"
        learning_status = "exclude_from_box_learning"
    elif "subtype_diff" in flags and ("控制箱" in query or "报警" in query or "模块箱" in top_name or "端子箱" in top_name or "远程控制器" in positive_text):
        primary = "subtype_device_mismatch"
        learning_status = "review_only"
    elif dim_hint is not None and ("half_perimeter_tier_diff" in flags or "install_method_diff" in flags):
        primary = "explicit_dimension_tier_evidence"
        learning_status = "candidate_for_future_what_if"
    elif "circuit_tier_diff" in flags and (evidence["query_has_circuit"] or re.search(r"AT|AL|AE", query, flags=re.IGNORECASE)):
        primary = "circuit_tier_or_code_gap"
        learning_status = "weak_candidate_needs_code_mapping"
    elif evidence["query_is_generic_box"]:
        primary = "label_insufficient_generic_box_query"
        learning_status = "do_not_learn_direction"
    elif evidence["query_has_code"]:
        primary = "box_code_without_size_mapping"
        learning_status = "weak_candidate_needs_code_mapping"
    elif "install_method_diff" in flags:
        primary = "install_method_default_bias"
        learning_status = "review_only"
    elif "half_perimeter_tier_diff" in flags or "circuit_tier_diff" in flags:
        primary = "param_tier_gap_no_query_evidence"
        learning_status = "review_only"
    elif "book_or_chapter_bias" in flags:
        primary = "book_or_chapter_bias"
        learning_status = "review_only"
    else:
        primary = "other_near_miss"
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
        "query_tail": evidence["query_tail"],
        "query_has_code": evidence["query_has_code"],
        "query_has_dimension": evidence["query_has_dimension"],
        "query_has_method": evidence["query_has_method"],
        "query_is_generic_box": evidence["query_is_generic_box"],
        "dimension_half_perimeter_hint": "" if dim_hint is None else dim_hint,
        "top_methods": ",".join(top_methods),
        "positive_methods": ",".join(positive_methods),
        "top_subtypes": ",".join(top_subtypes),
        "positive_subtypes": ",".join(positive_subtypes),
        "top_half_perimeter": _value_text(top_half),
        "positive_half_perimeter": _value_text(positive_half),
        "top_circuits": _value_text(top_circuits),
        "positive_circuits": _value_text(positive_circuits),
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": top_name,
        "top1_family": top_family,
        "top1_book": top_book,
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
            "query_has_code",
            "query_has_dimension",
            "query_is_generic_box",
            "positive_rank_min",
            "top_methods",
            "positive_methods",
        ):
            counters[dimension][_clean(row.get(dimension)) or "<empty>"] += 1
        for flag in _clean(row.get("flags")).split("|"):
            if flag:
                counters["flag"][flag] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_electrical_box_near_miss", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


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


def _bucket_preview(buckets: list[dict[str, Any]], dimension: str, limit: int = 10) -> list[dict[str, Any]]:
    return [row for row in buckets if row["dimension"] == dimension][:limit]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    previews = report["artifacts_preview"]["top_buckets"]
    lines = [
        "# Stage 9.7 Electrical Box Near-miss Audit",
        "",
        "Dev-only audit of 44 `electrical_box + near_miss_rank_2_5` rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["candidate_for_future_what_if", report["metrics"]["candidate_for_future_what_if"]],
                ["weak_candidate_needs_code_mapping", report["metrics"]["weak_candidate_needs_code_mapping"]],
                ["do_not_learn_direction", report["metrics"]["do_not_learn_direction"]],
                ["exclude_from_box_learning", report["metrics"]["exclude_from_box_learning"]],
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
    parser = argparse.ArgumentParser(description="Stage 9.7 dev-only electrical_box near-miss audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    source_rows = _read_csv(Path(args.rows))
    audited = [_classify(row) for row in source_rows]
    buckets = _bucket_rows(audited)
    primary_counter = Counter(row["primary_issue"] for row in audited)
    learning_counter = Counter(row["learning_status"] for row in audited)

    next_issue_order = [
        "box_code_without_size_mapping",
        "label_insufficient_generic_box_query",
        "explicit_dimension_tier_evidence",
        "subtype_device_mismatch",
    ]
    selected_key = next((key for key in next_issue_order if primary_counter.get(key, 0) >= 5), primary_counter.most_common(1)[0][0] if primary_counter else "")
    selected_support = primary_counter.get(selected_key, 0)
    if selected_key == "box_code_without_size_mapping":
        next_stage = "9.8 electrical_box code-evidence audit; do not design a rule yet"
        next_goal = "audit whether AL/AE/AT/AP-style box codes provide transferable size or install-method evidence, before any what-if"
    elif selected_key == "label_insufficient_generic_box_query":
        next_stage = "9.8 electrical_box label-insufficient review"
        next_goal = "separate generic box labels from learnable query evidence; likely stop if evidence remains absent"
    else:
        next_stage = "9.8 electrical_box residual design review"
        next_goal = "review the selected issue only if it has enough cross-source support"

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.7 electrical_box near-miss audit",
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
            "weak_candidate_needs_code_mapping": learning_counter.get("weak_candidate_needs_code_mapping", 0),
            "do_not_learn_direction": learning_counter.get("do_not_learn_direction", 0),
            "exclude_from_box_learning": learning_counter.get("exclude_from_box_learning", 0),
            "review_only": learning_counter.get("review_only", 0),
            "query_has_code_rows": sum(_to_int(row.get("query_has_code")) for row in audited),
            "generic_box_query_rows": sum(_to_int(row.get("query_is_generic_box")) for row in audited),
            "query_dimension_rows": sum(_to_int(row.get("query_has_dimension")) for row in audited),
        },
        "primary_issue_buckets": _bucket_preview(buckets, "primary_issue", 20),
        "learning_status_buckets": _bucket_preview(buckets, "learning_status", 20),
        "next_candidate": {
            "selected_from": "dev_only_electrical_box_near_miss",
            "primary_issue": selected_key,
            "support": selected_support,
            "selection_policy": "prefer high-support weak evidence buckets for audit, not direct rules; exclude false positives and generic labels from learning direction",
            "next_stage": next_stage,
            "next_goal": next_goal,
        },
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "primary_issue": _bucket_preview(buckets, "primary_issue"),
                "learning_status": _bucket_preview(buckets, "learning_status"),
                "flag": _bucket_preview(buckets, "flag"),
                "province": _bucket_preview(buckets, "province"),
                "source_file": _bucket_preview(buckets, "source_file"),
            },
            "sample_rows": audited[:12],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.7 audits electrical_box near-miss rows only. Most rows lack explicit size/install-method evidence, so they cannot become direct distribution-box rules. Future work must first audit code evidence instead of assuming AL/AE/AT labels imply a half-perimeter tier.",
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
        "query_tail",
        "query_has_code",
        "query_has_dimension",
        "query_has_method",
        "query_is_generic_box",
        "dimension_half_perimeter_hint",
        "top_methods",
        "positive_methods",
        "top_subtypes",
        "positive_subtypes",
        "top_half_perimeter",
        "positive_half_perimeter",
        "top_circuits",
        "positive_circuits",
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
