from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from tools.goal_build_ltr_features import DIAG_COLUMNS, FEATURE_COLUMNS  # noqa: E402


DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_pretrain_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_pretrain_audit_summary.md"
DEFAULT_WHITELIST_JSON = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_WHITELIST_TXT = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.txt"


class FeatureStats:
    def __init__(self, feature: str) -> None:
        self.feature = feature
        self.present = 0
        self.numeric = 0
        self.nonzero = 0
        self.sum_value = 0.0
        self.min_value: float | None = None
        self.max_value: float | None = None

    def add(self, value: object) -> None:
        if value in ("", None):
            return
        self.present += 1
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if math.isnan(number) or math.isinf(number):
            return
        self.numeric += 1
        self.sum_value += number
        if number != 0:
            self.nonzero += 1
        self.min_value = number if self.min_value is None else min(self.min_value, number)
        self.max_value = number if self.max_value is None else max(self.max_value, number)

    def to_dict(self, total_rows: int, near_zero_threshold: float) -> dict[str, Any]:
        nonzero_rate = self.nonzero / total_rows if total_rows else 0.0
        return {
            "feature": self.feature,
            "present": self.present,
            "present_rate": round(self.present / total_rows, 6) if total_rows else 0.0,
            "numeric": self.numeric,
            "nonzero": self.nonzero,
            "nonzero_rate": round(nonzero_rate, 6),
            "mean": round(self.sum_value / self.numeric, 6) if self.numeric else 0.0,
            "min": self.min_value if self.min_value is not None else None,
            "max": self.max_value if self.max_value is not None else None,
            "is_all_zero": self.nonzero == 0,
            "is_near_zero": 0 < nonzero_rate <= near_zero_threshold,
        }


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} invalid json: {exc}") from exc


def _as_label(value: object) -> int | None:
    if value in (0, "0", False):
        return 0
    if value in (1, "1", True):
        return 1
    return None


