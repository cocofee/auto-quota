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

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_TRIAL_SUMMARY = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial_summary.json"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial" / "goal_query_anchored_ltr_dev_trial.txt"
DEFAULT_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_query_anchored_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_whatif_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_whatif_summary.md"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_whatif_variants.csv"
DEFAULT_BUCKET_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_whatif_buckets.csv"
DEFAULT_DETAILS_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_safety_gate_whatif_details.jsonl"

PARAM_MATCH_FEATURES = (
    "param_exact_count",
    "param_tier_up_count",
    "dn_exact",
    "dn_tier_up",
    "cable_section_exact",
    "cable_section_tier_up",
    "cable_cores_exact",
    "circuits_exact",
    "circuits_tier_up",
    "concrete_grade_exact",
    "thickness_exact",
    "thickness_tier_up",
    "width_height_exact",
    "width_height_tier_match",
)

STRUCTURAL_FEATURES = (
    "field_score",
    "numeric_score",
    "domain_rule_score",
    "family_match",
    "action_match",
    "material_match",
    "connection_match",
    "install_method_match",
    "candidate_family_present",
    "param_exact_count",
    "dn_exact",
    "width_height_exact",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(float(np.median(values)), 6) if values else None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_group(path: Path) -> list[int]:
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_features(path: Path) -> list[str]:
    payload = _read_json(path)
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} missing training_features")
    return [_clean(feature) for feature in features if _clean(feature)]


def _load_split(
    data_dir: Path,
    split: str,
    features: list[str],
) -> tuple[pd.DataFrame, np.ndarray, list[int], list[dict[str, Any]], list[dict[str, Any]]]:
    matrix_path = data_dir / f"ltr_matrix_{split}.csv"
    group_path = data_dir / f"ltr_group_{split}.txt"
    meta_path = data_dir / f"ltr_group_{split}.jsonl"
    feature_path = data_dir / f"ltr_features_{split}.jsonl"

    df = pd.read_csv(matrix_path, encoding="utf-8-sig")
    missing = [feature for feature in features if feature not in df.columns]
    if missing:
        raise ValueError(f"{matrix_path} missing features: {missing[:10]}")
    labels = df["label"].astype(np.int32).to_numpy()
    groups = _read_group(group_path)
    meta = _read_jsonl(meta_path)
    feature_rows = _read_jsonl(feature_path)
    if sum(groups) != len(df):
        raise ValueError(f"{split} group sum {sum(groups)} != matrix rows {len(df)}")
    if len(groups) != len(meta):
        raise ValueError(f"{split} group count {len(groups)} != meta rows {len(meta)}")
    if len(feature_rows) != len(df):
        raise ValueError(f"{split} feature rows {len(feature_rows)} != matrix rows {len(df)}")
    return df[features].astype(np.float32), labels, groups, meta, feature_rows


def _dcg(labels: np.ndarray, order: np.ndarray, k: int) -> float:
    score = 0.0
    for rank, idx in enumerate(order[:k], start=1):
        rel = float(labels[idx])
        if rel <= 0:
            continue
        score += (2.0**rel - 1.0) / math.log2(rank + 1.0)
    return score


def _ndcg_at(labels: np.ndarray, order: np.ndarray, k: int) -> float:
    ideal = np.argsort(-labels, kind="stable")
    ideal_score = _dcg(labels, ideal, k)
    if ideal_score <= 0:
        return 0.0
    return _dcg(labels, order, k) / ideal_score


def _first_positive_rank(labels: np.ndarray, order: np.ndarray) -> int | None:
    hits = np.flatnonzero(labels[order] > 0)
    return int(hits[0] + 1) if len(hits) else None


def _has_param_support(row: dict[str, Any]) -> bool:
    return any(_int(row, feature) > 0 for feature in PARAM_MATCH_FEATURES)


def _same_non_empty(left: str, right: str) -> bool:
    return bool(left and right and left == right)


