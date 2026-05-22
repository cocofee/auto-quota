from __future__ import annotations

import argparse
import csv
import json
import random
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
    DEFAULT_PAIRS_CSV,
    DEFAULT_PLAN_CSV,
    DEFAULT_PROVINCE_ROOT,
    _allocate_targets,
    _int,
    _load_plan,
    _load_province_records,
    _possible_contrast_pairs,
    _rate,
    _reject_row,
    _sample_from_bucket,
    _summarize,
    _write_csv,
    _write_jsonl,
)

DEFAULT_TIGHT_PARAM_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates.csv"
DEFAULT_TIGHT_PARAM_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates.jsonl"
DEFAULT_COMBINED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_combined_candidates.csv"
DEFAULT_COMBINED_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_combined_candidates.jsonl"
DEFAULT_REJECTED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates_rejected.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates_buckets.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_tight_param_candidates_summary.md"

PAIR_FIELDS = [
    "pair_id",
    "province",
    "family",
    "pair_type",
    "contrast_field",
    "positive_contrast_value",
    "negative_contrast_value",
    "positive_id",
    "positive_name",
    "positive_unit",
    "positive_book",
    "positive_chapter",
    "negative_id",
    "negative_name",
    "negative_unit",
    "negative_book",
    "negative_chapter",
    "positive_subtype_key",
    "negative_subtype_key",
    "quality_flags",
    "source_db_path",
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_subtype_rows(path: Path, allowed: set[tuple[str, str]] | None) -> tuple[list[dict[str, Any]], Counter[str]]:
    rows: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pair_type = _clean(row.get("pair_type"))
            province = _clean(row.get("province"))
            family = _clean(row.get("family"))
            if pair_type != "subtype_contrast":
                skipped[f"base_{pair_type or 'unknown'}"] += 1
                continue
            if allowed is not None and (province, family) not in allowed:
                skipped["base_subtype_outside_limited_plan"] += 1
                continue
            rows.append({field: row.get(field, "") for field in PAIR_FIELDS})
    return rows, skipped


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
                "value_to_records": value_to_records,
                "available_pairs": available,
            }
        )
    return buckets


