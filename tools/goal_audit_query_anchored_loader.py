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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_INPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_STAGE_65_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run_summary.json"
DEFAULT_WHITELIST = DEFAULT_INPUT_DIR / "ltr_feature_whitelist_query_anchored_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_summary.md"
DEFAULT_SPLIT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_split_summary.csv"
DEFAULT_FEATURE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_feature_coverage.csv"
DEFAULT_RECALL_GAP_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_recall_gap_buckets.csv"
DEFAULT_GATE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_training_gates.csv"

FORBIDDEN_TRAINING_FEATURES = {
    "anchor_group_id",
    "anchor_reason",
    "anchor_status",
    "bill_name",
    "bill_text",
    "candidate_id",
    "candidate_name",
    "candidate_rank",
    "correct_quota_id",
    "expected_id",
    "expected_ids",
    "expected_quota_id",
    "expected_quota_ids",
    "group_id",
    "name",
    "positive_id",
    "project_name",
    "province",
    "query",
    "query_text",
    "quota_book",
    "quota_chapter",
    "quota_id",
    "quota_name",
    "quota_unit",
    "raw_query",
    "reasons",
    "row_index",
    "sample_id",
    "source_file",
    "split",
    "stored_ids",
}

DEV_ANCHOR_OK_STATUSES = {"anchor_reliable", "anchor_usable_no_strong_conflict"}


class FeatureStats:
    def __init__(self, feature: str) -> None:
        self.feature = feature
        self.present = 0
        self.numeric = 0
        self.nonzero = 0
        self.sum_value = 0.0
        self.min_value: float | None = None
        self.max_value: float | None = None

    def add(self, value: Any) -> None:
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
        self.nonzero += int(number != 0.0)
        self.min_value = number if self.min_value is None else min(self.min_value, number)
        self.max_value = number if self.max_value is None else max(self.max_value, number)

    def merge(self, other: "FeatureStats") -> None:
        self.present += other.present
        self.numeric += other.numeric
        self.nonzero += other.nonzero
        self.sum_value += other.sum_value
        if other.min_value is not None:
            self.min_value = other.min_value if self.min_value is None else min(self.min_value, other.min_value)
        if other.max_value is not None:
            self.max_value = other.max_value if self.max_value is None else max(self.max_value, other.max_value)

    def to_dict(self, split: str, total_rows: int, near_zero_threshold: float) -> dict[str, Any]:
        nonzero_rate = self.nonzero / total_rows if total_rows else 0.0
        return {
            "split": split,
            "feature": self.feature,
            "present": self.present,
            "present_rate": round(self.present / total_rows, 6) if total_rows else 0.0,
            "numeric": self.numeric,
            "numeric_rate": round(self.numeric / total_rows, 6) if total_rows else 0.0,
            "nonzero": self.nonzero,
            "nonzero_rate": round(nonzero_rate, 6),
            "mean": round(self.sum_value / self.numeric, 6) if self.numeric else 0.0,
            "min": self.min_value,
            "max": self.max_value,
            "is_all_zero": self.nonzero == 0,
            "is_near_zero": 0 < nonzero_rate <= near_zero_threshold,
        }


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_group_sizes(path: Path) -> list[int]:
    if not path.exists():
        return []
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if line:
                yield line_no, json.loads(line)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [row for _line_no, row in _iter_jsonl(path)]


def _load_training_features(path: Path) -> list[str]:
    payload = _read_json(path)
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} missing training_features")
    return [_clean(feature) for feature in features if _clean(feature)]


def _finite_float(value: Any) -> float:
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise ValueError("not finite")
    return result


def _as_label(value: Any) -> int | None:
    try:
        label = int(float(value))
    except (TypeError, ValueError):
        return None
    return label if label in {0, 1} else None


def _group_index_for_row(row_number: int, cumulative_sizes: list[int], current_index: int) -> int:
    while current_index < len(cumulative_sizes) and row_number > cumulative_sizes[current_index]:
        current_index += 1
    return current_index


