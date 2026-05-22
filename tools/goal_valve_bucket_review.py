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

DEFAULT_INPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_install_strong_signal_samples.csv"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_bucket_review.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_bucket_review.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_bucket_review.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _classify_valve_row(row: dict[str, str]) -> tuple[str, str]:
    query = _clean(row.get("query"))
    expected = _clean(row.get("expected_names"))

    if "消声器" in query or "消声器" in expected:
        return "false_trigger", "valve keyword is inside a silencer item"
    if "喷头" in expected:
        return "false_trigger", "expected item is sprinkler, not valve"
    if query == "过滤器" and "吸收装置" in expected:
        return "false_trigger", "filter query points to chlorine absorber expected item"

    if "软接头" in query or "软管" in query or "补偿器" in query:
        return "soft_joint_compensator", "soft joint / hose / compensator signal"
    if "倒流防止器" in query or "真空破坏器" in query:
        return "backflow_vacuum", "backflow preventer / vacuum breaker signal"
    if "阀" in query or "阀" in expected:
        return "ordinary_valve", "plain valve or damper-valve signal"
    return "false_trigger", "no clear valve subtype after review"


def _read_valve_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return [row for row in rows if row.get("target_family") == "valve"]


def _counter_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common()]


def _build_report(rows: list[dict[str, str]], input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reviewed_rows: list[dict[str, Any]] = []
    for row in rows:
        review_subtype, review_note = _classify_valve_row(row)
        reviewed = dict(row)
        reviewed["review_subtype"] = review_subtype
        reviewed["review_note"] = review_note
        reviewed["stage_decision"] = "review_only_no_rule_change"
        reviewed["actionable_for_valve_rule"] = review_subtype != "false_trigger"
        reviewed_rows.append(reviewed)

    subtype_counts = Counter(row["review_subtype"] for row in reviewed_rows)
    split_counts = Counter(row.get("split", "") for row in reviewed_rows)
    province_counts = Counter(row.get("province", "") for row in reviewed_rows)
    bucket_counts = Counter(row.get("bucket", "") for row in reviewed_rows)
    reason_counts = Counter(row.get("miss_reason", "") for row in reviewed_rows)

    false_rows = [row for row in reviewed_rows if row["review_subtype"] == "false_trigger"]
    actionable_rows = [row for row in reviewed_rows if row["review_subtype"] != "false_trigger"]
    next_subtype = ""
    for subtype, _count in subtype_counts.most_common():
        if subtype != "false_trigger":
            next_subtype = subtype
            break

    report = {
        "summary": {
            "input": str(input_path),
            "scope": "valve bucket review only; no rule or weight changes",
            "rows": len(reviewed_rows),
            "review_only_no_rule_change": True,
            "actionable_rows": len(actionable_rows),
            "false_trigger_rows": len(false_rows),
            "suggested_next_subtype": next_subtype,
            "subtype_counts": _counter_items(subtype_counts),
            "split_counts": _counter_items(split_counts),
            "province_counts": _counter_items(province_counts),
            "source_bucket_counts": _counter_items(bucket_counts),
            "miss_reason_counts": _counter_items(reason_counts),
        },
        "rows": reviewed_rows,
        "actionable_samples": actionable_rows,
        "false_trigger_samples": false_rows,
    }
    return report, reviewed_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "review_subtype",
        "actionable_for_valve_rule",
        "review_note",
        "stage_decision",
        "split",
        "province",
        "sample_id",
        "bucket",
        "miss_reason",
        "query",
        "query_family",
        "expected_families",
        "expected_ids",
        "expected_names",
        "top_ids",
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


def _rows_table(rows: list[dict[str, Any]]) -> str:
    table = [["subtype", "split", "province", "sample_id", "miss", "query_family", "query", "expected", "top_ids", "note"]]
    for row in rows:
        table.append(
            [
                row.get("review_subtype", ""),
                row.get("split", ""),
                row.get("province", ""),
                row.get("sample_id", ""),
                row.get("miss_reason", ""),
                row.get("query_family", ""),
                row.get("query", ""),
                row.get("expected_names", ""),
                row.get("top_ids", ""),
                row.get("review_note", ""),
            ]
        )
    return _md_table(table)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines: list[str] = [
        "# Goal Expanded Post Pipe Valve Bucket Review",
        "",
        "Review only: valve bucket split into soft joint/compensator, backflow/vacuum, ordinary valve, and false triggers. No rule or weight changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["actionable_rows", summary["actionable_rows"]],
                ["false_trigger_rows", summary["false_trigger_rows"]],
                ["suggested_next_subtype", summary["suggested_next_subtype"]],
                ["scope", summary["scope"]],
            ]
        ),
        "",
        "## Subtype Counts",
        "",
        _md_table([["subtype", "count"]] + [[item["key"], item["count"]] for item in summary["subtype_counts"]]),
        "",
        "## Actionable Review Rows",
        "",
        _rows_table(report["actionable_samples"]),
        "",
        "## False Triggers",
        "",
        _rows_table(report["false_trigger_samples"]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review the strong-signal valve bucket without changing rules")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = _read_valve_rows(input_path)
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
