from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_PAIR_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.csv"
DEFAULT_TASK_REDEFINITION_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_summary.json"
DEFAULT_LABEL_DIRECTION_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_summary.json"
DEFAULT_SCHEMA_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_schema.json"
DEFAULT_FEATURE_SCHEMA_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_feature_schema.csv"
DEFAULT_LABEL_SCHEMA_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_label_schema.csv"
DEFAULT_COVERAGE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_coverage.csv"
DEFAULT_SAMPLE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_samples.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_schema_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_undirected_contrast_schema_summary.md"

TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
PARAM_MARKER_RE = re.compile(r"(DN|mm2|mm|m2|m3|<=|>=|<|>|≤|≥|\d+)", re.IGNORECASE)

FeatureFn = Callable[[dict[str, Any], bool], Any]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["available"] = True
    payload["path"] = str(path)
    return payload


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _side(row: dict[str, Any], swapped: bool, field: str, left: bool) -> str:
    if swapped:
        left = not left
    prefix = "positive" if left else "negative"
    return _clean(row.get(f"{prefix}_{field}"))


def _signal(row: dict[str, Any], swapped: bool, field: str, left: bool) -> str:
    if swapped:
        left = not left
    prefix = "positive" if left else "negative"
    return _clean(row.get(f"{prefix}_{field}"))


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(value or "")}


