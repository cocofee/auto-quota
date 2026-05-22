from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_REVIEW_INPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_bucket_review.csv"
DEFAULT_EVAL_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_true_eval_details.jsonl"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_remaining_review.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_remaining_review.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_remaining_review.csv"
TARGET_SUBTYPES = ("branch_pipe", "water_pipe")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _clean(row.get("split")),
        _clean(row.get("province")),
        _clean(row.get("sample_id")),
        _clean(row.get("query") or row.get("bill_name")),
    )


def _loose_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _clean(row.get("province")),
        _clean(row.get("sample_id")),
        _clean(row.get("query") or row.get("bill_name")),
    )


def _eval_by_key(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        result[_key(row)] = row
        result[_loose_key(row)] = row
    return result


def _diagnose(row: dict[str, Any], eval_row: dict[str, Any]) -> tuple[str, str]:
    subtype = _clean(row.get("review_subtype"))
    query_signal = eval_row.get("query_signal") if isinstance(eval_row.get("query_signal"), dict) else {}
    expected_names = _clean(row.get("expected_names"))
    top_ids = eval_row.get("top_ids") or []

    if subtype == "branch_pipe":
        return (
            "branch_pipe_family_missing",
            "query has explicit 分歧器 but current query family is empty; expected is 分歧管, while Top5 is unrelated equipment.",
        )
    if subtype == "water_pipe":
        family = _clean(query_signal.get("family"))
        if family == "support":
            return (
                "water_pipe_support_context_override",
                "bill name is 冷凝水管/给水管, but bill text contains 管道支架 and insulation context; current family is support.",
            )
        if top_ids and "C10-1-328" not in top_ids:
            return (
                "water_pipe_recall_missing",
                "expected plastic drainage pipe is not in Top5; sample also has a second insulation sleeve expected id.",
            )
    if "复合型风管" in _clean(row.get("query")) or "复合型风管" in expected_names:
        return ("excluded_composite_duct", "excluded by this stage")
    return ("needs_manual_review", "no narrow diagnosis")


def _top_summary(eval_row: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in (eval_row.get("top") or [])[:3]:
        result.append(
            {
                "quota_id": item.get("quota_id"),
                "name": item.get("name"),
                "score": item.get("score"),
                "reasons": item.get("reasons") or [],
            }
        )
    return result


def _build_report(review_rows: list[dict[str, str]], eval_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    eval_lookup = _eval_by_key(eval_rows)
    rows: list[dict[str, Any]] = []
    excluded_rows = 0
    for row in review_rows:
        if row.get("review_subtype") not in TARGET_SUBTYPES:
            continue
        if "复合型风管" in _clean(row.get("query")) or "复合型风管" in _clean(row.get("expected_names")):
            excluded_rows += 1
            continue
        eval_row = eval_lookup.get(_key(row)) or eval_lookup.get(_loose_key(row), {})
        diagnosis_key, diagnosis_note = _diagnose(row, eval_row)
        item = {
            **row,
            "hit1": bool(eval_row.get("hit1")),
            "hit5": bool(eval_row.get("hit5")),
            "current_miss_reason": _clean(eval_row.get("miss_reason") or row.get("miss_reason")),
            "current_query_family": _clean((eval_row.get("query_signal") or {}).get("family")) if isinstance(eval_row.get("query_signal"), dict) else "",
            "current_top_ids": eval_row.get("top_ids") or [],
            "current_top": _top_summary(eval_row),
            "diagnosis_key": diagnosis_key,
            "diagnosis_note": diagnosis_note,
            "stage_decision": "review_only_no_rule_change",
        }
        rows.append(item)

    subtype_counts = Counter(row["review_subtype"] for row in rows)
    diagnosis_counts = Counter(row["diagnosis_key"] for row in rows)
    miss_counts = Counter(row["current_miss_reason"] for row in rows)
    report = {
        "summary": {
            "scope": "branch_pipe/water_pipe review only; composite duct excluded",
            "target_subtypes": list(TARGET_SUBTYPES),
            "rows": len(rows),
            "excluded_rows": excluded_rows,
            "hit1": sum(1 for row in rows if row["hit1"]),
            "hit5": sum(1 for row in rows if row["hit5"]),
            "subtype_counts": [{"key": key, "count": count} for key, count in subtype_counts.most_common()],
            "diagnosis_counts": [{"key": key, "count": count} for key, count in diagnosis_counts.most_common()],
            "miss_reason_counts": [{"key": key, "count": count} for key, count in miss_counts.most_common()],
        },
        "rows": rows,
    }
    return report, rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "review_subtype",
        "split",
        "province",
        "sample_id",
        "bucket",
        "current_miss_reason",
        "hit1",
        "hit5",
        "current_query_family",
        "query",
        "expected_ids",
        "expected_names",
        "current_top_ids",
        "diagnosis_key",
        "diagnosis_note",
        "stage_decision",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: "; ".join(str(value) for value in row.get(field, []))
                    if isinstance(row.get(field), list)
                    else row.get(field, "")
                    for field in fields
                }
            )


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines: list[str] = [
        "# Goal Expanded Pipe Remaining Review",
        "",
        "Review only: branch_pipe / water_pipe. No rule or weight changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["excluded_rows", summary["excluded_rows"]],
                ["hit1", summary["hit1"]],
                ["hit5", summary["hit5"]],
                ["scope", summary["scope"]],
            ]
        ),
        "",
        "## Diagnosis Counts",
        "",
        _md_table([["diagnosis", "count"]] + [[item["key"], item["count"]] for item in summary["diagnosis_counts"]]),
        "",
        "## Review Rows",
        "",
    ]
    table = [["subtype", "province", "sample_id", "miss", "query_family", "query", "expected", "top_ids", "diagnosis"]]
    for row in report["rows"]:
        table.append(
            [
                row.get("review_subtype", ""),
                row.get("province", ""),
                row.get("sample_id", ""),
                row.get("current_miss_reason", ""),
                row.get("current_query_family", ""),
                row.get("query", ""),
                row.get("expected_names", ""),
                "; ".join(row.get("current_top_ids") or []),
                row.get("diagnosis_key", ""),
            ]
        )
    lines.extend([_md_table(table), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review remaining branch_pipe/water_pipe rows without changing rules")
    parser.add_argument("--review-input", default=str(DEFAULT_REVIEW_INPUT))
    parser.add_argument("--eval-details", default=str(DEFAULT_EVAL_DETAILS))
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    report, rows = _build_report(_read_csv(Path(args.review_input)), _read_jsonl(Path(args.eval_details)))
    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.md_output), report)
    _write_csv(Path(args.csv_output), rows)

    print(
        json.dumps(
            {
                "json_output": str(json_output),
                "md_output": args.md_output,
                "csv_output": args.csv_output,
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
