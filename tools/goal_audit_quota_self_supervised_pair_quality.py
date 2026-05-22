from __future__ import annotations

import argparse
import csv
import json
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

from src.goal_search.national_index import extract_signal  # noqa: E402

DEFAULT_PAIRS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates.csv"
DEFAULT_REJECTED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_rejected.csv"
DEFAULT_NARROW_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_narrow.csv"
DEFAULT_NARROW_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_narrow.jsonl"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_quality_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_quality_summary.md"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_quality_buckets.csv"
DEFAULT_SAMPLES_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_quality_issue_samples.csv"
DEFAULT_SHORTFALL_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_quality_shortfalls.csv"


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _signal_parts(name: str, chapter: str) -> dict[str, str]:
    signal = extract_signal(" ".join(part for part in (_clean(name), _clean(chapter)) if part))
    return {
        "family": _clean(signal.family),
        "action": _clean(signal.action),
        "material": _clean(signal.material),
        "connection": _clean(signal.connection),
        "install_method": _clean(signal.install_method),
        "param_type": _clean(signal.param_type),
    }


def _part_mismatches(positive: dict[str, str], negative: dict[str, str]) -> list[str]:
    fields = ["action", "material", "connection", "install_method"]
    return [field for field in fields if positive.get(field, "") != negative.get(field, "")]