def _jaccard(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _parts(value: str) -> set[str]:
    return {part for part in (_clean(part) for part in value.split("|")) if part}


def _part_jaccard(left: str, right: str) -> float:
    left_parts = _parts(left)
    right_parts = _parts(right)
    union = left_parts | right_parts
    if not union:
        return 0.0
    return len(left_parts & right_parts) / len(union)


def _as_float(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        match = NUMBER_RE.search(text)
        if not match:
            return None
        try:
            number = float(match.group(0))
        except ValueError:
            return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _first_number(value: str) -> float | None:
    match = NUMBER_RE.search(_clean(value))
    if not match:
        return None
    return _as_float(match.group(0))


def _log1p_abs(value: float | None) -> float:
    if value is None:
        return 0.0
    return math.log1p(abs(value))


def _same_text(left: str, right: str) -> int:
    return int(bool(left and right and left == right))


def _text_conflict(left: str, right: str) -> int:
    return int(bool(left and right and left != right))


def _numeric_pair(row: dict[str, Any], swapped: bool) -> tuple[float | None, float | None]:
    left = _as_float(_side(row, swapped, "contrast_value", True))
    right = _as_float(_side(row, swapped, "contrast_value", False))
    return left, right


def _numeric_abs_delta(row: dict[str, Any], swapped: bool) -> float:
    left, right = _numeric_pair(row, swapped)
    if left is None or right is None:
        return 0.0
    return abs(left - right)


def _numeric_ratio_gap(row: dict[str, Any], swapped: bool) -> float:
    left, right = _numeric_pair(row, swapped)
    if left is None or right is None:
        return 0.0
    denominator = max(abs(left), abs(right), 1.0)
    return abs(left - right) / denominator


def _bool(value: bool) -> int:
    return 1 if value else 0


def _field_defs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, FeatureFn]]:
    feature_defs = [
        ("family_code", "categorical_context", "string", ["family"], "Object family; categorical source, not a ranking label."),
        ("pair_type_code", "categorical_context", "string", ["pair_type"], "param_contrast or subtype_contrast."),
        ("contrast_field_code", "categorical_context", "string", ["contrast_field"], "The contrasted field, such as dn or subtype_key."),
        ("same_unit", "pair_consistency", "binary", ["positive_unit", "negative_unit"], "Whether both candidates have the same nonempty unit."),
        ("same_book", "pair_consistency", "binary", ["positive_book", "negative_book"], "Whether both candidates have the same nonempty book."),
        ("same_chapter", "pair_consistency", "binary", ["positive_chapter", "negative_chapter"], "Whether both candidates have the same nonempty chapter."),
        ("same_subtype_key", "subtype_contrast", "binary", ["positive_subtype_key", "negative_subtype_key"], "Whether subtype_key is identical."),
        ("subtype_part_jaccard", "subtype_contrast", "float", ["positive_subtype_key", "negative_subtype_key"], "Jaccard over subtype_key parts."),
        ("subtype_part_count_abs_delta", "subtype_contrast", "float", ["positive_subtype_key", "negative_subtype_key"], "Absolute difference in subtype part counts."),
        ("name_token_jaccard", "candidate_text", "float", ["positive_name", "negative_name"], "Jaccard over candidate name tokens."),
        ("name_length_abs_delta", "candidate_text", "float", ["positive_name", "negative_name"], "Absolute name length difference."),
        ("name_digit_count_abs_delta", "candidate_text", "float", ["positive_name", "negative_name"], "Absolute digit count difference."),
        ("name_param_marker_abs_delta", "candidate_text", "float", ["positive_name", "negative_name"], "Absolute DN/mm/number marker count difference."),
        ("both_names_have_digits", "candidate_text", "binary", ["positive_name", "negative_name"], "Whether both candidate names include digits."),
        ("same_action", "signal_consistency", "binary", ["positive_action", "negative_action"], "Whether extracted action is identical and nonempty."),
        ("same_material", "signal_consistency", "binary", ["positive_material", "negative_material"], "Whether extracted material is identical and nonempty."),
        ("same_connection", "signal_consistency", "binary", ["positive_connection", "negative_connection"], "Whether extracted connection is identical and nonempty."),
        ("same_install_method", "signal_consistency", "binary", ["positive_install_method", "negative_install_method"], "Whether extracted install method is identical and nonempty."),
        ("same_param_type", "signal_consistency", "binary", ["positive_param_type", "negative_param_type"], "Whether extracted param type is identical and nonempty."),
        ("param_value_text_match", "param_contrast", "binary", ["positive_contrast_value", "negative_contrast_value"], "Whether raw contrast values match."),
        ("param_numeric_both_present", "param_contrast", "binary", ["positive_contrast_value", "negative_contrast_value"], "Whether both contrast values parse as numbers."),
        ("param_numeric_abs_delta_log1p", "param_contrast", "float", ["positive_contrast_value", "negative_contrast_value"], "log1p(abs numeric contrast delta)."),
        ("param_numeric_ratio_gap", "param_contrast", "float", ["positive_contrast_value", "negative_contrast_value"], "Normalized absolute numeric gap."),
        ("book_number_abs_delta_log1p", "book_chapter_unit", "float", ["positive_book", "negative_book"], "log1p(abs book number delta) if numbers exist."),
        ("chapter_number_abs_delta_log1p", "book_chapter_unit", "float", ["positive_chapter", "negative_chapter"], "log1p(abs chapter number delta) if numbers exist."),
    ]
    label_defs = [
        ("contrast_label_any_conflict", "conflict_label", "binary", "Any unit/chapter/subtype/signal/param conflict; not a ranking target."),
        ("contrast_label_param_value_conflict", "conflict_label", "binary", "Raw contrast values differ; not a ranking target."),
        ("contrast_label_param_numeric_gap", "conflict_label", "binary", "Both values numeric and not equal; not a ranking target."),
        ("contrast_label_subtype_conflict", "conflict_label", "binary", "Subtype keys differ; not a ranking target."),
        ("contrast_label_action_conflict", "conflict_label", "binary", "Actions differ when both present; not a ranking target."),
        ("contrast_label_material_conflict", "conflict_label", "binary", "Materials differ when both present; not a ranking target."),
        ("contrast_label_connection_conflict", "conflict_label", "binary", "Connections differ when both present; not a ranking target."),
        ("contrast_label_install_method_conflict", "conflict_label", "binary", "Install methods differ when both present; not a ranking target."),
        ("contrast_label_param_type_conflict", "conflict_label", "binary", "Param types differ when both present; not a ranking target."),
        ("contrast_label_unit_conflict", "conflict_label", "binary", "Units differ when both present; not a ranking target."),
        ("contrast_label_chapter_conflict", "conflict_label", "binary", "Chapters differ when both present; not a ranking target."),
        ("contrast_label_book_conflict", "conflict_label", "binary", "Books differ when both present; not a ranking target."),
    ]

    def name_digit_count(row: dict[str, Any], swapped: bool, left: bool) -> int:
        return sum(1 for ch in _side(row, swapped, "name", left) if ch.isdigit())

    def marker_count(row: dict[str, Any], swapped: bool, left: bool) -> int:
        return len(PARAM_MARKER_RE.findall(_side(row, swapped, "name", left)))

    functions: dict[str, FeatureFn] = {
        "family_code": lambda row, swapped: _clean(row.get("family")),
        "pair_type_code": lambda row, swapped: _clean(row.get("pair_type")),
        "contrast_field_code": lambda row, swapped: _clean(row.get("contrast_field")),
        "same_unit": lambda row, swapped: _same_text(_side(row, swapped, "unit", True), _side(row, swapped, "unit", False)),
        "same_book": lambda row, swapped: _same_text(_side(row, swapped, "book", True), _side(row, swapped, "book", False)),
        "same_chapter": lambda row, swapped: _same_text(_side(row, swapped, "chapter", True), _side(row, swapped, "chapter", False)),
        "same_subtype_key": lambda row, swapped: _same_text(_side(row, swapped, "subtype_key", True), _side(row, swapped, "subtype_key", False)),
        "subtype_part_jaccard": lambda row, swapped: _part_jaccard(_side(row, swapped, "subtype_key", True), _side(row, swapped, "subtype_key", False)),
        "subtype_part_count_abs_delta": lambda row, swapped: abs(len(_parts(_side(row, swapped, "subtype_key", True))) - len(_parts(_side(row, swapped, "subtype_key", False)))),
        "name_token_jaccard": lambda row, swapped: _jaccard(_side(row, swapped, "name", True), _side(row, swapped, "name", False)),
        "name_length_abs_delta": lambda row, swapped: abs(len(_side(row, swapped, "name", True)) - len(_side(row, swapped, "name", False))),
        "name_digit_count_abs_delta": lambda row, swapped: abs(name_digit_count(row, swapped, True) - name_digit_count(row, swapped, False)),
        "name_param_marker_abs_delta": lambda row, swapped: abs(marker_count(row, swapped, True) - marker_count(row, swapped, False)),
        "both_names_have_digits": lambda row, swapped: _bool(name_digit_count(row, swapped, True) > 0 and name_digit_count(row, swapped, False) > 0),
        "same_action": lambda row, swapped: _same_text(_signal(row, swapped, "action", True), _signal(row, swapped, "action", False)),
        "same_material": lambda row, swapped: _same_text(_signal(row, swapped, "material", True), _signal(row, swapped, "material", False)),
        "same_connection": lambda row, swapped: _same_text(_signal(row, swapped, "connection", True), _signal(row, swapped, "connection", False)),
        "same_install_method": lambda row, swapped: _same_text(_signal(row, swapped, "install_method", True), _signal(row, swapped, "install_method", False)),
        "same_param_type": lambda row, swapped: _same_text(_signal(row, swapped, "param_type", True), _signal(row, swapped, "param_type", False)),
        "param_value_text_match": lambda row, swapped: _same_text(_side(row, swapped, "contrast_value", True), _side(row, swapped, "contrast_value", False)),
        "param_numeric_both_present": lambda row, swapped: _bool(_numeric_pair(row, swapped)[0] is not None and _numeric_pair(row, swapped)[1] is not None),
        "param_numeric_abs_delta_log1p": lambda row, swapped: _log1p_abs(_numeric_abs_delta(row, swapped)),
        "param_numeric_ratio_gap": lambda row, swapped: _numeric_ratio_gap(row, swapped),
        "book_number_abs_delta_log1p": lambda row, swapped: _number_abs_delta_log1p(_side(row, swapped, "book", True), _side(row, swapped, "book", False)),
        "chapter_number_abs_delta_log1p": lambda row, swapped: _number_abs_delta_log1p(_side(row, swapped, "chapter", True), _side(row, swapped, "chapter", False)),
    }
    feature_rows = [
        {
            "field_name": name,
            "field_group": group,
            "dtype": dtype,
            "role": "order_invariant_feature",
            "order_invariant": "true",
            "source_fields": "|".join(source_fields),
            "notes": notes,
        }
        for name, group, dtype, source_fields, notes in feature_defs
    ]
    label_rows = [
        {
            "field_name": name,
            "field_group": group,
            "dtype": dtype,
            "role": "contrast_label_not_ranking_target",
            "order_invariant": "true",
            "source_fields": "computed_from_candidate_a_b",
            "notes": notes,
        }
        for name, group, dtype, notes in label_defs
    ]
    return feature_rows, label_rows, functions


