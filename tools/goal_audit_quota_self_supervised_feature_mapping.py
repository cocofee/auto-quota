from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_MATRIX_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_matrix_draft.csv"
DEFAULT_PAIR_WHITELIST_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_training_pair_whitelist.csv"
DEFAULT_FEATURE_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_feature_mapping_plan.csv"
DEFAULT_FAMILY_COVERAGE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_feature_mapping_family_coverage.csv"
DEFAULT_MISSING_FIELDS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_feature_mapping_missing_fields.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_feature_mapping_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_feature_mapping_summary.md"

PARAM_MARKER_RE = re.compile(r"(DN|mm2|mm|m2|m3|<=|>=|<|>|≤|≥|\d+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")

FEATURE_PLAN_FIELDS = [
    "feature_name",
    "feature_group",
    "mapping_status",
    "source",
    "source_fields",
    "rows",
    "covered_rows",
    "coverage_rate",
    "nonzero_rows",
    "nonzero_rate",
    "cardinality",
    "notes",
]

FAMILY_COVERAGE_FIELDS = [
    "family",
    "groups",
    "rows",
    "param_rows",
    "param_row_rate",
    "unit_nonempty_rate",
    "numeric_contrast_rate",
    "quota_name_digit_nonzero_rate",
    "action_signal_pair_rate",
    "material_signal_pair_rate",
    "connection_signal_pair_rate",
    "install_method_signal_pair_rate",
    "param_type_signal_pair_rate",
]

MISSING_FIELDS = [
    {
        "field": "counterpart_unit",
        "needed_for": "same_unit_with_counterpart",
        "pair_whitelist_fields": "positive_unit|negative_unit",
        "recommendation": "Carry counterpart unit into matrix v2 or join pair whitelist when generating numeric features.",
    },
    {
        "field": "counterpart_book",
        "needed_for": "same_book_with_counterpart/book_delta",
        "pair_whitelist_fields": "positive_book|negative_book",
        "recommendation": "Carry counterpart book into matrix v2; current row book alone cannot compute pair consistency.",
    },
    {
        "field": "counterpart_chapter",
        "needed_for": "same_chapter_with_counterpart/chapter_delta",
        "pair_whitelist_fields": "positive_chapter|negative_chapter|same_chapter",
        "recommendation": "Carry counterpart chapter or precomputed same_chapter into matrix v2.",
    },
    {
        "field": "candidate_action",
        "needed_for": "action/subtype conflict features",
        "pair_whitelist_fields": "positive_action|negative_action",
        "recommendation": "Keep as optional pairwise pretrain feature; do not use as answer prior.",
    },
    {
        "field": "candidate_material",
        "needed_for": "material conflict features",
        "pair_whitelist_fields": "positive_material|negative_material",
        "recommendation": "Keep as optional pairwise pretrain feature; expect family-dependent sparsity.",
    },
    {
        "field": "candidate_connection",
        "needed_for": "connection conflict features",
        "pair_whitelist_fields": "positive_connection|negative_connection",
        "recommendation": "Use only for families where coverage is meaningful.",
    },
    {
        "field": "candidate_install_method",
        "needed_for": "install method conflict features",
        "pair_whitelist_fields": "positive_install_method|negative_install_method",
        "recommendation": "Use only for families where coverage is meaningful.",
    },
    {
        "field": "candidate_param_type",
        "needed_for": "param type guard",
        "pair_whitelist_fields": "positive_param_type|negative_param_type",
        "recommendation": "Carry into matrix v2 for param_contrast sanity checks.",
    },
    {
        "field": "bm25_score",
        "needed_for": "real Top80 rerank",
        "pair_whitelist_fields": "",
        "recommendation": "Requires query/search candidate context; unavailable in quota self-supervised pairs.",
    },
    {
        "field": "query_token_overlap",
        "needed_for": "real Top80 rerank",
        "pair_whitelist_fields": "",
        "recommendation": "Requires bill/query text; unavailable in quota self-supervised pairs.",
    },
    {
        "field": "query_param_match",
        "needed_for": "real Top80 rerank",
        "pair_whitelist_fields": "",
        "recommendation": "Requires bill/query text; unavailable in quota self-supervised pairs.",
    },
    {
        "field": "national_cluster_bonus",
        "needed_for": "real Top80 rerank",
        "pair_whitelist_fields": "",
        "recommendation": "Requires search-time national cluster features; unavailable in quota self-supervised pairs.",
    },
    {
        "field": "baseline_rule_score",
        "needed_for": "real Top80 rerank",
        "pair_whitelist_fields": "",
        "recommendation": "Requires search-time candidate scoring context; unavailable in quota self-supervised pairs.",
    },
]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


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