def _same_or_unknown(left: str, right: str) -> bool:
    return not left or not right or left == right


def _candidate_label(row: dict[str, Any]) -> str:
    return f"{_clean(row.get('quota_id'))} {_clean(row.get('quota_name'))}".strip()


def _base_metrics(prefix: str, labels: np.ndarray, order: np.ndarray) -> dict[str, Any]:
    positive_rank = _first_positive_rank(labels, order)
    return {
        f"{prefix}_positive_rank": positive_rank,
        f"{prefix}_hit1": bool(positive_rank == 1),
        f"{prefix}_hit5": bool(positive_rank is not None and positive_rank <= 5),
        f"{prefix}_ndcg1": _ndcg_at(labels, order, 1),
        f"{prefix}_ndcg5": _ndcg_at(labels, order, 5),
    }


def _gate_facts(
    *,
    baseline_row: dict[str, Any],
    ltr_row: dict[str, Any],
    baseline_score: float,
    ltr_score: float,
) -> dict[str, Any]:
    baseline_family = _clean(baseline_row.get("candidate_family"))
    ltr_family = _clean(ltr_row.get("candidate_family"))
    query_family = _clean(baseline_row.get("query_family"))
    baseline_book = _clean(baseline_row.get("quota_book"))
    ltr_book = _clean(ltr_row.get("quota_book"))

    same_family_strong = _same_non_empty(baseline_family, ltr_family)
    same_family_or_empty = baseline_family == ltr_family
    same_book_strong = _same_non_empty(baseline_book, ltr_book)
    same_book_or_unknown = _same_or_unknown(baseline_book, ltr_book)
    no_family_conflict = _int(ltr_row, "family_conflict") == 0 and _int(ltr_row, "has_family_conflict_reason") == 0
    no_book_conflict = _int(ltr_row, "book_conflict") == 0 and _int(ltr_row, "has_book_conflict_reason") == 0
    no_param_conflict = _int(ltr_row, "param_conflict_count") == 0 and _int(ltr_row, "has_param_conflict_reason") == 0
    query_family_conflict = bool(query_family and ltr_family and query_family != ltr_family)
    model_family_empty = bool(query_family and not ltr_family)
    strong_family_book_param = same_family_strong and same_book_or_unknown and no_family_conflict and no_book_conflict and no_param_conflict
    weak_same_empty_book_param = same_family_or_empty and same_book_or_unknown and no_family_conflict and no_book_conflict and no_param_conflict
    lexical_gain = _float(ltr_row, "bm25_score") > _float(baseline_row, "bm25_score") + 0.10 or _float(ltr_row, "token_overlap") > _float(baseline_row, "token_overlap") + 0.05
    structural_loss = any(_float(ltr_row, feature) < _float(baseline_row, feature) for feature in STRUCTURAL_FEATURES)

    return {
        "baseline_family": baseline_family,
        "ltr_family": ltr_family,
        "query_family": query_family,
        "baseline_book": baseline_book,
        "ltr_book": ltr_book,
        "same_family_strong": same_family_strong,
        "same_family_or_empty": same_family_or_empty,
        "same_book_strong": same_book_strong,
        "same_book_or_unknown": same_book_or_unknown,
        "no_family_conflict": no_family_conflict,
        "no_book_conflict": no_book_conflict,
        "no_param_conflict": no_param_conflict,
        "query_family_conflict": query_family_conflict,
        "model_family_empty": model_family_empty,
        "strong_family_book_param": strong_family_book_param,
        "weak_same_empty_book_param": weak_same_empty_book_param,
        "ltr_param_support": _has_param_support(ltr_row),
        "baseline_param_support": _has_param_support(baseline_row),
        "score_margin": float(ltr_score - baseline_score),
        "model_rank": int(_float(ltr_row, "candidate_rank") or 0),
        "lexical_gain": lexical_gain,
        "structural_loss": structural_loss,
        "lexical_over_structure": lexical_gain and structural_loss,
    }