def _number_abs_delta_log1p(left: str, right: str) -> float:
    left_number = _first_number(left)
    right_number = _first_number(right)
    if left_number is None or right_number is None:
        return 0.0
    return math.log1p(abs(left_number - right_number))


def _label_values(row: dict[str, Any], swapped: bool) -> dict[str, int]:
    param_text_conflict = _text_conflict(_side(row, swapped, "contrast_value", True), _side(row, swapped, "contrast_value", False))
    numeric_gap = int(_numeric_abs_delta(row, swapped) > 0)
    subtype_conflict = _text_conflict(_side(row, swapped, "subtype_key", True), _side(row, swapped, "subtype_key", False))
    action_conflict = _text_conflict(_signal(row, swapped, "action", True), _signal(row, swapped, "action", False))
    material_conflict = _text_conflict(_signal(row, swapped, "material", True), _signal(row, swapped, "material", False))
    connection_conflict = _text_conflict(_signal(row, swapped, "connection", True), _signal(row, swapped, "connection", False))
    install_method_conflict = _text_conflict(_signal(row, swapped, "install_method", True), _signal(row, swapped, "install_method", False))
    param_type_conflict = _text_conflict(_signal(row, swapped, "param_type", True), _signal(row, swapped, "param_type", False))
    unit_conflict = _text_conflict(_side(row, swapped, "unit", True), _side(row, swapped, "unit", False))
    chapter_conflict = _text_conflict(_side(row, swapped, "chapter", True), _side(row, swapped, "chapter", False))
    book_conflict = _text_conflict(_side(row, swapped, "book", True), _side(row, swapped, "book", False))
    any_conflict = int(
        any(
            [
                param_text_conflict,
                numeric_gap,
                subtype_conflict,
                action_conflict,
                material_conflict,
                connection_conflict,
                install_method_conflict,
                param_type_conflict,
                unit_conflict,
                chapter_conflict,
                book_conflict,
            ]
        )
    )
    return {
        "contrast_label_any_conflict": any_conflict,
        "contrast_label_param_value_conflict": param_text_conflict,
        "contrast_label_param_numeric_gap": numeric_gap,
        "contrast_label_subtype_conflict": subtype_conflict,
        "contrast_label_action_conflict": action_conflict,
        "contrast_label_material_conflict": material_conflict,
        "contrast_label_connection_conflict": connection_conflict,
        "contrast_label_install_method_conflict": install_method_conflict,
        "contrast_label_param_type_conflict": param_type_conflict,
        "contrast_label_unit_conflict": unit_conflict,
        "contrast_label_chapter_conflict": chapter_conflict,
        "contrast_label_book_conflict": book_conflict,
    }


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return True


