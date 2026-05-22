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

DEFAULT_FAMILY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_stats_family.csv"
DEFAULT_PROVINCE_FAMILY_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_stats_province_family.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_sampling_plan_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_sampling_plan_summary.md"
DEFAULT_FAMILY_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_sampling_plan_family.csv"
DEFAULT_PROVINCE_FAMILY_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_sampling_plan_province_family.csv"
DEFAULT_EXCLUDED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_sampling_plan_excluded.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


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


def _family_available(row: dict[str, Any]) -> int:
    return _int(row.get("total_self_supervised_pairs"))


def _eligible_family(row: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    if _int(row.get("quota_records")) < args.min_family_records:
        return False, "family_records_below_min"
    if _int(row.get("province_count")) < args.min_family_provinces:
        return False, "family_province_count_below_min"
    if _family_available(row) < args.min_family_pairs:
        return False, "family_pairs_below_min"
    return True, ""


def _largest_remainder(target: int, weights: dict[str, float], caps: dict[str, int], floors: dict[str, int] | None = None) -> dict[str, int]:
    floors = floors or {}
    if target <= 0 or not weights:
        return {key: 0 for key in weights}

    total_weight = sum(max(0.0, value) for value in weights.values())
    if total_weight <= 0:
        return {key: 0 for key in weights}

    allocations: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for key, weight in weights.items():
        raw = target * max(0.0, weight) / total_weight
        floor = min(caps.get(key, 0), floors.get(key, 0))
        base = max(floor, int(math.floor(raw)))
        base = min(base, caps.get(key, 0))
        allocations[key] = base
        remainders.append((raw - math.floor(raw), key))

    current = sum(allocations.values())
    if current > target:
        for _remainder, key in sorted(remainders, key=lambda item: (allocations[item[1]], item[0]), reverse=True):
            while current > target and allocations[key] > floors.get(key, 0):
                allocations[key] -= 1
                current -= 1
            if current <= target:
                break
    elif current < target:
        for _remainder, key in sorted(remainders, reverse=True):
            if current >= target:
                break
            room = caps.get(key, 0) - allocations[key]
            if room <= 0:
                continue
            add = min(room, target - current)
            allocations[key] += add
            current += add
    return allocations


def _make_family_plan(family_rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in family_rows:
        ok, reason = _eligible_family(row, args)
        if ok:
            eligible.append(row)
        else:
            excluded.append(
                {
                    "level": "family",
                    "family": _clean(row.get("family")),
                    "province": "",
                    "exclude_reason": reason,
                    "quota_records": _int(row.get("quota_records")),
                    "available_pairs": _family_available(row),
                }
            )

    weights = {row["family"]: math.sqrt(_family_available(row)) for row in eligible}
    caps = {row["family"]: min(args.max_family_target, _family_available(row)) for row in eligible}
    floors = {row["family"]: min(args.min_family_target, caps[row["family"]]) for row in eligible}
    family_targets = _largest_remainder(args.target_pairs, weights, caps, floors)

    plan_rows: list[dict[str, Any]] = []
    for row in eligible:
        family = _clean(row.get("family"))
        target = family_targets.get(family, 0)
        param_available = _int(row.get("param_contrast_pairs"))
        subtype_available = _int(row.get("subtype_contrast_pairs"))
        param_floor = min(param_available, int(target * args.min_param_share)) if target else 0
        param_cap = min(param_available, int(target * args.max_param_share)) if target else 0
        desired_param = min(param_available, max(param_floor, int(target * args.target_param_share)))
        desired_param = min(desired_param, param_cap) if param_cap else 0
        subtype_target = min(subtype_available, target - desired_param)
        if desired_param + subtype_target < target and param_available > desired_param:
            desired_param += min(param_available - desired_param, target - desired_param - subtype_target)
        plan_rows.append(
            {
                "family": family,
                "quota_records": _int(row.get("quota_records")),
                "province_count": _int(row.get("province_count")),
                "available_param_pairs": param_available,
                "available_subtype_pairs": subtype_available,
                "available_total_pairs": _family_available(row),
                "planned_param_pairs": desired_param,
                "planned_subtype_pairs": subtype_target,
                "planned_total_pairs": desired_param + subtype_target,
                "available_to_planned_ratio": round(_family_available(row) / max(1, desired_param + subtype_target), 3),
            }
        )
    return plan_rows, excluded


def _make_province_family_plan(
    province_family_rows: list[dict[str, Any]],
    family_plan: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_by_family = {row["family"]: _int(row.get("planned_total_pairs")) for row in family_plan}
    param_target_by_family = {row["family"]: _int(row.get("planned_param_pairs")) for row in family_plan}
    subtype_target_by_family = {row["family"]: _int(row.get("planned_subtype_pairs")) for row in family_plan}
    rows_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded: list[dict[str, Any]] = []
    for row in province_family_rows:
        family = _clean(row.get("family"))
        available = _int(row.get("total_self_supervised_pairs"))
        if family not in target_by_family:
            continue
        if _int(row.get("quota_records")) < args.min_province_family_records:
            excluded.append(
                {
                    "level": "province_family",
                    "family": family,
                    "province": _clean(row.get("province")),
                    "exclude_reason": "province_family_records_below_min",
                    "quota_records": _int(row.get("quota_records")),
                    "available_pairs": available,
                }
            )
            continue
        if available < args.min_province_family_pairs:
            excluded.append(
                {
                    "level": "province_family",
                    "family": family,
                    "province": _clean(row.get("province")),
                    "exclude_reason": "province_family_pairs_below_min",
                    "quota_records": _int(row.get("quota_records")),
                    "available_pairs": available,
                }
            )
            continue
        rows_by_family[family].append(row)

    plan_rows: list[dict[str, Any]] = []
    province_totals: Counter[str] = Counter()
    for family, rows in rows_by_family.items():
        family_target = target_by_family.get(family, 0)
        if family_target <= 0:
            continue
        weights = {f"{_clean(row.get('province'))}\u241f{family}": math.sqrt(_int(row.get("total_self_supervised_pairs"))) for row in rows}
        caps = {
            f"{_clean(row.get('province'))}\u241f{family}": min(args.max_province_family_target, _int(row.get("total_self_supervised_pairs")))
            for row in rows
        }
        allocations = _largest_remainder(family_target, weights, caps)
        for row in rows:
            province = _clean(row.get("province"))
            key = f"{province}\u241f{family}"
            planned_total = allocations.get(key, 0)
            if planned_total <= 0:
                continue
            province_room = max(0, args.max_province_target - province_totals[province])
            planned_total = min(planned_total, province_room)
            if planned_total <= 0:
                continue
            province_totals[province] += planned_total

            family_total = max(1, target_by_family.get(family, 0))
            param_share = param_target_by_family.get(family, 0) / family_total
            planned_param = min(_int(row.get("param_contrast_pairs")), int(round(planned_total * param_share)))
            planned_subtype = min(_int(row.get("subtype_contrast_pairs")), planned_total - planned_param)
            if planned_param + planned_subtype < planned_total and _int(row.get("param_contrast_pairs")) > planned_param:
                planned_param += min(_int(row.get("param_contrast_pairs")) - planned_param, planned_total - planned_param - planned_subtype)
            if planned_param + planned_subtype < planned_total and _int(row.get("subtype_contrast_pairs")) > planned_subtype:
                planned_subtype += min(_int(row.get("subtype_contrast_pairs")) - planned_subtype, planned_total - planned_param - planned_subtype)

            plan_rows.append(
                {
                    "province": province,
                    "family": family,
                    "quota_records": _int(row.get("quota_records")),
                    "available_param_pairs": _int(row.get("param_contrast_pairs")),
                    "available_subtype_pairs": _int(row.get("subtype_contrast_pairs")),
                    "available_total_pairs": _int(row.get("total_self_supervised_pairs")),
                    "planned_param_pairs": planned_param,
                    "planned_subtype_pairs": planned_subtype,
                    "planned_total_pairs": planned_param + planned_subtype,
                }
            )
    return plan_rows, excluded


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(
    family_plan: list[dict[str, Any]],
    province_family_plan: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    source_total_pairs: int,
    top_limit: int,
) -> dict[str, Any]:
    planned_total = sum(_int(row.get("planned_total_pairs")) for row in province_family_plan)
    planned_param = sum(_int(row.get("planned_param_pairs")) for row in province_family_plan)
    planned_subtype = sum(_int(row.get("planned_subtype_pairs")) for row in province_family_plan)
    by_family = Counter()
    by_province = Counter()
    for row in province_family_plan:
        by_family[_clean(row.get("family"))] += _int(row.get("planned_total_pairs"))
        by_province[_clean(row.get("province"))] += _int(row.get("planned_total_pairs"))
    exclude_reasons = Counter(row["exclude_reason"] for row in excluded)
    largest_family = by_family.most_common(1)[0] if by_family else ("", 0)
    largest_province = by_province.most_common(1)[0] if by_province else ("", 0)
    return {
        "source_total_pairs": source_total_pairs,
        "family_plan_count": len(family_plan),
        "province_family_plan_count": len(province_family_plan),
        "planned_total_pairs": planned_total,
        "planned_param_pairs": planned_param,
        "planned_subtype_pairs": planned_subtype,
        "planned_param_rate": _rate(planned_param, planned_total),
        "planned_subtype_rate": _rate(planned_subtype, planned_total),
        "compression_ratio": round(source_total_pairs / max(1, planned_total), 3),
        "distinct_planned_families": len(by_family),
        "distinct_planned_provinces": len(by_province),
        "largest_family": largest_family[0],
        "largest_family_pairs": largest_family[1],
        "largest_family_rate": _rate(largest_family[1], planned_total),
        "largest_province": largest_province[0],
        "largest_province_pairs": largest_province[1],
        "largest_province_rate": _rate(largest_province[1], planned_total),
        "excluded_count": len(excluded),
        "by_family": _counter_items(by_family, planned_total, top_limit),
        "by_province": _counter_items(by_province, planned_total, top_limit),
        "by_exclude_reason": _counter_items(exclude_reasons, len(excluded), top_limit),
        "passes_balance_gate": (
            planned_total > 0
            and len(by_family) >= 15
            and len(by_province) >= 50
            and _rate(largest_family[1], planned_total) <= 0.18
            and _rate(largest_province[1], planned_total) <= 0.05
            and _rate(planned_param, planned_total) >= 0.2
        ),
    }


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]]) -> list[list[object]]:
    return [["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in items]]


def _row_table(rows: list[dict[str, Any]], fields: list[str], limit: int) -> list[list[object]]:
    return [fields, *[[row.get(field, "") for field in fields] for row in rows[:limit]]]


def _write_markdown(path: Path, report: dict[str, Any], family_plan: list[dict[str, Any]], province_plan: list[dict[str, Any]]) -> None:
    summary = report["summary"]
    family_fields = [
        "family",
        "available_total_pairs",
        "planned_param_pairs",
        "planned_subtype_pairs",
        "planned_total_pairs",
        "available_to_planned_ratio",
    ]
    province_fields = [
        "province",
        "family",
        "available_total_pairs",
        "planned_param_pairs",
        "planned_subtype_pairs",
        "planned_total_pairs",
    ]
    lines = [
        "# Goal Quota Self-Supervised Sampling Plan",
        "",
        "Stage 5.0 eval-only sampling plan. It narrows quota self-supervised capacity into a balanced candidate pool. It does not enumerate pair rows, train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["source_total_pairs", summary["source_total_pairs"]],
                ["planned_total_pairs", summary["planned_total_pairs"]],
                ["planned_param_pairs", summary["planned_param_pairs"]],
                ["planned_subtype_pairs", summary["planned_subtype_pairs"]],
                ["planned_param_rate", summary["planned_param_rate"]],
                ["compression_ratio", summary["compression_ratio"]],
                ["distinct_planned_families", summary["distinct_planned_families"]],
                ["distinct_planned_provinces", summary["distinct_planned_provinces"]],
                ["largest_family", summary["largest_family"]],
                ["largest_family_rate", summary["largest_family_rate"]],
                ["largest_province", summary["largest_province"]],
                ["largest_province_rate", summary["largest_province_rate"]],
                ["passes_balance_gate", summary["passes_balance_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Family Plan",
        "",
        _md_table(_row_table(sorted(family_plan, key=lambda row: _int(row.get("planned_total_pairs")), reverse=True), family_fields, 30)),
        "",
        "## Province-Family Plan",
        "",
        _md_table(_row_table(sorted(province_plan, key=lambda row: _int(row.get("planned_total_pairs")), reverse=True), province_fields, 30)),
        "",
        "## Planned Families",
        "",
        _md_table(_counter_table(summary["by_family"])),
        "",
        "## Planned Provinces",
        "",
        _md_table(_counter_table(summary["by_province"])),
        "",
        "## Excluded",
        "",
        _md_table(_counter_table(summary["by_exclude_reason"])),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 5.0 eval-only quota self-supervised sampling plan")
    parser.add_argument("--family-csv", default=str(DEFAULT_FAMILY_CSV))
    parser.add_argument("--province-family-csv", default=str(DEFAULT_PROVINCE_FAMILY_CSV))
    parser.add_argument("--target-pairs", type=int, default=100_000)
    parser.add_argument("--min-family-records", type=int, default=250)
    parser.add_argument("--min-family-provinces", type=int, default=20)
    parser.add_argument("--min-family-pairs", type=int, default=1_000)
    parser.add_argument("--min-family-target", type=int, default=1_000)
    parser.add_argument("--max-family-target", type=int, default=10_000)
    parser.add_argument("--min-province-family-records", type=int, default=20)
    parser.add_argument("--min-province-family-pairs", type=int, default=50)
    parser.add_argument("--max-province-family-target", type=int, default=500)
    parser.add_argument("--max-province-target", type=int, default=3_000)
    parser.add_argument("--target-param-share", type=float, default=0.35)
    parser.add_argument("--min-param-share", type=float, default=0.20)
    parser.add_argument("--max-param-share", type=float, default=0.60)
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--family-plan-csv", default=str(DEFAULT_FAMILY_PLAN_CSV))
    parser.add_argument("--province-family-plan-csv", default=str(DEFAULT_PROVINCE_FAMILY_PLAN_CSV))
    parser.add_argument("--excluded-csv", default=str(DEFAULT_EXCLUDED_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    family_rows = _read_csv(Path(args.family_csv))
    province_family_rows = _read_csv(Path(args.province_family_csv))
    source_total_pairs = sum(_family_available(row) for row in family_rows)

    family_plan, family_excluded = _make_family_plan(family_rows, args)
    province_plan, province_excluded = _make_province_family_plan(province_family_rows, family_plan, args)
    excluded = family_excluded + province_excluded
    summary = _summarize(family_plan, province_plan, excluded, source_total_pairs, args.top_limit)

    family_fields = [
        "family",
        "quota_records",
        "province_count",
        "available_param_pairs",
        "available_subtype_pairs",
        "available_total_pairs",
        "planned_param_pairs",
        "planned_subtype_pairs",
        "planned_total_pairs",
        "available_to_planned_ratio",
    ]
    province_fields = [
        "province",
        "family",
        "quota_records",
        "available_param_pairs",
        "available_subtype_pairs",
        "available_total_pairs",
        "planned_param_pairs",
        "planned_subtype_pairs",
        "planned_total_pairs",
    ]
    excluded_fields = ["level", "province", "family", "exclude_reason", "quota_records", "available_pairs"]
    _write_csv(Path(args.family_plan_csv), family_plan, family_fields)
    _write_csv(Path(args.province_family_plan_csv), province_plan, province_fields)
    _write_csv(Path(args.excluded_csv), excluded, excluded_fields)

    artifacts = {
        "family_plan_csv": args.family_plan_csv,
        "province_family_plan_csv": args.province_family_plan_csv,
        "excluded_csv": args.excluded_csv,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 5.0 quota self-supervised sampling plan",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "family_csv": args.family_csv,
        "province_family_csv": args.province_family_csv,
        "target_pairs": args.target_pairs,
        "summary": summary,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, family_plan, province_plan)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_training": True,
                    "elapsed_sec": report["elapsed_sec"],
                    **{key: summary[key] for key in (
                        "source_total_pairs",
                        "planned_total_pairs",
                        "planned_param_pairs",
                        "planned_subtype_pairs",
                        "compression_ratio",
                        "distinct_planned_families",
                        "distinct_planned_provinces",
                        "largest_family_rate",
                        "largest_province_rate",
                        "passes_balance_gate",
                    )},
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
