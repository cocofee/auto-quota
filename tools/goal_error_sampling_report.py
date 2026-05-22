from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_INPUTS = (
    ("heldout", PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_heldout_baseline_details.jsonl"),
    ("hard", PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_hard_baseline_details.jsonl"),
)
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_error_sampling_report.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_error_sampling_report.md"
TARGET_REASONS = ("wrong_other", "wrong_rank", "wrong_family")
HARD_SOURCE_BUCKETS = {
    "recall_miss",
    "rank_miss",
    "confidence_miss",
    "historical_slow_proxy",
    "high_frequency_error",
    "recall_rank_minus_1",
    "snapshot_window_too_short",
    "pre_ltr_or_final_overturned_correct",
    "correct_low_in_snapshot",
}
CATEGORY_LABELS = {
    "source_hard_bias": "数据源本身偏错误集",
    "family_unrecognized": "对象族未识别/错识别",
    "same_family_ranking": "同族排序",
    "book_misrank": "书册错排",
    "recall_missing": "召回缺失/Top5缺失",
    "other_unclassified": "其它未归类",
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def _load_inputs(values: list[str]) -> list[tuple[str, Path]]:
    if not values:
        return list(DEFAULT_INPUTS)
    result: list[tuple[str, Path]] = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--input must use split=path format, got: {value}")
        split, path = value.split("=", 1)
        result.append((_clean(split), Path(path)))
    return result


def _expected_families(row: dict[str, Any]) -> set[str]:
    return {
        _clean(signal.get("family"))
        for signal in row.get("expected_signals") or []
        if _clean(signal.get("family"))
    }


def _expected_books(row: dict[str, Any]) -> set[str]:
    return {
        _clean(signal.get("book")).upper()
        for signal in row.get("expected_signals") or []
        if _clean(signal.get("book"))
    }


def _top_book(row: dict[str, Any]) -> str:
    return _clean((row.get("top_signal") or {}).get("book")).upper()


def _query_family(row: dict[str, Any]) -> str:
    return _clean((row.get("query_signal") or {}).get("family"))


def _top_family(row: dict[str, Any]) -> str:
    return _clean((row.get("top_signal") or {}).get("family"))


def classify(row: dict[str, Any]) -> tuple[str, list[str], str]:
    miss_reason = _clean(row.get("miss_reason"))
    bucket = _clean(row.get("bucket"))
    query_family = _query_family(row)
    top_family = _top_family(row)
    expected_families = _expected_families(row)
    expected_books = _expected_books(row)
    top_book = _top_book(row)
    expected_rank = row.get("expected_rank")

    flags: list[str] = []
    if bucket in HARD_SOURCE_BUCKETS:
        flags.append("source_hard_bias")

    if miss_reason == "wrong_book" or (top_book and expected_books and top_book not in expected_books):
        return "book_misrank", flags, f"expected_book={','.join(sorted(expected_books)) or '<empty>'}, top_book={top_book or '<empty>'}"

    if not query_family and expected_families:
        return "family_unrecognized", flags, f"query_family=<empty>, expected_family={','.join(sorted(expected_families))}"

    if miss_reason == "wrong_family":
        expected_text = ",".join(sorted(expected_families)) or "<empty>"
        return "family_unrecognized", flags, f"query_family={query_family or '<empty>'}, top_family={top_family or '<empty>'}, expected_family={expected_text}"

    if expected_rank is not None:
        return "same_family_ranking", flags, f"expected_rank={expected_rank}"

    if not query_family and not expected_families:
        return "family_unrecognized", flags, "query_family=<empty>, expected_family=<empty>"

    if expected_rank is None:
        return "recall_missing", flags, "expected not in Top5"

    return "other_unclassified", flags, "no heuristic matched"


def _sample_row(row: dict[str, Any], *, primary_category: str, evidence: str) -> dict[str, Any]:
    expected_signals = row.get("expected_signals") or []
    top_signal = row.get("top_signal") or {}
    return {
        "split": row.get("split"),
        "province": row.get("province"),
        "sample_id": row.get("sample_id"),
        "source_file": row.get("source_file"),
        "bucket": row.get("bucket"),
        "miss_reason": row.get("miss_reason"),
        "primary_category": primary_category,
        "category_label": CATEGORY_LABELS.get(primary_category, primary_category),
        "evidence": evidence,
        "query": row.get("query"),
        "expected_ids": row.get("expected_ids"),
        "expected_names": [signal.get("name") for signal in expected_signals[:2]],
        "expected_books": sorted(_expected_books(row)),
        "expected_families": sorted(_expected_families(row)),
        "top_ids": row.get("top_ids"),
        "top_book": _clean(top_signal.get("book")),
        "top_family": _clean(top_signal.get("family")),
        "query_family": _query_family(row),
        "expected_rank": row.get("expected_rank"),
    }


def _counter_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _collect(inputs: list[tuple[str, Path]], *, sample_limit: int, bucket_limit: int) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    for split, path in inputs:
        for row in _read_jsonl(path):
            row = dict(row)
            row["split"] = split
            all_rows.append(row)
            if not row.get("hit1") and _clean(row.get("miss_reason")):
                primary, flags, evidence = classify(row)
                row["_primary_category"] = primary
                row["_flags"] = flags
                row["_evidence"] = evidence
                missing_rows.append(row)

    target_rows = [row for row in missing_rows if _clean(row.get("miss_reason")) in TARGET_REASONS]
    summary = {
        "inputs": [{"split": split, "path": str(path)} for split, path in inputs],
        "total_rows": len(all_rows),
        "miss_rows": len(missing_rows),
        "target_reasons": list(TARGET_REASONS),
        "target_rows": len(target_rows),
        "hit1_rows": sum(1 for row in all_rows if row.get("hit1")),
        "source_hard_bias_miss_rows": sum(1 for row in missing_rows if "source_hard_bias" in row.get("_flags", [])),
    }

    split_counts: Counter[str] = Counter(_clean(row.get("split")) for row in target_rows)
    reason_counts: Counter[str] = Counter(_clean(row.get("miss_reason")) for row in target_rows)
    category_counts: Counter[str] = Counter(_clean(row.get("_primary_category")) for row in target_rows)
    bucket_counts: Counter[str] = Counter(_clean(row.get("bucket")) for row in target_rows)
    source_counts: Counter[str] = Counter(_clean(row.get("source_file")) for row in target_rows)

    by_reason: dict[str, Any] = {}
    for reason in TARGET_REASONS:
        rows = [row for row in target_rows if _clean(row.get("miss_reason")) == reason]
        group_counter: Counter[str] = Counter()
        province_counter: Counter[str] = Counter()
        family_counter: Counter[str] = Counter()
        category_counter: Counter[str] = Counter()
        bucket_counter: Counter[str] = Counter()
        for row in rows:
            category = _clean(row.get("_primary_category"))
            family = _query_family(row) or "<empty>"
            province = _clean(row.get("province"))
            group_counter[f"{CATEGORY_LABELS.get(category, category)}|{family}|{province}"] += 1
            province_counter[province] += 1
            family_counter[family] += 1
            category_counter[category] += 1
            bucket_counter[_clean(row.get("bucket"))] += 1

        group_rank = {item["key"]: index for index, item in enumerate(_counter_items(group_counter, bucket_limit))}
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                group_rank.get(
                    f"{CATEGORY_LABELS.get(_clean(row.get('_primary_category')), _clean(row.get('_primary_category')))}|{_query_family(row) or '<empty>'}|{_clean(row.get('province'))}",
                    9999,
                ),
                _clean(row.get("split")),
                _clean(row.get("province")),
                _clean(row.get("sample_id")),
            ),
        )
        by_reason[reason] = {
            "rows": len(rows),
            "category_counts": _counter_items(category_counter, bucket_limit),
            "query_family_counts": _counter_items(family_counter, bucket_limit),
            "province_counts": _counter_items(province_counter, bucket_limit),
            "source_bucket_counts": _counter_items(bucket_counter, bucket_limit),
            "top_groups": _counter_items(group_counter, bucket_limit),
            "samples": [
                _sample_row(row, primary_category=_clean(row.get("_primary_category")), evidence=_clean(row.get("_evidence")))
                for row in sorted_rows[:sample_limit]
            ],
        }

    return {
        "summary": summary,
        "counts": {
            "split": _counter_items(split_counts, bucket_limit),
            "miss_reason": _counter_items(reason_counts, bucket_limit),
            "primary_category": _counter_items(category_counts, bucket_limit),
            "source_bucket": _counter_items(bucket_counts, bucket_limit),
            "source_file": _counter_items(source_counts, bucket_limit),
        },
        "by_reason": by_reason,
    }


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    sep = ["---"] * len(header)
    lines = ["| " + " | ".join(str(item) for item in header) + " |", "| " + " | ".join(sep) + " |"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any], *, sample_limit: int) -> None:
    lines: list[str] = []
    summary = report["summary"]
    counts = report["counts"]
    lines.append("# Goal Expanded Error Sampling Report")
    lines.append("")
    lines.append("只读抽样报告：不调参、不改搜索逻辑。")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        _md_table(
            [
                ["metric", "value"],
                ["total_rows", summary["total_rows"]],
                ["miss_rows", summary["miss_rows"]],
                ["target_rows(wrong_other/rank/family)", summary["target_rows"]],
                ["source_hard_bias_miss_rows", summary["source_hard_bias_miss_rows"]],
            ]
        )
    )
    lines.append("")
    lines.append("## Primary Categories")
    lines.append("")
    lines.append(
        _md_table(
            [["category", "label", "count"]]
            + [[item["key"], CATEGORY_LABELS.get(item["key"], item["key"]), item["count"]] for item in counts["primary_category"]]
        )
    )
    lines.append("")
    lines.append("## Source Buckets")
    lines.append("")
    lines.append(_md_table([["bucket", "count"]] + [[item["key"], item["count"]] for item in counts["source_bucket"]]))
    lines.append("")

    for reason, data in report["by_reason"].items():
        lines.append(f"## {reason}")
        lines.append("")
        lines.append(f"Rows: {data['rows']}")
        lines.append("")
        lines.append("Top groups:")
        lines.append("")
        lines.append(_md_table([["group", "count"]] + [[item["key"], item["count"]] for item in data["top_groups"][:10]]))
        lines.append("")
        lines.append(f"Samples (top {sample_limit}):")
        lines.append("")
        sample_rows = [["split", "province", "bucket", "category", "query", "expected", "top_ids", "evidence"]]
        for sample in data["samples"][:sample_limit]:
            expected = ",".join(sample.get("expected_ids") or [])
            top_ids = ",".join((sample.get("top_ids") or [])[:3])
            sample_rows.append(
                [
                    sample.get("split", ""),
                    sample.get("province", ""),
                    sample.get("bucket", ""),
                    sample.get("category_label", ""),
                    sample.get("query", ""),
                    expected,
                    top_ids,
                    sample.get("evidence", ""),
                ]
            )
        lines.append(_md_table(sample_rows))
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sampling report for expanded goal-search errors")
    parser.add_argument("--input", action="append", default=[], help="split=details.jsonl. Defaults to expanded heldout/hard details")
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--sample-limit", type=int, default=12)
    parser.add_argument("--bucket-limit", type=int, default=20)
    args = parser.parse_args()

    inputs = _load_inputs(args.input)
    report = _collect(inputs, sample_limit=args.sample_limit, bucket_limit=args.bucket_limit)
    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.md_output), report, sample_limit=args.sample_limit)

    print(
        json.dumps(
            {
                "json_output": str(json_output),
                "md_output": args.md_output,
                "summary": report["summary"],
                "primary_category_counts": report["counts"]["primary_category"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
