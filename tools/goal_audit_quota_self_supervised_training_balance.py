from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_NARROW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_narrow.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_balance_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_balance_summary.md"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_balance_buckets.csv"
DEFAULT_FAMILY_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_whitelist_family_plan.csv"
DEFAULT_CELL_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_whitelist_cell_plan.csv"
DEFAULT_PARAM_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_param_buckets.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _bucket_row(dimension: str, key: str, count: int, total: int, extra: str = "") -> dict[str, Any]:
    return {"dimension": dimension, "key": key, "count": count, "rate": _rate(count, total), "extra": extra}


def _pair_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (_clean(row.get("province")), _clean(row.get("family")), _clean(row.get("pair_type")))


def _param_bucket_key(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        _clean(row.get("province")),
        _clean(row.get("family")),
        _clean(row.get("positive_book")),
        _clean(row.get("positive_unit")),
        _clean(row.get("contrast_field")),
        _clean(row.get("positive_subtype_key")),
    )


def _decision_for_family(row: dict[str, Any], args: argparse.Namespace) -> tuple[str, str]:
    total = row["total_pairs"]
    provinces = row["province_count"]
    param = row["param_pairs"]
    subtype = row["subtype_pairs"]
    param_shortfall = row["param_shortfall_for_target"]
    subtype_shortfall = row["subtype_shortfall_for_target"]
    if total < args.min_family_pairs:
        return "exclude_low_family_pairs", f"total<{args.min_family_pairs}"
    if provinces < args.min_family_provinces:
        return "exclude_low_province_coverage", f"province_count<{args.min_family_provinces}"
    if subtype >= args.min_subtype_pairs_per_family and param == 0:
        return "whitelist_subtype_only_no_param", ""
    if subtype >= args.min_subtype_pairs_per_family and param < args.min_param_pairs_per_family:
        return "review_param_low_support", f"param<{args.min_param_pairs_per_family}"
    if param >= args.min_param_pairs_per_family and subtype < args.min_subtype_pairs_per_family:
        return "review_subtype_low_support", f"subtype<{args.min_subtype_pairs_per_family}"
    if param >= args.min_param_pairs_per_family and subtype >= args.min_subtype_pairs_per_family:
        if param_shortfall > 0:
            return "whitelist_both_param_under_target", f"param_target_shortfall={param_shortfall}"
        if subtype_shortfall > 0:
            return "whitelist_both_subtype_under_target", f"subtype_target_shortfall={subtype_shortfall}"
        return "whitelist_both_pair_types", ""
    return "exclude_low_pair_type_support", "param_and_subtype_low"