def _gate_decision(variant: dict[str, Any], ltr_top_idx: int, facts: dict[str, Any]) -> tuple[bool, str]:
    if ltr_top_idx == 0:
        return True, "same_as_baseline"
    mode = variant["mode"]
    margin = float(variant.get("margin") or 0.0)
    if mode == "baseline":
        return False, "blocked_baseline_only"
    if mode == "raw":
        return True, "raw_ltr"
    if mode == "strict_strong":
        if facts["strong_family_book_param"]:
            return True, "strong_same_family_book_no_conflict"
        return False, "blocked_not_strong_safe"
    if mode == "strict_weak":
        if facts["weak_same_empty_book_param"]:
            return True, "same_or_empty_family_book_no_conflict"
        return False, "blocked_not_weak_safe"
    if mode == "strong_or_margin":
        if facts["strong_family_book_param"]:
            return True, "strong_same_family_book_no_conflict"
        if facts["score_margin"] >= margin and not facts["query_family_conflict"] and facts["no_param_conflict"]:
            return True, "large_margin_no_family_param_conflict"
        return False, "blocked_by_strong_or_margin_gate"
    if mode == "weak_or_margin":
        if facts["weak_same_empty_book_param"]:
            return True, "same_or_empty_family_book_no_conflict"
        if facts["score_margin"] >= margin and not facts["query_family_conflict"] and facts["no_param_conflict"]:
            return True, "large_margin_no_family_param_conflict"
        return False, "blocked_by_weak_or_margin_gate"
    if mode == "guarded_margin":
        if facts["strong_family_book_param"]:
            return True, "strong_same_family_book_no_conflict"
        if facts["score_margin"] < margin:
            return False, "blocked_margin_too_small"
        if facts["query_family_conflict"] or facts["model_family_empty"]:
            return False, "blocked_family_risk"
        if not facts["no_param_conflict"]:
            return False, "blocked_param_conflict"
        if facts["model_rank"] >= 10 and not facts["ltr_param_support"]:
            return False, "blocked_deep_rank_without_param_support"
        if facts["lexical_over_structure"]:
            return False, "blocked_lexical_over_structure"
        return True, "guarded_large_margin"
    raise ValueError(f"unknown gate mode: {mode}")


def _gated_order(raw_order: np.ndarray, allow_override: bool) -> np.ndarray:
    if allow_override:
        return raw_order
    remaining = [idx for idx in raw_order.tolist() if idx != 0]
    return np.array([0, *remaining], dtype=np.int64)


def _make_variants(margins: list[float]) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = [
        {"name": "baseline_only", "mode": "baseline"},
        {"name": "raw_ltr", "mode": "raw"},
        {"name": "strict_strong_family_book_param", "mode": "strict_strong"},
        {"name": "strict_weak_empty_family_book_param", "mode": "strict_weak"},
    ]
    for margin in margins:
        label = str(margin).replace(".", "_")
        variants.append({"name": f"strong_or_margin_{label}", "mode": "strong_or_margin", "margin": margin})
        variants.append({"name": f"weak_or_margin_{label}", "mode": "weak_or_margin", "margin": margin})
        variants.append({"name": f"guarded_margin_{label}", "mode": "guarded_margin", "margin": margin})
    return variants


