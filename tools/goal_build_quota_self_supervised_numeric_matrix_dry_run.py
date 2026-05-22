from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
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

DEFAULT_MATRIX_DRAFT_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_draft.csv"
DEFAULT_PAIR_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.csv"
DEFAULT_FEATURE_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_feature_mapping_plan.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_numeric_matrix_dry_run"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_numeric_matrix_dry_run_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_numeric_matrix_dry_run_summary.md"

DEFAULT_SPLIT = "quota_selfsup"

PARAM_MARKER_RE = re.compile(r"(DN|mm2|mm|m2|m3|<=|>=|<|>|≤|≥|\d+)", re.IGNORECASE)
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")

DIAGNOSTIC_FORBIDDEN_SUBSTRINGS = [
    "group_id",
    "row_in_group",
    "quota_id",
    "quota_name",
    "source",
    "expected",
    "sample",
    "project",
    "province",
    "bill",
    "query",
    "raw",
]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _safe_name(value: str) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "empty"
    if text[0].isdigit():
        text = f"v_{text}"
    return text


def _as_float(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _first_number(value: Any) -> float | None:
    match = NUMBER_RE.search(_clean(value))
    if not match:
        return None
    return _as_float(match.group(0))


def _num(value: float | int | bool | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _log1p_abs(value: float | None) -> float:
    if value is None:
        return 0.0
    return math.log1p(abs(value))


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(value or "")}


def _token_count(value: str) -> int:
    return len(TOKEN_RE.findall(value or ""))


def _jaccard(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _parts(value: str) -> list[str]:
    return [part for part in (_clean(part) for part in value.split("|")) if part]


def _stable_hash_unit(value: str) -> float:
    text = _clean(value)
    if not text:
        return 0.0
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    integer = int.from_bytes(digest, "big")
    return integer / float(2**64 - 1)


def _load_allowed_plan(path: Path) -> dict[str, Any]:
    rows = _read_csv(path)
    allowed_statuses = {
        "ready_categorical_or_source",
        "ready_numeric",
        "ready_numeric_pairwise",
        "ready_numeric_partial",
        "needs_matrix_v2_or_join",
    }
    return {
        "source_path": str(path),
        "allowed_feature_defs": [row for row in rows if _clean(row.get("mapping_status")) in allowed_statuses],
        "excluded_feature_defs": [row for row in rows if _clean(row.get("mapping_status")) not in allowed_statuses],
    }


def _category_values(rows: list[dict[str, Any]], field: str) -> list[str]:
    return sorted({_clean(row.get(field)) for row in rows if _clean(row.get(field))})


def _field_from_pair(pair_row: dict[str, Any] | None, role: str, name: str) -> str:
    if not pair_row:
        return ""
    prefix = "positive" if role == "positive" else "negative"
    return _clean(pair_row.get(f"{prefix}_{name}"))


def _other_field_from_pair(pair_row: dict[str, Any] | None, role: str, name: str) -> str:
    if not pair_row:
        return ""
    prefix = "negative" if role == "positive" else "positive"
    return _clean(pair_row.get(f"{prefix}_{name}"))


def _same(left: str, right: str) -> bool:
    return bool(left and right and left == right)


def _build_pair_lookup(pair_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in pair_rows:
        pair_id = _clean(row.get("pair_id"))
        group_id = _clean(row.get("training_group_id"))
        if pair_id:
            lookup[pair_id] = row
        if group_id:
            lookup[group_id] = row
    return lookup


def _base_feature_names(matrix_rows: list[dict[str, Any]]) -> list[str]:
    feature_names: list[str] = []
    feature_names.extend(
        [
            "pair_type_is_param_contrast",
            "pair_type_is_subtype_contrast",
            "training_mode_is_both",
            "training_mode_is_subtype_only",
        ]
    )
    feature_names.extend(f"family_is_{_safe_name(value)}" for value in _category_values(matrix_rows, "family"))
    feature_names.extend(f"contrast_field_is_{_safe_name(value)}" for value in _category_values(matrix_rows, "contrast_field"))
    feature_names.extend(
        [
            "candidate_name_length",
            "candidate_name_token_count",
            "candidate_name_digit_count",
            "candidate_name_has_digits",
            "candidate_name_param_marker_count",
            "counterpart_name_length",
            "candidate_name_length_signed_delta",
            "counterpart_name_token_jaccard",
            "unit_present",
            "book_present",
            "chapter_present",
            "candidate_book_number_present",
            "candidate_book_number_log1p",
            "candidate_chapter_number_present",
            "candidate_chapter_number_log1p",
            "same_book_with_counterpart",
            "same_chapter_with_counterpart",
            "same_unit_with_counterpart",
            "same_subtype_key_with_counterpart",
            "candidate_contrast_numeric_present",
            "candidate_contrast_numeric_log1p",
            "counterpart_contrast_numeric_present",
            "counterpart_contrast_numeric_log1p",
            "contrast_numeric_abs_delta_log1p",
            "contrast_numeric_signed_delta",
            "candidate_contrast_is_larger",
            "candidate_contrast_is_smaller",
            "candidate_action_present",
            "candidate_action_part_count",
            "same_action_with_counterpart",
            "candidate_material_present",
            "candidate_material_part_count",
            "same_material_with_counterpart",
            "candidate_connection_present",
            "candidate_connection_part_count",
            "same_connection_with_counterpart",
            "candidate_install_method_present",
            "candidate_install_method_part_count",
            "same_install_method_with_counterpart",
            "candidate_param_type_present",
            "candidate_param_type_part_count",
            "same_param_type_with_counterpart",
            "candidate_action_hash",
            "candidate_material_hash",
            "candidate_connection_hash",
            "candidate_install_method_hash",
            "candidate_param_type_hash",
        ]
    )
    return feature_names


def _numeric_features(row: dict[str, Any], pair_row: dict[str, Any] | None, family_values: list[str], contrast_values: list[str]) -> dict[str, float]:
    role = _clean(row.get("row_in_group"))
    candidate_name = _clean(row.get("quota_name"))
    counterpart_name = _clean(row.get("counterpart_quota_name"))
    current_contrast = _as_float(row.get("contrast_value"))
    other_contrast = _as_float(row.get("counterpart_contrast_value"))
    contrast_delta = None
    if current_contrast is not None and other_contrast is not None:
        contrast_delta = current_contrast - other_contrast

    current_book = _clean(row.get("book")) or _field_from_pair(pair_row, role, "book")
    other_book = _other_field_from_pair(pair_row, role, "book")
    current_chapter = _clean(row.get("chapter")) or _field_from_pair(pair_row, role, "chapter")
    other_chapter = _other_field_from_pair(pair_row, role, "chapter")
    current_unit = _clean(row.get("unit")) or _field_from_pair(pair_row, role, "unit")
    other_unit = _other_field_from_pair(pair_row, role, "unit")
    current_subtype = _clean(row.get("subtype_key"))
    other_subtype = _clean(row.get("counterpart_subtype_key"))

    current_book_number = _first_number(current_book)
    current_chapter_number = _first_number(current_chapter)

    action = _field_from_pair(pair_row, role, "action")
    other_action = _other_field_from_pair(pair_row, role, "action")
    material = _field_from_pair(pair_row, role, "material")
    other_material = _other_field_from_pair(pair_row, role, "material")
    connection = _field_from_pair(pair_row, role, "connection")
    other_connection = _other_field_from_pair(pair_row, role, "connection")
    install_method = _field_from_pair(pair_row, role, "install_method")
    other_install_method = _other_field_from_pair(pair_row, role, "install_method")
    param_type = _field_from_pair(pair_row, role, "param_type")
    other_param_type = _other_field_from_pair(pair_row, role, "param_type")

    features: dict[str, float] = {
        "pair_type_is_param_contrast": _num(_clean(row.get("pair_type")) == "param_contrast"),
        "pair_type_is_subtype_contrast": _num(_clean(row.get("pair_type")) == "subtype_contrast"),
        "training_mode_is_both": _num(_clean(row.get("training_mode")) == "both"),
        "training_mode_is_subtype_only": _num(_clean(row.get("training_mode")) == "subtype_only"),
    }
    family = _clean(row.get("family"))
    for value in family_values:
        features[f"family_is_{_safe_name(value)}"] = _num(family == value)
    contrast_field = _clean(row.get("contrast_field"))
    for value in contrast_values:
        features[f"contrast_field_is_{_safe_name(value)}"] = _num(contrast_field == value)

    digit_count = sum(1 for char in candidate_name if char.isdigit())
    param_marker_count = len(PARAM_MARKER_RE.findall(candidate_name))
    features.update(
        {
            "candidate_name_length": _num(len(candidate_name)),
            "candidate_name_token_count": _num(_token_count(candidate_name)),
            "candidate_name_digit_count": _num(digit_count),
            "candidate_name_has_digits": _num(digit_count > 0),
            "candidate_name_param_marker_count": _num(param_marker_count),
            "counterpart_name_length": _num(len(counterpart_name)),
            "candidate_name_length_signed_delta": _num(len(candidate_name) - len(counterpart_name)),
            "counterpart_name_token_jaccard": _num(_jaccard(candidate_name, counterpart_name)),
            "unit_present": _num(bool(current_unit)),
            "book_present": _num(bool(current_book)),
            "chapter_present": _num(bool(current_chapter)),
            "candidate_book_number_present": _num(current_book_number is not None),
            "candidate_book_number_log1p": _log1p_abs(current_book_number),
            "candidate_chapter_number_present": _num(current_chapter_number is not None),
            "candidate_chapter_number_log1p": _log1p_abs(current_chapter_number),
            "same_book_with_counterpart": _num(_same(current_book, other_book)),
            "same_chapter_with_counterpart": _num(_same(current_chapter, other_chapter)),
            "same_unit_with_counterpart": _num(_same(current_unit, other_unit)),
            "same_subtype_key_with_counterpart": _num(_same(current_subtype, other_subtype)),
            "candidate_contrast_numeric_present": _num(current_contrast is not None),
            "candidate_contrast_numeric_log1p": _log1p_abs(current_contrast),
            "counterpart_contrast_numeric_present": _num(other_contrast is not None),
            "counterpart_contrast_numeric_log1p": _log1p_abs(other_contrast),
            "contrast_numeric_abs_delta_log1p": _log1p_abs(contrast_delta),
            "contrast_numeric_signed_delta": _num(contrast_delta),
            "candidate_contrast_is_larger": _num(contrast_delta is not None and contrast_delta > 0),
            "candidate_contrast_is_smaller": _num(contrast_delta is not None and contrast_delta < 0),
            "candidate_action_present": _num(bool(action)),
            "candidate_action_part_count": _num(len(_parts(action))),
            "same_action_with_counterpart": _num(_same(action, other_action)),
            "candidate_material_present": _num(bool(material)),
            "candidate_material_part_count": _num(len(_parts(material))),
            "same_material_with_counterpart": _num(_same(material, other_material)),
            "candidate_connection_present": _num(bool(connection)),
            "candidate_connection_part_count": _num(len(_parts(connection))),
            "same_connection_with_counterpart": _num(_same(connection, other_connection)),
            "candidate_install_method_present": _num(bool(install_method)),
            "candidate_install_method_part_count": _num(len(_parts(install_method))),
            "same_install_method_with_counterpart": _num(_same(install_method, other_install_method)),
            "candidate_param_type_present": _num(bool(param_type)),
            "candidate_param_type_part_count": _num(len(_parts(param_type))),
            "same_param_type_with_counterpart": _num(_same(param_type, other_param_type)),
            "candidate_action_hash": _stable_hash_unit(action),
            "candidate_material_hash": _stable_hash_unit(material),
            "candidate_connection_hash": _stable_hash_unit(connection),
            "candidate_install_method_hash": _stable_hash_unit(install_method),
            "candidate_param_type_hash": _stable_hash_unit(param_type),
        }
    )
    return features


def _group_matrix_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current_group = ""
    current_rows: list[dict[str, Any]] = []
    for row in rows:
        group_id = _clean(row.get("group_id"))
        if not current_group:
            current_group = group_id
        elif group_id != current_group:
            groups.append(current_rows)
            current_group = group_id
            current_rows = []
        current_rows.append(row)
    if current_rows:
        groups.append(current_rows)
    return groups


def _build_numeric_matrix(
    matrix_rows: list[dict[str, Any]],
    pair_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int], list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, Any]]:
    pair_lookup = _build_pair_lookup(pair_rows)
    family_values = _category_values(matrix_rows, "family")
    contrast_values = _category_values(matrix_rows, "contrast_field")
    feature_names = _base_feature_names(matrix_rows)
    groups = _group_matrix_rows(matrix_rows)

    numeric_rows: list[dict[str, Any]] = []
    group_sizes: list[int] = []
    group_meta: list[dict[str, Any]] = []
    group_audit: list[dict[str, Any]] = []
    missing_pair_rows = 0
    invalid_groups = 0
    duplicate_noncontiguous = 0
    seen_finished: set[str] = set()

    for group in groups:
        group_id = _clean(group[0].get("group_id")) if group else ""
        if group_id in seen_finished:
            duplicate_noncontiguous += 1
        seen_finished.add(group_id)
        labels = [_clean(row.get("label")) for row in group]
        roles = [_clean(row.get("row_in_group")) for row in group]
        ok = len(group) == 2 and sorted(labels) == ["0", "1"] and sorted(roles) == ["negative", "positive"]
        if not ok:
            invalid_groups += 1

        positive_row = next((row for row in group if _clean(row.get("label")) == "1"), group[0] if group else {})
        negative_row = next((row for row in group if _clean(row.get("label")) == "0"), group[-1] if group else {})
        group_sizes.append(len(group))
        group_meta.append(
            {
                "group_id": group_id,
                "rows": len(group),
                "positive_count": labels.count("1"),
                "family": _clean(positive_row.get("family")),
                "province": _clean(positive_row.get("province")),
                "pair_type": _clean(positive_row.get("pair_type")),
                "training_mode": _clean(positive_row.get("training_mode")),
                "positive_id": _clean(positive_row.get("quota_id")),
                "negative_id": _clean(negative_row.get("quota_id")),
                "source_pair_id": _clean(positive_row.get("source_pair_id")),
            }
        )
        group_audit.append(
            {
                "group_id": group_id,
                "rows": len(group),
                "labels": "|".join(labels),
                "roles": "|".join(roles),
                "is_valid_pair_group": str(ok).lower(),
                "family": _clean(positive_row.get("family")),
                "pair_type": _clean(positive_row.get("pair_type")),
            }
        )

        for row in group:
            pair_key = _clean(row.get("source_pair_id")) or group_id
            pair_row = pair_lookup.get(pair_key) or pair_lookup.get(group_id)
            if not pair_row:
                missing_pair_rows += 1
            features = _numeric_features(row, pair_row, family_values, contrast_values)
            numeric_row: dict[str, Any] = {"label": int(_clean(row.get("label")) or 0)}
            for feature in feature_names:
                numeric_row[feature] = round(_num(features.get(feature)), 8)
            numeric_rows.append(numeric_row)

    audit_summary = {
        "invalid_groups": invalid_groups,
        "duplicate_noncontiguous_groups": duplicate_noncontiguous,
        "missing_pair_join_rows": missing_pair_rows,
        "family_categories": len(family_values),
        "contrast_field_categories": len(contrast_values),
    }
    return numeric_rows, group_sizes, group_meta, group_audit, feature_names, audit_summary


def _feature_coverage(rows: list[dict[str, Any]], feature_names: list[str], selected_features: set[str]) -> list[dict[str, Any]]:
    total = len(rows)
    result: list[dict[str, Any]] = []
    for feature in feature_names:
        nonzero = 0
        values: list[float] = []
        for row in rows:
            value = _num(row.get(feature))
            values.append(value)
            if value != 0:
                nonzero += 1
        minimum = min(values) if values else 0.0
        maximum = max(values) if values else 0.0
        mean = sum(values) / len(values) if values else 0.0
        result.append(
            {
                "feature": feature,
                "selected_for_training": str(feature in selected_features).lower(),
                "rows": total,
                "nonzero_rows": nonzero,
                "nonzero_rate": _rate(nonzero, total),
                "min": round(minimum, 8),
                "max": round(maximum, 8),
                "mean": round(mean, 8),
                "is_zero_variance": str(minimum == maximum).lower(),
            }
        )
    return result


def _zero_variance_features(rows: list[dict[str, Any]], feature_names: list[str]) -> list[str]:
    result: list[str] = []
    for feature in feature_names:
        values = {_num(row.get(feature)) for row in rows}
        if len(values) <= 1:
            result.append(feature)
    return result


def _diagnostic_leaks(feature_names: list[str]) -> list[str]:
    leaks: list[str] = []
    for feature in feature_names:
        lower = feature.lower()
        if any(token in lower for token in DIAGNOSTIC_FORBIDDEN_SUBSTRINGS):
            leaks.append(feature)
    return leaks


def _validate_loader(output_dir: Path, split: str, whitelist_path: Path) -> dict[str, Any]:
    try:
        from tools.goal_train_ltr import _load_feature_whitelist, _load_matrix
    except Exception as exc:  # pragma: no cover - environment guard for dry-run report.
        return {
            "loader_import_ok": False,
            "loader_matrix_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        features = _load_feature_whitelist(whitelist_path)
        matrix, labels, groups, meta = _load_matrix(output_dir, split, features)
    except Exception as exc:  # pragma: no cover - surfaced in report.
        return {
            "loader_import_ok": True,
            "loader_matrix_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "loader_import_ok": True,
        "loader_matrix_ok": True,
        "features_loaded": len(features),
        "matrix_shape_rows": int(matrix.shape[0]),
        "matrix_shape_cols": int(matrix.shape[1]),
        "labels": int(len(labels)),
        "groups": int(len(groups)),
        "group_sum": int(sum(groups)),
        "meta_rows": int(len(meta)),
        "label_counts": {str(key): int(value) for key, value in Counter(labels.tolist()).items()},
    }


def _write_whitelist(path: Path, feature_names: list[str], feature_plan: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "stage": "Goal LTR v1 / stage 5.9 quota self-supervised numeric matrix dry-run",
        "eval_only": True,
        "no_training": True,
        "training_features": feature_names,
        "label_column": "label",
        "group_file": "ltr_group_quota_selfsup.txt",
        "group_meta_file": "ltr_group_quota_selfsup.jsonl",
        "source_feature_mapping_plan": feature_plan["source_path"],
        "excluded_diagnostic_columns": [
            "group_id",
            "row_in_group",
            "sample_source",
            "training_role",
            "selection_stage",
            "province",
            "quota_id",
            "quota_name",
            "counterpart_quota_id",
            "counterpart_quota_name",
            "source_pair_id",
            "source_db_path",
        ],
        "notes": [
            "Generated from stage 5.8 allowed mapping statuses only.",
            "Search-context features are intentionally absent.",
            "Raw identifiers, raw text, province, and source metadata are excluded from training_features.",
            "Zero-variance numeric columns are kept in the matrix audit but excluded from training_features.",
        ],
    }
    _write_json(path, payload)
    return payload


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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    loader = summary["loader_validation"]
    sparse_rows = [["feature", "nonzero_rate"]]
    for item in summary["near_zero_features"][:20]:
        sparse_rows.append([item["feature"], item["nonzero_rate"]])
    zero_variance_rows = [["feature"]]
    for feature in summary["zero_variance_features"][:20]:
        zero_variance_rows.append([feature])

    lines = [
        "# Goal Self-Supervised Numeric Matrix Dry Run",
        "",
        "Stage 5.9 eval-only dry run. It writes a numeric matrix and feature whitelist that the existing LTR loader can read, without training or changing ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["matrix_rows", summary["matrix_rows"]],
                ["groups", summary["groups"]],
                ["matrix_numeric_feature_columns", summary["matrix_numeric_feature_columns"]],
                ["feature_count", summary["feature_count"]],
                ["zero_variance_feature_count", summary["zero_variance_feature_count"]],
                ["invalid_groups", summary["invalid_groups"]],
                ["missing_pair_join_rows", summary["missing_pair_join_rows"]],
                ["diagnostic_feature_leaks", summary["diagnostic_feature_leaks"]],
                ["loader_matrix_ok", loader.get("loader_matrix_ok")],
                ["loader_rows", loader.get("matrix_shape_rows", "")],
                ["loader_cols", loader.get("matrix_shape_cols", "")],
                ["passes_numeric_dry_run_gate", summary["passes_numeric_dry_run_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Near-Zero Features",
        "",
        _md_table(sparse_rows),
        "",
        "## Zero-Variance Excluded From Whitelist",
        "",
        _md_table(zero_variance_rows),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["matrix_csv", report["artifacts"]["matrix_csv"]],
                ["group_txt", report["artifacts"]["group_txt"]],
                ["group_meta_jsonl", report["artifacts"]["group_meta_jsonl"]],
                ["feature_whitelist_json", report["artifacts"]["feature_whitelist_json"]],
                ["feature_coverage_csv", report["artifacts"]["feature_coverage_csv"]],
                ["group_audit_csv", report["artifacts"]["group_audit_csv"]],
                ["summary_json", report["artifacts"]["summary_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.9 eval-only numeric matrix dry-run for quota self-supervised pairs")
    parser.add_argument("--matrix-draft-csv", default=str(DEFAULT_MATRIX_DRAFT_CSV))
    parser.add_argument("--pair-whitelist-csv", default=str(DEFAULT_PAIR_WHITELIST_CSV))
    parser.add_argument("--feature-plan-csv", default=str(DEFAULT_FEATURE_PLAN_CSV))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--near-zero-threshold", type=float, default=0.005)
    args = parser.parse_args()

    started = time.perf_counter()
    output_dir = Path(args.output_dir)
    split = _safe_name(args.split)
    matrix_path = output_dir / f"ltr_matrix_{split}.csv"
    group_path = output_dir / f"ltr_group_{split}.txt"
    group_meta_path = output_dir / f"ltr_group_{split}.jsonl"
    whitelist_path = output_dir / f"ltr_feature_whitelist_{split}.json"
    feature_coverage_path = output_dir / "feature_coverage.csv"
    group_audit_path = output_dir / "group_audit.csv"

    matrix_rows = _read_csv(Path(args.matrix_draft_csv))
    pair_rows = _read_csv(Path(args.pair_whitelist_csv))
    feature_plan = _load_allowed_plan(Path(args.feature_plan_csv))

    numeric_rows, groups, group_meta, group_audit, feature_names, audit_summary = _build_numeric_matrix(matrix_rows, pair_rows)
    zero_variance = _zero_variance_features(numeric_rows, feature_names)
    selected_feature_names = [feature for feature in feature_names if feature not in set(zero_variance)]
    coverage_rows = _feature_coverage(numeric_rows, feature_names, set(selected_feature_names))
    diagnostic_leaks = _diagnostic_leaks(selected_feature_names)

    _write_csv(matrix_path, numeric_rows, ["label", *feature_names])
    group_path.parent.mkdir(parents=True, exist_ok=True)
    group_path.write_text("\n".join(str(size) for size in groups) + "\n", encoding="utf-8")
    _write_jsonl(group_meta_path, group_meta)
    whitelist_payload = _write_whitelist(whitelist_path, selected_feature_names, feature_plan)
    _write_csv(feature_coverage_path, coverage_rows, ["feature", "selected_for_training", "rows", "nonzero_rows", "nonzero_rate", "min", "max", "mean", "is_zero_variance"])
    _write_csv(group_audit_path, group_audit, ["group_id", "rows", "labels", "roles", "is_valid_pair_group", "family", "pair_type"])

    loader_validation = _validate_loader(output_dir, split, whitelist_path)
    near_zero = [row for row in coverage_rows if float(row["nonzero_rate"]) <= args.near_zero_threshold]
    label_counts = Counter(str(row["label"]) for row in numeric_rows)
    group_size_counts = Counter(str(size) for size in groups)
    passes_gate = (
        len(numeric_rows) == len(matrix_rows)
        and len(groups) == len(pair_rows)
        and sum(groups) == len(numeric_rows)
        and set(groups) == {2}
        and audit_summary["invalid_groups"] == 0
        and audit_summary["duplicate_noncontiguous_groups"] == 0
        and audit_summary["missing_pair_join_rows"] == 0
        and not diagnostic_leaks
        and bool(loader_validation.get("loader_matrix_ok"))
        and loader_validation.get("matrix_shape_rows") == len(numeric_rows)
        and loader_validation.get("matrix_shape_cols") == len(selected_feature_names)
    )

    summary = {
        "matrix_rows": len(numeric_rows),
        "groups": len(groups),
        "group_sum": sum(groups),
        "matrix_numeric_feature_columns": len(feature_names),
        "feature_count": len(selected_feature_names),
        "zero_variance_feature_count": len(zero_variance),
        "zero_variance_features": zero_variance,
        "label_counts": dict(label_counts),
        "group_size_counts": dict(group_size_counts),
        "invalid_groups": audit_summary["invalid_groups"],
        "duplicate_noncontiguous_groups": audit_summary["duplicate_noncontiguous_groups"],
        "missing_pair_join_rows": audit_summary["missing_pair_join_rows"],
        "family_categories": audit_summary["family_categories"],
        "contrast_field_categories": audit_summary["contrast_field_categories"],
        "diagnostic_feature_leaks": len(diagnostic_leaks),
        "diagnostic_feature_leak_names": diagnostic_leaks,
        "near_zero_threshold": args.near_zero_threshold,
        "near_zero_feature_count": len(near_zero),
        "near_zero_features": near_zero[:30],
        "loader_validation": loader_validation,
        "feature_whitelist_feature_count": len(whitelist_payload["training_features"]),
        "feature_plan_allowed_defs": len(feature_plan["allowed_feature_defs"]),
        "feature_plan_excluded_defs": len(feature_plan["excluded_feature_defs"]),
        "passes_numeric_dry_run_gate": passes_gate,
        "recommended_next_stage": "Stage 6.0 eval-only self-supervised pretrain experiment on this dry-run matrix, still not wired into search.",
    }
    report = {
        "stage": "Goal LTR v1 / stage 5.9 quota self-supervised numeric matrix dry-run",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "matrix_draft_csv": str(Path(args.matrix_draft_csv)),
        "pair_whitelist_csv": str(Path(args.pair_whitelist_csv)),
        "feature_plan_csv": str(Path(args.feature_plan_csv)),
        "summary": summary,
        "artifacts": {
            "output_dir": str(output_dir),
            "matrix_csv": str(matrix_path),
            "group_txt": str(group_path),
            "group_meta_jsonl": str(group_meta_path),
            "feature_whitelist_json": str(whitelist_path),
            "feature_coverage_csv": str(feature_coverage_path),
            "group_audit_csv": str(group_audit_path),
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
                    "matrix_rows": summary["matrix_rows"],
                    "groups": summary["groups"],
                    "feature_count": summary["feature_count"],
                    "invalid_groups": summary["invalid_groups"],
                    "missing_pair_join_rows": summary["missing_pair_join_rows"],
                    "diagnostic_feature_leaks": summary["diagnostic_feature_leaks"],
                    "loader_matrix_ok": loader_validation.get("loader_matrix_ok"),
                    "passes_numeric_dry_run_gate": summary["passes_numeric_dry_run_gate"],
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