def _audit(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    total = 0
    pair_type = Counter()
    family = Counter()
    province = Counter()
    contrast_field = Counter()
    quality_bucket = Counter()
    family_pair_type = Counter()
    province_pair_type = Counter()
    family_province = Counter()
    cell = Counter()
    param_bucket = Counter()
    province_sets: dict[str, set[str]] = defaultdict(set)
    family_param_bucket_sets: dict[str, set[tuple[str, str, str, str, str, str]]] = defaultdict(set)

    with Path(args.narrow_csv).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total += 1
            province_key, family_key, pair_type_key = _pair_key(row)
            pair_type[pair_type_key] += 1
            family[family_key] += 1
            province[province_key] += 1
            contrast_field[_clean(row.get("contrast_field"))] += 1
            quality_bucket[_clean(row.get("quality_bucket"))] += 1
            family_pair_type[f"{family_key}:{pair_type_key}"] += 1
            province_pair_type[f"{province_key}:{pair_type_key}"] += 1
            family_province[f"{family_key}:{province_key}"] += 1
            cell[(province_key, family_key, pair_type_key)] += 1
            province_sets[family_key].add(province_key)
            if pair_type_key == "param_contrast":
                bucket_key = _param_bucket_key(row)
                param_bucket[bucket_key] += 1
                family_param_bucket_sets[family_key].add(bucket_key)

    largest_family = family.most_common(1)[0] if family else ("", 0)
    largest_province = province.most_common(1)[0] if province else ("", 0)
    param_pairs = pair_type.get("param_contrast", 0)
    subtype_pairs = pair_type.get("subtype_contrast", 0)
    global_param_rate = _rate(param_pairs, total)
    global_subtype_rate = _rate(subtype_pairs, total)

    family_plan: list[dict[str, Any]] = []
    for family_key, family_total in family.most_common():
        param_count = family_pair_type.get(f"{family_key}:param_contrast", 0)
        subtype_count = family_pair_type.get(f"{family_key}:subtype_contrast", 0)
        planned_cap = min(family_total, args.max_family_cap)
        target_param_for_cap = math.ceil(planned_cap * args.target_param_rate)
        target_subtype_for_cap = max(0, planned_cap - target_param_for_cap)
        plan_row = {
            "family": family_key,
            "total_pairs": family_total,
            "family_rate": _rate(family_total, total),
            "province_count": len(province_sets[family_key]),
            "param_pairs": param_count,
            "param_rate_within_family": _rate(param_count, family_total),
            "subtype_pairs": subtype_count,
            "subtype_rate_within_family": _rate(subtype_count, family_total),
            "param_bucket_count": len(family_param_bucket_sets.get(family_key, set())),
            "planned_family_cap": planned_cap,
            "target_param_for_cap": target_param_for_cap,
            "target_subtype_for_cap": target_subtype_for_cap,
            "param_shortfall_for_target": max(0, target_param_for_cap - param_count),
            "subtype_shortfall_for_target": max(0, target_subtype_for_cap - subtype_count),
        }
        decision, reason = _decision_for_family(plan_row, args)
        plan_row["whitelist_decision"] = decision
        plan_row["reason"] = reason
        family_plan.append(plan_row)

    family_plan.sort(
        key=lambda row: (
            row["whitelist_decision"]
            not in {
                "whitelist_both_pair_types",
                "whitelist_both_param_under_target",
                "whitelist_both_subtype_under_target",
                "whitelist_subtype_only_no_param",
            },
            row["param_shortfall_for_target"],
            -row["total_pairs"],
        )
    )

    cell_plan: list[dict[str, Any]] = []
    for (province_key, family_key, pair_type_key), count in cell.most_common():
        decision = "whitelist_cell" if count >= args.min_cell_pairs else "low_support_cell"
        cell_plan.append(
            {
                "province": province_key,
                "family": family_key,
                "pair_type": pair_type_key,
                "pairs": count,
                "cell_rate": _rate(count, total),
                "cell_decision": decision,
            }
        )

    param_bucket_rows: list[dict[str, Any]] = []
    for (province_key, family_key, book, unit, field, subtype_key), count in param_bucket.most_common():
        param_bucket_rows.append(
            {
                "province": province_key,
                "family": family_key,
                "book": book,
                "unit": unit,
                "contrast_field": field,
                "subtype_key": subtype_key,
                "pairs": count,
                "param_bucket_rate": _rate(count, param_pairs),
            }
        )

    decision_counter = Counter(row["whitelist_decision"] for row in family_plan)
    param_shortfall_families = [row for row in family_plan if row["param_shortfall_for_target"] > 0]
    low_support_cells = sum(1 for row in cell_plan if row["cell_decision"] == "low_support_cell")
    whitelist_cells = len(cell_plan) - low_support_cells

    bucket_rows: list[dict[str, Any]] = []
    for key, count in pair_type.most_common():
        bucket_rows.append(_bucket_row("pair_type", key, count, total))
    for key, count in family.most_common():
        param_count = family_pair_type.get(f"{key}:param_contrast", 0)
        bucket_rows.append(_bucket_row("family", key, count, total, extra=f"param={param_count};provinces={len(province_sets[key])}"))
    for key, count in province.most_common(args.top_provinces):
        bucket_rows.append(_bucket_row("province", key, count, total))
    for key, count in contrast_field.most_common():
        bucket_rows.append(_bucket_row("contrast_field", key, count, total))
    for key, count in quality_bucket.most_common():
        bucket_rows.append(_bucket_row("quality_bucket", key, count, total))
    for key, count in decision_counter.most_common():
        bucket_rows.append(_bucket_row("family_whitelist_decision", key, count, len(family_plan)))
    for key, count in province_pair_type.most_common(args.top_limit):
        bucket_rows.append(_bucket_row("province_pair_type", key, count, total))
    for key, count in family_pair_type.most_common(args.top_limit):
        bucket_rows.append(_bucket_row("family_pair_type", key, count, total))

    passes_basic_balance_gate = (
        total >= args.min_total_pairs
        and len(family) >= args.min_families
        and len(province) >= args.min_provinces
        and _rate(largest_family[1], total) <= args.max_largest_family_rate
        and _rate(largest_province[1], total) <= args.max_largest_province_rate
    )
    passes_pair_type_balance_gate = global_param_rate >= args.min_global_param_rate
    passes_cell_support_gate = whitelist_cells >= args.min_whitelist_cells

    summary = {
        "stage": "Goal LTR v1 / stage 5.3 quota self-supervised training balance audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "narrow_csv": str(Path(args.narrow_csv)),
        "summary": {
            "total_pairs": total,
            "distinct_families": len(family),
            "distinct_provinces": len(province),
            "param_pairs": param_pairs,
            "subtype_pairs": subtype_pairs,
            "param_rate": global_param_rate,
            "subtype_rate": global_subtype_rate,
            "largest_family": largest_family[0],
            "largest_family_pairs": largest_family[1],
            "largest_family_rate": _rate(largest_family[1], total),
            "largest_province": largest_province[0],
            "largest_province_pairs": largest_province[1],
            "largest_province_rate": _rate(largest_province[1], total),
            "family_whitelist_decision": _counter_items(decision_counter, len(family_plan), args.top_limit),
            "whitelist_cells": whitelist_cells,
            "low_support_cells": low_support_cells,
            "param_bucket_count": len(param_bucket),
            "largest_param_bucket_pairs": param_bucket.most_common(1)[0][1] if param_bucket else 0,
            "largest_param_bucket_rate": _rate(param_bucket.most_common(1)[0][1], param_pairs) if param_bucket else 0.0,
            "param_shortfall_family_count": len(param_shortfall_families),
            "param_shortfall_pairs_for_target": sum(row["param_shortfall_for_target"] for row in param_shortfall_families),
            "passes_basic_balance_gate": passes_basic_balance_gate,
            "passes_pair_type_balance_gate": passes_pair_type_balance_gate,
            "passes_cell_support_gate": passes_cell_support_gate,
            "recommend_param_tight_resample": not passes_pair_type_balance_gate,
            "recommendation": (
                "audit_only_param_tight_resample_feasibility"
                if not passes_pair_type_balance_gate
                else "audit_only_no_resample_required_before_whitelist_sampling"
            ),
            "by_pair_type": _counter_items(pair_type, total, args.top_limit),
            "by_family": _counter_items(family, total, args.top_limit),
            "by_province": _counter_items(province, total, args.top_limit),
            "by_contrast_field": _counter_items(contrast_field, total, args.top_limit),
        },
        "thresholds": {
            "min_total_pairs": args.min_total_pairs,
            "min_families": args.min_families,
            "min_provinces": args.min_provinces,
            "max_largest_family_rate": args.max_largest_family_rate,
            "max_largest_province_rate": args.max_largest_province_rate,
            "min_global_param_rate": args.min_global_param_rate,
            "target_param_rate": args.target_param_rate,
            "max_family_cap": args.max_family_cap,
            "min_family_pairs": args.min_family_pairs,
            "min_family_provinces": args.min_family_provinces,
            "min_param_pairs_per_family": args.min_param_pairs_per_family,
            "min_subtype_pairs_per_family": args.min_subtype_pairs_per_family,
            "min_cell_pairs": args.min_cell_pairs,
            "min_whitelist_cells": args.min_whitelist_cells,
        },
    }
    return summary, bucket_rows, family_plan, cell_plan, param_bucket_rows


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]], extra: list[str] | None = None) -> list[list[Any]]:
    extra = extra or []
    rows = [["key", "count", "rate", *extra]]
    for item in items:
        rows.append([item.get("key", ""), item.get("count", ""), item.get("rate", ""), *[item.get(field, "") for field in extra]])
    return rows