def _summarize_variant(
    *,
    split: str,
    variant: dict[str, Any],
    rows: list[dict[str, Any]],
    eligible_rows: int,
    recall_gap_groups: int,
) -> dict[str, Any]:
    total = len(rows)
    baseline_hit1 = sum(int(row["baseline_hit1"]) for row in rows)
    baseline_hit5 = sum(int(row["baseline_hit5"]) for row in rows)
    raw_hit1 = sum(int(row["raw_ltr_hit1"]) for row in rows)
    raw_hit5 = sum(int(row["raw_ltr_hit5"]) for row in rows)
    gated_hit1 = sum(int(row["gated_hit1"]) for row in rows)
    gated_hit5 = sum(int(row["gated_hit5"]) for row in rows)
    hit1_gain = sum(int((not row["baseline_hit1"]) and row["gated_hit1"]) for row in rows)
    hit1_loss = sum(int(row["baseline_hit1"] and (not row["gated_hit1"])) for row in rows)
    hit5_gain = sum(int((not row["baseline_hit5"]) and row["gated_hit5"]) for row in rows)
    hit5_loss = sum(int(row["baseline_hit5"] and (not row["gated_hit5"])) for row in rows)
    raw_hit1_gain = sum(int((not row["baseline_hit1"]) and row["raw_ltr_hit1"]) for row in rows)
    raw_hit1_loss = sum(int(row["baseline_hit1"] and (not row["raw_ltr_hit1"])) for row in rows)
    prevented_raw_loss = sum(int(row["baseline_hit1"] and (not row["raw_ltr_hit1"]) and row["gated_hit1"]) for row in rows)
    blocked_raw_gain = sum(int((not row["baseline_hit1"]) and row["raw_ltr_hit1"] and (not row["gated_hit1"])) for row in rows)
    passed_raw_gain = sum(int((not row["baseline_hit1"]) and row["raw_ltr_hit1"] and row["gated_hit1"]) for row in rows)
    passed_raw_loss = sum(int(row["baseline_hit1"] and (not row["raw_ltr_hit1"]) and (not row["gated_hit1"])) for row in rows)
    raw_override_count = sum(int(not row["raw_same_as_baseline"]) for row in rows)
    gated_override_count = sum(int((not row["raw_same_as_baseline"]) and row["gate_allowed"]) for row in rows)
    ranks = [float(row["gated_positive_rank"]) for row in rows if row["gated_positive_rank"] is not None]
    reason_counts = Counter(row["gate_reason"] for row in rows)
    decision_counts = Counter("allowed" if row["gate_allowed"] else "blocked" for row in rows if not row["raw_same_as_baseline"])

    return {
        "split": split,
        "variant": variant["name"],
        "mode": variant["mode"],
        "margin": variant.get("margin"),
        "matrix_groups": total,
        "eligible_anchor_rows": eligible_rows,
        "recall_gap_groups": recall_gap_groups,
        "top80_recall_rate": _rate(total, eligible_rows),
        "baseline_hit1": baseline_hit1,
        "baseline_hit1_rate_matrix": _rate(baseline_hit1, total),
        "baseline_hit1_rate_eligible": _rate(baseline_hit1, eligible_rows),
        "baseline_hit5": baseline_hit5,
        "baseline_hit5_rate_matrix": _rate(baseline_hit5, total),
        "baseline_hit5_rate_eligible": _rate(baseline_hit5, eligible_rows),
        "raw_ltr_hit1": raw_hit1,
        "raw_ltr_hit1_rate_matrix": _rate(raw_hit1, total),
        "raw_ltr_hit1_rate_eligible": _rate(raw_hit1, eligible_rows),
        "raw_ltr_hit5": raw_hit5,
        "raw_ltr_hit5_rate_matrix": _rate(raw_hit5, total),
        "raw_ltr_hit5_rate_eligible": _rate(raw_hit5, eligible_rows),
        "raw_ltr_hit1_gain": raw_hit1_gain,
        "raw_ltr_hit1_loss": raw_hit1_loss,
        "raw_ltr_hit1_net": raw_hit1_gain - raw_hit1_loss,
        "gated_hit1": gated_hit1,
        "gated_hit1_rate_matrix": _rate(gated_hit1, total),
        "gated_hit1_rate_eligible": _rate(gated_hit1, eligible_rows),
        "gated_hit1_gain": hit1_gain,
        "gated_hit1_loss": hit1_loss,
        "gated_hit1_net": hit1_gain - hit1_loss,
        "gated_hit5": gated_hit5,
        "gated_hit5_rate_matrix": _rate(gated_hit5, total),
        "gated_hit5_rate_eligible": _rate(gated_hit5, eligible_rows),
        "gated_hit5_gain": hit5_gain,
        "gated_hit5_loss": hit5_loss,
        "gated_hit5_net": hit5_gain - hit5_loss,
        "prevented_raw_hit1_loss": prevented_raw_loss,
        "blocked_raw_hit1_gain": blocked_raw_gain,
        "passed_raw_hit1_gain": passed_raw_gain,
        "passed_raw_hit1_loss": passed_raw_loss,
        "raw_override_count": raw_override_count,
        "gated_override_count": gated_override_count,
        "gated_override_rate": _rate(gated_override_count, raw_override_count),
        "gate_decisions": dict(decision_counts),
        "gate_reasons": dict(reason_counts),
        "gated_rank_avg": _mean(ranks),
        "gated_rank_median": _median(ranks),
        "gated_ndcg1": _mean([float(row["gated_ndcg1"]) for row in rows]),
        "gated_ndcg5": _mean([float(row["gated_ndcg5"]) for row in rows]),
    }