def _as_numeric(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _format_number(value: object) -> str:
    number = _as_numeric(value)
    if number is None:
        return "0"
    if number.is_integer():
        return str(int(number))
    return f"{number:.10g}"


def _write_whitelist(json_path: Path, txt_path: Path, near_zero_threshold: float) -> None:
    payload = {
        "stage": "Goal LTR v1 / stage 1.5 pretrain audit",
        "training_features": FEATURE_COLUMNS,
        "label_column": "label",
        "group_column": "group_id",
        "excluded_diagnostic_columns": DIAG_COLUMNS,
        "near_zero_threshold": near_zero_threshold,
        "notes": [
            "Whitelist excludes identifiers, source metadata, candidate text, and expected ids.",
            "Near-zero features are reported but not automatically removed.",
            "No model is trained by this stage.",
        ],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text("\n".join(FEATURE_COLUMNS) + "\n", encoding="utf-8")


def _finish_group(
    *,
    groups: list[int],
    group_meta: list[dict[str, Any]],
    group_id: str | None,
    row_count: int,
    positive_count: int,
    first_sample: dict[str, Any] | None,
) -> None:
    if not group_id:
        return
    groups.append(row_count)
    group_meta.append(
        {
            "group_id": group_id,
            "rows": row_count,
            "positive_count": positive_count,
            "has_positive": positive_count > 0,
            "sample_id": first_sample.get("sample_id", "") if first_sample else "",
            "source_file": first_sample.get("source_file", "") if first_sample else "",
            "project_name": first_sample.get("project_name", "") if first_sample else "",
            "province": first_sample.get("province", "") if first_sample else "",
            "query": first_sample.get("query", "") if first_sample else "",
            "expected_ids": first_sample.get("expected_ids", "") if first_sample else "",
        }
    )


def _write_group_files(group_path: Path, group_meta_path: Path, groups: list[int], group_meta: list[dict[str, Any]]) -> None:
    group_path.parent.mkdir(parents=True, exist_ok=True)
    group_path.write_text("\n".join(str(size) for size in groups) + "\n", encoding="utf-8")
    with group_meta_path.open("w", encoding="utf-8") as handle:
        for item in group_meta:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def _audit_split(
    *,
    split: str,
    input_path: Path,
    matrix_path: Path,
    group_path: Path,
    group_meta_path: Path,
    near_zero_threshold: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    feature_stats = {feature: FeatureStats(feature) for feature in FEATURE_COLUMNS}
    missing_columns: Counter[str] = Counter()
    invalid_numeric: Counter[str] = Counter()
    unexpected_columns: Counter[str] = Counter()
    label_counts: Counter[int] = Counter()
    group_size_counts: Counter[int] = Counter()

    total_rows = 0
    expected_columns = {"label", *DIAG_COLUMNS, *FEATURE_COLUMNS}
    seen_groups: set[str] = set()
    duplicate_noncontiguous_groups: set[str] = set()
    groups: list[int] = []
    group_meta: list[dict[str, Any]] = []
    current_group: str | None = None
    current_group_rows = 0
    current_group_positive = 0
    current_group_first: dict[str, Any] | None = None
    invalid_label_rows = 0

    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    with matrix_path.open("w", encoding="utf-8-sig", newline="") as matrix_handle:
        writer = csv.DictWriter(matrix_handle, fieldnames=["label", *FEATURE_COLUMNS])
        writer.writeheader()

        for _line_no, row in _iter_jsonl(input_path):
            total_rows += 1
            row_columns = set(row.keys())
            for column in expected_columns - row_columns:
                missing_columns[column] += 1
            for column in row_columns - expected_columns:
                unexpected_columns[column] += 1

            label = _as_label(row.get("label"))
            if label is None:
                invalid_label_rows += 1
                label = 0
            label_counts[label] += 1

            group_id = str(row.get("group_id") or "")
            if not group_id:
                missing_columns["group_id"] += 1
                group_id = f"missing:{total_rows}"
            if current_group is None:
                current_group = group_id
                current_group_first = row
            elif group_id != current_group:
                _finish_group(
                    groups=groups,
                    group_meta=group_meta,
                    group_id=current_group,
                    row_count=current_group_rows,
                    positive_count=current_group_positive,
                    first_sample=current_group_first,
                )
                group_size_counts[current_group_rows] += 1
                seen_groups.add(current_group)
                if group_id in seen_groups:
                    duplicate_noncontiguous_groups.add(group_id)
                current_group = group_id
                current_group_rows = 0
                current_group_positive = 0
                current_group_first = row

            current_group_rows += 1
            current_group_positive += label

            matrix_row: dict[str, Any] = {"label": label}
            for feature in FEATURE_COLUMNS:
                value = row.get(feature, 0)
                feature_stats[feature].add(value)
                if _as_numeric(value) is None:
                    invalid_numeric[feature] += 1
                matrix_row[feature] = _format_number(value)
            writer.writerow(matrix_row)

    if current_group is not None:
        _finish_group(
            groups=groups,
            group_meta=group_meta,
            group_id=current_group,
            row_count=current_group_rows,
            positive_count=current_group_positive,
            first_sample=current_group_first,
        )
        group_size_counts[current_group_rows] += 1
        seen_groups.add(current_group)

    _write_group_files(group_path, group_meta_path, groups, group_meta)
    positive_groups = sum(1 for item in group_meta if item["has_positive"])
    feature_summaries = [feature_stats[feature].to_dict(total_rows, near_zero_threshold) for feature in FEATURE_COLUMNS]
    all_zero = [item["feature"] for item in feature_summaries if item["is_all_zero"]]
    near_zero = [item["feature"] for item in feature_summaries if item["is_near_zero"]]

    return {
        "split": split,
        "input": str(input_path),
        "matrix_csv": str(matrix_path),
        "group_file": str(group_path),
        "group_meta_jsonl": str(group_meta_path),
        "rows": total_rows,
        "groups": len(groups),
        "label_counts": {str(key): value for key, value in sorted(label_counts.items())},
        "positive_rows": label_counts.get(1, 0),
        "positive_groups": positive_groups,
        "positive_group_rate": round(positive_groups / len(groups), 6) if groups else 0.0,
        "missing_positive_groups": len(groups) - positive_groups,
        "group_size_counts": {str(key): value for key, value in sorted(group_size_counts.items())},
        "invalid_label_rows": invalid_label_rows,
        "missing_columns": [{"column": key, "count": value} for key, value in missing_columns.most_common()],
        "unexpected_columns": [{"column": key, "count": value} for key, value in unexpected_columns.most_common()],
        "invalid_numeric": [{"feature": key, "count": value} for key, value in invalid_numeric.most_common() if value],
        "duplicate_noncontiguous_groups": sorted(duplicate_noncontiguous_groups)[:20],
        "all_zero_features": all_zero,
        "near_zero_features": near_zero,
        "feature_summaries": feature_summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }


def _global_feature_summary(split_reports: list[dict[str, Any]], near_zero_threshold: float) -> dict[str, Any]:
    total_rows = sum(report["rows"] for report in split_reports)
    by_feature: dict[str, dict[str, float]] = {
        feature: {"present": 0, "numeric": 0, "nonzero": 0} for feature in FEATURE_COLUMNS
    }
    for report in split_reports:
        for item in report["feature_summaries"]:
            agg = by_feature[item["feature"]]
            agg["present"] += item["present"]
            agg["numeric"] += item["numeric"]
            agg["nonzero"] += item["nonzero"]
    features = []
    for feature in FEATURE_COLUMNS:
        agg = by_feature[feature]
        nonzero_rate = agg["nonzero"] / total_rows if total_rows else 0.0
        features.append(
            {
                "feature": feature,
                "present": int(agg["present"]),
                "numeric": int(agg["numeric"]),
                "nonzero": int(agg["nonzero"]),
                "nonzero_rate": round(nonzero_rate, 6),
                "is_all_zero": agg["nonzero"] == 0,
                "is_near_zero": 0 < nonzero_rate <= near_zero_threshold,
            }
        )
    return {
        "total_rows": total_rows,
        "all_zero_features": [item["feature"] for item in features if item["is_all_zero"]],
        "near_zero_features": [item["feature"] for item in features if item["is_near_zero"]],
        "features": features,
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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines: list[str] = [
        "# Goal LTR Pretrain Audit Summary",
        "",
        "Stage 1.5 only: feature whitelist, training matrices, LightGBM group files, and audit statistics. No model training.",
        "",
        "## Whitelist",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["training_features", len(report["training_features"])],
                ["excluded_diagnostic_columns", len(report["excluded_diagnostic_columns"])],
                ["near_zero_threshold", report["near_zero_threshold"]],
                ["no_model_training", report["no_model_training"]],
            ]
        ),
        "",
        "## Split Summary",
        "",
    ]
    split_rows = [["split", "rows", "groups", "positive_rows", "positive_groups", "positive_group_rate", "missing_positive_groups", "group_size_counts", "elapsed_sec"]]
    for split in report["splits"]:
        split_rows.append(
            [
                split["split"],
                split["rows"],
                split["groups"],
                split["positive_rows"],
                split["positive_groups"],
                split["positive_group_rate"],
                split["missing_positive_groups"],
                split["group_size_counts"],
                split["elapsed_sec"],
            ]
        )
    lines.extend([_md_table(split_rows), "", "## Outputs", ""])
    lines.extend(
        [
            _md_table(
                [["split", "matrix_csv", "group_file", "group_meta_jsonl"]]
                + [[item["split"], item["matrix_csv"], item["group_file"], item["group_meta_jsonl"]] for item in report["splits"]]
            ),
            "",
            "## Near-Zero Features",
            "",
            _md_table(
                [["scope", "all_zero", "near_zero"]]
                + [["global", ", ".join(report["global_feature_summary"]["all_zero_features"]), ", ".join(report["global_feature_summary"]["near_zero_features"])]]
                + [
                    [item["split"], ", ".join(item["all_zero_features"]), ", ".join(item["near_zero_features"])]
                    for item in report["splits"]
                ]
            ),
            "",
            "## Validation Flags",
            "",
        ]
    )
    validation_rows = [["split", "invalid_label_rows", "missing_columns", "unexpected_columns", "invalid_numeric", "noncontiguous_groups"]]
    for split in report["splits"]:
        validation_rows.append(
            [
                split["split"],
                split["invalid_label_rows"],
                len(split["missing_columns"]),
                len(split["unexpected_columns"]),
                len(split["invalid_numeric"]),
                len(split["duplicate_noncontiguous_groups"]),
            ]
        )
    lines.extend([_md_table(validation_rows), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Goal LTR feature files before model training")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--splits", default="dev,heldout,hard")
    parser.add_argument("--near-zero-threshold", type=float, default=0.005)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--whitelist-json", default=str(DEFAULT_WHITELIST_JSON))
    parser.add_argument("--whitelist-txt", default=str(DEFAULT_WHITELIST_TXT))
    args = parser.parse_args()

    started = time.perf_counter()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    splits = [part.strip() for part in args.splits.split(",") if part.strip()]
    _write_whitelist(Path(args.whitelist_json), Path(args.whitelist_txt), args.near_zero_threshold)

    split_reports: list[dict[str, Any]] = []
    for split in splits:
        input_path = input_dir / f"ltr_features_{split}.jsonl"
        split_reports.append(
            _audit_split(
                split=split,
                input_path=input_path,
                matrix_path=output_dir / f"ltr_matrix_{split}.csv",
                group_path=output_dir / f"ltr_group_{split}.txt",
                group_meta_path=output_dir / f"ltr_group_{split}.jsonl",
                near_zero_threshold=args.near_zero_threshold,
            )
        )

    report = {
        "stage": "Goal LTR v1 / stage 1.5 pretrain audit",
        "no_model_training": True,
        "no_ranking_change": True,
        "no_rule_change": True,
        "training_features": FEATURE_COLUMNS,
        "excluded_diagnostic_columns": DIAG_COLUMNS,
        "near_zero_threshold": args.near_zero_threshold,
        "whitelist_json": args.whitelist_json,
        "whitelist_txt": args.whitelist_txt,
        "splits": split_reports,
        "global_feature_summary": _global_feature_summary(split_reports, args.near_zero_threshold),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_model_training": report["no_model_training"],
                    "training_features": len(FEATURE_COLUMNS),
                    "elapsed_sec": report["elapsed_sec"],
                    "whitelist_json": args.whitelist_json,
                    "whitelist_txt": args.whitelist_txt,
                },
                "splits": [
                    {
                        key: split[key]
                        for key in (
                            "split",
                            "rows",
                            "groups",
                            "positive_rows",
                            "positive_groups",
                            "positive_group_rate",
                            "missing_positive_groups",
                            "group_size_counts",
                            "elapsed_sec",
                        )
                    }
                    for split in split_reports
                ],
                "global_all_zero_features": report["global_feature_summary"]["all_zero_features"],
                "global_near_zero_features": report["global_feature_summary"]["near_zero_features"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