def _write_markdown(path: Path, report: dict[str, Any], family_plan: list[dict[str, Any]]) -> None:
    summary = report["summary"]
    thresholds = report["thresholds"]
    family_rows = [
        [
            row["family"],
            row["total_pairs"],
            row["param_pairs"],
            row["param_rate_within_family"],
            row["subtype_pairs"],
            row["province_count"],
            row["param_shortfall_for_target"],
            row["whitelist_decision"],
        ]
        for row in sorted(family_plan, key=lambda item: item["total_pairs"], reverse=True)[:30]
    ]
    lines = [
        "# Goal Quota Self-Supervised Training Balance Audit",
        "",
        "Stage 5.3 eval-only balance audit and whitelist plan. It inspects the narrowed clean pair pool and writes planning files only. It does not train, tune, resample, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["total_pairs", summary["total_pairs"]],
                ["distinct_families", summary["distinct_families"]],
                ["distinct_provinces", summary["distinct_provinces"]],
                ["param_pairs", summary["param_pairs"]],
                ["param_rate", summary["param_rate"]],
                ["subtype_pairs", summary["subtype_pairs"]],
                ["largest_family", summary["largest_family"]],
                ["largest_family_rate", summary["largest_family_rate"]],
                ["largest_province", summary["largest_province"]],
                ["largest_province_rate", summary["largest_province_rate"]],
                ["whitelist_cells", summary["whitelist_cells"]],
                ["param_bucket_count", summary["param_bucket_count"]],
                ["param_shortfall_family_count", summary["param_shortfall_family_count"]],
                ["param_shortfall_pairs_for_target", summary["param_shortfall_pairs_for_target"]],
                ["passes_basic_balance_gate", summary["passes_basic_balance_gate"]],
                ["passes_pair_type_balance_gate", summary["passes_pair_type_balance_gate"]],
                ["passes_cell_support_gate", summary["passes_cell_support_gate"]],
                ["recommend_param_tight_resample", summary["recommend_param_tight_resample"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Thresholds",
        "",
        _md_table([["metric", "value"], *[[key, value] for key, value in thresholds.items()]]),
        "",
        "## Pair Type",
        "",
        _md_table(_counter_table(summary["by_pair_type"])),
        "",
        "## Family Plan",
        "",
        _md_table(
            [
                ["family", "total", "param", "param_rate", "subtype", "provinces", "param_shortfall", "decision"],
                *family_rows,
            ]
        ),
        "",
        "## Family Decision",
        "",
        _md_table(_counter_table(summary["family_whitelist_decision"])),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["summary_json", report["artifacts"]["summary_json"]],
                ["buckets_csv", report["artifacts"]["buckets_csv"]],
                ["family_plan_csv", report["artifacts"]["family_plan_csv"]],
                ["cell_plan_csv", report["artifacts"]["cell_plan_csv"]],
                ["param_buckets_csv", report["artifacts"]["param_buckets_csv"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.3 eval-only self-supervised pair balance audit and whitelist plan")
    parser.add_argument("--narrow-csv", default=str(DEFAULT_NARROW_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--family-plan-csv", default=str(DEFAULT_FAMILY_PLAN_CSV))
    parser.add_argument("--cell-plan-csv", default=str(DEFAULT_CELL_PLAN_CSV))
    parser.add_argument("--param-buckets-csv", default=str(DEFAULT_PARAM_BUCKETS_CSV))
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--top-provinces", type=int, default=80)
    parser.add_argument("--min-total-pairs", type=int, default=50_000)
    parser.add_argument("--min-families", type=int, default=15)
    parser.add_argument("--min-provinces", type=int, default=50)
    parser.add_argument("--max-largest-family-rate", type=float, default=0.18)
    parser.add_argument("--max-largest-province-rate", type=float, default=0.05)
    parser.add_argument("--min-global-param-rate", type=float, default=0.20)
    parser.add_argument("--target-param-rate", type=float, default=0.25)
    parser.add_argument("--max-family-cap", type=int, default=4_000)
    parser.add_argument("--min-family-pairs", type=int, default=500)
    parser.add_argument("--min-family-provinces", type=int, default=3)
    parser.add_argument("--min-param-pairs-per-family", type=int, default=200)
    parser.add_argument("--min-subtype-pairs-per-family", type=int, default=200)
    parser.add_argument("--min-cell-pairs", type=int, default=20)
    parser.add_argument("--min-whitelist-cells", type=int, default=500)
    args = parser.parse_args()

    started = time.perf_counter()
    report, bucket_rows, family_plan, cell_plan, param_bucket_rows = _audit(args)
    report["elapsed_sec"] = round(time.perf_counter() - started, 3)
    report["artifacts"] = {
        "summary_json": str(Path(args.report_json)),
        "summary_md": str(Path(args.report_md)),
        "buckets_csv": str(Path(args.buckets_csv)),
        "family_plan_csv": str(Path(args.family_plan_csv)),
        "cell_plan_csv": str(Path(args.cell_plan_csv)),
        "param_buckets_csv": str(Path(args.param_buckets_csv)),
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report, family_plan)
    _write_csv(Path(args.buckets_csv), bucket_rows, ["dimension", "key", "count", "rate", "extra"])
    _write_csv(
        Path(args.family_plan_csv),
        family_plan,
        [
            "family",
            "total_pairs",
            "family_rate",
            "province_count",
            "param_pairs",
            "param_rate_within_family",
            "subtype_pairs",
            "subtype_rate_within_family",
            "param_bucket_count",
            "planned_family_cap",
            "target_param_for_cap",
            "target_subtype_for_cap",
            "param_shortfall_for_target",
            "subtype_shortfall_for_target",
            "whitelist_decision",
            "reason",
        ],
    )
    _write_csv(Path(args.cell_plan_csv), cell_plan, ["province", "family", "pair_type", "pairs", "cell_rate", "cell_decision"])
    _write_csv(
        Path(args.param_buckets_csv),
        param_bucket_rows,
        ["province", "family", "book", "unit", "contrast_field", "subtype_key", "pairs", "param_bucket_rate"],
    )

    print(
        json.dumps(
            {
                "summary": {
                    "total_pairs": report["summary"]["total_pairs"],
                    "param_pairs": report["summary"]["param_pairs"],
                    "param_rate": report["summary"]["param_rate"],
                    "subtype_pairs": report["summary"]["subtype_pairs"],
                    "passes_basic_balance_gate": report["summary"]["passes_basic_balance_gate"],
                    "passes_pair_type_balance_gate": report["summary"]["passes_pair_type_balance_gate"],
                    "passes_cell_support_gate": report["summary"]["passes_cell_support_gate"],
                    "recommend_param_tight_resample": report["summary"]["recommend_param_tight_resample"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