def _token_set(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text or "")}


def _jaccard(left: str, right: str) -> float | None:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens and not right_tokens:
        return None
    union = left_tokens | right_tokens
    if not union:
        return None
    return len(left_tokens & right_tokens) / len(union)


def _nonempty(row: dict[str, Any], field: str) -> bool:
    return _clean(row.get(field)) != ""


def _feature_row(
    *,
    feature_name: str,
    feature_group: str,
    mapping_status: str,
    source: str,
    source_fields: list[str],
    rows: int,
    covered_rows: int,
    nonzero_rows: int,
    cardinality: int | str,
    notes: str,
) -> dict[str, Any]:
    return {
        "feature_name": feature_name,
        "feature_group": feature_group,
        "mapping_status": mapping_status,
        "source": source,
        "source_fields": "|".join(source_fields),
        "rows": rows,
        "covered_rows": covered_rows,
        "coverage_rate": _rate(covered_rows, rows),
        "nonzero_rows": nonzero_rows,
        "nonzero_rate": _rate(nonzero_rows, rows),
        "cardinality": cardinality,
        "notes": notes,
    }


def _field_feature(rows: list[dict[str, Any]], field: str, feature_name: str, feature_group: str, status: str, notes: str) -> dict[str, Any]:
    total = len(rows)
    covered = 0
    nonzero = 0
    values: set[str] = set()
    for row in rows:
        value = _clean(row.get(field))
        if value:
            covered += 1
            values.add(value)
            number = _as_float(value)
            if (number is not None and number != 0) or (number is None and value):
                nonzero += 1
    return _feature_row(
        feature_name=feature_name,
        feature_group=feature_group,
        mapping_status=status,
        source="matrix",
        source_fields=[field],
        rows=total,
        covered_rows=covered,
        nonzero_rows=nonzero,
        cardinality=len(values),
        notes=notes,
    )


def _matrix_layout_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = 0
    invalid_groups = 0
    duplicate_noncontiguous_groups = 0
    current_group = ""
    current_rows: list[dict[str, Any]] = []
    seen_finished: set[str] = set()
    label_counts = Counter()
    group_size_counts = Counter()
    row_key_counts = Counter()

    def finish(group_rows: list[dict[str, Any]]) -> None:
        nonlocal groups, invalid_groups
        if not group_rows:
            return
        groups += 1
        labels = [_clean(row.get("label")) for row in group_rows]
        roles = [_clean(row.get("row_in_group")) for row in group_rows]
        group_size_counts[len(group_rows)] += 1
        ok = len(group_rows) == 2 and sorted(labels) == ["0", "1"] and sorted(roles) == ["negative", "positive"]
        if not ok:
            invalid_groups += 1

    for row in rows:
        group_id = _clean(row.get("group_id"))
        role = _clean(row.get("row_in_group"))
        row_key_counts[(group_id, role)] += 1
        label_counts[_clean(row.get("label"))] += 1
        if not current_group:
            current_group = group_id
        elif group_id != current_group:
            finish(current_rows)
            seen_finished.add(current_group)
            if group_id in seen_finished:
                duplicate_noncontiguous_groups += 1
            current_group = group_id
            current_rows = []
        current_rows.append(row)
    finish(current_rows)
    duplicate_row_keys = sum(count - 1 for count in row_key_counts.values() if count > 1)
    return {
        "rows": len(rows),
        "groups": groups,
        "invalid_groups": invalid_groups,
        "duplicate_row_keys": duplicate_row_keys,
        "duplicate_noncontiguous_groups": duplicate_noncontiguous_groups,
        "label_counts": dict(label_counts),
        "group_size_counts": {str(key): value for key, value in sorted(group_size_counts.items())},
    }


def _group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_clean(row.get("group_id"))].append(row)
    return grouped