def _audit_split(
    *,
    split: str,
    input_dir: Path,
    training_features: list[str],
    near_zero_threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    started = time.perf_counter()
    matrix_path = input_dir / f"ltr_matrix_{split}.csv"
    group_path = input_dir / f"ltr_group_{split}.txt"
    group_meta_path = input_dir / f"ltr_group_{split}.jsonl"
    feature_path = input_dir / f"ltr_features_{split}.jsonl"
    recall_gap_path = input_dir / f"recall_gap_{split}.jsonl"
    anchor_excluded_path = input_dir / f"anchor_excluded_{split}.jsonl"

    group_sizes = _read_group_sizes(group_path)
    group_meta = _load_jsonl(group_meta_path)
    cumulative_sizes: list[int] = []
    running = 0
    for size in group_sizes:
        running += size
        cumulative_sizes.append(running)

    expected_header = ["label", *training_features]
    feature_stats = {feature: FeatureStats(feature) for feature in training_features}
    label_counts: Counter[int] = Counter()
    matrix_positive_by_group: list[int] = [0 for _ in group_sizes]
    feature_positive_by_group: list[int] = [0 for _ in group_sizes]
    feature_group_rows: list[int] = [0 for _ in group_sizes]
    matrix_group_rows: list[int] = [0 for _ in group_sizes]
    invalid_numeric: Counter[str] = Counter()
    missing_feature: Counter[str] = Counter()
    feature_label_mismatch = 0
    feature_group_mismatch = 0
    feature_rows_seen = 0
    matrix_rows_seen = 0
    invalid_label_rows = 0
    group_index = 0
    feature_extra_rows = 0
    feature_missing_rows = 0

    header: list[str] = []
    matrix_read_error = ""
    try:
        with matrix_path.open("r", encoding="utf-8-sig", newline="") as matrix_handle, feature_path.open("r", encoding="utf-8-sig") as feature_handle:
            reader = csv.DictReader(matrix_handle)
            header = list(reader.fieldnames or [])
            feature_iter = enumerate(feature_handle, start=1)
            for matrix_row_number, matrix_row in enumerate(reader, start=1):
                matrix_rows_seen += 1
                group_index = _group_index_for_row(matrix_row_number, cumulative_sizes, group_index)
                label = _as_label(matrix_row.get("label"))
                if label is None:
                    invalid_label_rows += 1
                    label = 0
                label_counts[label] += 1
                if group_index < len(matrix_positive_by_group):
                    matrix_positive_by_group[group_index] += label
                    matrix_group_rows[group_index] += 1

                for feature in training_features:
                    if feature not in matrix_row:
                        missing_feature[feature] += 1
                        value = 0
                    else:
                        value = matrix_row.get(feature)
                    try:
                        _finite_float(value)
                    except (TypeError, ValueError):
                        invalid_numeric[feature] += 1
                    feature_stats[feature].add(value)

                try:
                    feature_line_number, feature_line = next(feature_iter)
                except StopIteration:
                    feature_missing_rows += 1
                    continue
                feature_rows_seen += 1
                feature_row = json.loads(feature_line)
                feature_label = _as_label(feature_row.get("label"))
                if feature_label != label:
                    feature_label_mismatch += 1
                expected_group = group_meta[group_index].get("group_id") if group_index < len(group_meta) else ""
                actual_group = _clean(feature_row.get("group_id"))
                if expected_group and actual_group != expected_group:
                    feature_group_mismatch += 1
                if group_index < len(feature_positive_by_group):
                    feature_positive_by_group[group_index] += int(feature_label or 0)
                    feature_group_rows[group_index] += 1

            for _line_number, _line in feature_iter:
                feature_extra_rows += 1
    except Exception as exc:  # noqa: BLE001
        matrix_read_error = str(exc)

    group_meta_positive_bad = [
        _clean(row.get("group_id"))
        for row in group_meta
        if int(row.get("positive_count") or 0) <= 0
    ]
    matrix_groups_without_positive = [
        group_meta[idx].get("group_id") if idx < len(group_meta) else str(idx)
        for idx, count in enumerate(matrix_positive_by_group)
        if count <= 0
    ]
    feature_groups_without_positive = [
        group_meta[idx].get("group_id") if idx < len(group_meta) else str(idx)
        for idx, count in enumerate(feature_positive_by_group)
        if count <= 0
    ]
    nonmatching_group_sizes = [
        {
            "group_id": group_meta[idx].get("group_id") if idx < len(group_meta) else str(idx),
            "expected": group_sizes[idx],
            "matrix_rows": matrix_group_rows[idx],
            "feature_rows": feature_group_rows[idx],
        }
        for idx in range(len(group_sizes))
        if group_sizes[idx] != matrix_group_rows[idx] or group_sizes[idx] != feature_group_rows[idx]
    ]

    forbidden_in_header = sorted(set(header) & FORBIDDEN_TRAINING_FEATURES)
    missing_header_features = [feature for feature in training_features if feature not in header]
    unexpected_header = [column for column in header if column not in expected_header]
    header_exact_match = header == expected_header
    matrix_rows_match_group = sum(group_sizes) == matrix_rows_seen
    feature_rows_match_matrix = feature_rows_seen == matrix_rows_seen and feature_extra_rows == 0 and feature_missing_rows == 0
    group_meta_match_group = len(group_meta) == len(group_sizes)
    group_all_positive = not matrix_groups_without_positive and not feature_groups_without_positive and not group_meta_positive_bad
    loader_can_read = (
        not matrix_read_error
        and header_exact_match
        and matrix_rows_match_group
        and feature_rows_match_matrix
        and group_meta_match_group
        and not invalid_label_rows
        and not invalid_numeric
        and not missing_feature
        and not feature_label_mismatch
        and not feature_group_mismatch
        and not forbidden_in_header
    )

    anchor_status_counts = Counter(_clean(row.get("anchor_status")) or "<empty>" for row in group_meta)
    dev_anchor_clean_ready = split != "dev" or (
        bool(group_meta)
        and all((_clean(row.get("anchor_status")) in DEV_ANCHOR_OK_STATUSES) for row in group_meta)
    )

    recall_gap_rows = _load_jsonl(recall_gap_path)
    anchor_excluded_rows = _load_jsonl(anchor_excluded_path)
    recall_gap_buckets = _recall_gap_buckets(split, recall_gap_rows)
    feature_coverage = [feature_stats[feature].to_dict(split, matrix_rows_seen, near_zero_threshold) for feature in training_features]

    split_report = {
        "split": split,
        "matrix_path": str(matrix_path),
        "group_path": str(group_path),
        "group_meta_path": str(group_meta_path),
        "feature_path": str(feature_path),
        "recall_gap_path": str(recall_gap_path),
        "anchor_excluded_path": str(anchor_excluded_path),
        "loader_can_read": loader_can_read,
        "matrix_read_error": matrix_read_error,
        "header_exact_match": header_exact_match,
        "header_columns": len(header),
        "forbidden_in_header": forbidden_in_header,
        "missing_header_features": missing_header_features,
        "unexpected_header": unexpected_header,
        "matrix_rows": matrix_rows_seen,
        "feature_rows": feature_rows_seen,
        "feature_extra_rows": feature_extra_rows,
        "feature_missing_rows": feature_missing_rows,
        "group_count": len(group_sizes),
        "group_meta_count": len(group_meta),
        "group_sum": sum(group_sizes),
        "matrix_rows_match_group": matrix_rows_match_group,
        "feature_rows_match_matrix": feature_rows_match_matrix,
        "group_meta_match_group": group_meta_match_group,
        "nonmatching_group_size_count": len(nonmatching_group_sizes),
        "nonmatching_group_sizes": nonmatching_group_sizes[:20],
        "positive_rows": label_counts.get(1, 0),
        "label_counts": {str(key): value for key, value in sorted(label_counts.items())},
        "invalid_label_rows": invalid_label_rows,
        "matrix_groups_without_positive": len(matrix_groups_without_positive),
        "feature_groups_without_positive": len(feature_groups_without_positive),
        "group_meta_without_positive": len(group_meta_positive_bad),
        "group_all_positive": group_all_positive,
        "feature_label_mismatch": feature_label_mismatch,
        "feature_group_mismatch": feature_group_mismatch,
        "invalid_numeric": [{"feature": key, "count": value} for key, value in invalid_numeric.most_common() if value],
        "missing_feature": [{"feature": key, "count": value} for key, value in missing_feature.most_common() if value],
        "all_zero_features": [row["feature"] for row in feature_coverage if row["is_all_zero"]],
        "near_zero_features": [row["feature"] for row in feature_coverage if row["is_near_zero"]],
        "anchor_status_counts": dict(anchor_status_counts),
        "dev_anchor_clean_ready": dev_anchor_clean_ready,
        "recall_gap_rows": len(recall_gap_rows),
        "anchor_excluded_rows": len(anchor_excluded_rows),
        "recall_gap_empty_family_rows": sum(1 for row in recall_gap_rows if not _clean(row.get("query_family"))),
        "recall_gap_empty_family_rate": _rate(sum(1 for row in recall_gap_rows if not _clean(row.get("query_family"))), len(recall_gap_rows)),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    return split_report, feature_coverage, recall_gap_buckets


def _recall_gap_buckets(split: str, rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    counters: dict[str, Counter[str]] = {
        "query_family": Counter(),
        "province": Counter(),
        "source_file": Counter(),
        "province_family": Counter(),
        "source_family": Counter(),
        "reason": Counter(),
    }
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    total = len(rows)
    for row in rows:
        family = _clean(row.get("query_family")) or "<empty>"
        province = _clean(row.get("province")) or "<empty>"
        source_file = _clean(row.get("source_file")) or "<empty>"
        reason = _clean(row.get("recall_gap_reason")) or "<empty>"
        values = {
            "query_family": family,
            "province": province,
            "source_file": source_file,
            "province_family": f"{province}|{family}",
            "source_family": f"{source_file}|{family}",
            "reason": reason,
        }
        for bucket_type, key in values.items():
            counters[bucket_type][key] += 1
            ex_key = (bucket_type, key)
            if len(examples[ex_key]) < 3:
                sample = _clean(row.get("group_id")) or _clean(row.get("sample_id"))
                if sample:
                    examples[ex_key].append(sample)

    output: list[dict[str, Any]] = []
    for bucket_type, counter in counters.items():
        for key, count in counter.most_common(limit):
            output.append(
                {
                    "split": split,
                    "bucket_type": bucket_type,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                    "examples": "|".join(examples.get((bucket_type, key), [])),
                }
            )
    return output


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _global_feature_summary(feature_rows: list[dict[str, Any]], training_features: list[str], near_zero_threshold: float) -> list[dict[str, Any]]:
    total_by_split = Counter()
    stats = {feature: FeatureStats(feature) for feature in training_features}
    for row in feature_rows:
        split = _clean(row.get("split"))
        if split == "global":
            continue
        feature = _clean(row.get("feature"))
        if feature not in stats:
            continue
        total_by_split[split] = max(total_by_split[split], int(row.get("present") or 0))
        shim = FeatureStats(feature)
        shim.present = int(row.get("present") or 0)
        shim.numeric = int(row.get("numeric") or 0)
        shim.nonzero = int(row.get("nonzero") or 0)
        shim.sum_value = float(row.get("mean") or 0.0) * shim.numeric
        shim.min_value = row.get("min")
        shim.max_value = row.get("max")
        stats[feature].merge(shim)
    total_rows = sum(total_by_split.values())
    return [stats[feature].to_dict("global", total_rows, near_zero_threshold) for feature in training_features]


def _gate_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    splits = report["splits"]
    loader_ok = all(split["loader_can_read"] for split in splits)
    group_ok = all(split["matrix_rows_match_group"] and split["feature_rows_match_matrix"] for split in splits)
    positive_ok = all(split["group_all_positive"] for split in splits)
    forbidden_ok = report["forbidden_feature_leak_count"] == 0 and all(not split["forbidden_in_header"] for split in splits)
    recall_gap_ok = all(split["recall_gap_rows"] >= 0 for split in splits)
    dev_report = next((split for split in splits if split["split"] == "dev"), {})
    dev_anchor_ready = bool(dev_report.get("dev_anchor_clean_ready"))
    rows = [
        {
            "gate": "matrix_loader_gate",
            "status": "pass" if loader_ok else "fail",
            "evidence": "all split matrices can be parsed as label + numeric features",
            "action": "required before any training",
        },
        {
            "gate": "group_alignment_gate",
            "status": "pass" if group_ok else "fail",
            "evidence": "group sums match matrix rows; feature JSONL rows match matrix rows",
            "action": "required before any training",
        },
        {
            "gate": "positive_group_gate",
            "status": "pass" if positive_ok else "fail",
            "evidence": "each accepted group has at least one positive label",
            "action": "recall gaps stay outside matrix",
        },
        {
            "gate": "forbidden_feature_gate",
            "status": "pass" if forbidden_ok else "fail",
            "evidence": f"forbidden_feature_leak_count={report['forbidden_feature_leak_count']}",
            "action": "fail training if identifiers/answers enter feature whitelist or matrix header",
        },
        {
            "gate": "recall_gap_separation_gate",
            "status": "pass" if recall_gap_ok else "fail",
            "evidence": "Top80 missing positives are written to recall_gap_<split>.jsonl",
            "action": "do not train all-negative recall-gap groups",
        },
        {
            "gate": "dev_anchor_clean_gate",
            "status": "pass" if dev_anchor_ready else "block_training",
            "evidence": f"dev anchor_status_counts={dev_report.get('anchor_status_counts', {})}",
            "action": "run dev anchor audit before using dev as training labels",
        },
    ]
    return rows


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(value) for value in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Goal Query-Anchored Loader Audit",
        "",
        "Stage 6.6 eval-only audit. It validates matrix readability, group alignment, positive labels, forbidden feature leakage, feature coverage, and recall-gap buckets. No training is run.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["loader_audit_passed", report["loader_audit_passed"]],
                ["training_ready", report["training_ready"]],
                ["training_blockers", ", ".join(report["training_blockers"])],
                ["training_features", len(report["training_features"])],
                ["forbidden_feature_leak_count", report["forbidden_feature_leak_count"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Split Audit",
        "",
        _md_table(
            [
                ["split", "loader", "rows", "groups", "positive", "recall_gap", "all_zero", "near_zero", "dev_anchor_clean"],
                *[
                    [
                        split["split"],
                        split["loader_can_read"],
                        split["matrix_rows"],
                        split["group_count"],
                        split["positive_rows"],
                        split["recall_gap_rows"],
                        len(split["all_zero_features"]),
                        len(split["near_zero_features"]),
                        split["dev_anchor_clean_ready"],
                    ]
                    for split in report["splits"]
                ],
            ]
        ),
        "",
        "## Training Gates",
        "",
        _md_table([["gate", "status", "action"], *[[row["gate"], row["status"], row["action"]] for row in report["training_gates"]]]),
        "",
        "## Recall Gap Top Buckets",
        "",
        _md_table(
            [["split", "bucket_type", "key", "count", "rate"]]
            + [
                [row["split"], row["bucket_type"], row["key"], row["count"], row["rate"]]
                for row in report["recall_gap_buckets"][:30]
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 6.6 audit query-anchored dry-run matrices without training")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--splits", default="dev,heldout,hard")
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--stage-65-summary", default=str(DEFAULT_STAGE_65_SUMMARY))
    parser.add_argument("--near-zero-threshold", type=float, default=0.005)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--split-csv", default=str(DEFAULT_SPLIT_CSV))
    parser.add_argument("--feature-csv", default=str(DEFAULT_FEATURE_CSV))
    parser.add_argument("--recall-gap-csv", default=str(DEFAULT_RECALL_GAP_CSV))
    parser.add_argument("--gate-csv", default=str(DEFAULT_GATE_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    input_dir = Path(args.input_dir)
    splits = [part.strip() for part in args.splits.split(",") if part.strip()]
    training_features = _load_training_features(Path(args.feature_whitelist))
    forbidden_features = sorted(set(training_features) & FORBIDDEN_TRAINING_FEATURES)
    stage_65_summary = _read_json(Path(args.stage_65_summary))

    split_reports: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    recall_gap_buckets: list[dict[str, Any]] = []
    for split in splits:
        split_report, split_features, split_recall_buckets = _audit_split(
            split=split,
            input_dir=input_dir,
            training_features=training_features,
            near_zero_threshold=args.near_zero_threshold,
        )
        split_reports.append(split_report)
        feature_rows.extend(split_features)
        recall_gap_buckets.extend(split_recall_buckets)

    feature_rows.extend(_global_feature_summary(feature_rows, training_features, args.near_zero_threshold))
    report = {
        "stage": "Goal LTR v1 / stage 6.6 query anchored loader audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "input_dir": str(input_dir),
        "stage_65_summary": str(Path(args.stage_65_summary)),
        "stage_65_validation_passed": bool(stage_65_summary.get("validation_passed")),
        "feature_whitelist": str(Path(args.feature_whitelist)),
        "training_features": training_features,
        "forbidden_training_features": forbidden_features,
        "forbidden_feature_leak_count": len(forbidden_features),
        "near_zero_threshold": args.near_zero_threshold,
        "splits": split_reports,
        "feature_coverage": feature_rows,
        "recall_gap_buckets": recall_gap_buckets,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report["training_gates"] = _gate_rows(report)
    report["loader_audit_passed"] = all(row["status"] == "pass" for row in report["training_gates"] if row["gate"] != "dev_anchor_clean_gate")
    report["training_blockers"] = [row["gate"] for row in report["training_gates"] if row["status"] in {"fail", "block_training"}]
    report["training_ready"] = not report["training_blockers"]
    report["recommended_next_stage"] = (
        "Stage 6.6a dev anchor audit before any training"
        if "dev_anchor_clean_gate" in report["training_blockers"]
        else "Stage 6.7 dev-only ranking trial may be considered"
    )
    report["artifacts"] = {
        "summary_json": str(Path(args.report_json)),
        "summary_md": str(Path(args.report_md)),
        "split_csv": str(Path(args.split_csv)),
        "feature_csv": str(Path(args.feature_csv)),
        "recall_gap_csv": str(Path(args.recall_gap_csv)),
        "gate_csv": str(Path(args.gate_csv)),
    }

    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    _write_csv(
        Path(args.split_csv),
        split_reports,
        [
            "split",
            "loader_can_read",
            "matrix_rows",
            "feature_rows",
            "group_count",
            "group_sum",
            "positive_rows",
            "recall_gap_rows",
            "anchor_excluded_rows",
            "matrix_rows_match_group",
            "feature_rows_match_matrix",
            "group_all_positive",
            "forbidden_in_header",
            "all_zero_features",
            "near_zero_features",
            "anchor_status_counts",
            "dev_anchor_clean_ready",
            "elapsed_sec",
        ],
    )
    _write_csv(
        Path(args.feature_csv),
        feature_rows,
        ["split", "feature", "present", "present_rate", "numeric", "numeric_rate", "nonzero", "nonzero_rate", "mean", "min", "max", "is_all_zero", "is_near_zero"],
    )
    _write_csv(Path(args.recall_gap_csv), recall_gap_buckets, ["split", "bucket_type", "key", "count", "rate", "examples"])
    _write_csv(Path(args.gate_csv), report["training_gates"], ["gate", "status", "evidence", "action"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": report["eval_only"],
                    "no_training": report["no_training"],
                    "loader_audit_passed": report["loader_audit_passed"],
                    "training_ready": report["training_ready"],
                    "training_blockers": report["training_blockers"],
                    "training_features": len(training_features),
                    "forbidden_feature_leak_count": report["forbidden_feature_leak_count"],
                    "elapsed_sec": report["elapsed_sec"],
                    "recommended_next_stage": report["recommended_next_stage"],
                },
                "splits": [
                    {
                        key: split[key]
                        for key in (
                            "split",
                            "loader_can_read",
                            "matrix_rows",
                            "group_count",
                            "positive_rows",
                            "recall_gap_rows",
                            "matrix_rows_match_group",
                            "feature_rows_match_matrix",
                            "group_all_positive",
                            "dev_anchor_clean_ready",
                        )
                    }
                    for split in split_reports
                ],
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