def _generate_tight_param(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    rng = random.Random(args.seed)
    plan_by_province = _load_plan(Path(args.plan_csv), args.limit_province_families)
    pairs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    bucket_diagnostics: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    seen_pairs: set[tuple[str, str, str, str]] = set()
    province_root = Path(args.province_root)

    for province_index, (province, plan_rows) in enumerate(plan_by_province.items(), 1):
        db_path = province_root / province / "quota.db"
        if not db_path.exists():
            planned = sum(_int(row.get("planned_param_pairs")) for row in plan_rows)
            rejected.append(_reject_row(province, "", "quota_db_missing", planned, detail=str(db_path)))
            continue

        needed_families = {_clean(row.get("family")) for row in plan_rows}
        records, province_skipped = _load_province_records(db_path, province, needed_families)
        skipped.update(province_skipped)
        records_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            records_by_family[record["family"]].append(record)

        for plan_row in plan_rows:
            family = _clean(plan_row.get("family"))
            target = _int(plan_row.get("planned_param_pairs"))
            if target <= 0:
                continue
            family_records = records_by_family.get(family, [])
            if not family_records:
                rejected.append(_reject_row(province, family, "no_family_records_after_rescan", target))
                continue

            buckets = _build_tight_param_buckets(family_records, family)
            if not buckets:
                rejected.append(_reject_row(province, family, "no_tight_param_contrast_buckets", target))
                continue

            allocations = _allocate_targets(target, buckets, args.max_bucket_pairs)
            generated = 0
            shortfall = 0
            for bucket_index, bucket_target in allocations.items():
                bucket = buckets[bucket_index]
                bucket_rows, bucket_shortfall = _sample_from_bucket(
                    rng=rng,
                    pair_type="param_contrast",
                    province=province,
                    family=family,
                    bucket=bucket,
                    target=bucket_target,
                    seen_pairs=seen_pairs,
                    max_attempt_factor=args.max_attempt_factor,
                )
                generated += len(bucket_rows)
                shortfall += bucket_shortfall
                pairs.extend(bucket_rows)
                bucket_diagnostics.append(
                    {
                        "province": province,
                        "family": family,
                        "book": bucket["book"],
                        "unit": bucket["unit"],
                        "contrast_field": bucket["contrast_field"],
                        "subtype_key": bucket["subtype_key"],
                        "available_pairs": bucket["available_pairs"],
                        "allocated_pairs": len(bucket_rows),
                        "shortfall": bucket_shortfall,
                    }
                )
            if generated < target:
                rejected.append(
                    _reject_row(
                        province,
                        family,
                        "tight_param_contrast_sampling_shortfall",
                        target,
                        generated,
                        detail=f"buckets={len(buckets)} allocation_shortfall={shortfall}",
                    )
                )

        if args.progress_every > 0 and province_index % args.progress_every == 0:
            print(f"processed {province_index}/{len(plan_by_province)} provinces; tight_param_pairs={len(pairs)}", file=sys.stderr)

    return pairs, rejected, bucket_diagnostics, skipped


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _combined_summary(combined: list[dict[str, Any]], tight_param: list[dict[str, Any]], subtype_rows: list[dict[str, Any]], rejected: list[dict[str, Any]], skipped: Counter[str], top_limit: int) -> dict[str, Any]:
    summary, _buckets = _summarize(combined, rejected, skipped, top_limit)
    by_tight_family = Counter(row["family"] for row in tight_param)
    by_subtype_family = Counter(row["family"] for row in subtype_rows)
    total = len(combined)
    return {
        **summary,
        "tight_param_pairs": len(tight_param),
        "base_subtype_pairs_reused": len(subtype_rows),
        "projected_clean_param_rate": _rate(len(tight_param), total),
        "passes_param_20_gate": _rate(len(tight_param), total) >= 0.2,
        "tight_param_by_family": _counter_items(by_tight_family, len(tight_param), top_limit),
        "subtype_by_family": _counter_items(by_subtype_family, len(subtype_rows), top_limit),
    }


def _bucket_rows(combined: list[dict[str, Any]], tight_param: list[dict[str, Any]], rejected: list[dict[str, Any]], skipped: Counter[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(combined)
    dimensions = [
        ("combined_pair_type", Counter(row["pair_type"] for row in combined), total),
        ("combined_family", Counter(row["family"] for row in combined), total),
        ("tight_param_family", Counter(row["family"] for row in tight_param), len(tight_param)),
        ("reject_reason", Counter(row["reject_reason"] for row in rejected), len(rejected)),
        ("skipped", skipped, sum(skipped.values())),
    ]
    for dimension, counter, denominator in dimensions:
        for key, count in counter.most_common():
            rows.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, denominator)})
    return rows


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Tight Param Candidate Generation",
        "",
        "Stage 5.5 eval-only generation. It replaces param_contrast candidates with tight buckets keyed by book + unit + param_type + subtype_key, while reusing existing subtype_contrast rows unchanged. It does not train, tune, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["combined_pairs", summary["generated_pairs"]],
                ["tight_param_pairs", summary["tight_param_pairs"]],
                ["base_subtype_pairs_reused", summary["base_subtype_pairs_reused"]],
                ["projected_clean_param_rate", summary["projected_clean_param_rate"]],
                ["passes_param_20_gate", summary["passes_param_20_gate"]],
                ["rejected_buckets", summary["rejected_buckets"]],
                ["rejected_shortfall_pairs", summary["rejected_shortfall_pairs"]],
                ["largest_family", summary["largest_family"]],
                ["largest_family_rate", summary["largest_family_rate"]],
                ["largest_province", summary["largest_province"]],
                ["largest_province_rate", summary["largest_province_rate"]],
                ["passes_generation_balance_gate", summary["passes_generation_balance_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Combined Pair Type",
        "",
        _md_table(_counter_table(summary["by_pair_type"])),
        "",
        "## Tight Param Family",
        "",
        _md_table(_counter_table(summary["tight_param_by_family"])),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["tight_param_csv", report["artifacts"]["tight_param_csv"]],
                ["tight_param_jsonl", report["artifacts"]["tight_param_jsonl"]],
                ["combined_csv", report["artifacts"]["combined_csv"]],
                ["combined_jsonl", report["artifacts"]["combined_jsonl"]],
                ["rejected_csv", report["artifacts"]["rejected_csv"]],
                ["buckets_csv", report["artifacts"]["buckets_csv"]],
                ["report_json", report["artifacts"]["report_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 5.5 eval-only tight param candidate generator")
    parser.add_argument("--province-root", default=str(DEFAULT_PROVINCE_ROOT))
    parser.add_argument("--plan-csv", default=str(DEFAULT_PLAN_CSV))
    parser.add_argument("--base-pairs-csv", default=str(DEFAULT_PAIRS_CSV))
    parser.add_argument("--seed", type=int, default=20260522)
    parser.add_argument("--limit-province-families", type=int, default=0)
    parser.add_argument("--max-bucket-pairs", type=int, default=80)
    parser.add_argument("--max-attempt-factor", type=int, default=80)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--tight-param-csv", default=str(DEFAULT_TIGHT_PARAM_CSV))
    parser.add_argument("--tight-param-jsonl", default=str(DEFAULT_TIGHT_PARAM_JSONL))
    parser.add_argument("--combined-csv", default=str(DEFAULT_COMBINED_CSV))
    parser.add_argument("--combined-jsonl", default=str(DEFAULT_COMBINED_JSONL))
    parser.add_argument("--rejected-csv", default=str(DEFAULT_REJECTED_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    plan_by_province = _load_plan(Path(args.plan_csv), args.limit_province_families)
    allowed = {(province, _clean(row.get("family"))) for province, rows in plan_by_province.items() for row in rows}
    allowed_filter = allowed if args.limit_province_families > 0 else None
    subtype_rows, subtype_skipped = _read_subtype_rows(Path(args.base_pairs_csv), allowed_filter)
    tight_param_rows, rejected, bucket_diagnostics, skipped = _generate_tight_param(args)
    skipped.update(subtype_skipped)
    combined_rows = [*subtype_rows, *tight_param_rows]

    _write_csv(Path(args.tight_param_csv), tight_param_rows, PAIR_FIELDS)
    _write_jsonl(Path(args.tight_param_jsonl), tight_param_rows)
    _write_csv(Path(args.combined_csv), combined_rows, PAIR_FIELDS)
    _write_jsonl(Path(args.combined_jsonl), combined_rows)
    _write_csv(Path(args.rejected_csv), rejected, ["province", "family", "reject_reason", "planned_pairs", "generated_pairs", "shortfall", "detail"])
    combined_buckets = _bucket_rows(combined_rows, tight_param_rows, rejected, skipped)
    detail_bucket_rows = [
        {
            "dimension": "tight_param_bucket",
            "key": f"{row['province']}:{row['family']}:{row['book']}:{row['unit']}:{row['contrast_field']}:{row['subtype_key']}",
            "count": row["allocated_pairs"],
            "rate": _rate(row["allocated_pairs"], len(tight_param_rows)),
        }
        for row in bucket_diagnostics
    ]
    summary = _combined_summary(combined_rows, tight_param_rows, subtype_rows, rejected, skipped, args.top_limit)
    artifacts = {
        "tight_param_csv": str(Path(args.tight_param_csv)),
        "tight_param_jsonl": str(Path(args.tight_param_jsonl)),
        "combined_csv": str(Path(args.combined_csv)),
        "combined_jsonl": str(Path(args.combined_jsonl)),
        "rejected_csv": str(Path(args.rejected_csv)),
        "buckets_csv": str(Path(args.buckets_csv)),
        "report_json": str(Path(args.report_json)),
        "report_md": str(Path(args.report_md)),
    }
    report = {
        "stage": "Goal LTR v1 / stage 5.5 tight param candidate generation",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "subtype_reused_unchanged": True,
        "province_root": str(Path(args.province_root)),
        "plan_csv": str(Path(args.plan_csv)),
        "base_pairs_csv": str(Path(args.base_pairs_csv)),
        "seed": args.seed,
        "summary": summary,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    _write_csv(Path(args.buckets_csv), [*combined_buckets, *detail_bucket_rows], ["dimension", "key", "count", "rate"])
    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "combined_pairs": summary["generated_pairs"],
                    "tight_param_pairs": summary["tight_param_pairs"],
                    "base_subtype_pairs_reused": summary["base_subtype_pairs_reused"],
                    "projected_clean_param_rate": summary["projected_clean_param_rate"],
                    "passes_param_20_gate": summary["passes_param_20_gate"],
                    "passes_generation_balance_gate": summary["passes_generation_balance_gate"],
                    "elapsed_sec": report["elapsed_sec"],
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
