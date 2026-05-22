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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from tools.goal_generate_quota_self_supervised_pair_candidates import (  # noqa: E402
    DEFAULT_PLAN_CSV,
    DEFAULT_PROVINCE_ROOT,
    _allocate_targets,
    _int,
    _load_plan,
    _load_province_records,
    _possible_contrast_pairs,
)

DEFAULT_NARROW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_narrow.csv"
DEFAULT_FAMILY_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_whitelist_family_plan.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_param_tight_feasibility_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_param_tight_feasibility_summary.md"
DEFAULT_FAMILY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_param_tight_feasibility_family.csv"
DEFAULT_PROVINCE_FAMILY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_param_tight_feasibility_province_family.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_param_tight_feasibility_buckets.csv"
DEFAULT_PRIORITY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_param_tight_feasibility_priority_gaps.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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


def _read_current_clean_counts(path: Path) -> dict[str, Any]:
    pair_type = Counter()
    family_param = Counter()
    family_subtype = Counter()
    province_family_param = Counter()
    province_family_subtype = Counter()
    family_provinces: dict[str, set[str]] = defaultdict(set)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            province = _clean(row.get("province"))
            family = _clean(row.get("family"))
            row_pair_type = _clean(row.get("pair_type"))
            pair_type[row_pair_type] += 1
            family_provinces[family].add(province)
            key = (province, family)
            if row_pair_type == "param_contrast":
                family_param[family] += 1
                province_family_param[key] += 1
            elif row_pair_type == "subtype_contrast":
                family_subtype[family] += 1
                province_family_subtype[key] += 1

    return {
        "pair_type": pair_type,
        "family_param": family_param,
        "family_subtype": family_subtype,
        "province_family_param": province_family_param,
        "province_family_subtype": province_family_subtype,
        "family_provinces": family_provinces,
    }