def _derived_feature_rows(matrix_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(matrix_rows)
    grouped = _group_rows(matrix_rows)
    numeric_contrast_rows = 0
    numeric_delta_nonzero_rows = 0
    same_subtype_rows = 0
    same_subtype_true_rows = 0
    name_overlap_rows = 0
    name_overlap_nonzero_rows = 0
    param_marker_rows = 0
    param_marker_nonzero_rows = 0
    name_length_rows = 0
    name_digit_rows = 0

    for row in matrix_rows:
        name = _clean(row.get("quota_name"))
        if name:
            name_length_rows += 1
            if any(ch.isdigit() for ch in name):
                name_digit_rows += 1
            if PARAM_MARKER_RE.search(name):
                param_marker_rows += 1
                param_marker_nonzero_rows += 1

        subtype = _clean(row.get("subtype_key"))
        other_subtype = _clean(row.get("counterpart_subtype_key"))
        if subtype and other_subtype:
            same_subtype_rows += 1
            if subtype == other_subtype:
                same_subtype_true_rows += 1

        overlap = _jaccard(_clean(row.get("quota_name")), _clean(row.get("counterpart_quota_name")))
        if overlap is not None:
            name_overlap_rows += 1
            if overlap > 0:
                name_overlap_nonzero_rows += 1

    for group in grouped.values():
        if len(group) != 2:
            continue
        values = [_as_float(row.get("contrast_value")) for row in group]
        if values[0] is None or values[1] is None:
            continue
        numeric_contrast_rows += len(group)
        if abs(values[0] - values[1]) > 0:
            numeric_delta_nonzero_rows += len(group)

    return [
        _feature_row(
            feature_name="quota_name_length",
            feature_group="candidate_text",
            mapping_status="ready_numeric",
            source="matrix",
            source_fields=["quota_name"],
            rows=total,
            covered_rows=name_length_rows,
            nonzero_rows=name_length_rows,
            cardinality="derived",
            notes="Simple text length feature; does not require query context.",
        ),
        _feature_row(
            feature_name="quota_name_has_digits",
            feature_group="candidate_text",
            mapping_status="ready_numeric",
            source="matrix",
            source_fields=["quota_name"],
            rows=total,
            covered_rows=name_length_rows,
            nonzero_rows=name_digit_rows,
            cardinality=2,
            notes="Detects explicit size/tier numbers in quota names.",
        ),
        _feature_row(
            feature_name="quota_name_param_marker_count",
            feature_group="candidate_text",
            mapping_status="ready_numeric",
            source="matrix",
            source_fields=["quota_name"],
            rows=total,
            covered_rows=param_marker_rows,
            nonzero_rows=param_marker_nonzero_rows,
            cardinality="derived",
            notes="Narrow marker count for DN/mm/inequality/numeric tier hints.",
        ),
        _feature_row(
            feature_name="counterpart_name_token_jaccard",
            feature_group="pairwise_text",
            mapping_status="ready_numeric_pairwise",
            source="matrix",
            source_fields=["quota_name", "counterpart_quota_name"],
            rows=total,
            covered_rows=name_overlap_rows,
            nonzero_rows=name_overlap_nonzero_rows,
            cardinality="derived",
            notes="Pairwise similarity inside the self-supervised pair, not a query-match feature.",
        ),
        _feature_row(
            feature_name="same_subtype_key_with_counterpart",
            feature_group="pairwise_candidate",
            mapping_status="ready_numeric_pairwise",
            source="matrix",
            source_fields=["subtype_key", "counterpart_subtype_key"],
            rows=total,
            covered_rows=same_subtype_rows,
            nonzero_rows=same_subtype_true_rows,
            cardinality=2,
            notes="Can be computed from current 5.7 matrix.",
        ),
        _feature_row(
            feature_name="contrast_numeric_abs_delta",
            feature_group="pairwise_param",
            mapping_status="ready_numeric_partial",
            source="matrix",
            source_fields=["contrast_value", "counterpart_contrast_value"],
            rows=total,
            covered_rows=numeric_contrast_rows,
            nonzero_rows=numeric_delta_nonzero_rows,
            cardinality="derived",
            notes="Only applies when both pair rows have numeric contrast values.",
        ),
    ]


def _candidate_signal_coverage(pair_rows: list[dict[str, Any]], left_field: str, right_field: str) -> tuple[int, int]:
    covered = 0
    nonzero = 0
    for row in pair_rows:
        left = _clean(row.get(left_field))
        right = _clean(row.get(right_field))
        if left:
            covered += 1
            nonzero += 1
        if right:
            covered += 1
            nonzero += 1
    return covered, nonzero


def _pairwise_same_coverage(pair_rows: list[dict[str, Any]], left_field: str, right_field: str) -> tuple[int, int]:
    covered = 0
    nonzero = 0
    for row in pair_rows:
        left = _clean(row.get(left_field))
        right = _clean(row.get(right_field))
        if left and right:
            covered += 2
            if left == right:
                nonzero += 2
    return covered, nonzero


def _whitelist_feature_rows(pair_rows: list[dict[str, Any]], matrix_row_count: int) -> list[dict[str, Any]]:
    specs = [
        ("candidate_action", "candidate_signal", "positive_action", "negative_action", "candidate", "Needs matrix v2 or pair whitelist join."),
        ("candidate_material", "candidate_signal", "positive_material", "negative_material", "candidate", "Needs matrix v2 or pair whitelist join."),
        ("candidate_connection", "candidate_signal", "positive_connection", "negative_connection", "candidate", "Sparse; use family-dependent guards."),
        ("candidate_install_method", "candidate_signal", "positive_install_method", "negative_install_method", "candidate", "Sparse; use family-dependent guards."),
        ("candidate_param_type", "candidate_signal", "positive_param_type", "negative_param_type", "candidate", "Useful for param_contrast sanity checks."),
        ("same_book_with_counterpart", "pairwise_candidate", "positive_book", "negative_book", "same", "Current matrix lacks counterpart_book."),
        ("same_unit_with_counterpart", "pairwise_candidate", "positive_unit", "negative_unit", "same", "Current matrix lacks counterpart_unit; unit itself is sparse."),
        ("same_chapter_with_counterpart", "pairwise_candidate", "positive_chapter", "negative_chapter", "same", "Current matrix lacks counterpart_chapter; whitelist also has same_chapter."),
    ]
    rows: list[dict[str, Any]] = []
    for feature_name, group, left, right, coverage_mode, notes in specs:
        if coverage_mode == "same":
            covered, nonzero = _pairwise_same_coverage(pair_rows, left, right)
        else:
            covered, nonzero = _candidate_signal_coverage(pair_rows, left, right)
        rows.append(
            _feature_row(
                feature_name=feature_name,
                feature_group=group,
                mapping_status="needs_matrix_v2_or_join",
                source="pair_whitelist",
                source_fields=[left, right],
                rows=matrix_row_count,
                covered_rows=min(covered, matrix_row_count),
                nonzero_rows=min(nonzero, matrix_row_count),
                cardinality="source_dependent",
                notes=notes,
            )
        )
    return rows


def _search_context_feature_rows(row_count: int) -> list[dict[str, Any]]:
    features = [
        ("bm25_score", "search_context", "baseline lexical score from Top80 retrieval"),
        ("baseline_rule_score", "search_context", "current pure-search score before rerank"),
        ("query_token_overlap", "search_context", "bill/query tokens matched by candidate"),
        ("query_family_match", "search_context", "query family versus candidate family"),
        ("query_param_match", "search_context", "query DN/section/tier versus candidate tier"),
        ("national_cluster_bonus", "search_context", "offline national index cluster signal at search time"),
    ]
    return [
        _feature_row(
            feature_name=name,
            feature_group=group,
            mapping_status="requires_top80_search_context",
            source="unavailable_in_self_supervised_pairs",
            source_fields=[],
            rows=row_count,
            covered_rows=0,
            nonzero_rows=0,
            cardinality=0,
            notes=notes,
        )
        for name, group, notes in features
    ]


def _build_feature_plan(matrix_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    layout_specs = [
        ("group_id", "group_id", "layout", "group key; validated separately"),
        ("label", "label", "target", "target column, not a feature"),
        ("row_weight", "row_weight", "weight", "optional training weight"),
        ("group_weight", "group_weight", "weight", "optional group weight"),
    ]
    for field, feature_name, group, notes in layout_specs:
        plan.append(_field_feature(matrix_rows, field, feature_name, group, "ready_layout", notes))

    direct_specs = [
        ("family", "family_hash", "candidate_categorical", "High-value categorical feature source."),
        ("province", "province_hash", "candidate_categorical", "Useful for provincial style; keep leakage audit in mind."),
        ("pair_type", "pair_type_one_hot", "pair_metadata", "Separates param_contrast and subtype_contrast."),
        ("contrast_field", "contrast_field_hash", "pair_metadata", "Identifies DN/thickness/section/subtype contrast type."),
        ("quota_id", "quota_id_diagnostic_only", "diagnostic", "Do not use as model feature; id is province-local and leak-prone."),
        ("quota_name", "quota_name_text_source", "candidate_text", "Source for numeric/text derived features; not passed raw."),
        ("unit", "unit_hash", "candidate_categorical", "Useful but sparse in current quota snapshot."),
        ("book", "book_hash", "candidate_categorical", "Book/volume source from local quota."),
        ("chapter", "chapter_hash", "candidate_categorical", "Chapter source from local quota."),
        ("subtype_key", "subtype_key_hash", "candidate_categorical", "Candidate subtype signal from quota text."),
        ("contrast_value", "contrast_value_raw", "pair_metadata", "Raw value; convert numeric only when possible."),
        ("counterpart_contrast_value", "counterpart_contrast_value_raw", "pair_metadata", "Raw counterpart value; convert numeric only when possible."),
    ]
    for field, feature_name, group, notes in direct_specs:
        status = "diagnostic_only" if "diagnostic" in feature_name else "ready_categorical_or_source"
        plan.append(_field_feature(matrix_rows, field, feature_name, group, status, notes))

    plan.extend(_derived_feature_rows(matrix_rows))
    plan.extend(_whitelist_feature_rows(pair_rows, len(matrix_rows)))
    plan.extend(_search_context_feature_rows(len(matrix_rows)))
    return plan


def _missing_field_rows(matrix_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matrix_fields = set(matrix_rows[0].keys()) if matrix_rows else set()
    whitelist_fields = set(pair_rows[0].keys()) if pair_rows else set()
    total_pair_rows = len(pair_rows)
    rows: list[dict[str, Any]] = []
    for item in MISSING_FIELDS:
        raw_fields = [field for field in item["pair_whitelist_fields"].split("|") if field]
        present_in_whitelist = all(field in whitelist_fields for field in raw_fields) if raw_fields else False
        covered_pairs = 0
        if raw_fields:
            for row in pair_rows:
                comparable_fields = [field for field in raw_fields if field != "same_chapter"]
                if item["field"].startswith("counterpart_") and comparable_fields:
                    covered = all(_clean(row.get(field)) for field in comparable_fields)
                else:
                    covered = any(_clean(row.get(field)) for field in raw_fields)
                if covered:
                    covered_pairs += 1
        rows.append(
            {
                "field": item["field"],
                "needed_for": item["needed_for"],
                "present_in_matrix": str(item["field"] in matrix_fields).lower(),
                "present_in_pair_whitelist": str(present_in_whitelist).lower(),
                "pair_whitelist_fields": item["pair_whitelist_fields"],
                "pair_whitelist_pair_coverage_rate": _rate(covered_pairs, total_pair_rows),
                "recommendation": item["recommendation"],
            }
        )
    return rows


def _family_coverage(matrix_rows: list[dict[str, Any]], pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_family_pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in matrix_rows:
        by_family_rows[_clean(row.get("family"))].append(row)
    for row in pair_rows:
        by_family_pairs[_clean(row.get("family"))].append(row)

    result: list[dict[str, Any]] = []
    for family in sorted(by_family_rows):
        rows = by_family_rows[family]
        pair_subset = by_family_pairs.get(family, [])
        row_count = len(rows)
        group_count = len({_clean(row.get("group_id")) for row in rows})
        param_rows = sum(1 for row in rows if _clean(row.get("pair_type")) == "param_contrast")
        unit_nonempty = sum(1 for row in rows if _nonempty(row, "unit"))
        numeric_contrast = sum(1 for row in rows if _as_float(row.get("contrast_value")) is not None)
        digit_nonzero = sum(1 for row in rows if any(ch.isdigit() for ch in _clean(row.get("quota_name"))))

        def signal_rate(left: str, right: str) -> float:
            pair_total = len(pair_subset)
            if not pair_total:
                return 0.0
            covered = sum(1 for row in pair_subset if _clean(row.get(left)) or _clean(row.get(right)))
            return _rate(covered, pair_total)

        result.append(
            {
                "family": family,
                "groups": group_count,
                "rows": row_count,
                "param_rows": param_rows,
                "param_row_rate": _rate(param_rows, row_count),
                "unit_nonempty_rate": _rate(unit_nonempty, row_count),
                "numeric_contrast_rate": _rate(numeric_contrast, row_count),
                "quota_name_digit_nonzero_rate": _rate(digit_nonzero, row_count),
                "action_signal_pair_rate": signal_rate("positive_action", "negative_action"),
                "material_signal_pair_rate": signal_rate("positive_material", "negative_material"),
                "connection_signal_pair_rate": signal_rate("positive_connection", "negative_connection"),
                "install_method_signal_pair_rate": signal_rate("positive_install_method", "negative_install_method"),
                "param_type_signal_pair_rate": signal_rate("positive_param_type", "negative_param_type"),
            }
        )
    result.sort(key=lambda row: (-int(row["groups"]), row["family"]))
    return result


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


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
    top_mapping_rows = [["mapping_status", "features"]]
    for item in summary["by_mapping_status"]:
        top_mapping_rows.append([item["key"], item["count"]])

    high_sparsity_rows = [["feature_name", "coverage_rate", "status"]]
    for item in summary["high_sparsity_features"]:
        high_sparsity_rows.append([item["feature_name"], item["coverage_rate"], item["mapping_status"]])

    lines = [
        "# Goal Self-Supervised Feature Mapping Audit",
        "",
        "Stage 5.8 eval-only audit. It maps the 5.7 matrix draft to future numeric-feature readiness and does not train, tune, or change ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["matrix_rows", summary["matrix_rows"]],
                ["groups", summary["groups"]],
                ["pair_whitelist_pairs", summary["pair_whitelist_pairs"]],
                ["invalid_groups", summary["invalid_groups"]],
                ["ready_layout_features", summary["ready_layout_features"]],
                ["ready_or_source_features", summary["ready_or_source_features"]],
                ["needs_matrix_v2_or_join_features", summary["needs_matrix_v2_or_join_features"]],
                ["requires_search_context_features", summary["requires_search_context_features"]],
                ["pairwise_pretrain_input_ready", summary["pairwise_pretrain_input_ready"]],
                ["real_search_rerank_ready", summary["real_search_rerank_ready"]],
                ["passes_feature_mapping_audit_gate", summary["passes_feature_mapping_audit_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Mapping Status",
        "",
        _md_table(top_mapping_rows),
        "",
        "## Sparse Or Unavailable Features",
        "",
        _md_table(high_sparsity_rows),
        "",
        "## Artifacts",
        "",
        _md_table(
            [
                ["artifact", "path"],
                ["feature_plan_csv", report["artifacts"]["feature_plan_csv"]],
                ["family_coverage_csv", report["artifacts"]["family_coverage_csv"]],
                ["missing_fields_csv", report["artifacts"]["missing_fields_csv"]],
                ["summary_json", report["artifacts"]["summary_json"]],
            ]
        ),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5.8 eval-only feature mapping audit for quota self-supervised matrix")
    parser.add_argument("--matrix-csv", default=str(DEFAULT_MATRIX_CSV))
    parser.add_argument("--pair-whitelist-csv", default=str(DEFAULT_PAIR_WHITELIST_CSV))
    parser.add_argument("--feature-plan-csv", default=str(DEFAULT_FEATURE_PLAN_CSV))
    parser.add_argument("--family-coverage-csv", default=str(DEFAULT_FAMILY_COVERAGE_CSV))
    parser.add_argument("--missing-fields-csv", default=str(DEFAULT_MISSING_FIELDS_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--top-limit", type=int, default=20)
    args = parser.parse_args()

    started = time.perf_counter()
    matrix_rows = _read_csv(Path(args.matrix_csv))
    pair_rows = _read_csv(Path(args.pair_whitelist_csv))

    layout = _matrix_layout_audit(matrix_rows)
    feature_plan = _build_feature_plan(matrix_rows, pair_rows)
    missing_fields = _missing_field_rows(matrix_rows, pair_rows)
    family_coverage = _family_coverage(matrix_rows, pair_rows)

    _write_csv(Path(args.feature_plan_csv), feature_plan, FEATURE_PLAN_FIELDS)
    _write_csv(Path(args.family_coverage_csv), family_coverage, FAMILY_COVERAGE_FIELDS)
    _write_csv(
        Path(args.missing_fields_csv),
        missing_fields,
        [
            "field",
            "needed_for",
            "present_in_matrix",
            "present_in_pair_whitelist",
            "pair_whitelist_fields",
            "pair_whitelist_pair_coverage_rate",
            "recommendation",
        ],
    )

    status_counts = Counter(_clean(row.get("mapping_status")) for row in feature_plan)
    high_sparsity = [
        {
            "feature_name": row["feature_name"],
            "coverage_rate": row["coverage_rate"],
            "mapping_status": row["mapping_status"],
        }
        for row in feature_plan
        if float(row["coverage_rate"]) < 0.5
        or row["mapping_status"] in {"requires_top80_search_context", "needs_matrix_v2_or_join"}
    ]
    high_sparsity.sort(key=lambda row: (float(row["coverage_rate"]), row["feature_name"]))

    matrix_fields = set(matrix_rows[0].keys()) if matrix_rows else set()
    required_layout_fields = {"group_id", "row_in_group", "label", "row_weight", "group_weight"}
    ready_or_source_statuses = {"ready_categorical_or_source", "ready_numeric", "ready_numeric_pairwise", "ready_numeric_partial"}
    summary = {
        "matrix_rows": len(matrix_rows),
        "groups": layout["groups"],
        "pair_whitelist_pairs": len(pair_rows),
        "invalid_groups": layout["invalid_groups"],
        "duplicate_row_keys": layout["duplicate_row_keys"],
        "duplicate_noncontiguous_groups": layout["duplicate_noncontiguous_groups"],
        "label_counts": layout["label_counts"],
        "group_size_counts": layout["group_size_counts"],
        "feature_defs": len(feature_plan),
        "ready_layout_features": status_counts.get("ready_layout", 0),
        "ready_or_source_features": sum(status_counts.get(status, 0) for status in ready_or_source_statuses),
        "needs_matrix_v2_or_join_features": status_counts.get("needs_matrix_v2_or_join", 0),
        "requires_search_context_features": status_counts.get("requires_top80_search_context", 0),
        "diagnostic_only_features": status_counts.get("diagnostic_only", 0),
        "missing_required_layout_fields": sorted(required_layout_fields - matrix_fields),
        "pairwise_pretrain_input_ready": (
            len(matrix_rows) == len(pair_rows) * 2
            and layout["invalid_groups"] == 0
            and layout["duplicate_row_keys"] == 0
            and layout["duplicate_noncontiguous_groups"] == 0
            and not (required_layout_fields - matrix_fields)
            and len(pair_rows) >= 50000
            and status_counts.get("ready_numeric_pairwise", 0) >= 2
        ),
        "real_search_rerank_ready": False,
        "passes_feature_mapping_audit_gate": (
            len(matrix_rows) == len(pair_rows) * 2
            and layout["invalid_groups"] == 0
            and layout["duplicate_row_keys"] == 0
            and layout["duplicate_noncontiguous_groups"] == 0
            and not (required_layout_fields - matrix_fields)
        ),
        "by_mapping_status": _counter_items(status_counts, len(feature_plan), args.top_limit),
        "high_sparsity_features": high_sparsity[: args.top_limit],
        "top_family_groups": [
            {
                "family": row["family"],
                "groups": row["groups"],
                "param_row_rate": row["param_row_rate"],
                "unit_nonempty_rate": row["unit_nonempty_rate"],
            }
            for row in family_coverage[: args.top_limit]
        ],
        "recommended_next_stage": "Stage 5.9 eval-only numeric feature matrix dry run; still no training.",
    }

    report = {
        "stage": "Goal LTR v1 / stage 5.8 quota self-supervised feature mapping audit",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "matrix_csv": str(Path(args.matrix_csv)),
        "pair_whitelist_csv": str(Path(args.pair_whitelist_csv)),
        "summary": summary,
        "artifacts": {
            "feature_plan_csv": str(Path(args.feature_plan_csv)),
            "family_coverage_csv": str(Path(args.family_coverage_csv)),
            "missing_fields_csv": str(Path(args.missing_fields_csv)),
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
                    "pair_whitelist_pairs": summary["pair_whitelist_pairs"],
                    "invalid_groups": summary["invalid_groups"],
                    "ready_or_source_features": summary["ready_or_source_features"],
                    "needs_matrix_v2_or_join_features": summary["needs_matrix_v2_or_join_features"],
                    "requires_search_context_features": summary["requires_search_context_features"],
                    "pairwise_pretrain_input_ready": summary["pairwise_pretrain_input_ready"],
                    "real_search_rerank_ready": summary["real_search_rerank_ready"],
                    "passes_feature_mapping_audit_gate": summary["passes_feature_mapping_audit_gate"],
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