def _is_nonzero(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return bool(value)


def _equal_values(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        try:
            return abs(float(left) - float(right)) <= 1e-12
        except (TypeError, ValueError):
            return False
    return left == right


def _coverage(rows: list[dict[str, Any]], field_defs: list[dict[str, Any]], functions: dict[str, FeatureFn], is_label: bool = False) -> tuple[list[dict[str, Any]], int]:
    total = len(rows)
    coverage_rows: list[dict[str, Any]] = []
    total_violations = 0
    for field in field_defs:
        name = field["field_name"]
        nonempty = 0
        nonzero = 0
        violations = 0
        values = Counter()
        for row in rows:
            value = _label_values(row, False)[name] if is_label else functions[name](row, False)
            swapped_value = _label_values(row, True)[name] if is_label else functions[name](row, True)
            if not _equal_values(value, swapped_value):
                violations += 1
            if _is_nonempty(value):
                nonempty += 1
                values[str(value)] += 1
            if _is_nonzero(value):
                nonzero += 1
        total_violations += violations
        coverage_rows.append(
            {
                "field_name": name,
                "field_kind": "contrast_label" if is_label else "feature",
                "field_group": field["field_group"],
                "dtype": field["dtype"],
                "rows": total,
                "covered_rows": nonempty,
                "coverage_rate": _rate(nonempty, total),
                "nonzero_rows": nonzero,
                "nonzero_rate": _rate(nonzero, total),
                "order_invariant_violations": violations,
                "top_values": "; ".join(f"{key}:{count}" for key, count in values.most_common(6)),
            }
        )
    return coverage_rows, total_violations


def _candidate_pair_id(row: dict[str, Any]) -> str:
    ids = sorted([_clean(row.get("positive_id")), _clean(row.get("negative_id"))])
    return "|".join(ids)


def _samples(rows: list[dict[str, Any]], functions: dict[str, FeatureFn], limit: int) -> list[dict[str, Any]]:
    sample_rows: list[dict[str, Any]] = []
    for row in rows[:limit]:
        labels = _label_values(row, False)
        sample_rows.append(
            {
                "contrast_pair_id": _candidate_pair_id(row),
                "family": _clean(row.get("family")),
                "pair_type": _clean(row.get("pair_type")),
                "contrast_field": _clean(row.get("contrast_field")),
                "candidate_a_id": min(_clean(row.get("positive_id")), _clean(row.get("negative_id"))),
                "candidate_b_id": max(_clean(row.get("positive_id")), _clean(row.get("negative_id"))),
                "name_token_jaccard": round(float(functions["name_token_jaccard"](row, False)), 6),
                "param_numeric_abs_delta_log1p": round(float(functions["param_numeric_abs_delta_log1p"](row, False)), 6),
                "contrast_label_any_conflict": labels["contrast_label_any_conflict"],
                "contrast_label_subtype_conflict": labels["contrast_label_subtype_conflict"],
                "contrast_label_param_value_conflict": labels["contrast_label_param_value_conflict"],
            }
        )
    return sample_rows


def _forbidden_feature_names(feature_rows: list[dict[str, Any]]) -> list[str]:
    forbidden_tokens = ["positive", "negative", "quota_id", "province", "rank", "expected", "query"]
    result: list[str] = []
    for row in feature_rows:
        name = row["field_name"].lower()
        if any(token in name for token in forbidden_tokens) or name == "label":
            result.append(row["field_name"])
    return result


def _summary(
    *,
    rows: list[dict[str, Any]],
    task_redefinition: dict[str, Any],
    label_direction: dict[str, Any],
    feature_rows: list[dict[str, Any]],
    label_rows: list[dict[str, Any]],
    feature_violations: int,
    label_violations: int,
    coverage_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_pair_type = Counter(_clean(row.get("pair_type")) for row in rows)
    by_family = Counter(_clean(row.get("family")) for row in rows)
    forbidden_features = _forbidden_feature_names(feature_rows)
    label_nonzero = {row["field_name"]: row["nonzero_rate"] for row in coverage_rows if row["field_kind"] == "contrast_label"}
    task_gate = bool(_value(task_redefinition, "summary", "passes_task_redefinition_gate", default=False))
    ranking_allowed = _value(label_direction, "summary", "ranking_supervision_allowed", default=True)
    return {
        "pairs": len(rows),
        "feature_count": len(feature_rows),
        "contrast_label_count": len(label_rows),
        "by_pair_type": [{"key": key, "count": count, "rate": _rate(count, len(rows))} for key, count in by_pair_type.most_common()],
        "top_families": [{"key": key, "count": count, "rate": _rate(count, len(rows))} for key, count in by_family.most_common(20)],
        "feature_order_invariant_violations": feature_violations,
        "label_order_invariant_violations": label_violations,
        "forbidden_feature_count": len(forbidden_features),
        "forbidden_features": forbidden_features,
        "label_any_conflict_rate": label_nonzero.get("contrast_label_any_conflict", 0),
        "label_subtype_conflict_rate": label_nonzero.get("contrast_label_subtype_conflict", 0),
        "label_param_value_conflict_rate": label_nonzero.get("contrast_label_param_value_conflict", 0),
        "task_redefinition_gate_passed": task_gate,
        "ranking_supervision_allowed": ranking_allowed,
        "schema_policy": "undirected_pair_order_invariant_no_ranking_label",
        "passes_undirected_schema_gate": (
            len(rows) > 0
            and task_gate
            and ranking_allowed is False
            and feature_violations == 0
            and label_violations == 0
            and not forbidden_features
        ),
        "recommended_next_stage": "Stage 6.4 eval-only query anchored ranking matrix design; no training.",
    }


def _schema_payload(feature_rows: list[dict[str, Any]], label_rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "Goal LTR v1 / stage 6.3 undirected contrast matrix schema",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "table_policy": {
            "row_unit": "one unordered pair per row",
            "candidate_order": "candidate_a/candidate_b are diagnostic only; features and contrast labels must be invariant to swapping candidates",
            "ranking_label": "forbidden",
            "allowed_labels": "conflict labels only, never Top1 or LambdaRank target",
        },
        "diagnostic_fields": [
            {"field_name": "contrast_pair_id", "dtype": "string", "notes": "Stable unordered pair id; diagnostic only."},
            {"field_name": "candidate_a_id", "dtype": "string", "notes": "Canonical candidate id; diagnostic only, not a feature."},
            {"field_name": "candidate_b_id", "dtype": "string", "notes": "Canonical candidate id; diagnostic only, not a feature."},
            {"field_name": "source_pair_id", "dtype": "string", "notes": "Original random-order pair id; diagnostic only."},
        ],
        "features": feature_rows,
        "contrast_labels": label_rows,
        "summary": summary,
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
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    coverage = report["top_coverage"]
    lines = [
        "# Goal Undirected Contrast Matrix Schema",
        "",
        "Stage 6.3 eval-only schema. It defines order-invariant pair features and conflict labels for current self-supervised quota pairs. It does not train, tune, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["pairs", summary["pairs"]],
                ["feature_count", summary["feature_count"]],
                ["contrast_label_count", summary["contrast_label_count"]],
                ["feature_order_invariant_violations", summary["feature_order_invariant_violations"]],
                ["label_order_invariant_violations", summary["label_order_invariant_violations"]],
                ["forbidden_feature_count", summary["forbidden_feature_count"]],
                ["label_any_conflict_rate", summary["label_any_conflict_rate"]],
                ["label_subtype_conflict_rate", summary["label_subtype_conflict_rate"]],
                ["label_param_value_conflict_rate", summary["label_param_value_conflict_rate"]],
                ["passes_undirected_schema_gate", summary["passes_undirected_schema_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Pair Type",
        "",
        _md_table([["key", "count", "rate"], *[[row["key"], row["count"], row["rate"]] for row in summary["by_pair_type"]]]),
        "",
        "## Coverage Highlights",
        "",
        _md_table(
            [
                ["field", "kind", "coverage_rate", "nonzero_rate", "violations"],
                *[
                    [row["field_name"], row["field_kind"], row["coverage_rate"], row["nonzero_rate"], row["order_invariant_violations"]]
                    for row in coverage[:18]
                ],
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 6.3 eval-only undirected contrast matrix schema and coverage audit")
    parser.add_argument("--pair-whitelist-csv", default=str(DEFAULT_PAIR_WHITELIST_CSV))
    parser.add_argument("--task-redefinition-json", default=str(DEFAULT_TASK_REDEFINITION_JSON))
    parser.add_argument("--label-direction-json", default=str(DEFAULT_LABEL_DIRECTION_JSON))
    parser.add_argument("--schema-json", default=str(DEFAULT_SCHEMA_JSON))
    parser.add_argument("--feature-schema-csv", default=str(DEFAULT_FEATURE_SCHEMA_CSV))
    parser.add_argument("--label-schema-csv", default=str(DEFAULT_LABEL_SCHEMA_CSV))
    parser.add_argument("--coverage-csv", default=str(DEFAULT_COVERAGE_CSV))
    parser.add_argument("--sample-csv", default=str(DEFAULT_SAMPLE_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--sample-limit", type=int, default=30)
    args = parser.parse_args()

    started = time.perf_counter()
    rows = _read_csv(Path(args.pair_whitelist_csv))
    task_redefinition = _read_json(Path(args.task_redefinition_json))
    label_direction = _read_json(Path(args.label_direction_json))
    feature_rows, label_rows, functions = _field_defs()
    feature_coverage, feature_violations = _coverage(rows, feature_rows, functions, is_label=False)
    label_coverage, label_violations = _coverage(rows, label_rows, functions, is_label=True)
    coverage_rows = [*feature_coverage, *label_coverage]
    sample_rows = _samples(rows, functions, args.sample_limit)
    summary = _summary(
        rows=rows,
        task_redefinition=task_redefinition,
        label_direction=label_direction,
        feature_rows=feature_rows,
        label_rows=label_rows,
        feature_violations=feature_violations,
        label_violations=label_violations,
        coverage_rows=coverage_rows,
    )

    _write_csv(Path(args.feature_schema_csv), feature_rows, ["field_name", "field_group", "dtype", "role", "order_invariant", "source_fields", "notes"])
    _write_csv(Path(args.label_schema_csv), label_rows, ["field_name", "field_group", "dtype", "role", "order_invariant", "source_fields", "notes"])
    _write_csv(
        Path(args.coverage_csv),
        coverage_rows,
        ["field_name", "field_kind", "field_group", "dtype", "rows", "covered_rows", "coverage_rate", "nonzero_rows", "nonzero_rate", "order_invariant_violations", "top_values"],
    )
    _write_csv(
        Path(args.sample_csv),
        sample_rows,
        [
            "contrast_pair_id",
            "family",
            "pair_type",
            "contrast_field",
            "candidate_a_id",
            "candidate_b_id",
            "name_token_jaccard",
            "param_numeric_abs_delta_log1p",
            "contrast_label_any_conflict",
            "contrast_label_subtype_conflict",
            "contrast_label_param_value_conflict",
        ],
    )
    _write_json(Path(args.schema_json), _schema_payload(feature_rows, label_rows, summary))

    report = {
        "stage": "Goal LTR v1 / stage 6.3 undirected contrast matrix schema",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "inputs": {
            "pair_whitelist_csv": str(Path(args.pair_whitelist_csv)),
            "task_redefinition_json": str(Path(args.task_redefinition_json)),
            "label_direction_json": str(Path(args.label_direction_json)),
        },
        "summary": summary,
        "top_coverage": coverage_rows[:30],
        "artifacts": {
            "schema_json": str(Path(args.schema_json)),
            "feature_schema_csv": str(Path(args.feature_schema_csv)),
            "label_schema_csv": str(Path(args.label_schema_csv)),
            "coverage_csv": str(Path(args.coverage_csv)),
            "sample_csv": str(Path(args.sample_csv)),
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
                    "pairs": summary["pairs"],
                    "feature_count": summary["feature_count"],
                    "contrast_label_count": summary["contrast_label_count"],
                    "feature_order_invariant_violations": summary["feature_order_invariant_violations"],
                    "label_order_invariant_violations": summary["label_order_invariant_violations"],
                    "forbidden_feature_count": summary["forbidden_feature_count"],
                    "passes_undirected_schema_gate": summary["passes_undirected_schema_gate"],
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