def _evaluate_split(
    *,
    split: str,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    preds: np.ndarray,
    variants: list[dict[str, Any]],
    eligible_rows: int,
    recall_gap_groups: int,
    detail_mode: str,
    detail_handle,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    bucket_source_rows: list[dict[str, Any]] = []
    variant_rows: dict[str, list[dict[str, Any]]] = {variant["name"]: [] for variant in variants}
    start = 0

    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = preds[start:stop]
        group_rows = feature_rows[start:stop]
        baseline_order = np.arange(size)
        raw_order = np.lexsort((np.arange(size), -group_preds))
        baseline_idx = 0
        ltr_top_idx = int(raw_order[0])
        baseline_row = group_rows[baseline_idx]
        ltr_row = group_rows[ltr_top_idx]
        group_meta = meta[group_idx]
        facts = _gate_facts(
            baseline_row=baseline_row,
            ltr_row=ltr_row,
            baseline_score=float(group_preds[baseline_idx]),
            ltr_score=float(group_preds[ltr_top_idx]),
        )
        baseline_metrics = _base_metrics("baseline", group_labels, baseline_order)
        raw_metrics = _base_metrics("raw_ltr", group_labels, raw_order)

        for variant in variants:
            allow_override, gate_reason = _gate_decision(variant, ltr_top_idx, facts)
            gated_order = _gated_order(raw_order, allow_override)
            gated_metrics = _base_metrics("gated", group_labels, gated_order)
            gated_idx = int(gated_order[0])
            gated_row = group_rows[gated_idx]
            detail = {
                "split": split,
                "variant": variant["name"],
                "group_index": group_idx + 1,
                "group_id": _clean(group_meta.get("group_id")),
                "sample_id": _clean(group_meta.get("sample_id")),
                "source_file": _clean(group_meta.get("source_file")),
                "project_name": _clean(group_meta.get("project_name")),
                "province": _clean(group_meta.get("province")),
                "query": _clean(group_meta.get("query")),
                "query_family": _clean(group_meta.get("query_family")) or "<empty>",
                "expected_ids": "|".join(str(value) for value in group_meta.get("expected_ids", [])),
                "positive_count": int(np.sum(group_labels > 0)),
                "raw_same_as_baseline": ltr_top_idx == baseline_idx,
                "gate_allowed": bool(allow_override),
                "gate_reason": gate_reason,
                "score_margin": round(float(facts["score_margin"]), 8),
                "same_family_strong": bool(facts["same_family_strong"]),
                "same_family_or_empty": bool(facts["same_family_or_empty"]),
                "same_book_or_unknown": bool(facts["same_book_or_unknown"]),
                "no_family_conflict": bool(facts["no_family_conflict"]),
                "no_book_conflict": bool(facts["no_book_conflict"]),
                "no_param_conflict": bool(facts["no_param_conflict"]),
                "query_family_conflict": bool(facts["query_family_conflict"]),
                "model_family_empty": bool(facts["model_family_empty"]),
                "strong_family_book_param": bool(facts["strong_family_book_param"]),
                "weak_same_empty_book_param": bool(facts["weak_same_empty_book_param"]),
                "ltr_param_support": bool(facts["ltr_param_support"]),
                "baseline_param_support": bool(facts["baseline_param_support"]),
                "lexical_over_structure": bool(facts["lexical_over_structure"]),
                "baseline_top_rank": 1,
                "baseline_top_score": round(float(group_preds[baseline_idx]), 8),
                "baseline_top_id": _clean(baseline_row.get("quota_id")),
                "baseline_top_name": _clean(baseline_row.get("quota_name")),
                "baseline_top_family": _clean(baseline_row.get("candidate_family")) or "<empty>",
                "baseline_top_book": _clean(baseline_row.get("quota_book")) or "<empty>",
                "baseline_top": _candidate_label(baseline_row),
                "raw_ltr_top_rank": int(ltr_top_idx + 1),
                "raw_ltr_top_score": round(float(group_preds[ltr_top_idx]), 8),
                "raw_ltr_top_id": _clean(ltr_row.get("quota_id")),
                "raw_ltr_top_name": _clean(ltr_row.get("quota_name")),
                "raw_ltr_top_family": _clean(ltr_row.get("candidate_family")) or "<empty>",
                "raw_ltr_top_book": _clean(ltr_row.get("quota_book")) or "<empty>",
                "raw_ltr_top": _candidate_label(ltr_row),
                "gated_top_rank": int(gated_idx + 1),
                "gated_top_score": round(float(group_preds[gated_idx]), 8),
                "gated_top_id": _clean(gated_row.get("quota_id")),
                "gated_top_name": _clean(gated_row.get("quota_name")),
                "gated_top_family": _clean(gated_row.get("candidate_family")) or "<empty>",
                "gated_top_book": _clean(gated_row.get("quota_book")) or "<empty>",
                "gated_top": _candidate_label(gated_row),
                **baseline_metrics,
                **raw_metrics,
                **gated_metrics,
            }
            detail.update(
                {
                    "raw_hit1_delta_vs_baseline": int(detail["raw_ltr_hit1"]) - int(detail["baseline_hit1"]),
                    "gated_hit1_delta_vs_baseline": int(detail["gated_hit1"]) - int(detail["baseline_hit1"]),
                    "raw_hit5_delta_vs_baseline": int(detail["raw_ltr_hit5"]) - int(detail["baseline_hit5"]),
                    "gated_hit5_delta_vs_baseline": int(detail["gated_hit5"]) - int(detail["baseline_hit5"]),
                    "prevented_raw_hit1_loss": bool(detail["baseline_hit1"] and (not detail["raw_ltr_hit1"]) and detail["gated_hit1"]),
                    "blocked_raw_hit1_gain": bool((not detail["baseline_hit1"]) and detail["raw_ltr_hit1"] and (not detail["gated_hit1"])),
                }
            )
            variant_rows[variant["name"]].append(detail)
            bucket_source_rows.append(detail)
            should_write_detail = (
                detail_mode == "all"
                or not detail["raw_same_as_baseline"]
                or detail["raw_hit1_delta_vs_baseline"] != 0
                or detail["gated_hit1_delta_vs_baseline"] != 0
                or detail["prevented_raw_hit1_loss"]
                or detail["blocked_raw_hit1_gain"]
            )
            if should_write_detail:
                detail_handle.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")
        start = stop

    for variant in variants:
        summaries.append(
            _summarize_variant(
                split=split,
                variant=variant,
                rows=variant_rows[variant["name"]],
                eligible_rows=eligible_rows,
                recall_gap_groups=recall_gap_groups,
            )
        )
    return summaries, bucket_source_rows


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "gate_reason",
        "query_family",
        "source_file",
        "province",
        "raw_ltr_top_family",
        "raw_ltr_top_book",
    ]
    counters: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)
    totals: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        if row["raw_hit1_delta_vs_baseline"] == 1 and row["blocked_raw_hit1_gain"]:
            outcome = "blocked_gain"
        elif row["raw_hit1_delta_vs_baseline"] == -1 and row["prevented_raw_hit1_loss"]:
            outcome = "saved_loss"
        elif row["raw_hit1_delta_vs_baseline"] == 1 and row["gated_hit1_delta_vs_baseline"] == 1:
            outcome = "passed_gain"
        elif row["raw_hit1_delta_vs_baseline"] == -1 and row["gated_hit1_delta_vs_baseline"] == -1:
            outcome = "passed_loss"
        else:
            continue
        key_base = (row["split"], row["variant"], outcome)
        totals[key_base] += 1
        for field in fields:
            counters[(*key_base, field)][_clean(row.get(field)) or "<empty>"] += 1

    result: list[dict[str, Any]] = []
    for (split, variant, outcome, field), counter in sorted(counters.items()):
        total = totals[(split, variant, outcome)]
        for key, count in counter.most_common(20):
            result.append(
                {
                    "split": split,
                    "variant": variant,
                    "outcome": outcome,
                    "bucket": field,
                    "key": key,
                    "count": count,
                    "rate": _rate(count, total),
                }
            )
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
    rows = report["variant_summaries"]
    compact = [
        [
            item["split"],
            item["variant"],
            item["gated_hit1_rate_eligible"],
            item["gated_hit1_net"],
            item["gated_hit1_gain"],
            item["gated_hit1_loss"],
            item["prevented_raw_hit1_loss"],
            item["blocked_raw_hit1_gain"],
            item["gated_hit5_rate_eligible"],
            item["gated_hit5_net"],
            item["gated_override_rate"],
        ]
        for item in rows
    ]
    lines = [
        "# Goal Query-Anchored LTR Safety Gate What-If",
        "",
        "Stage 6.9 eval-only safety gate what-if over all heldout/hard matrix groups. It loads the existing offline model and matrix for prediction only; no training, no search integration, no rerank switch.",
        "",
        "When a gate blocks an LTR Top1 override, baseline Top1 is kept and the remaining candidates stay ordered by LTR score for Top5 diagnostics. Recall-gap groups are counted as misses in eligible rates.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["splits", ", ".join(report["splits"])],
                ["variants", len(report["variants"])],
                ["detail_mode", report["detail_mode"]],
                ["model_path", report["model_path"]],
                ["elapsed_sec", report["elapsed_sec"]],
                ["recommended_next_stage", report["recommended_next_stage"]],
            ]
        ),
        "",
        "## Variant Metrics",
        "",
        _md_table(
            [
                [
                    "split",
                    "variant",
                    "top1_all",
                    "net",
                    "gain",
                    "loss",
                    "saved_loss",
                    "blocked_gain",
                    "top5_all",
                    "top5_net",
                    "override_rate",
                ],
                *compact,
            ]
        ),
        "",
        "## Notes",
        "",
        "- This is diagnostic what-if only. Do not choose a production threshold from heldout/hard.",
        "- Use dev/calibration to freeze any threshold, then rerun heldout/hard once.",
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _variant_fields() -> list[str]:
    return [
        "split",
        "variant",
        "mode",
        "margin",
        "matrix_groups",
        "eligible_anchor_rows",
        "recall_gap_groups",
        "top80_recall_rate",
        "baseline_hit1_rate_eligible",
        "raw_ltr_hit1_rate_eligible",
        "gated_hit1_rate_eligible",
        "gated_hit1_net",
        "gated_hit1_gain",
        "gated_hit1_loss",
        "prevented_raw_hit1_loss",
        "blocked_raw_hit1_gain",
        "passed_raw_hit1_gain",
        "passed_raw_hit1_loss",
        "baseline_hit5_rate_eligible",
        "raw_ltr_hit5_rate_eligible",
        "gated_hit5_rate_eligible",
        "gated_hit5_net",
        "raw_override_count",
        "gated_override_count",
        "gated_override_rate",
        "gated_ndcg5",
    ]