def _base_issues(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not _clean(row.get("province")):
        issues.append("missing_province")
    if not _clean(row.get("family")):
        issues.append("missing_family")
    if not _clean(row.get("positive_id")) or not _clean(row.get("negative_id")):
        issues.append("missing_quota_id")
    if _clean(row.get("positive_id")) == _clean(row.get("negative_id")):
        issues.append("same_quota_id")
    if not _clean(row.get("positive_name")) or not _clean(row.get("negative_name")):
        issues.append("missing_name")
    if _clean(row.get("positive_book")) != _clean(row.get("negative_book")):
        issues.append("book_mismatch")
    if _clean(row.get("positive_unit")) != _clean(row.get("negative_unit")):
        issues.append("unit_mismatch")
    return issues


def _audit_row(row: dict[str, Any], derive_parts: bool) -> dict[str, Any]:
    pair_type = _clean(row.get("pair_type"))
    positive_subtype = _clean(row.get("positive_subtype_key"))
    negative_subtype = _clean(row.get("negative_subtype_key"))
    positive_value = _clean(row.get("positive_contrast_value"))
    negative_value = _clean(row.get("negative_contrast_value"))
    base_issues = _base_issues(row)

    positive_parts = _signal_parts(row.get("positive_name", ""), row.get("positive_chapter", "")) if derive_parts else {}
    negative_parts = _signal_parts(row.get("negative_name", ""), row.get("negative_chapter", "")) if derive_parts else {}
    part_mismatches = _part_mismatches(positive_parts, negative_parts) if derive_parts else []

    if base_issues:
        decision = "exclude"
        bucket = "invalid_base"
        issue = "|".join(base_issues)
    elif pair_type == "param_contrast":
        if not positive_value or not negative_value or positive_value == negative_value:
            decision = "exclude"
            bucket = "invalid_param_value"
            issue = "missing_or_same_param_value"
        elif not positive_subtype or not negative_subtype:
            decision = "exclude"
            bucket = "gray_param_missing_subtype"
            issue = "param_contrast_missing_subtype"
        elif positive_subtype != negative_subtype:
            decision = "exclude"
            bucket = "gray_param_subtype_mixed"
            issue = "param_contrast_subtype_mixed"
        else:
            decision = "keep"
            bucket = "clean_param_same_subtype"
            issue = ""
    elif pair_type == "subtype_contrast":
        if not positive_subtype or not negative_subtype:
            decision = "exclude"
            bucket = "invalid_subtype_missing"
            issue = "subtype_contrast_missing_subtype"
        elif positive_subtype == negative_subtype:
            decision = "exclude"
            bucket = "invalid_subtype_same"
            issue = "subtype_contrast_same_subtype"
        else:
            decision = "keep"
            bucket = "clean_subtype_contrast"
            issue = ""
    else:
        decision = "exclude"
        bucket = "invalid_pair_type"
        issue = "unknown_pair_type"

    return {
        "quality_decision": decision,
        "quality_bucket": bucket,
        "quality_issue": issue,
        "param_subtype_same": str(positive_subtype == negative_subtype).lower(),
        "same_chapter": str(_clean(row.get("positive_chapter")) == _clean(row.get("negative_chapter"))).lower(),
        "positive_signal_family": positive_parts.get("family", ""),
        "negative_signal_family": negative_parts.get("family", ""),
        "positive_action": positive_parts.get("action", ""),
        "negative_action": negative_parts.get("action", ""),
        "positive_material": positive_parts.get("material", ""),
        "negative_material": negative_parts.get("material", ""),
        "positive_connection": positive_parts.get("connection", ""),
        "negative_connection": negative_parts.get("connection", ""),
        "positive_install_method": positive_parts.get("install_method", ""),
        "negative_install_method": negative_parts.get("install_method", ""),
        "positive_param_type": positive_parts.get("param_type", ""),
        "negative_param_type": negative_parts.get("param_type", ""),
        "signal_part_mismatch": "|".join(part_mismatches),
    }


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _append_bucket_row(
    rows: list[dict[str, Any]],
    dimension: str,
    key: str,
    count: int,
    total: int,
    kept: int | None = None,
    extra: str = "",
) -> None:
    rows.append(
        {
            "dimension": dimension,
            "key": key,
            "count": count,
            "rate": _rate(count, total),
            "kept": "" if kept is None else kept,
            "excluded": "" if kept is None else max(0, count - kept),
            "keep_rate": "" if kept is None else _rate(kept, count),
            "extra": extra,
        }
    )


def _audit_pairs(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    pairs_csv = Path(args.pairs_csv)
    narrow_csv = Path(args.narrow_csv)
    narrow_jsonl = Path(args.narrow_jsonl)
    narrow_csv.parent.mkdir(parents=True, exist_ok=True)
    narrow_jsonl.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    pair_type = Counter()
    family = Counter()
    province = Counter()
    bucket = Counter()
    decision = Counter()
    issue = Counter()
    kept_by_family = Counter()
    kept_by_province = Counter()
    kept_by_pair_type = Counter()
    param_subtype_mixed_by_family = Counter()
    param_part_mismatch = Counter()
    param_part_mismatch_by_family = Counter()
    family_pair_type_bucket = Counter()
    issue_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fieldnames: list[str] = []
    audit_fields = [
        "quality_decision",
        "quality_bucket",
        "quality_issue",
        "param_subtype_same",
        "same_chapter",
        "positive_signal_family",
        "negative_signal_family",
        "positive_action",
        "negative_action",
        "positive_material",
        "negative_material",
        "positive_connection",
        "negative_connection",
        "positive_install_method",
        "negative_install_method",
        "positive_param_type",
        "negative_param_type",
        "signal_part_mismatch",
    ]

    with pairs_csv.open("r", encoding="utf-8-sig", newline="") as input_handle:
        reader = csv.DictReader(input_handle)
        fieldnames = list(reader.fieldnames or [])
        output_fields = [*fieldnames, *audit_fields]
        with narrow_csv.open("w", encoding="utf-8-sig", newline="") as csv_handle, narrow_jsonl.open("w", encoding="utf-8") as jsonl_handle:
            writer = csv.DictWriter(csv_handle, fieldnames=output_fields, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                total += 1
                audit = _audit_row(row, derive_parts=not args.no_derive_signal_parts)
                audited = {**row, **audit}
                row_pair_type = _clean(row.get("pair_type"))
                row_family = _clean(row.get("family"))
                row_province = _clean(row.get("province"))
                pair_type[row_pair_type] += 1
                family[row_family] += 1
                province[row_province] += 1
                bucket[audit["quality_bucket"]] += 1
                decision[audit["quality_decision"]] += 1
                if audit["quality_issue"]:
                    issue[audit["quality_issue"]] += 1
                family_pair_type_bucket[(row_family, row_pair_type, audit["quality_bucket"])] += 1

                if row_pair_type == "param_contrast" and audit["quality_bucket"] == "gray_param_subtype_mixed":
                    param_subtype_mixed_by_family[row_family] += 1
                    for part in audit["signal_part_mismatch"].split("|"):
                        if part:
                            param_part_mismatch[part] += 1
                            param_part_mismatch_by_family[f"{row_family}:{part}"] += 1

                if audit["quality_decision"] == "keep":
                    kept += 1
                    kept_by_family[row_family] += 1
                    kept_by_province[row_province] += 1
                    kept_by_pair_type[row_pair_type] += 1
                    writer.writerow(audited)
                    jsonl_handle.write(json.dumps(audited, ensure_ascii=False, sort_keys=True) + "\n")
                elif len(issue_samples[audit["quality_bucket"]]) < args.max_samples_per_bucket:
                    issue_samples[audit["quality_bucket"]].append(audited)

    bucket_rows: list[dict[str, Any]] = []
    for key, count in decision.most_common():
        _append_bucket_row(bucket_rows, "decision", key, count, total, kept if key == "keep" else 0)
    for key, count in bucket.most_common():
        bucket_kept = count if key.startswith("clean_") else 0
        _append_bucket_row(bucket_rows, "quality_bucket", key, count, total, bucket_kept)
    for key, count in pair_type.most_common():
        _append_bucket_row(bucket_rows, "pair_type", key, count, total, kept_by_pair_type[key])
    for key, count in family.most_common():
        extra = f"param_subtype_mixed={param_subtype_mixed_by_family.get(key, 0)}"
        _append_bucket_row(bucket_rows, "family", key, count, total, kept_by_family[key], extra)
    for key, count in province.most_common(args.top_provinces):
        _append_bucket_row(bucket_rows, "province", key, count, total, kept_by_province[key])
    for key, count in issue.most_common():
        _append_bucket_row(bucket_rows, "issue", key, count, total)
    for key, count in param_part_mismatch.most_common():
        _append_bucket_row(bucket_rows, "param_part_mismatch", key, count, sum(param_part_mismatch.values()))
    for key, count in param_part_mismatch_by_family.most_common(args.top_limit):
        _append_bucket_row(bucket_rows, "param_part_mismatch_by_family", key, count, sum(param_part_mismatch_by_family.values()))
    for (row_family, row_pair_type, row_bucket), count in family_pair_type_bucket.most_common(args.top_limit):
        _append_bucket_row(bucket_rows, "family_pair_type_bucket", f"{row_family}:{row_pair_type}:{row_bucket}", count, total)

    samples = [sample for samples_for_bucket in issue_samples.values() for sample in samples_for_bucket]
    summary = {
        "stage": "Goal LTR v1 / stage 5.2 quota self-supervised pair quality audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "pairs_csv": str(pairs_csv),
        "narrow_csv": str(narrow_csv),
        "narrow_jsonl": str(narrow_jsonl),
        "summary": {
            "total_pairs": total,
            "narrow_pairs": kept,
            "excluded_pairs": total - kept,
            "narrow_rate": _rate(kept, total),
            "excluded_rate": _rate(total - kept, total),
            "pair_type": _counter_items(pair_type, total, args.top_limit),
            "quality_bucket": _counter_items(bucket, total, args.top_limit),
            "decision": _counter_items(decision, total, args.top_limit),
            "issue": _counter_items(issue, total, args.top_limit),
            "family": [
                {
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                    "kept": kept_by_family[key],
                    "keep_rate": _rate(kept_by_family[key], count),
                    "param_subtype_mixed": param_subtype_mixed_by_family.get(key, 0),
                }
                for key, count in family.most_common(args.top_limit)
            ],
            "param_part_mismatch": _counter_items(param_part_mismatch, sum(param_part_mismatch.values()), args.top_limit),
            "param_part_mismatch_by_family": _counter_items(param_part_mismatch_by_family, sum(param_part_mismatch_by_family.values()), args.top_limit),
        },
    }
    return summary, bucket_rows, samples


def _audit_shortfalls(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = Path(args.rejected_csv)
    if not path.exists():
        return [], {"rejected_csv_exists": False}

    rows = _read_csv(path)
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    provinces_by_key: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    total_shortfall = 0
    total_buckets = 0
    for row in rows:
        reason = _clean(row.get("reject_reason"))
        family = _clean(row.get("family"))
        province = _clean(row.get("province"))
        planned = _int(row.get("planned_pairs"))
        generated = _int(row.get("generated_pairs"))
        shortfall = _int(row.get("shortfall"))
        key = (reason, family)
        if key not in aggregate:
            aggregate[key] = {
                "reject_reason": reason,
                "family": family,
                "bucket_count": 0,
                "planned_pairs": 0,
                "generated_pairs": 0,
                "shortfall_pairs": 0,
                "shortfall_rate": 0.0,
                "top_provinces": "",
            }
        aggregate[key]["bucket_count"] += 1
        aggregate[key]["planned_pairs"] += planned
        aggregate[key]["generated_pairs"] += generated
        aggregate[key]["shortfall_pairs"] += shortfall
        provinces_by_key[key][province] += shortfall
        total_shortfall += shortfall
        total_buckets += 1

    output_rows: list[dict[str, Any]] = []
    for key, output in aggregate.items():
        planned = _int(output.get("planned_pairs"))
        output["shortfall_rate"] = _rate(_int(output.get("shortfall_pairs")), planned)
        output["top_provinces"] = "; ".join(f"{province}:{count}" for province, count in provinces_by_key[key].most_common(5))
        output_rows.append(output)
    output_rows.sort(key=lambda row: (_int(row.get("shortfall_pairs")), _int(row.get("bucket_count"))), reverse=True)
    summary = {
        "rejected_csv_exists": True,
        "rejected_buckets": len(rows),
        "aggregated_shortfall_rows": len(output_rows),
        "shortfall_pairs": total_shortfall,
        "shortfall_bucket_count": total_buckets,
        "top_shortfalls": output_rows[: min(args.top_limit, len(output_rows))],
    }
    return output_rows, summary


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _counter_table(items: list[dict[str, Any]], extra_fields: list[str] | None = None) -> list[list[Any]]:
    extra_fields = extra_fields or []
    header = ["key", "count", "rate", *extra_fields]
    rows = [header]
    for item in items:
        rows.append([item.get("key", ""), item.get("count", ""), item.get("rate", ""), *[item.get(field, "") for field in extra_fields]])
    return rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    shortfalls = report["shortfalls"]
    lines = [
        "# Goal Quota Self-Supervised Pair Quality Audit",
        "",
        "Stage 5.2 eval-only audit and narrowing. It labels Stage 5.1 quota pairs, writes a narrowed candidate pool, and summarizes shortfall buckets. It does not train, tune, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["total_pairs", summary["total_pairs"]],
                ["narrow_pairs", summary["narrow_pairs"]],
                ["excluded_pairs", summary["excluded_pairs"]],
                ["narrow_rate", summary["narrow_rate"]],
                ["excluded_rate", summary["excluded_rate"]],
                ["shortfall_pairs", shortfalls.get("shortfall_pairs", "")],
                ["shortfall_bucket_count", shortfalls.get("shortfall_bucket_count", "")],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Quality Buckets",
        "",
        _md_table(_counter_table(summary["quality_bucket"])),
        "",
        "## Pair Type",
        "",
        _md_table(_counter_table(summary["pair_type"])),
        "",
        "## Family Cleanliness",
        "",
        _md_table(_counter_table(summary["family"], ["kept", "keep_rate", "param_subtype_mixed"])),
        "",
        "## Param Part Mismatch",
        "",
        _md_table(_counter_table(summary["param_part_mismatch"])),
        "",
        "## Top Shortfalls",
        "",
        _md_table(
            [
                ["reject_reason", "family", "bucket_count", "planned", "generated", "shortfall", "rate"],
                *[
                    [
                        row.get("reject_reason", ""),
                        row.get("family", ""),
                        row.get("bucket_count", ""),
                        row.get("planned_pairs", ""),
                        row.get("generated_pairs", ""),
                        row.get("shortfall_pairs", ""),
                        row.get("shortfall_rate", ""),
                    ]
                    for row in shortfalls.get("top_shortfalls", [])
                ],
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["narrow_csv", report["artifacts"]["narrow_csv"]],
                ["narrow_jsonl", report["artifacts"]["narrow_jsonl"]],
                ["buckets_csv", report["artifacts"]["buckets_csv"]],
                ["samples_csv", report["artifacts"]["samples_csv"]],
                ["shortfall_csv", report["artifacts"]["shortfall_csv"]],
                ["summary_json", report["artifacts"]["summary_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.2 eval-only quota pair quality audit and narrowing")
    parser.add_argument("--pairs-csv", default=str(DEFAULT_PAIRS_CSV))
    parser.add_argument("--rejected-csv", default=str(DEFAULT_REJECTED_CSV))
    parser.add_argument("--narrow-csv", default=str(DEFAULT_NARROW_CSV))
    parser.add_argument("--narrow-jsonl", default=str(DEFAULT_NARROW_JSONL))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    parser.add_argument("--samples-csv", default=str(DEFAULT_SAMPLES_CSV))
    parser.add_argument("--shortfall-csv", default=str(DEFAULT_SHORTFALL_CSV))
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--top-provinces", type=int, default=80)
    parser.add_argument("--max-samples-per-bucket", type=int, default=20)
    parser.add_argument("--no-derive-signal-parts", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    summary_report, bucket_rows, samples = _audit_pairs(args)
    shortfall_rows, shortfall_summary = _audit_shortfalls(args)
    elapsed = round(time.perf_counter() - started, 3)

    _write_csv(
        Path(args.buckets_csv),
        bucket_rows,
        ["dimension", "key", "count", "rate", "kept", "excluded", "keep_rate", "extra"],
    )
    if samples:
        sample_fields = list(samples[0].keys())
    else:
        sample_fields = ["pair_id", "quality_bucket", "quality_issue"]
    _write_csv(Path(args.samples_csv), samples, sample_fields)
    _write_csv(
        Path(args.shortfall_csv),
        shortfall_rows,
        ["reject_reason", "family", "bucket_count", "planned_pairs", "generated_pairs", "shortfall_pairs", "shortfall_rate", "top_provinces"],
    )

    report = {
        **summary_report,
        "shortfalls": shortfall_summary,
        "elapsed_sec": elapsed,
        "artifacts": {
            "narrow_csv": str(Path(args.narrow_csv)),
            "narrow_jsonl": str(Path(args.narrow_jsonl)),
            "buckets_csv": str(Path(args.buckets_csv)),
            "samples_csv": str(Path(args.samples_csv)),
            "shortfall_csv": str(Path(args.shortfall_csv)),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "total_pairs": report["summary"]["total_pairs"],
                    "narrow_pairs": report["summary"]["narrow_pairs"],
                    "excluded_pairs": report["summary"]["excluded_pairs"],
                    "narrow_rate": report["summary"]["narrow_rate"],
                    "shortfall_pairs": report["shortfalls"].get("shortfall_pairs"),
                    "elapsed_sec": elapsed,
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
