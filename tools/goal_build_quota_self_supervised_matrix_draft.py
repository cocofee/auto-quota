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

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_PAIR_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.csv"
DEFAULT_MATRIX_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_draft.csv"
DEFAULT_MATRIX_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_draft.jsonl"
DEFAULT_GROUP_TXT = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_group.txt"
DEFAULT_GROUP_META_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_group_meta.jsonl"
DEFAULT_SCHEMA_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_schema.json"
DEFAULT_COVERAGE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_coverage.csv"
DEFAULT_GROUP_AUDIT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_group_audit.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_draft_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_draft_summary.md"

MATRIX_FIELDS = [
    "group_id",
    "row_in_group",
    "label",
    "row_weight",
    "group_weight",
    "sample_source",
    "training_role",
    "training_mode",
    "selection_stage",
    "family",
    "province",
    "pair_type",
    "contrast_field",
    "contrast_value",
    "counterpart_contrast_value",
    "quota_id",
    "quota_name",
    "unit",
    "book",
    "chapter",
    "subtype_key",
    "counterpart_quota_id",
    "counterpart_quota_name",
    "counterpart_subtype_key",
    "source_pair_id",
    "source_db_path",
]

SCHEMA_ROWS = [
    ("group_id", "string", "group", "LightGBM group key. Two contiguous rows per pair."),
    ("row_in_group", "enum", "diagnostic", "positive or negative row role."),
    ("label", "integer", "target", "1 for positive quota, 0 for negative quota."),
    ("row_weight", "float", "weight", "Per-row weight placeholder; uniform 1.0 in stage 5.7."),
    ("group_weight", "float", "weight", "Per-group weight placeholder; uniform 1.0 in stage 5.7."),
    ("sample_source", "string", "diagnostic", "Self-supervised source type."),
    ("training_role", "string", "diagnostic", "Future role, always train_candidate here."),
    ("training_mode", "string", "diagnostic", "Family freeze mode: both or subtype_only."),
    ("selection_stage", "string", "diagnostic", "Freeze stage that selected the pair."),
    ("family", "string", "feature_source", "Object family extracted from quota text."),
    ("province", "string", "feature_source", "Source local quota database name."),
    ("pair_type", "string", "feature_source", "param_contrast or subtype_contrast."),
    ("contrast_field", "string", "feature_source", "Parameter or subtype field contrasted by this pair."),
    ("contrast_value", "string", "feature_source", "Current row's contrast value."),
    ("counterpart_contrast_value", "string", "diagnostic", "Other row's contrast value."),
    ("quota_id", "string", "candidate", "Current row quota id."),
    ("quota_name", "string", "candidate", "Current row quota name."),
    ("unit", "string", "candidate", "Current row unit."),
    ("book", "string", "candidate", "Current row quota book."),
    ("chapter", "string", "candidate", "Current row chapter."),
    ("subtype_key", "string", "candidate", "Current row subtype key."),
    ("counterpart_quota_id", "string", "diagnostic", "Other row quota id."),
    ("counterpart_quota_name", "string", "diagnostic", "Other row quota name."),
    ("counterpart_subtype_key", "string", "diagnostic", "Other row subtype key."),
    ("source_pair_id", "string", "diagnostic", "Original stage 5.6 pair id."),
    ("source_db_path", "string", "diagnostic", "quota.db source path."),
]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _as_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _matrix_row(source: dict[str, Any], role: str) -> dict[str, Any]:
    positive = role == "positive"
    prefix = "positive" if positive else "negative"
    other = "negative" if positive else "positive"
    return {
        "group_id": _clean(source.get("training_group_id")),
        "row_in_group": role,
        "label": 1 if positive else 0,
        "row_weight": "1.0",
        "group_weight": "1.0",
        "sample_source": "quota_self_supervised_pair",
        "training_role": _clean(source.get("training_role")) or "train_candidate",
        "training_mode": _clean(source.get("training_mode")),
        "selection_stage": _clean(source.get("selection_stage")),
        "family": _clean(source.get("family")),
        "province": _clean(source.get("province")),
        "pair_type": _clean(source.get("pair_type")),
        "contrast_field": _clean(source.get("contrast_field")),
        "contrast_value": _clean(source.get(f"{prefix}_contrast_value")),
        "counterpart_contrast_value": _clean(source.get(f"{other}_contrast_value")),
        "quota_id": _clean(source.get(f"{prefix}_id")),
        "quota_name": _clean(source.get(f"{prefix}_name")),
        "unit": _clean(source.get(f"{prefix}_unit")),
        "book": _clean(source.get(f"{prefix}_book")),
        "chapter": _clean(source.get(f"{prefix}_chapter")),
        "subtype_key": _clean(source.get(f"{prefix}_subtype_key")),
        "counterpart_quota_id": _clean(source.get(f"{other}_id")),
        "counterpart_quota_name": _clean(source.get(f"{other}_name")),
        "counterpart_subtype_key": _clean(source.get(f"{other}_subtype_key")),
        "source_pair_id": _clean(source.get("pair_id")),
        "source_db_path": _clean(source.get("source_db_path")),
    }


