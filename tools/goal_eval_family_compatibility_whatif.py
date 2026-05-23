from __future__ import annotations

import argparse
import csv
import json
import re
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

DEFAULT_OOF_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration_summary.json"
DEFAULT_OOF_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_details.jsonl"
DEFAULT_OOF_VARIANTS = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_variants.csv"
DEFAULT_EVAL_DETAILS = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "frozen_eval_details.jsonl"
DEFAULT_EVAL_VARIANTS = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "frozen_eval_variants.csv"
DEFAULT_COMPAT_SPEC = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_spec_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_summary.md"
DEFAULT_ROWS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_rows.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_buckets.csv"
DEFAULT_EXAMPLES_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_family_compatibility_whatif_examples.jsonl"

FORBIDDEN_SPEC_KEYS = {
    "sample_id",
    "source_file",
    "project_name",
    "expected_id",
    "expected_ids",
    "quota_id",
    "quota_ids",
    "raw_ltr_top_id",
    "baseline_top_id",
    "province",
    "province_id",
}

ALLOWABLE_RELATION_TYPES = {
    "taxonomy_alias",
    "semantic_neighbor",
    "taxonomy_alias_or_semantic_neighbor",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _family(value: Any) -> str:
    value = _clean(value)
    return value if value else "<empty>"


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _clean(value).lower() in {"1", "true", "yes", "y"}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if line:
                yield line_no, json.loads(line)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _default_spec() -> dict[str, Any]:
    return {
        "stage": "Goal LTR v1 / stage 7.5 family compatibility what-if",
        "version": "v1",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "scope": "generic compatibility records; no sample/source/expected/province-id dependency",
        "records": [
            {
                "relation_id": "sleeve_support_taxonomy_alias",
                "left_family": "sleeve",
                "right_family": "support",
                "relation_type": "taxonomy_alias",
                "confidence": "high",
                "allow_override": True,
                "required_any_query_terms": ["套管", "防水套管", "穿墙套管", "密闭穿墙", "穿墙密闭", "人防套管"],
                "required_any_candidate_terms": ["套管", "防水套管", "塑料套管", "穿墙套管"],
                "negative_any_candidate_terms": ["支架", "吊架", "支吊架", "托架"],
                "param_policy": "review_or_non_blocking_when_family_taxonomy_alias",
                "reason": "套管类定额在部分库中被候选族标成 support，安全门不应只因 family_conflict 挡住。",
            },
            {
                "relation_id": "sleeve_duct_closed_wall_neighbor",
                "left_family": "sleeve",
                "right_family": "duct",
                "relation_type": "semantic_neighbor",
                "confidence": "medium",
                "allow_override": True,
                "required_any_query_terms": ["密闭穿墙", "穿墙密闭", "密闭套管", "人防密闭"],
                "required_any_candidate_terms": ["密闭穿墙", "穿墙密闭", "密闭套管", "穿墙管"],
                "negative_any_candidate_terms": ["风管制作", "风管安装", "风口"],
                "param_policy": "review_or_non_blocking_when_family_taxonomy_alias",
                "reason": "密闭穿墙管/套管在通风或人防册中可能落到 duct 族。",
            },
            {
                "relation_id": "valve_duct_air_system_neighbor",
                "left_family": "valve",
                "right_family": "duct",
                "relation_type": "semantic_neighbor",
                "confidence": "medium",
                "allow_override": True,
                "required_any_query_terms": ["阀", "止回阀", "插板阀", "防火阀", "调节阀", "密闭阀", "取样"],
                "required_any_candidate_terms": ["阀", "止回阀", "防火阀", "调节阀", "密闭阀", "阀门", "取样接头"],
                "param_policy": "review_or_non_blocking_when_air_system_valve",
                "reason": "风管系统阀件/取样接头会被 duct 族承载，但 query_family 常识别成 valve。",
            },
            {
                "relation_id": "conduit_pipe_electrical_neighbor",
                "left_family": "conduit",
                "right_family": "pipe",
                "relation_type": "semantic_neighbor",
                "confidence": "medium",
                "allow_override": True,
                "required_any_query_terms": ["配管", "电线管", "金属软管", "阻燃管", "SC", "JDG", "KBG"],
                "required_any_candidate_terms": ["配管", "电线管", "金属软管", "阻燃管", "钢管敷设"],
                "negative_any_candidate_terms": ["给水", "排水", "雨水", "污水", "燃气", "采暖"],
                "param_policy": "non_conflicting_or_review",
                "reason": "电气配管候选可能被 pipe 族承载，但需要电气管线词保护。",
            },
            {
                "relation_id": "formwork_concrete_taxonomy_alias",
                "left_family": "formwork",
                "right_family": "concrete",
                "relation_type": "taxonomy_alias",
                "confidence": "medium",
                "allow_override": True,
                "required_any_query_terms": ["模板"],
                "required_any_candidate_terms": ["模板"],
                "param_policy": "review",
                "reason": "土建库里模板常在混凝土章节或混凝土族附近出现。",
            },
            {
                "relation_id": "pump_concrete_query_family_suspect",
                "left_family": "pump",
                "right_family": "concrete",
                "relation_type": "query_family_suspect",
                "confidence": "review",
                "allow_override": False,
                "required_any_candidate_terms": ["混凝土", "路面", "基础", "垫层"],
                "param_policy": "blocked",
                "reason": "雨水口/水泥混凝土等样本更像 query_family 误识别，不应靠兼容层放行。",
            },
            {
                "relation_id": "support_pipe_query_family_suspect",
                "left_family": "support",
                "right_family": "pipe",
                "relation_type": "query_family_suspect",
                "confidence": "review",
                "allow_override": False,
                "required_any_candidate_terms": ["给水管", "排水管", "铸铁排水管", "管道"],
                "param_policy": "blocked",
                "reason": "支吊架族命中管道候选多为 query_family 误触发或清单文本主对象不是支吊架。",
            },
        ],
    }


def _load_or_create_spec(path: Path) -> dict[str, Any]:
    if path.exists():
        return _read_json(path)
    spec = _default_spec()
    _write_json(path, spec)
    return spec


def _records(spec: dict[str, Any]) -> list[dict[str, Any]]:
    records = spec.get("records")
    if not isinstance(records, list):
        raise ValueError("compat spec missing records list")
    return [record for record in records if isinstance(record, dict)]


def _load_selected_gate(summary: dict[str, Any]) -> dict[str, Any]:
    gate = ((summary.get("selection") or {}).get("selected_gate") or {})
    if not gate.get("name"):
        raise ValueError("OOF summary missing selection.selected_gate.name")
    return gate


def _read_variant_metrics(paths: list[Path], selected_variant: str) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if _clean(row.get("variant")) != selected_variant:
                    continue
                split = _clean(row.get("split"))
                eligible = _to_int(row.get("eligible_anchor_rows"))
                matrix_groups = _to_int(row.get("matrix_groups"))
                baseline = int(round(_to_float(row.get("baseline_hit1_rate_eligible")) * eligible))
                raw = int(round(_to_float(row.get("raw_ltr_hit1_rate_eligible")) * eligible))
                gated = int(round(_to_float(row.get("gated_hit1_rate_eligible")) * eligible))
                metrics[split] = {
                    "split": split,
                    "variant": _clean(row.get("variant")),
                    "mode": _clean(row.get("mode")),
                    "margin": row.get("margin"),
                    "matrix_groups": matrix_groups,
                    "eligible_anchor_rows": eligible,
                    "recall_gap_groups": _to_int(row.get("recall_gap_groups")),
                    "top80_recall_rate": _to_float(row.get("top80_recall_rate")),
                    "baseline_hit1": baseline,
                    "raw_ltr_hit1": raw,
                    "gated_hit1": gated,
                    "baseline_hit1_rate_eligible": _to_float(row.get("baseline_hit1_rate_eligible")),
                    "raw_ltr_hit1_rate_eligible": _to_float(row.get("raw_ltr_hit1_rate_eligible")),
                    "gated_hit1_rate_eligible": _to_float(row.get("gated_hit1_rate_eligible")),
                    "gated_hit1_net": _to_int(row.get("gated_hit1_net")),
                    "gated_hit1_gain": _to_int(row.get("gated_hit1_gain")),
                    "gated_hit1_loss": _to_int(row.get("gated_hit1_loss")),
                    "prevented_raw_hit1_loss": _to_int(row.get("prevented_raw_hit1_loss")),
                    "blocked_raw_hit1_gain": _to_int(row.get("blocked_raw_hit1_gain")),
                    "passed_raw_hit1_gain": _to_int(row.get("passed_raw_hit1_gain")),
                    "passed_raw_hit1_loss": _to_int(row.get("passed_raw_hit1_loss")),
                    "raw_override_count": _to_int(row.get("raw_override_count")),
                    "gated_override_count": _to_int(row.get("gated_override_count")),
                    "gated_override_rate": _to_float(row.get("gated_override_rate")),
                    "gated_ndcg5": _to_float(row.get("gated_ndcg5")),
                }
    return metrics


def _terms(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key) or []
    if isinstance(value, str):
        return [_clean(value)] if _clean(value) else []
    if isinstance(value, list):
        return [_clean(item) for item in value if _clean(item)]
    return []


def _has_any(text: str, terms: list[str]) -> bool:
    if not terms:
        return True
    text_lower = text.lower()
    return any(term.lower() in text_lower for term in terms)


def _hits(text: str, terms: list[str]) -> list[str]:
    text_lower = text.lower()
    return [term for term in terms if term.lower() in text_lower]


def _has_none(text: str, terms: list[str]) -> bool:
    if not terms:
        return True
    text_lower = text.lower()
    return not any(term.lower() in text_lower for term in terms)


def _outcome(row: dict[str, Any]) -> str:
    baseline_hit1 = _to_bool(row.get("baseline_hit1"))
    raw_hit1 = _to_bool(row.get("raw_ltr_hit1"))
    gated_hit1 = _to_bool(row.get("gated_hit1"))
    if baseline_hit1 and not raw_hit1 and not gated_hit1:
        return "residual_loss"
    if baseline_hit1 and not raw_hit1 and gated_hit1:
        return "saved_loss"
    if not baseline_hit1 and raw_hit1 and not gated_hit1:
        return "blocked_gain"
    if not baseline_hit1 and raw_hit1 and gated_hit1:
        return "passed_gain"
    return "neutral"


def _compatibility(row: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    query_family = _family(row.get("query_family"))
    candidate_family = _family(row.get("raw_ltr_top_family"))
    query_text = _clean(row.get("query"))
    candidate_text = " ".join(
        part
        for part in [
            _clean(row.get("raw_ltr_top_name")),
            _clean(row.get("raw_ltr_top")),
        ]
        if part
    )
    for record in records:
        if _family(record.get("left_family")) != query_family:
            continue
        if _family(record.get("right_family")) != candidate_family:
            continue
        query_terms = _terms(record, "required_any_query_terms")
        candidate_terms = _terms(record, "required_any_candidate_terms")
        negative_query_terms = _terms(record, "negative_any_query_terms")
        negative_candidate_terms = _terms(record, "negative_any_candidate_terms")
        if not _has_any(query_text, query_terms):
            continue
        if not _has_any(candidate_text, candidate_terms):
            continue
        if not _has_none(query_text, negative_query_terms):
            continue
        if not _has_none(candidate_text, negative_candidate_terms):
            continue
        relation_type = _clean(record.get("relation_type"))
        allow_override = (
            _to_bool(record.get("allow_override"))
            and relation_type in ALLOWABLE_RELATION_TYPES
        )
        return {
            "compatibility_matched": True,
            "compatibility_allowed": allow_override,
            "compatibility_class": relation_type,
            "compatibility_relation_id": _clean(record.get("relation_id")),
            "compatibility_confidence": _clean(record.get("confidence")),
            "compatibility_reason": _clean(record.get("reason")),
            "compatibility_param_policy": _clean(record.get("param_policy")),
            "compatibility_query_hits": "|".join(_hits(query_text, query_terms)),
            "compatibility_candidate_hits": "|".join(_hits(candidate_text, candidate_terms)),
        }
    return {
        "compatibility_matched": False,
        "compatibility_allowed": False,
        "compatibility_class": "not_compatible",
        "compatibility_relation_id": "",
        "compatibility_confidence": "",
        "compatibility_reason": "",
        "compatibility_param_policy": "",
        "compatibility_query_hits": "",
        "compatibility_candidate_hits": "",
    }


def _row_summary(row: dict[str, Any], line_no: int, records: list[dict[str, Any]]) -> dict[str, Any]:
    gate_allowed = _to_bool(row.get("gate_allowed"))
    raw_same_as_baseline = _to_bool(row.get("raw_same_as_baseline"))
    selected_outcome = _outcome(row)
    should_check = not gate_allowed and not raw_same_as_baseline
    compat = _compatibility(row, records) if should_check else {
        "compatibility_matched": False,
        "compatibility_allowed": False,
        "compatibility_class": "not_checked_current_gate_allowed",
        "compatibility_relation_id": "",
        "compatibility_confidence": "",
        "compatibility_reason": "",
        "compatibility_param_policy": "",
        "compatibility_query_hits": "",
        "compatibility_candidate_hits": "",
    }
    compat_allowed = bool(should_check and compat["compatibility_allowed"])
    baseline_hit1 = _to_bool(row.get("baseline_hit1"))
    raw_hit1 = _to_bool(row.get("raw_ltr_hit1"))
    gated_hit1 = _to_bool(row.get("gated_hit1"))
    whatif_hit1 = raw_hit1 if compat_allowed else gated_hit1

    if selected_outcome == "blocked_gain" and compat_allowed:
        whatif_event = "rescued_blocked_gain"
    elif selected_outcome == "saved_loss" and compat_allowed:
        whatif_event = "new_residual_loss"
    elif selected_outcome == "blocked_gain":
        whatif_event = "still_blocked_gain"
    elif selected_outcome == "saved_loss":
        whatif_event = "saved_loss_retained"
    elif selected_outcome == "passed_gain":
        whatif_event = "already_passed_gain"
    elif selected_outcome == "residual_loss":
        whatif_event = "existing_residual_loss"
    elif compat_allowed:
        whatif_event = "allowed_neutral_override"
    else:
        whatif_event = "neutral"

    query_family = _family(row.get("query_family"))
    raw_family = _family(row.get("raw_ltr_top_family"))
    item = {
        "split": _clean(row.get("split")),
        "line_no": line_no,
        "variant": _clean(row.get("variant")),
        "group_index": row.get("group_index"),
        "group_id": _clean(row.get("group_id")),
        "sample_id": _clean(row.get("sample_id")),
        "source_file": _clean(row.get("source_file")),
        "project_name": _clean(row.get("project_name")),
        "province": _clean(row.get("province")),
        "query": _clean(row.get("query")),
        "query_family": query_family,
        "raw_ltr_top_family": raw_family,
        "family_pair": f"{query_family}->{raw_family}",
        "baseline_top_family": _family(row.get("baseline_top_family")),
        "baseline_top_book": _clean(row.get("baseline_top_book")),
        "raw_ltr_top_book": _clean(row.get("raw_ltr_top_book")),
        "gate_allowed": gate_allowed,
        "gate_reason": _clean(row.get("gate_reason")),
        "selected_outcome": selected_outcome,
        "whatif_event": whatif_event,
        "compat_checked": should_check,
        "compat_override_allowed": compat_allowed,
        "baseline_hit1": baseline_hit1,
        "raw_ltr_hit1": raw_hit1,
        "gated_hit1": gated_hit1,
        "whatif_hit1": whatif_hit1,
        "whatif_hit1_delta_vs_gated": int(whatif_hit1) - int(gated_hit1),
        "whatif_hit1_delta_vs_baseline": int(whatif_hit1) - int(baseline_hit1),
        "score_margin": round(_to_float(row.get("score_margin")), 8),
        "no_family_conflict": _to_bool(row.get("no_family_conflict")),
        "no_book_conflict": _to_bool(row.get("no_book_conflict")),
        "no_param_conflict": _to_bool(row.get("no_param_conflict")),
        "query_family_conflict": _to_bool(row.get("query_family_conflict")),
        "model_family_empty": _to_bool(row.get("model_family_empty")),
        "baseline_top_id": _clean(row.get("baseline_top_id")),
        "baseline_top_name": _clean(row.get("baseline_top_name")),
        "raw_ltr_top_id": _clean(row.get("raw_ltr_top_id")),
        "raw_ltr_top_name": _clean(row.get("raw_ltr_top_name")),
        "baseline_positive_rank": row.get("baseline_positive_rank"),
        "raw_ltr_positive_rank": row.get("raw_ltr_positive_rank"),
        "gated_positive_rank": row.get("gated_positive_rank"),
        **compat,
    }
    return item


def _load_split_rows(path: Path, selected_variant: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, row in _iter_jsonl(path):
        if _clean(row.get("variant")) != selected_variant:
            continue
        rows.append(_row_summary(row, line_no, records))
    return rows


def _split_metrics(rows: list[dict[str, Any]], base_metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for split in sorted(set(base_metrics) | {row["split"] for row in rows}):
        subset = [row for row in rows if row["split"] == split]
        rescued = sum(1 for row in subset if row["whatif_event"] == "rescued_blocked_gain")
        new_loss = sum(1 for row in subset if row["whatif_event"] == "new_residual_loss")
        base = base_metrics.get(split) or {}
        eligible = _to_int(base.get("eligible_anchor_rows"))
        matrix_groups = _to_int(base.get("matrix_groups"))
        baseline_hit1 = _to_int(base.get("baseline_hit1"))
        raw_hit1 = _to_int(base.get("raw_ltr_hit1"))
        gated_hit1 = _to_int(base.get("gated_hit1"))
        if not base:
            # Fallback for ad hoc detail files. Official 7.5 reports use variant CSV counts.
            eligible = len(subset)
            matrix_groups = len(subset)
            baseline_hit1 = sum(1 for row in subset if row["baseline_hit1"])
            raw_hit1 = sum(1 for row in subset if row["raw_ltr_hit1"])
            gated_hit1 = sum(1 for row in subset if row["gated_hit1"])
        whatif_hit1 = gated_hit1 + rescued - new_loss
        non_sleeve_rescue = sum(
            1
            for row in subset
            if row["whatif_event"] == "rescued_blocked_gain" and row["query_family"] != "sleeve"
        )
        rescue_pairs = {
            row["family_pair"]
            for row in subset
            if row["whatif_event"] == "rescued_blocked_gain" and row["query_family"] != "sleeve"
        }
        result.append(
            {
                "split": split,
                "matrix_groups": matrix_groups,
                "eligible_anchor_rows": eligible,
                "recall_gap_groups": _to_int(base.get("recall_gap_groups")),
                "top80_recall_rate": _to_float(base.get("top80_recall_rate")),
                "override_detail_rows": len(subset),
                "baseline_hit1": baseline_hit1,
                "baseline_hit1_rate_matrix": _rate(baseline_hit1, matrix_groups),
                "baseline_hit1_rate_eligible": _rate(baseline_hit1, eligible),
                "raw_ltr_hit1": raw_hit1,
                "raw_ltr_hit1_rate_matrix": _rate(raw_hit1, matrix_groups),
                "raw_ltr_hit1_rate_eligible": _rate(raw_hit1, eligible),
                "gated_hit1": gated_hit1,
                "gated_hit1_rate_matrix": _rate(gated_hit1, matrix_groups),
                "gated_hit1_rate_eligible": _rate(gated_hit1, eligible),
                "whatif_hit1": whatif_hit1,
                "whatif_hit1_rate_matrix": _rate(whatif_hit1, matrix_groups),
                "whatif_hit1_rate_eligible": _rate(whatif_hit1, eligible),
                "whatif_net_vs_gated": whatif_hit1 - gated_hit1,
                "whatif_net_vs_baseline": whatif_hit1 - baseline_hit1,
                "whatif_net_vs_raw_ltr": whatif_hit1 - raw_hit1,
                "rescued_blocked_gain": rescued,
                "new_residual_loss": new_loss,
                "non_sleeve_rescue_count": non_sleeve_rescue,
                "non_sleeve_rescue_pair_count": len(rescue_pairs),
                "blocked_gain_remaining": sum(1 for row in subset if row["whatif_event"] == "still_blocked_gain"),
                "saved_loss_retained": sum(1 for row in subset if row["whatif_event"] == "saved_loss_retained"),
                "compat_override_count": sum(1 for row in subset if row["compat_override_allowed"]),
                "compat_matched_count": sum(1 for row in subset if row["compatibility_matched"]),
            }
        )
    return result


def _counter_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "whatif_event",
        "family_pair",
        "query_family",
        "raw_ltr_top_family",
        "compatibility_relation_id",
        "compatibility_class",
        "compatibility_confidence",
        "source_file",
        "province",
    ]
    counters: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    totals: Counter[tuple[str, str]] = Counter()
    for row in rows:
        split = row["split"]
        event = row["whatif_event"]
        totals[(split, event)] += 1
        for field in fields:
            counters[(split, event, field)][_clean(row.get(field)) or "<empty>"] += 1
    result: list[dict[str, Any]] = []
    for (split, event, field), counter in sorted(counters.items()):
        total = totals[(split, event)]
        for key, count in counter.most_common(30):
            result.append(
                {
                    "split": split,
                    "whatif_event": event,
                    "bucket": field,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )
    return result


def _examples(rows: list[dict[str, Any]], limit_per_bucket: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: Counter[tuple[str, str, str]] = Counter()
    interesting = {
        "rescued_blocked_gain",
        "new_residual_loss",
        "still_blocked_gain",
        "saved_loss_retained",
        "allowed_neutral_override",
    }
    for row in sorted(
        rows,
        key=lambda item: (
            item["split"],
            item["whatif_event"],
            item["compatibility_relation_id"],
            item["family_pair"],
            str(item["group_id"]),
        ),
    ):
        if row["whatif_event"] not in interesting and not row["compatibility_matched"]:
            continue
        key = (row["split"], row["whatif_event"], row["compatibility_relation_id"] or row["family_pair"])
        if seen[key] >= limit_per_bucket:
            continue
        seen[key] += 1
        result.append(row)
    return result


def _validate_spec(records: list[dict[str, Any]]) -> dict[str, Any]:
    forbidden_key_hits: list[str] = []
    forbidden_value_hits: list[str] = []
    quota_id_like = re.compile(r"\b\d+[.-]\d+\b")
    for idx, record in enumerate(records, start=1):
        for key in record:
            if key in FORBIDDEN_SPEC_KEYS:
                forbidden_key_hits.append(f"record_{idx}:{key}")
        for key, value in record.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str) and quota_id_like.search(item):
                    forbidden_value_hits.append(f"record_{idx}:{key}:{item}")
    pairs = {(_family(record.get("left_family")), _family(record.get("right_family"))) for record in records}
    required_pairs = {
        ("sleeve", "support"),
        ("sleeve", "duct"),
        ("valve", "duct"),
    }
    non_install_pairs = {
        ("formwork", "concrete"),
        ("pump", "concrete"),
        ("concrete", "formwork"),
    }
    missing_required_pairs = sorted(f"{left}->{right}" for left, right in required_pairs - pairs)
    has_non_install_or_civil = bool(pairs & non_install_pairs)
    return {
        "forbidden_key_hits": forbidden_key_hits,
        "forbidden_value_hits": forbidden_value_hits,
        "missing_required_pairs": missing_required_pairs,
        "has_non_install_or_civil_ambiguity": has_non_install_or_civil,
        "passes_no_leakage_dependency": not forbidden_key_hits and not forbidden_value_hits,
        "passes_schema_expressiveness": not missing_required_pairs and has_non_install_or_civil,
    }


def _anti_drift_gates(
    rows: list[dict[str, Any]],
    spec_validation: dict[str, Any],
    *,
    max_single_family_rescue_share: float,
    min_non_sleeve_rescue_pairs: int,
    max_new_loss_vs_rescue_rate: float,
) -> dict[str, Any]:
    rescued = [row for row in rows if row["split"] == "dev_oof" and row["whatif_event"] == "rescued_blocked_gain"]
    new_losses = [row for row in rows if row["split"] == "dev_oof" and row["whatif_event"] == "new_residual_loss"]
    family_counts = Counter(row["query_family"] for row in rescued)
    top_family, top_family_count = ("", 0)
    if family_counts:
        top_family, top_family_count = family_counts.most_common(1)[0]
    top_family_share = _rate(top_family_count, len(rescued))
    non_sleeve_pairs = {
        row["family_pair"]
        for row in rescued
        if row["query_family"] != "sleeve"
    }
    max_new_losses = int(len(rescued) * max_new_loss_vs_rescue_rate)
    gates = [
        {
            "gate": "has_rescued_blocked_gain",
            "passed": len(rescued) > 0,
            "value": len(rescued),
            "threshold": ">0",
            "reason": "兼容层必须能在 OOF 上救回至少一个被挡 gain。",
        },
        {
            "gate": "not_single_family_patch",
            "passed": top_family_share <= max_single_family_rescue_share,
            "value": top_family_share,
            "threshold": f"<={max_single_family_rescue_share}",
            "reason": "救回样本不能 80% 以上只来自一个 query_family。",
        },
        {
            "gate": "non_sleeve_family_pair_coverage",
            "passed": len(non_sleeve_pairs) >= min_non_sleeve_rescue_pairs,
            "value": len(non_sleeve_pairs),
            "threshold": f">={min_non_sleeve_rescue_pairs}",
            "reason": "至少两个非 sleeve family pair 在 OOF 上救回 gain，避免 sleeve 专属补丁。",
        },
        {
            "gate": "new_loss_not_exceed_rescue_benefit",
            "passed": len(new_losses) <= max_new_losses,
            "value": len(new_losses),
            "threshold": f"<={max_new_losses}",
            "reason": "新增 residual loss 不能超过救回收益。",
        },
        {
            "gate": "no_leakage_dependency_in_spec",
            "passed": spec_validation["passes_no_leakage_dependency"],
            "value": {
                "forbidden_key_hits": spec_validation["forbidden_key_hits"],
                "forbidden_value_hits": spec_validation["forbidden_value_hits"],
            },
            "threshold": "no sample/source/expected/province-id keys or quota-id-like values",
            "reason": "兼容规则不能依赖样本、来源、答案或省份定额编号。",
        },
        {
            "gate": "schema_expresses_multi_family_ambiguity",
            "passed": spec_validation["passes_schema_expressiveness"],
            "value": {
                "missing_required_pairs": spec_validation["missing_required_pairs"],
                "has_non_install_or_civil_ambiguity": spec_validation["has_non_install_or_civil_ambiguity"],
            },
            "threshold": "express sleeve/support, sleeve/duct, valve/duct, and one civil/non-install ambiguity",
            "reason": "schema 必须能表达多个对象族关系，不是 sleeve-only。",
        },
    ]
    return {
        "passed": all(gate["passed"] for gate in gates),
        "gates": gates,
        "rescued_blocked_gain": len(rescued),
        "new_residual_loss": len(new_losses),
        "non_sleeve_rescue_count": sum(1 for row in rescued if row["query_family"] != "sleeve"),
        "non_sleeve_rescue_pair_count": len(non_sleeve_pairs),
        "top_rescue_family": top_family,
        "top_rescue_family_count": top_family_count,
        "top_rescue_family_share": top_family_share,
        "rescue_family_counts": [{"key": key, "count": count, "rate": _rate(count, len(rescued))} for key, count in family_counts.most_common()],
        "non_sleeve_rescue_pairs": sorted(non_sleeve_pairs),
    }


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
    oof_gate = report["anti_drift"]
    split_rows = report["split_metrics"]
    gates = oof_gate["gates"]
    lines = [
        "# Goal Family Compatibility What-if",
        "",
        "Stage 7.5 simulates a generic family compatibility layer over the OOF-selected safety gate. It does not train, tune, change GoalSearcher, or connect any online path.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["selected_variant", report["selected_variant"]],
                ["oof_gates_passed", oof_gate["passed"]],
                ["heldout_hard_status", report["heldout_hard_status"]],
                ["oof_rescued_blocked_gain", oof_gate["rescued_blocked_gain"]],
                ["oof_new_residual_loss", oof_gate["new_residual_loss"]],
                ["oof_non_sleeve_rescue_count", oof_gate["non_sleeve_rescue_count"]],
                ["oof_non_sleeve_rescue_pair_count", oof_gate["non_sleeve_rescue_pair_count"]],
                ["top_rescue_family_share", oof_gate["top_rescue_family_share"]],
                ["anti_drift_conclusion", report["anti_drift_conclusion"]],
            ]
        ),
        "",
        "## Split Metrics",
        "",
        _md_table(
            [["split", "matrix_groups", "eligible_rows", "gated_top1_matrix", "whatif_top1_matrix", "net_vs_gated", "rescued", "new_loss", "non_sleeve_rescue"]]
            + [
                [
                    row["split"],
                    row["matrix_groups"],
                    row["eligible_anchor_rows"],
                    row["gated_hit1_rate_matrix"],
                    row["whatif_hit1_rate_matrix"],
                    row["whatif_net_vs_gated"],
                    row["rescued_blocked_gain"],
                    row["new_residual_loss"],
                    row["non_sleeve_rescue_count"],
                ]
                for row in split_rows
            ]
        ),
        "",
        "## Anti-Drift Gates",
        "",
        _md_table(
            [["gate", "passed", "value", "threshold"]]
            + [[gate["gate"], gate["passed"], gate["value"], gate["threshold"]] for gate in gates]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["name", "path"]] + [[key, value] for key, value in report["artifacts"].items()]),
        "",
        "## Next",
        "",
        report["recommended_next_stage"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _row_fields() -> list[str]:
    return [
        "split",
        "line_no",
        "variant",
        "group_index",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "raw_ltr_top_family",
        "family_pair",
        "baseline_top_family",
        "baseline_top_book",
        "raw_ltr_top_book",
        "gate_allowed",
        "gate_reason",
        "selected_outcome",
        "whatif_event",
        "compat_checked",
        "compat_override_allowed",
        "compatibility_matched",
        "compatibility_allowed",
        "compatibility_class",
        "compatibility_relation_id",
        "compatibility_confidence",
        "compatibility_param_policy",
        "compatibility_query_hits",
        "compatibility_candidate_hits",
        "compatibility_reason",
        "baseline_hit1",
        "raw_ltr_hit1",
        "gated_hit1",
        "whatif_hit1",
        "whatif_hit1_delta_vs_gated",
        "whatif_hit1_delta_vs_baseline",
        "score_margin",
        "no_family_conflict",
        "no_book_conflict",
        "no_param_conflict",
        "query_family_conflict",
        "model_family_empty",
        "baseline_positive_rank",
        "raw_ltr_positive_rank",
        "gated_positive_rank",
        "baseline_top_id",
        "baseline_top_name",
        "raw_ltr_top_id",
        "raw_ltr_top_name",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 7.5 eval-only family compatibility safety-gate what-if")
    parser.add_argument("--oof-summary", default=str(DEFAULT_OOF_SUMMARY))
    parser.add_argument("--oof-details", default=str(DEFAULT_OOF_DETAILS))
    parser.add_argument("--oof-variants", default=str(DEFAULT_OOF_VARIANTS))
    parser.add_argument("--eval-details", default=str(DEFAULT_EVAL_DETAILS))
    parser.add_argument("--eval-variants", default=str(DEFAULT_EVAL_VARIANTS))
    parser.add_argument("--compat-spec", default=str(DEFAULT_COMPAT_SPEC))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--rows-csv", default=str(DEFAULT_ROWS_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--examples-jsonl", default=str(DEFAULT_EXAMPLES_JSONL))
    parser.add_argument("--examples-per-bucket", type=int, default=4)
    parser.add_argument("--max-single-family-rescue-share", type=float, default=0.8)
    parser.add_argument("--min-non-sleeve-rescue-pairs", type=int, default=2)
    parser.add_argument("--max-new-loss-vs-rescue-rate", type=float, default=1.0)
    args = parser.parse_args()

    started = time.perf_counter()
    oof_summary = _read_json(Path(args.oof_summary))
    selected_gate = _load_selected_gate(oof_summary)
    selected_variant = _clean(selected_gate.get("name"))
    spec = _load_or_create_spec(Path(args.compat_spec))
    records = _records(spec)
    spec_validation = _validate_spec(records)

    oof_rows = _load_split_rows(Path(args.oof_details), selected_variant, records)
    base_metric_paths = [Path(args.oof_variants)]
    anti_drift = _anti_drift_gates(
        oof_rows,
        spec_validation,
        max_single_family_rescue_share=args.max_single_family_rescue_share,
        min_non_sleeve_rescue_pairs=args.min_non_sleeve_rescue_pairs,
        max_new_loss_vs_rescue_rate=args.max_new_loss_vs_rescue_rate,
    )

    eval_rows: list[dict[str, Any]] = []
    heldout_hard_status = "skipped_due_to_oof_anti_drift_gate_failure"
    if anti_drift["passed"]:
        eval_rows = _load_split_rows(Path(args.eval_details), selected_variant, records)
        base_metric_paths.append(Path(args.eval_variants))
        heldout_hard_status = "evaluated_once_after_oof_gates_passed"

    all_rows = oof_rows + eval_rows
    base_metrics = _read_variant_metrics(base_metric_paths, selected_variant)
    split_metrics = _split_metrics(all_rows, base_metrics)
    bucket_rows = _counter_rows(all_rows)
    examples = _examples(all_rows, args.examples_per_bucket)
    anti_drift_conclusion = (
        "PASS: OOF 兼容层收益跨多个对象族，未依赖泄漏字段，可按冻结 spec 对 heldout/hard 做一次验收。"
        if anti_drift["passed"]
        else "STOP: OOF 防跑偏 gate 未通过，按约束不允许 heldout/hard 验收。"
    )

    report = {
        "stage": "Goal LTR v1 / stage 7.5 family compatibility what-if",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "oof_summary": str(Path(args.oof_summary)),
        "oof_details": str(Path(args.oof_details)),
        "oof_variants": str(Path(args.oof_variants)),
        "eval_details": str(Path(args.eval_details)),
        "eval_variants": str(Path(args.eval_variants)),
        "compat_spec": str(Path(args.compat_spec)),
        "selected_gate": selected_gate,
        "selected_variant": selected_variant,
        "spec_validation": spec_validation,
        "anti_drift": anti_drift,
        "heldout_hard_status": heldout_hard_status,
        "split_metrics": split_metrics,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": anti_drift_conclusion,
        "recommended_next_stage": (
            "Stage 7.6: if heldout/hard net is positive and new losses remain low, audit compatibility residuals by relation before any eval-only switch wiring."
            if anti_drift["passed"]
            else "Stage 7.6: revise the generic compatibility schema, not per-family patches, then rerun OOF only."
        ),
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "rows_csv": str(Path(args.rows_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "examples_jsonl": str(Path(args.examples_jsonl)),
            "compat_spec": str(Path(args.compat_spec)),
        },
    }

    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.rows_csv), all_rows, _row_fields())
    _write_csv(Path(args.bucket_csv), bucket_rows, ["split", "whatif_event", "bucket", "key", "count", "rate"])
    _write_jsonl(Path(args.examples_jsonl), examples)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "selected_variant": selected_variant,
                    "heldout_hard_status": heldout_hard_status,
                    "oof_gates_passed": anti_drift["passed"],
                    "rescued_blocked_gain": anti_drift["rescued_blocked_gain"],
                    "new_residual_loss": anti_drift["new_residual_loss"],
                    "non_sleeve_rescue_count": anti_drift["non_sleeve_rescue_count"],
                    "non_sleeve_rescue_pair_count": anti_drift["non_sleeve_rescue_pair_count"],
                    "top_rescue_family_share": anti_drift["top_rescue_family_share"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_metrics": split_metrics,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
