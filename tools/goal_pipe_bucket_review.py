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

DEFAULT_INPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_install_strong_signal_samples.csv"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_bucket_review.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_bucket_review.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_pipe_bucket_review.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _classify_pipe_row(row: dict[str, str]) -> tuple[str, str, str]:
    query = _clean(row.get("query"))
    expected = _clean(row.get("expected_names"))

    if "风管式空调" in query or "风管机" in query or ("空调" in query and "风管" in query):
        return "device_false_trigger", "hvac_air_conditioner", "pipe keyword appears inside HVAC equipment name"
    if "管道风机" in query or ("风机" in query and "管道" in query):
        return "device_false_trigger", "fan_equipment", "pipe keyword appears inside fan equipment name"

    if "分歧器" in query or "分歧管" in query:
        return "true_pipe", "branch_pipe", "explicit branch pipe/fitting query"
    if "风管" in query or "通风管道" in query or "风管" in expected:
        return "true_pipe", "air_duct", "explicit duct query or duct expected item"
    if "钢管" in query:
        return "true_pipe", "steel_pipe", "explicit steel pipe query"
    if "水管" in query or "给水管" in query or "排水管" in query or "冷凝水管" in query:
        return "true_pipe", "water_pipe", "explicit water/drain pipe query"
    if "管道" in query:
        return "true_pipe", "generic_pipe", "explicit generic pipe query"
    return "needs_manual_review", "unknown", "no confident true-pipe or device trigger"


def _read_pipe_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return [row for row in rows if row.get("target_family") == "pipe"]


def _counter_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common()]


def _build_report(rows: list[dict[str, str]], input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reviewed_rows: list[dict[str, Any]] = []
    for row in rows:
        review_class, review_subtype, review_note = _classify_pipe_row(row)
        reviewed = dict(row)
        reviewed["review_class"] = review_class
        reviewed["review_subtype"] = review_subtype
        reviewed["review_note"] = review_note
        reviewed["actionable_for_pipe_rule"] = review_class == "true_pipe"
        reviewed_rows.append(reviewed)

    class_counts = Counter(row["review_class"] for row in reviewed_rows)
    subtype_counts = Counter(row["review_subtype"] for row in reviewed_rows)
    split_counts = Counter(row.get("split", "") for row in reviewed_rows)
    province_counts = Counter(row.get("province", "") for row in reviewed_rows)
    bucket_counts = Counter(row.get("bucket", "") for row in reviewed_rows)
    reason_counts = Counter(row.get("miss_reason", "") for row in reviewed_rows)

    true_rows = [row for row in reviewed_rows if row["review_class"] == "true_pipe"]
    false_rows = [row for row in reviewed_rows if row["review_class"] == "device_false_trigger"]
    report = {
        "summary": {
            "input": str(input_path),
            "rows": len(reviewed_rows),
            "true_pipe_rows": len(true_rows),
            "device_false_trigger_rows": len(false_rows),
            "needs_manual_review_rows": class_counts.get("needs_manual_review", 0),
            "class_counts": _counter_items(class_counts),
            "subtype_counts": _counter_items(subtype_counts),
            "split_counts": _counter_items(split_counts),
            "province_counts": _counter_items(province_counts),
            "source_bucket_counts": _counter_items(bucket_counts),
            "miss_reason_counts": _counter_items(reason_counts),
        },
        "true_pipe_samples": true_rows,
        "device_false_trigger_samples": false_rows,
        "needs_manual_review_samples": [
            row for row in reviewed_rows if row["review_class"] == "needs_manual_review"
        ],
    }
    return report, reviewed_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "review_class",
        "review_subtype",
        "actionable_for_pipe_rule",
        "review_note",
        "split",
        "province",
        "sample_id",
        "bucket",
        "miss_reason",
        "query",
        "expected_names",
        "expected_ids",
        "strong_evidence",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


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
        "# Goal Expanded Pipe Bucket Review",
        "",
        "Only reviews the pipe bucket. No rule or weight changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["true_pipe_rows", summary["true_pipe_rows"]],
                ["device_false_trigger_rows", summary["device_false_trigger_rows"]],
                ["needs_manual_review_rows", summary["needs_manual_review_rows"]],
            ]
        ),
        "",
        "## Class Counts",
        "",
        _md_table([["class", "count"]] + [[item["key"], item["count"]] for item in summary["class_counts"]]),
        "",
        "## Subtype Counts",
        "",
        _md_table([["subtype", "count"]] + [[item["key"], item["count"]] for item in summary["subtype_counts"]]),
        "",
        "## True Pipe / Duct / Branch Pipe",
        "",
    ]
    true_rows = [["split", "province", "bucket", "miss_reason", "subtype", "query", "expected"]]
    for row in report["true_pipe_samples"]:
        true_rows.append(
            [
                row.get("split", ""),
                row.get("province", ""),
                row.get("bucket", ""),
                row.get("miss_reason", ""),
                row.get("review_subtype", ""),
                row.get("query", ""),
                row.get("expected_names", ""),
            ]
        )
    lines.extend([_md_table(true_rows), "", "## Device False Triggers", ""])

    false_rows = [["split", "province", "bucket", "miss_reason", "subtype", "query", "expected", "note"]]
    for row in report["device_false_trigger_samples"]:
        false_rows.append(
            [
                row.get("split", ""),
                row.get("province", ""),
                row.get("bucket", ""),
                row.get("miss_reason", ""),
                row.get("review_subtype", ""),
                row.get("query", ""),
                row.get("expected_names", ""),
                row.get("review_note", ""),
            ]
        )
    lines.extend([_md_table(false_rows), ""])

    if report["needs_manual_review_samples"]:
        lines.extend(["## Needs Manual Review", ""])
        manual_rows = [["split", "province", "query", "expected"]]
        for row in report["needs_manual_review_samples"]:
            manual_rows.append([row.get("split", ""), row.get("province", ""), row.get("query", ""), row.get("expected_names", "")])
        lines.extend([_md_table(manual_rows), ""])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review the strong-signal pipe bucket without changing rules")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = _read_pipe_rows(input_path)
    report, reviewed_rows = _build_report(rows, input_path)

    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.md_output), report)
    _write_csv(Path(args.csv_output), reviewed_rows)

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
