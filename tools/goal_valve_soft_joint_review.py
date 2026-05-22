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

DEFAULT_INPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_bucket_review.csv"
DEFAULT_JSON_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_soft_joint_review.json"
DEFAULT_MD_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_soft_joint_review.md"
DEFAULT_CSV_OUTPUT = PROJECT_ROOT / "reports" / "agent_state" / "goal_expanded_post_pipe_valve_soft_joint_review.csv"

GUANGDONG = "\u5e7f\u4e1c"
CHONGQING = "\u91cd\u5e86"
SOFT_JOINT = "\u8f6f\u63a5\u5934"
HOSE = "\u8f6f\u7ba1"
COMPENSATOR = "\u8865\u507f\u5668"
VALVE = "\u9600"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _classify_soft_joint(row: dict[str, str]) -> tuple[str, str, str]:
    province = _clean(row.get("province"))
    query = _clean(row.get("query"))
    expected = _clean(row.get("expected_names"))
    expected_families = _clean(row.get("expected_families"))

    if GUANGDONG in province and _contains_any(query, (SOFT_JOINT, HOSE)) and VALVE in expected:
        return (
            "guangdong_soft_joint_valve_book",
            "\u5e7f\u4e1c\u8f6f\u63a5\u5934\u8d70\u9600\u95e8\u518c",
            "Guangdong soft-joint rows currently expect welded flange valve items; keep province/book scoped.",
        )
    if CHONGQING in province and COMPENSATOR in query and (COMPENSATOR in expected or expected_families == "pipe"):
        return (
            "chongqing_compensator_pipe_book",
            "\u91cd\u5e86\u8865\u507f\u5668\u8d70\u7ba1\u9053/\u8865\u507f\u5668\u518c",
            "Chongqing compensator row expects a compensator item, not a generic valve item.",
        )
    return (
        "needs_manual_review",
        "\u9700\u8981\u4eba\u5de5\u590d\u6838",
        "Soft-joint/compensator row does not fit the two narrow province/book patterns.",
    )


def _read_soft_joint_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    return [row for row in rows if row.get("review_subtype") == "soft_joint_compensator"]


def _counter_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common()]


def _build_report(rows: list[dict[str, str]], input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reviewed_rows: list[dict[str, Any]] = []
    for row in rows:
        review_group, review_group_label, review_note = _classify_soft_joint(row)
        reviewed = dict(row)
        reviewed["review_group"] = review_group
        reviewed["review_group_label"] = review_group_label
        reviewed["review_note_detail"] = review_note
        reviewed["stage_decision"] = "review_only_no_rule_change"
        reviewed["avoid_global_valve_rule"] = True
        reviewed_rows.append(reviewed)

    group_counts = Counter(row["review_group"] for row in reviewed_rows)
    label_counts = Counter(row["review_group_label"] for row in reviewed_rows)
    split_counts = Counter(row.get("split", "") for row in reviewed_rows)
    province_counts = Counter(row.get("province", "") for row in reviewed_rows)
    bucket_counts = Counter(row.get("bucket", "") for row in reviewed_rows)
    reason_counts = Counter(row.get("miss_reason", "") for row in reviewed_rows)

    report = {
        "summary": {
            "input": str(input_path),
            "scope": "soft_joint_compensator review only; no rule or weight changes",
            "rows": len(reviewed_rows),
            "review_only_no_rule_change": True,
            "avoid_global_valve_rule": True,
            "needs_manual_review_rows": group_counts.get("needs_manual_review", 0),
            "group_counts": _counter_items(group_counts),
            "group_label_counts": _counter_items(label_counts),
            "split_counts": _counter_items(split_counts),
            "province_counts": _counter_items(province_counts),
            "source_bucket_counts": _counter_items(bucket_counts),
            "miss_reason_counts": _counter_items(reason_counts),
        },
        "rows": reviewed_rows,
    }
    return report, reviewed_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "review_group",
        "review_group_label",
        "avoid_global_valve_rule",
        "review_note_detail",
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
        "# Goal Expanded Post Pipe Valve Soft Joint Review",
        "",
        "Review only: split soft_joint_compensator into province/book patterns. No rule or weight changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["needs_manual_review_rows", summary["needs_manual_review_rows"]],
                ["avoid_global_valve_rule", summary["avoid_global_valve_rule"]],
                ["scope", summary["scope"]],
            ]
        ),
        "",
        "## Group Counts",
        "",
        _md_table([["group", "count"]] + [[item["key"], item["count"]] for item in summary["group_counts"]]),
        "",
        "## Group Label Counts",
        "",
        _md_table([["label", "count"]] + [[item["key"], item["count"]] for item in summary["group_label_counts"]]),
        "",
        "## Review Rows",
        "",
    ]

    table = [["group", "label", "split", "province", "sample_id", "miss", "query_family", "query", "expected", "top_ids", "note"]]
    for row in report["rows"]:
        table.append(
            [
                row.get("review_group", ""),
                row.get("review_group_label", ""),
                row.get("split", ""),
                row.get("province", ""),
                row.get("sample_id", ""),
                row.get("miss_reason", ""),
                row.get("query_family", ""),
                row.get("query", ""),
                row.get("expected_names", ""),
                row.get("top_ids", ""),
                row.get("review_note_detail", ""),
            ]
        )
    lines.extend([_md_table(table), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Review valve soft-joint/compensator rows without changing rules")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--json-output", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-output", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--csv-output", default=str(DEFAULT_CSV_OUTPUT))
    args = parser.parse_args()

    input_path = Path(args.input)
    rows = _read_soft_joint_rows(input_path)
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