def _bucket_fields() -> list[str]:
    return ["split", "variant", "outcome", "bucket", "key", "count", "rate"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 6.9 eval-only query-anchored LTR safety gate what-if")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--trial-summary", default=str(DEFAULT_TRIAL_SUMMARY))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--margins", nargs="+", type=float, default=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--bucket-csv", default=str(DEFAULT_BUCKET_CSV))
    parser.add_argument("--details-jsonl", default=str(DEFAULT_DETAILS_JSONL))
    parser.add_argument("--detail-mode", choices=["events", "all"], default="events")
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    model_path = Path(args.model)
    features = _load_features(Path(args.feature_whitelist))
    trial_summary = _read_json(Path(args.trial_summary))
    eval_by_split = {item.get("split"): item for item in trial_summary.get("evaluations", [])}
    booster = lgb.Booster(model_file=str(model_path))
    variants = _make_variants(args.margins)

    all_summaries: list[dict[str, Any]] = []
    all_bucket_source_rows: list[dict[str, Any]] = []
    details_path = Path(args.details_jsonl)
    details_path.parent.mkdir(parents=True, exist_ok=True)
    with details_path.open("w", encoding="utf-8") as detail_handle:
        for split in args.splits:
            x, labels, groups, meta, feature_rows = _load_split(data_dir, split, features)
            preds = booster.predict(x, num_iteration=booster.current_iteration())
            split_summary = eval_by_split.get(split, {})
            eligible_rows = int(split_summary.get("eligible_anchor_rows") or len(groups))
            recall_gap_groups = int(split_summary.get("recall_gap_groups") or max(0, eligible_rows - len(groups)))
            summaries, bucket_source_rows = _evaluate_split(
                split=split,
                labels=labels,
                groups=groups,
                meta=meta,
                feature_rows=feature_rows,
                preds=preds,
                variants=variants,
                eligible_rows=eligible_rows,
                recall_gap_groups=recall_gap_groups,
                detail_mode=args.detail_mode,
                detail_handle=detail_handle,
            )
            all_summaries.extend(summaries)
            all_bucket_source_rows.extend(bucket_source_rows)

    bucket_rows = _bucket_rows(all_bucket_source_rows)
    report = {
        "stage": "Goal LTR v1 / stage 6.9 eval-only query anchored safety gate what-if",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_search_integration": True,
        "model_path": str(model_path),
        "data_dir": str(data_dir),
        "feature_whitelist": str(Path(args.feature_whitelist)),
        "trial_summary": str(Path(args.trial_summary)),
        "splits": args.splits,
        "variants": variants,
        "detail_mode": args.detail_mode,
        "variant_summaries": all_summaries,
        "bucket_rows": bucket_rows,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "recommended_next_stage": "Stage 7.0: choose safety gate thresholds on dev/calibration only, then rerun heldout/hard once.",
        "artifacts": {
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
            "variants_csv": str(Path(args.variants_csv)),
            "bucket_csv": str(Path(args.bucket_csv)),
            "details_jsonl": str(details_path),
        },
    }

    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    _write_csv(Path(args.variants_csv), all_summaries, _variant_fields())
    _write_csv(Path(args.bucket_csv), bucket_rows, _bucket_fields())

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "eval_only": report["eval_only"],
                    "no_training": report["no_training"],
                    "no_search_integration": report["no_search_integration"],
                    "splits": args.splits,
                    "variants": [variant["name"] for variant in variants],
                    "detail_mode": args.detail_mode,
                    "elapsed_sec": report["elapsed_sec"],
                    "recommended_next_stage": report["recommended_next_stage"],
                },
                "variant_summaries": all_summaries,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