def _read_priority_families(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    priorities: dict[str, dict[str, Any]] = {}
    for row in _read_csv(path):
        shortfall = _int(row.get("param_shortfall_for_target"))
        decision = _clean(row.get("whitelist_decision"))
        if shortfall > 0 or decision in {"review_param_low_support", "whitelist_both_param_under_target", "whitelist_subtype_only_no_param"}:
            priorities[_clean(row.get("family"))] = {
                "param_shortfall_for_target": shortfall,
                "decision": decision,
                "current_param_pairs": _int(row.get("param_pairs")),
                "current_subtype_pairs": _int(row.get("subtype_pairs")),
                "current_total_pairs": _int(row.get("total_pairs")),
            }
    return priorities


def _build_tight_param_buckets(records: list[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        if record["family"] != family:
            continue
        if not record["param_type"] or not record["param_value"] or not record["subtype_key"]:
            continue
        key = (record["book"], record["unit"], record["param_type"], record["subtype_key"])
        groups[key][record["param_value"]].append(record)

    buckets: list[dict[str, Any]] = []
    for (book, unit, param_type, subtype_key), value_to_records in groups.items():
        available = _possible_contrast_pairs(value_to_records)
        if not available:
            continue
        buckets.append(
            {
                "book": book,
                "unit": unit,
                "contrast_field": param_type,
                "subtype_key": subtype_key,
                "value_count": len(value_to_records),
                "record_count": sum(len(rows) for rows in value_to_records.values()),
                "available_pairs": available,
                "value_to_records": value_to_records,
            }
        )
    return buckets


def _province_family_decision(planned: int, current: int, feasible: int, priority: bool) -> str:
    if planned <= 0:
        return "no_planned_param"
    if feasible >= planned:
        return "tight_can_fill_plan"
    if feasible > current:
        return "tight_partial_gain_priority" if priority else "tight_partial_gain"
    if feasible == current and feasible > 0:
        return "tight_same_as_current"
    return "tight_no_clean_param_capacity"


def _family_decision(row: dict[str, Any], args: argparse.Namespace) -> str:
    if row["tight_feasible_param_pairs"] <= row["current_clean_param_pairs"]:
        return "no_resample_gain"
    if row["priority_family"] == "yes":
        return "priority_tight_resample_candidate"
    if row["tight_feasible_param_rate_with_current_subtype"] >= args.target_param_rate:
        return "healthy_tight_resample_candidate"
    return "secondary_tight_resample_candidate"


def _audit(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    current = _read_current_clean_counts(Path(args.narrow_csv))
    priority_families = _read_priority_families(Path(args.family_plan_csv))
    current_pair_type = current["pair_type"]
    current_param_total = current_pair_type.get("param_contrast", 0)
    current_subtype_total = current_pair_type.get("subtype_contrast", 0)
    needed_param_for_gate = math.ceil(args.min_global_param_rate * current_subtype_total / max(0.000001, 1 - args.min_global_param_rate))

    plan_by_province = _load_plan(Path(args.plan_csv), args.limit_province_families)
    province_root = Path(args.province_root)
    province_family_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []
    skipped = Counter()
    planned_param_total = 0
    tight_available_total = 0
    tight_capped_total = 0
    tight_feasible_total = 0
    current_param_in_plan_total = 0
    priority_tight_feasible_total = 0
    priority_current_param_total = 0
    priority_planned_param_total = 0

    for province_index, (province, plan_rows) in enumerate(plan_by_province.items(), 1):
        db_path = province_root / province / "quota.db"
        if not db_path.exists():
            skipped["quota_db_missing"] += 1
            for plan_row in plan_rows:
                planned_param = _int(plan_row.get("planned_param_pairs"))
                planned_param_total += planned_param
                family = _clean(plan_row.get("family"))
                key = (province, family)
                current_clean = current["province_family_param"].get(key, 0)
                row = {
                    "province": province,
                    "family": family,
                    "priority_family": "yes" if family in priority_families else "no",
                    "planned_param_pairs": planned_param,
                    "current_clean_param_pairs": current_clean,
                    "tight_bucket_count": 0,
                    "tight_available_pairs": 0,
                    "tight_capped_capacity": 0,
                    "tight_feasible_param_pairs": 0,
                    "tight_shortfall_vs_plan": planned_param,
                    "tight_gain_vs_current": -current_clean,
                    "decision": "quota_db_missing",
                }
                province_family_rows.append(row)
            continue

        needed_families = {_clean(row.get("family")) for row in plan_rows}
        records, province_skipped = _load_province_records(db_path, province, needed_families)
        skipped.update(province_skipped)
        records_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            records_by_family[record["family"]].append(record)

        for plan_row in plan_rows:
            family = _clean(plan_row.get("family"))
            planned_param = _int(plan_row.get("planned_param_pairs"))
            planned_param_total += planned_param
            current_clean = current["province_family_param"].get((province, family), 0)
            current_param_in_plan_total += current_clean
            family_records = records_by_family.get(family, [])
            is_priority = family in priority_families
            if is_priority:
                priority_current_param_total += current_clean
                priority_planned_param_total += planned_param

            buckets = _build_tight_param_buckets(family_records, family) if family_records and planned_param > 0 else []
            allocations = _allocate_targets(planned_param, buckets, args.max_bucket_pairs) if buckets and planned_param > 0 else {}
            tight_feasible = sum(allocations.values())
            tight_available = sum(bucket["available_pairs"] for bucket in buckets)
            tight_capped = sum(min(args.max_bucket_pairs, bucket["available_pairs"]) for bucket in buckets)
            tight_available_total += tight_available
            tight_capped_total += tight_capped
            tight_feasible_total += tight_feasible
            if is_priority:
                priority_tight_feasible_total += tight_feasible

            for bucket_index, bucket in enumerate(buckets):
                allocated = allocations.get(bucket_index, 0)
                if allocated <= 0 and not args.write_zero_alloc_buckets:
                    continue
                bucket_rows.append(
                    {
                        "province": province,
                        "family": family,
                        "priority_family": "yes" if is_priority else "no",
                        "book": bucket["book"],
                        "unit": bucket["unit"],
                        "contrast_field": bucket["contrast_field"],
                        "subtype_key": bucket["subtype_key"],
                        "value_count": bucket["value_count"],
                        "record_count": bucket["record_count"],
                        "available_pairs": bucket["available_pairs"],
                        "capped_capacity": min(args.max_bucket_pairs, bucket["available_pairs"]),
                        "allocated_pairs": allocated,
                    }
                )

            decision = _province_family_decision(planned_param, current_clean, tight_feasible, is_priority)
            province_family_rows.append(
                {
                    "province": province,
                    "family": family,
                    "priority_family": "yes" if is_priority else "no",
                    "planned_param_pairs": planned_param,
                    "current_clean_param_pairs": current_clean,
                    "tight_bucket_count": len(buckets),
                    "tight_available_pairs": tight_available,
                    "tight_capped_capacity": tight_capped,
                    "tight_feasible_param_pairs": tight_feasible,
                    "tight_shortfall_vs_plan": max(0, planned_param - tight_feasible),
                    "tight_gain_vs_current": tight_feasible - current_clean,
                    "decision": decision,
                }
            )

        if args.progress_every > 0 and province_index % args.progress_every == 0:
            print(f"processed {province_index}/{len(plan_by_province)} provinces; tight_feasible={tight_feasible_total}", file=sys.stderr)

    family_aggregate: dict[str, dict[str, Any]] = {}
    family_provinces: dict[str, set[str]] = defaultdict(set)
    for row in province_family_rows:
        family = row["family"]
        if family not in family_aggregate:
            family_aggregate[family] = {
                "family": family,
                "priority_family": "yes" if family in priority_families else "no",
                "priority_source_decision": priority_families.get(family, {}).get("decision", ""),
                "priority_param_shortfall_for_target": priority_families.get(family, {}).get("param_shortfall_for_target", 0),
                "planned_param_pairs": 0,
                "current_clean_param_pairs": 0,
                "current_subtype_pairs": current["family_subtype"].get(family, 0),
                "tight_bucket_count": 0,
                "tight_available_pairs": 0,
                "tight_capped_capacity": 0,
                "tight_feasible_param_pairs": 0,
                "tight_shortfall_vs_plan": 0,
                "tight_gain_vs_current": 0,
                "province_family_count": 0,
            }
        target = family_aggregate[family]
        family_provinces[family].add(row["province"])
        target["planned_param_pairs"] += _int(row.get("planned_param_pairs"))
        target["current_clean_param_pairs"] += _int(row.get("current_clean_param_pairs"))
        target["tight_bucket_count"] += _int(row.get("tight_bucket_count"))
        target["tight_available_pairs"] += _int(row.get("tight_available_pairs"))
        target["tight_capped_capacity"] += _int(row.get("tight_capped_capacity"))
        target["tight_feasible_param_pairs"] += _int(row.get("tight_feasible_param_pairs"))
        target["tight_shortfall_vs_plan"] += _int(row.get("tight_shortfall_vs_plan"))
        target["tight_gain_vs_current"] += _int(row.get("tight_gain_vs_current"))
        target["province_family_count"] += 1

    family_rows: list[dict[str, Any]] = []
    for family, row in family_aggregate.items():
        current_subtype = _int(row.get("current_subtype_pairs"))
        tight_feasible = _int(row.get("tight_feasible_param_pairs"))
        current_clean = _int(row.get("current_clean_param_pairs"))
        row["province_count"] = len(family_provinces[family])
        row["current_param_rate_with_current_subtype"] = _rate(current_clean, current_clean + current_subtype)
        row["tight_feasible_param_rate_with_current_subtype"] = _rate(tight_feasible, tight_feasible + current_subtype)
        row["tight_plan_fill_rate"] = _rate(tight_feasible, _int(row.get("planned_param_pairs")))
        row["tight_gain_rate_vs_current"] = _rate(max(0, tight_feasible - current_clean), max(1, current_clean))
        row["family_decision"] = _family_decision(row, args)
        family_rows.append(row)

    family_rows.sort(key=lambda row: (_int(row.get("tight_gain_vs_current")), _int(row.get("priority_param_shortfall_for_target"))), reverse=True)
    province_family_rows.sort(key=lambda row: (_int(row.get("tight_gain_vs_current")), _int(row.get("planned_param_pairs"))), reverse=True)
    bucket_rows.sort(key=lambda row: (_int(row.get("allocated_pairs")), _int(row.get("available_pairs"))), reverse=True)

    projected_param_total = tight_feasible_total
    projected_total = projected_param_total + current_subtype_total
    current_total = current_param_total + current_subtype_total
    decision_counter = Counter(row["decision"] for row in province_family_rows)
    family_decision_counter = Counter(row["family_decision"] for row in family_rows)
    priority_family_rows = [row for row in family_rows if row["priority_family"] == "yes"]
    priority_rows = [
        row
        for row in family_rows
        if row["priority_family"] == "yes" or _int(row.get("tight_gain_vs_current")) > 0 or _int(row.get("tight_shortfall_vs_plan")) > 0
    ]
    priority_rows.sort(key=lambda row: (_int(row.get("priority_param_shortfall_for_target")), _int(row.get("tight_gain_vs_current"))), reverse=True)

    summary = {
        "stage": "Goal LTR v1 / stage 5.4 param contrast tight resampling feasibility audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "no_pair_generation": True,
        "province_root": str(province_root),
        "plan_csv": str(Path(args.plan_csv)),
        "narrow_csv": str(Path(args.narrow_csv)),
        "family_plan_csv": str(Path(args.family_plan_csv)),
        "summary": {
            "current_param_pairs": current_param_total,
            "current_subtype_pairs": current_subtype_total,
            "current_total_pairs": current_total,
            "current_param_rate": _rate(current_param_total, current_total),
            "planned_param_pairs": planned_param_total,
            "tight_available_pairs": tight_available_total,
            "tight_capped_capacity": tight_capped_total,
            "tight_feasible_param_pairs": tight_feasible_total,
            "tight_shortfall_vs_plan": max(0, planned_param_total - tight_feasible_total),
            "tight_gain_vs_current_clean": tight_feasible_total - current_param_total,
            "projected_total_pairs": projected_total,
            "projected_param_rate": _rate(projected_param_total, projected_total),
            "needed_param_for_min_rate": needed_param_for_gate,
            "needed_param_gain_from_current": max(0, needed_param_for_gate - current_param_total),
            "passes_min_param_rate_after_tight_resample": _rate(projected_param_total, projected_total) >= args.min_global_param_rate,
            "priority_family_count": len(priority_families),
            "priority_current_param_pairs": priority_current_param_total,
            "priority_planned_param_pairs": priority_planned_param_total,
            "priority_tight_feasible_param_pairs": priority_tight_feasible_total,
            "priority_tight_gain_vs_current": priority_tight_feasible_total - priority_current_param_total,
            "province_family_decision": _counter_items(decision_counter, len(province_family_rows), args.top_limit),
            "family_decision": _counter_items(family_decision_counter, len(family_rows), args.top_limit),
            "skipped": _counter_items(skipped, sum(skipped.values()), args.top_limit),
            "recommendation": (
                "tight_param_resample_feasible_audit_only"
                if _rate(projected_param_total, projected_total) >= args.min_global_param_rate
                else "tight_param_resample_not_enough_audit_more_capacity"
            ),
        },
        "thresholds": {
            "min_global_param_rate": args.min_global_param_rate,
            "target_param_rate": args.target_param_rate,
            "max_bucket_pairs": args.max_bucket_pairs,
            "limit_province_families": args.limit_province_families,
        },
    }
    return summary, family_rows, province_family_rows, bucket_rows, priority_rows


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


def _counter_table(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows = [["key", "count", "rate"]]
    for item in items:
        rows.append([item.get("key", ""), item.get("count", ""), item.get("rate", "")])
    return rows


def _write_markdown(path: Path, report: dict[str, Any], family_rows: list[dict[str, Any]]) -> None:
    summary = report["summary"]
    family_table = [
        [
            row["family"],
            row["priority_family"],
            row["current_clean_param_pairs"],
            row["tight_feasible_param_pairs"],
            row["tight_gain_vs_current"],
            row["current_subtype_pairs"],
            row["tight_feasible_param_rate_with_current_subtype"],
            row["tight_shortfall_vs_plan"],
            row["family_decision"],
        ]
        for row in family_rows[:30]
    ]
    lines = [
        "# Goal Param Tight Resampling Feasibility",
        "",
        "Stage 5.4 eval-only feasibility audit. It scans quota.db and simulates tight param buckets keyed by book + unit + param_type + subtype_key. It does not generate pairs, train, tune, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["current_param_pairs", summary["current_param_pairs"]],
                ["current_subtype_pairs", summary["current_subtype_pairs"]],
                ["current_param_rate", summary["current_param_rate"]],
                ["planned_param_pairs", summary["planned_param_pairs"]],
                ["tight_feasible_param_pairs", summary["tight_feasible_param_pairs"]],
                ["tight_gain_vs_current_clean", summary["tight_gain_vs_current_clean"]],
                ["projected_param_rate", summary["projected_param_rate"]],
                ["needed_param_for_min_rate", summary["needed_param_for_min_rate"]],
                ["needed_param_gain_from_current", summary["needed_param_gain_from_current"]],
                ["passes_min_param_rate_after_tight_resample", summary["passes_min_param_rate_after_tight_resample"]],
                ["priority_tight_gain_vs_current", summary["priority_tight_gain_vs_current"]],
                ["recommendation", summary["recommendation"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Province-Family Decision",
        "",
        _md_table(_counter_table(summary["province_family_decision"])),
        "",
        "## Family Decision",
        "",
        _md_table(_counter_table(summary["family_decision"])),
        "",
        "## Family Feasibility",
        "",
        _md_table(
            [
                ["family", "priority", "current_param", "tight_param", "gain", "subtype", "projected_param_rate", "shortfall", "decision"],
                *family_table,
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["summary_json", report["artifacts"]["summary_json"]],
                ["family_csv", report["artifacts"]["family_csv"]],
                ["province_family_csv", report["artifacts"]["province_family_csv"]],
                ["buckets_csv", report["artifacts"]["buckets_csv"]],
                ["priority_csv", report["artifacts"]["priority_csv"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.4 eval-only tight param contrast resampling feasibility audit")
    parser.add_argument("--province-root", default=str(DEFAULT_PROVINCE_ROOT))
    parser.add_argument("--plan-csv", default=str(DEFAULT_PLAN_CSV))
    parser.add_argument("--narrow-csv", default=str(DEFAULT_NARROW_CSV))
    parser.add_argument("--family-plan-csv", default=str(DEFAULT_FAMILY_PLAN_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--family-csv", default=str(DEFAULT_FAMILY_CSV))
    parser.add_argument("--province-family-csv", default=str(DEFAULT_PROVINCE_FAMILY_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--priority-csv", default=str(DEFAULT_PRIORITY_CSV))
    parser.add_argument("--limit-province-families", type=int, default=0)
    parser.add_argument("--max-bucket-pairs", type=int, default=80)
    parser.add_argument("--min-global-param-rate", type=float, default=0.20)
    parser.add_argument("--target-param-rate", type=float, default=0.25)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--write-zero-alloc-buckets", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    report, family_rows, province_family_rows, bucket_rows, priority_rows = _audit(args)
    report["elapsed_sec"] = round(time.perf_counter() - started, 3)
    report["artifacts"] = {
        "summary_json": str(Path(args.report_json)),
        "summary_md": str(Path(args.report_md)),
        "family_csv": str(Path(args.family_csv)),
        "province_family_csv": str(Path(args.province_family_csv)),
        "buckets_csv": str(Path(args.buckets_csv)),
        "priority_csv": str(Path(args.priority_csv)),
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report, family_rows)
    _write_csv(
        Path(args.family_csv),
        family_rows,
        [
            "family",
            "priority_family",
            "priority_source_decision",
            "priority_param_shortfall_for_target",
            "planned_param_pairs",
            "current_clean_param_pairs",
            "current_subtype_pairs",
            "current_param_rate_with_current_subtype",
            "tight_bucket_count",
            "tight_available_pairs",
            "tight_capped_capacity",
            "tight_feasible_param_pairs",
            "tight_feasible_param_rate_with_current_subtype",
            "tight_plan_fill_rate",
            "tight_shortfall_vs_plan",
            "tight_gain_vs_current",
            "tight_gain_rate_vs_current",
            "province_count",
            "province_family_count",
            "family_decision",
        ],
    )
    _write_csv(
        Path(args.province_family_csv),
        province_family_rows,
        [
            "province",
            "family",
            "priority_family",
            "planned_param_pairs",
            "current_clean_param_pairs",
            "tight_bucket_count",
            "tight_available_pairs",
            "tight_capped_capacity",
            "tight_feasible_param_pairs",
            "tight_shortfall_vs_plan",
            "tight_gain_vs_current",
            "decision",
        ],
    )
    _write_csv(
        Path(args.buckets_csv),
        bucket_rows,
        [
            "province",
            "family",
            "priority_family",
            "book",
            "unit",
            "contrast_field",
            "subtype_key",
            "value_count",
            "record_count",
            "available_pairs",
            "capped_capacity",
            "allocated_pairs",
        ],
    )
    _write_csv(
        Path(args.priority_csv),
        priority_rows,
        [
            "family",
            "priority_family",
            "priority_source_decision",
            "priority_param_shortfall_for_target",
            "planned_param_pairs",
            "current_clean_param_pairs",
            "current_subtype_pairs",
            "current_param_rate_with_current_subtype",
            "tight_feasible_param_pairs",
            "tight_feasible_param_rate_with_current_subtype",
            "tight_shortfall_vs_plan",
            "tight_gain_vs_current",
            "family_decision",
        ],
    )

    print(
        json.dumps(
            {
                "summary": {
                    "current_param_pairs": report["summary"]["current_param_pairs"],
                    "current_param_rate": report["summary"]["current_param_rate"],
                    "tight_feasible_param_pairs": report["summary"]["tight_feasible_param_pairs"],
                    "projected_param_rate": report["summary"]["projected_param_rate"],
                    "tight_gain_vs_current_clean": report["summary"]["tight_gain_vs_current_clean"],
                    "passes_min_param_rate_after_tight_resample": report["summary"]["passes_min_param_rate_after_tight_resample"],
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
