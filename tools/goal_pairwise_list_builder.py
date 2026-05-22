from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_INPUT_CSV = (
    PROJECT_ROOT
    / "reports"
    / "agent_state"
    / "goal_same_family_book_rank2_pairwise_audit_details.csv"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "goal_search" / "hard_pairs"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_pairwise_list_builder_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_pairwise_list_builder_summary.md"

LIST_OUTPUTS = {
    "train_whitelist": "same_family_book_rank2_train_whitelist.csv",
    "review_graylist": "same_family_book_rank2_review_graylist.csv",
    "exclude_or_downweight": "same_family_book_rank2_exclude_or_downweight.csv",
}

RECOMMENDATION_TO_LIST = {
    "include_as_hard_pair_candidate": (
        "train_whitelist",
        "eligible_for_future_hard_pair_training",
    ),
    "manual_label_review_before_training": (
        "review_graylist",
        "manual_label_review_required_before_training",
    ),
    "do_not_train_first_fix_or_audit_gate": (
        "review_graylist",
        "fix_or_audit_safety_gate_before_training",
    ),
    "exclude_or_downweight_from_hard_pair": (
        "exclude_or_downweight",
        "exclude_from_hard_pair_training_or_downweight_for_diagnostics",
    ),
}

LEADING_FIELDS = [
    "pair_id",
    "list_name",
    "use_policy",
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _make_pair_id(row: dict[str, Any]) -> str:
    split = _clean(row.get("split")) or "unknown_split"
    group_id = _clean(row.get("group_id")) or f"sample:{_clean(row.get('sample_id')) or 'unknown'}"
    top_id = _clean(row.get("top_id")) or "unknown_top"
    positive_id = _clean(row.get("positive_id")) or "unknown_positive"
    return f"{split}:{group_id}:{top_id}>{positive_id}"


def _map_row(row: dict[str, Any]) -> dict[str, Any]:
    recommendation = _clean(row.get("training_recommendation"))
    list_name, use_policy = RECOMMENDATION_TO_LIST.get(
        recommendation,
        ("review_graylist", "unknown_recommendation_requires_review"),
    )
    mapped = dict(row)
    mapped.update(
        {
            "pair_id": _make_pair_id(row),
            "list_name": list_name,
            "use_policy": use_policy,
        }
    )
    return mapped


def _counter_items(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count, "rate": _rate(count, total)}
        for key, count in counter.most_common()
    ]


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    pair_ids = Counter(_clean(row.get("pair_id")) for row in rows)
    for row in rows:
        list_name = _clean(row.get("list_name"))
        for dimension in (
            "list_name",
            "use_policy",
            "training_recommendation",
            "audit_verdict",
            "audit_reason",
            "query_family",
            "province",
            "expected_books",
            "diagnosis_4_1",
        ):
            counters[dimension][_clean(row.get(dimension)) or "(empty)"] += 1
        counters[f"list_family:{list_name}"][_clean(row.get("query_family")) or "(empty)"] += 1
        counters[f"list_verdict:{list_name}"][_clean(row.get("audit_verdict")) or "(empty)"] += 1

    list_counts = counters["list_name"]
    return {
        "rows": total,
        "unique_pair_ids": sum(1 for count in pair_ids.values() if count == 1),
        "duplicate_pair_id_count": sum(count - 1 for count in pair_ids.values() if count > 1),
        "train_whitelist_count": list_counts.get("train_whitelist", 0),
        "train_whitelist_rate": _rate(list_counts.get("train_whitelist", 0), total),
        "review_graylist_count": list_counts.get("review_graylist", 0),
        "review_graylist_rate": _rate(list_counts.get("review_graylist", 0), total),
        "exclude_or_downweight_count": list_counts.get("exclude_or_downweight", 0),
        "exclude_or_downweight_rate": _rate(list_counts.get("exclude_or_downweight", 0), total),
        "unknown_policy_count": counters["use_policy"].get("unknown_recommendation_requires_review", 0),
        "by_list": _counter_items(counters["list_name"], total),
        "by_use_policy": _counter_items(counters["use_policy"], total),
        "by_training_recommendation": _counter_items(counters["training_recommendation"], total),
        "by_audit_verdict": _counter_items(counters["audit_verdict"], total),
        "by_query_family": _counter_items(counters["query_family"], total),
        "by_province": _counter_items(counters["province"], total),
        "by_expected_books": _counter_items(counters["expected_books"], total),
        "by_diagnosis_4_1": _counter_items(counters["diagnosis_4_1"], total),
        "by_list_family": {
            list_name: _counter_items(counters[f"list_family:{list_name}"], total)
            for list_name in LIST_OUTPUTS
        },
        "by_list_verdict": {
            list_name: _counter_items(counters[f"list_verdict:{list_name}"], total)
            for list_name in LIST_OUTPUTS
        },
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


def _counter_table(items: list[dict[str, Any]], limit: int | None = None) -> list[list[object]]:
    selected = items if limit is None else items[:limit]
    return [["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in selected]]


def _sample_rows(rows: list[dict[str, Any]], list_name: str, limit: int) -> list[list[object]]:
    selected = [row for row in rows if row["list_name"] == list_name]
    selected.sort(
        key=lambda row: (
            _clean(row.get("query_family")),
            _clean(row.get("province")),
            _clean(row.get("query")),
            _clean(row.get("pair_id")),
        )
    )
    return [
        [
            row["pair_id"],
            row.get("query_family", ""),
            row.get("query", ""),
            row.get("positive_id", ""),
            row.get("top_id", ""),
            row.get("use_policy", ""),
        ]
        for row in selected[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], sample_limit: int) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Pairwise List Builder",
        "",
        "Stage 4.4 freezes the 43 same-family/book rank2 pairwise audit rows into train whitelist, review graylist, and exclude/downweight lists. It does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["rows", summary["rows"]],
                ["unique_pair_ids", summary["unique_pair_ids"]],
                ["duplicate_pair_id_count", summary["duplicate_pair_id_count"]],
                ["train_whitelist_count", summary["train_whitelist_count"]],
                ["train_whitelist_rate", summary["train_whitelist_rate"]],
                ["review_graylist_count", summary["review_graylist_count"]],
                ["review_graylist_rate", summary["review_graylist_rate"]],
                ["exclude_or_downweight_count", summary["exclude_or_downweight_count"]],
                ["exclude_or_downweight_rate", summary["exclude_or_downweight_rate"]],
                ["unknown_policy_count", summary["unknown_policy_count"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## List Counts",
        "",
        _md_table(_counter_table(summary["by_list"])),
        "",
        "## Training Recommendation",
        "",
        _md_table(_counter_table(summary["by_training_recommendation"])),
        "",
        "## Query Family",
        "",
        _md_table(_counter_table(summary["by_query_family"], limit=20)),
        "",
        "## Samples",
        "",
        "Train whitelist:",
        "",
        _md_table(
            [["pair_id", "family", "query", "positive_id", "top_id", "policy"]]
            + _sample_rows(rows, "train_whitelist", sample_limit)
        ),
        "",
        "Review graylist:",
        "",
        _md_table(
            [["pair_id", "family", "query", "positive_id", "top_id", "policy"]]
            + _sample_rows(rows, "review_graylist", sample_limit)
        ),
        "",
        "Exclude/downweight:",
        "",
        _md_table(
            [["pair_id", "family", "query", "positive_id", "top_id", "policy"]]
            + _sample_rows(rows, "exclude_or_downweight", sample_limit)
        ),
        "",
        "## Artifacts",
        "",
        _md_table(
            [["artifact", "path"]]
            + [[key, value] for key, value in report["artifacts"].items()]
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze stage 4.3 same-family/book rank2 audit rows into hard-pair lists"
    )
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--sample-limit", type=int, default=8)
    args = parser.parse_args()

    started = time.perf_counter()
    input_path = Path(args.input_csv)
    source_rows, input_fields = _read_csv(input_path)
    mapped_rows = [_map_row(row) for row in source_rows]
    summary = _summarize(mapped_rows)

    output_dir = Path(args.output_dir)
    output_paths: dict[str, str] = {}
    output_fields = LEADING_FIELDS + [field for field in input_fields if field not in LEADING_FIELDS]
    for list_name, filename in LIST_OUTPUTS.items():
        path = output_dir / filename
        rows = [row for row in mapped_rows if row["list_name"] == list_name]
        _write_csv(path, rows, output_fields)
        output_paths[list_name] = str(path)

    report_artifacts = {
        **output_paths,
        "report_json": str(Path(args.report_json)),
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 4.4 same-family/book rank2 pairwise list freeze",
        "read_only_input": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input_csv": str(input_path),
        "output_dir": str(output_dir),
        "artifacts": report_artifacts,
        "summary": summary,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, mapped_rows, args.sample_limit)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_training": True,
                    "elapsed_sec": report["elapsed_sec"],
                    "rows": summary["rows"],
                    "train_whitelist_count": summary["train_whitelist_count"],
                    "review_graylist_count": summary["review_graylist_count"],
                    "exclude_or_downweight_count": summary["exclude_or_downweight_count"],
                    "duplicate_pair_id_count": summary["duplicate_pair_id_count"],
                    "unknown_policy_count": summary["unknown_policy_count"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