def _build_matrix(pair_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int], list[dict[str, Any]]]:
    matrix_rows: list[dict[str, Any]] = []
    groups: list[int] = []
    group_meta: list[dict[str, Any]] = []
    for row in pair_rows:
        positive_row = _matrix_row(row, "positive")
        negative_row = _matrix_row(row, "negative")
        matrix_rows.extend([positive_row, negative_row])
        groups.append(2)
        group_meta.append(
            {
                "group_id": positive_row["group_id"],
                "rows": 2,
                "positive_count": 1,
                "family": positive_row["family"],
                "province": positive_row["province"],
                "pair_type": positive_row["pair_type"],
                "training_mode": positive_row["training_mode"],
                "positive_id": positive_row["quota_id"],
                "negative_id": negative_row["quota_id"],
                "source_pair_id": positive_row["source_pair_id"],
            }
        )
    return matrix_rows, groups, group_meta


def _coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(rows)
    result: list[dict[str, Any]] = []
    for field in MATRIX_FIELDS:
        nonempty = 0
        numeric = 0
        nonzero = 0
        values = Counter()
        for row in rows:
            value = row.get(field)
            if value not in ("", None):
                nonempty += 1
                values[_clean(value)] += 1
            number = _as_number(value)
            if number is not None:
                numeric += 1
                if number != 0:
                    nonzero += 1
        top_values = "; ".join(f"{key}:{count}" for key, count in values.most_common(5))
        result.append(
            {
                "field": field,
                "rows": total,
                "nonempty": nonempty,
                "nonempty_rate": _rate(nonempty, total),
                "numeric": numeric,
                "numeric_rate": _rate(numeric, total),
                "nonzero": nonzero,
                "nonzero_rate": _rate(nonzero, total),
                "top_values": top_values,
            }
        )
    return result


def _audit_groups(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audits: list[dict[str, Any]] = []
    seen_finished: set[str] = set()
    duplicate_noncontiguous: set[str] = set()
    duplicate_row_keys = 0
    row_keys: set[tuple[str, str]] = set()
    invalid_groups = 0
    label_counts = Counter()
    group_size_counts = Counter()
    group_positive_counts = Counter()
    current_group = ""
    current_rows: list[dict[str, Any]] = []

    def finish(group_rows: list[dict[str, Any]]) -> None:
        nonlocal invalid_groups
        if not group_rows:
            return
        group_id = _clean(group_rows[0].get("group_id"))
        labels = [_clean(row.get("label")) for row in group_rows]
        roles = [_clean(row.get("row_in_group")) for row in group_rows]
        positive_count = labels.count("1")
        negative_count = labels.count("0")
        ok = len(group_rows) == 2 and positive_count == 1 and negative_count == 1 and roles == ["positive", "negative"]
        if not ok:
            invalid_groups += 1
        group_size_counts[len(group_rows)] += 1
        group_positive_counts[positive_count] += 1
        audits.append(
            {
                "group_id": group_id,
                "rows": len(group_rows),
                "labels": "|".join(labels),
                "roles": "|".join(roles),
                "positive_count": positive_count,
                "negative_count": negative_count,
                "is_valid_pair_group": str(ok).lower(),
                "family": _clean(group_rows[0].get("family")),
                "province": _clean(group_rows[0].get("province")),
                "pair_type": _clean(group_rows[0].get("pair_type")),
                "source_pair_id": _clean(group_rows[0].get("source_pair_id")),
            }
        )

    for row in rows:
        group_id = _clean(row.get("group_id"))
        row_key = (group_id, _clean(row.get("row_in_group")))
        if row_key in row_keys:
            duplicate_row_keys += 1
        row_keys.add(row_key)
        label_counts[_clean(row.get("label"))] += 1
        if not current_group:
            current_group = group_id
        elif group_id != current_group:
            finish(current_rows)
            seen_finished.add(current_group)
            if group_id in seen_finished:
                duplicate_noncontiguous.add(group_id)
            current_group = group_id
            current_rows = []
        current_rows.append(row)
    finish(current_rows)
    summary = {
        "groups": len(audits),
        "invalid_groups": invalid_groups,
        "duplicate_row_keys": duplicate_row_keys,
        "duplicate_noncontiguous_groups": len(duplicate_noncontiguous),
        "label_counts": dict(label_counts),
        "group_size_counts": {str(key): value for key, value in sorted(group_size_counts.items())},
        "group_positive_counts": {str(key): value for key, value in sorted(group_positive_counts.items())},
    }
    return audits, summary


def _schema_payload() -> dict[str, Any]:
    return {
        "stage": "Goal LTR v1 / stage 5.7 quota self-supervised matrix draft",
        "eval_only": True,
        "no_training": True,
        "matrix_fields": [
            {"field": field, "dtype": dtype, "role": role, "description": description}
            for field, dtype, role, description in SCHEMA_ROWS
        ],
        "group_format": "LightGBM-compatible group txt: one group size per line; all groups size 2.",
        "row_order": "For every pair group: positive row first, negative row second.",
        "label_schema": {"positive": 1, "negative": 0},
        "weight_schema": {"row_weight": "uniform 1.0", "group_weight": "uniform 1.0"},
    }


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
        "# Goal Self-Supervised Matrix Draft",
        "",
        "Stage 5.7 eval-only draft. It expands frozen pair whitelist rows into positive/negative matrix rows, writes schema and coverage audits, and does not train a model.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["input_pairs", summary["input_pairs"]],
                ["matrix_rows", summary["matrix_rows"]],
                ["groups", summary["groups"]],
                ["positive_rows", summary["positive_rows"]],
                ["negative_rows", summary["negative_rows"]],
                ["row_weight_nonempty_rate", summary["row_weight_nonempty_rate"]],
                ["group_weight_nonempty_rate", summary["group_weight_nonempty_rate"]],
                ["invalid_groups", summary["invalid_groups"]],
                ["duplicate_row_keys", summary["duplicate_row_keys"]],
                ["duplicate_noncontiguous_groups", summary["duplicate_noncontiguous_groups"]],
                ["passes_matrix_draft_gate", summary["passes_matrix_draft_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Label Counts",
        "",
        _md_table(_counter_table(summary["by_label"])),
        "",
        "## Pair Type",
        "",
        _md_table(_counter_table(summary["by_pair_type"])),
        "",
        "## Training Mode",
        "",
        _md_table(_counter_table(summary["by_training_mode"])),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["matrix_csv", report["artifacts"]["matrix_csv"]],
                ["matrix_jsonl", report["artifacts"]["matrix_jsonl"]],
                ["group_txt", report["artifacts"]["group_txt"]],
                ["group_meta_jsonl", report["artifacts"]["group_meta_jsonl"]],
                ["schema_json", report["artifacts"]["schema_json"]],
                ["coverage_csv", report["artifacts"]["coverage_csv"]],
                ["group_audit_csv", report["artifacts"]["group_audit_csv"]],
                ["summary_json", report["artifacts"]["summary_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.7 eval-only self-supervised matrix draft and audit")
    parser.add_argument("--pair-whitelist-csv", default=str(DEFAULT_PAIR_WHITELIST_CSV))
    parser.add_argument("--matrix-csv", default=str(DEFAULT_MATRIX_CSV))
    parser.add_argument("--matrix-jsonl", default=str(DEFAULT_MATRIX_JSONL))
    parser.add_argument("--group-txt", default=str(DEFAULT_GROUP_TXT))
    parser.add_argument("--group-meta-jsonl", default=str(DEFAULT_GROUP_META_JSONL))
    parser.add_argument("--schema-json", default=str(DEFAULT_SCHEMA_JSON))
    parser.add_argument("--coverage-csv", default=str(DEFAULT_COVERAGE_CSV))
    parser.add_argument("--group-audit-csv", default=str(DEFAULT_GROUP_AUDIT_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--top-limit", type=int, default=30)
    args = parser.parse_args()

    started = time.perf_counter()
    pair_rows = _read_csv(Path(args.pair_whitelist_csv))
    matrix_rows, groups, group_meta = _build_matrix(pair_rows)
    coverage_rows = _coverage(matrix_rows)
    group_audit_rows, group_summary = _audit_groups(matrix_rows)

    _write_csv(Path(args.matrix_csv), matrix_rows, MATRIX_FIELDS)
    _write_jsonl(Path(args.matrix_jsonl), matrix_rows)
    Path(args.group_txt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.group_txt).write_text("\n".join(str(size) for size in groups) + "\n", encoding="utf-8")
    _write_jsonl(Path(args.group_meta_jsonl), group_meta)
    _write_json(Path(args.schema_json), _schema_payload())
    _write_csv(
        Path(args.coverage_csv),
        coverage_rows,
        ["field", "rows", "nonempty", "nonempty_rate", "numeric", "numeric_rate", "nonzero", "nonzero_rate", "top_values"],
    )
    _write_csv(
        Path(args.group_audit_csv),
        group_audit_rows,
        ["group_id", "rows", "labels", "roles", "positive_count", "negative_count", "is_valid_pair_group", "family", "province", "pair_type", "source_pair_id"],
    )

    label_counts = Counter(str(row["label"]) for row in matrix_rows)
    pair_type_counts = Counter(_clean(row.get("pair_type")) for row in matrix_rows)
    training_mode_counts = Counter(_clean(row.get("training_mode")) for row in matrix_rows)
    row_weight_coverage = next(row for row in coverage_rows if row["field"] == "row_weight")
    group_weight_coverage = next(row for row in coverage_rows if row["field"] == "group_weight")
    summary = {
        "input_pairs": len(pair_rows),
        "matrix_rows": len(matrix_rows),
        "groups": len(groups),
        "positive_rows": label_counts.get("1", 0),
        "negative_rows": label_counts.get("0", 0),
        "row_weight_nonempty_rate": row_weight_coverage["nonempty_rate"],
        "group_weight_nonempty_rate": group_weight_coverage["nonempty_rate"],
        **group_summary,
        "passes_matrix_draft_gate": (
            len(matrix_rows) == len(pair_rows) * 2
            and len(groups) == len(pair_rows)
            and set(groups) == {2}
            and group_summary["invalid_groups"] == 0
            and group_summary["duplicate_row_keys"] == 0
            and group_summary["duplicate_noncontiguous_groups"] == 0
            and label_counts.get("1", 0) == len(pair_rows)
            and label_counts.get("0", 0) == len(pair_rows)
            and row_weight_coverage["nonempty_rate"] == 1.0
            and group_weight_coverage["nonempty_rate"] == 1.0
        ),
        "by_label": _counter_items(label_counts, len(matrix_rows), args.top_limit),
        "by_pair_type": _counter_items(pair_type_counts, len(matrix_rows), args.top_limit),
        "by_training_mode": _counter_items(training_mode_counts, len(matrix_rows), args.top_limit),
    }
    report = {
        "stage": "Goal LTR v1 / stage 5.7 quota self-supervised matrix draft",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "pair_whitelist_csv": str(Path(args.pair_whitelist_csv)),
        "summary": summary,
        "schema": _schema_payload(),
        "artifacts": {
            "matrix_csv": str(Path(args.matrix_csv)),
            "matrix_jsonl": str(Path(args.matrix_jsonl)),
            "group_txt": str(Path(args.group_txt)),
            "group_meta_jsonl": str(Path(args.group_meta_jsonl)),
            "schema_json": str(Path(args.schema_json)),
            "coverage_csv": str(Path(args.coverage_csv)),
            "group_audit_csv": str(Path(args.group_audit_csv)),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "input_pairs": summary["input_pairs"],
                    "matrix_rows": summary["matrix_rows"],
                    "groups": summary["groups"],
                    "positive_rows": summary["positive_rows"],
                    "negative_rows": summary["negative_rows"],
                    "passes_matrix_draft_gate": summary["passes_matrix_draft_gate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
