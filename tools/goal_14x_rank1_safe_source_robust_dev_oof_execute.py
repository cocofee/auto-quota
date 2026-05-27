from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from tools.goal_10x_offline_ranking_experiment_dev_oof_execute import (  # noqa: E402
    _load_dev_matrix,
    _load_training_features,
    _take_groups,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_PLAN = AGENT_STATE / "goal_14x_rank1_safe_source_robust_experiment_plan_definition_candidate_matrix.csv"
DEFAULT_FEATURE_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_oss_source_aware_v1.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"

FORBIDDEN_FEATURES = {
    "source_file",
    "source_family",
    "source_region",
    "province",
    "sample_id",
    "group_id",
    "quota_id",
    "expected_id",
    "expected_ids",
    "positive_rank",
    "baseline_rank",
    "label",
}
BOOK_FEATURES = {"book_requested", "book_match", "book_conflict", "chapter_book_match"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text or "<empty>"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _rank_bucket(rank: int | None) -> str:
    if rank is None:
        return "missing_positive"
    if rank == 1:
        return "rank_1"
    if 2 <= rank <= 5:
        return "rank_2_5"
    if 6 <= rank <= 10:
        return "rank_6_10"
    if 11 <= rank <= 20:
        return "rank_11_20"
    if 21 <= rank <= 40:
        return "rank_21_40"
    return "rank_41_80"


def _first_positive_rank(labels: np.ndarray, order: np.ndarray) -> int | None:
    hits = np.flatnonzero(labels[order] > 0)
    return int(hits[0] + 1) if len(hits) else None


def _source_fold_assignments(meta: list[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    by_fold: dict[int, list[int]] = defaultdict(list)
    for idx, row in enumerate(meta):
        by_fold[int(row.get("oof_fold") or 0)].append(idx)
    return [(fold, by_fold[fold]) for fold in sorted(by_fold)]


def _candidate_features(toggle_id: str, all_features: list[str]) -> list[str]:
    if toggle_id in {"FT_R14_SAFE_CORE_PLUS_CHALLENGER", "FT_R14_SAFE_CORE_PLUS_CONFLICT"}:
        return list(all_features)
    if toggle_id == "FT_R14_SAFE_CORE_NO_BOOK_ID":
        return [feature for feature in all_features if feature not in BOOK_FEATURES]
    raise ValueError(f"unknown R14 feature toggle: {toggle_id}")


def _objective_params(objective_variant: str, seed: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 5, 10],
        "learning_rate": 0.035,
        "num_leaves": 27,
        "min_data_in_leaf": 45,
        "feature_fraction": 0.88,
        "bagging_fraction": 0.88,
        "bagging_freq": 1,
        "lambda_l2": 2.0,
        "label_gain": [0, 1],
        "verbosity": -1,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
        "num_threads": 0,
    }
    if objective_variant == "OBJ_R14_conflict_weighted":
        params["min_data_in_leaf"] = 55
        params["lambda_l2"] = 2.5
    elif objective_variant == "OBJ_R14_top1_demote_penalty_high":
        params["min_data_in_leaf"] = 65
        params["lambda_l2"] = 3.0
        params["num_leaves"] = 23
    elif objective_variant == "OBJ_R14_pairwise_near_miss_proxy":
        params["min_data_in_leaf"] = 40
        params["lambda_l2"] = 1.5
        params["num_leaves"] = 31
    return params


def _group_weights(objective_variant: str, labels: np.ndarray, groups: list[int]) -> np.ndarray:
    weights = np.ones(len(labels), dtype=np.float32)
    start = 0
    for size in groups:
        stop = start + size
        group_labels = labels[start:stop]
        pos = np.flatnonzero(group_labels > 0)
        positive_rank = int(pos[0] + 1) if len(pos) else None
        baseline_hit = positive_rank == 1
        near_miss = positive_rank is not None and 2 <= positive_rank <= 10
        if objective_variant == "OBJ_R14_top1_loss_guarded":
            weights[start:stop] = 2.2 if baseline_hit else 1.15
        elif objective_variant == "OBJ_R14_conflict_weighted":
            weights[start:stop] = 1.9 if baseline_hit else 1.05
        elif objective_variant == "OBJ_R14_top1_demote_penalty_high":
            weights[start:stop] = 3.0 if baseline_hit else 1.0
        elif objective_variant == "OBJ_R14_pairwise_near_miss_proxy":
            weights[start:stop] = 1.8 if near_miss else 1.2 if baseline_hit else 0.85
        start = stop
    return weights


def _train_candidate(
    *,
    candidate: dict[str, Any],
    df,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    features: list[str],
    num_boost_round: int,
    seed: int,
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    preds = np.zeros(len(labels), dtype=np.float32)
    fold_rows: list[dict[str, Any]] = []
    importance_counter: Counter[str] = Counter()
    group_indices = list(range(len(groups)))
    params = _objective_params(candidate["objective_variant"], seed)
    for fold_value, valid_group_indices in _source_fold_assignments(meta):
        valid_set = set(valid_group_indices)
        train_group_indices = [idx for idx in group_indices if idx not in valid_set]
        train_df, train_y, train_groups, _ = _take_groups(df, labels, groups, train_group_indices)
        valid_df, _valid_y, valid_groups, valid_row_indices = _take_groups(df, labels, groups, valid_group_indices)
        train_data = lgb.Dataset(
            train_df[features].astype(np.float32).to_numpy(),
            label=train_y,
            group=train_groups,
            weight=_group_weights(candidate["objective_variant"], train_y, train_groups),
            feature_name=features,
            free_raw_data=False,
        )
        booster = lgb.train(
            params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[train_data],
            valid_names=["dev_train"],
            callbacks=[],
        )
        preds[valid_row_indices] = booster.predict(valid_df[features].astype(np.float32).to_numpy(), num_iteration=booster.current_iteration())
        for feature, gain in zip(features, booster.feature_importance(importance_type="gain"), strict=True):
            importance_counter[feature] += float(gain)
        valid_sources = sorted({_clean(meta[idx].get("source_family")) for idx in valid_group_indices})
        fold_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "fold": fold_value,
                "train_groups": len(train_group_indices),
                "valid_groups": len(valid_group_indices),
                "train_rows": int(sum(train_groups)),
                "valid_rows": int(sum(valid_groups)),
                "valid_source_family_count": len(valid_sources),
                "valid_source_families": "|".join(valid_sources),
                "num_boost_round": num_boost_round,
                "feature_count": len(features),
            }
        )
    importance_rows = [
        {"candidate_id": candidate["candidate_id"], "feature": feature, "gain_sum": round(gain, 6)}
        for feature, gain in importance_counter.most_common(50)
    ]
    return preds, fold_rows, importance_rows


def _top1_margin(group_rows: list[dict[str, Any]]) -> float:
    if len(group_rows) < 2:
        return 999.0
    return _float(group_rows[0].get("current_score")) - _float(group_rows[1].get("current_score"))


def _top1_thresholds(feature_rows: list[dict[str, Any]], groups: list[int]) -> dict[str, Any]:
    confidence: list[float] = []
    margin: list[float] = []
    reason_count: list[float] = []
    start = 0
    for size in groups:
        stop = start + size
        rows = feature_rows[start:stop]
        if rows:
            confidence.append(_float(rows[0].get("confidence")))
            margin.append(_top1_margin(rows))
            reason_count.append(_float(rows[0].get("reason_count")))
        start = stop
    def q(values: list[float], quantile: float) -> float:
        return round(float(np.quantile(np.array(values, dtype=np.float64), quantile)), 8) if values else 0.0
    return {
        "confidence_q20": q(confidence, 0.20),
        "confidence_q25": q(confidence, 0.25),
        "confidence_q35": q(confidence, 0.35),
        "margin_q20": q(margin, 0.20),
        "margin_q25": q(margin, 0.25),
        "margin_q35": q(margin, 0.35),
        "reason_count_q25": q(reason_count, 0.25),
        "calibration_split": "dev_oof_only",
    }


def _explicit_conflict(row: dict[str, Any]) -> bool:
    return (
        _bool(row.get("family_conflict"))
        or _bool(row.get("book_conflict"))
        or _bool(row.get("unit_conflict"))
        or _int(row.get("domain_conflict_count")) > 0
        or _int(row.get("param_conflict_count")) > 0
        or _bool(row.get("has_domain_conflict"))
        or _bool(row.get("has_family_conflict_reason"))
        or _bool(row.get("has_book_conflict_reason"))
        or _bool(row.get("has_unit_conflict_reason"))
        or _bool(row.get("has_param_conflict_reason"))
    )


def _challenger_support_score(challenger: dict[str, Any], baseline: dict[str, Any]) -> tuple[int, str]:
    parts: list[str] = []
    if _bool(challenger.get("family_match")):
        parts.append("family_match")
    if _bool(challenger.get("book_match")):
        parts.append("book_match")
    if _bool(challenger.get("action_match")):
        parts.append("action_match")
    if _bool(challenger.get("material_match")):
        parts.append("material_match")
    if _bool(challenger.get("connection_match")):
        parts.append("connection_match")
    if _float(challenger.get("numeric_score")) > _float(baseline.get("numeric_score")) + 1e-9:
        parts.append("numeric_score_superior")
    return len(parts), "|".join(parts)


def _baseline_weak_or_conflicted(baseline: dict[str, Any], rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> tuple[bool, str]:
    reasons: list[str] = []
    if _explicit_conflict(baseline):
        reasons.append("explicit_conflict")
    if _float(baseline.get("confidence")) <= _float(thresholds["confidence_q25"]):
        reasons.append("low_conf_q25")
    if _top1_margin(rows) <= _float(thresholds["margin_q25"]):
        reasons.append("small_margin_q25")
    if _float(baseline.get("reason_count")) <= _float(thresholds["reason_count_q25"]):
        reasons.append("low_reason_count_q25")
    return bool(reasons), "|".join(reasons) if reasons else "clean_baseline"


def _taxonomy_empty(group_meta: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return _clean(group_meta.get("query_family")) == "<empty>" or _clean(baseline.get("candidate_family")) == "<empty>"


def _gate_decision(
    *,
    candidate_id: str,
    rows: list[dict[str, Any]],
    group_meta: dict[str, Any],
    raw_top_idx: int,
    raw_margin_delta: float,
    thresholds: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    baseline = rows[0]
    challenger = rows[raw_top_idx]
    support_score, support_parts = _challenger_support_score(challenger, baseline)
    weak, weak_reasons = _baseline_weak_or_conflicted(baseline, rows, thresholds)
    explicit_conflict = _explicit_conflict(baseline)
    low_conf_or_small_margin = (
        _float(baseline.get("confidence")) <= _float(thresholds["confidence_q25"])
        or _top1_margin(rows) <= _float(thresholds["margin_q25"])
    )
    taxonomy_empty = _taxonomy_empty(group_meta, baseline)
    clean_rank1_veto = not weak and support_score < 2
    if raw_top_idx == 0:
        return False, "baseline_already_top1", {
            "challenger_support_score": support_score,
            "support_parts": support_parts,
            "baseline_weak_reasons": weak_reasons,
            "clean_rank1_veto": clean_rank1_veto,
            "taxonomy_empty": taxonomy_empty,
        }
    if taxonomy_empty and support_score < 3:
        return False, "taxonomy_empty_guard_insufficient_support", {
            "challenger_support_score": support_score,
            "support_parts": support_parts,
            "baseline_weak_reasons": weak_reasons,
            "clean_rank1_veto": clean_rank1_veto,
            "taxonomy_empty": taxonomy_empty,
        }
    if clean_rank1_veto:
        return False, "clean_rank1_veto", {
            "challenger_support_score": support_score,
            "support_parts": support_parts,
            "baseline_weak_reasons": weak_reasons,
            "clean_rank1_veto": clean_rank1_veto,
            "taxonomy_empty": taxonomy_empty,
        }
    if candidate_id == "R14_A_rank1_veto_strong_challenger":
        applies = weak and support_score >= 2 and raw_margin_delta >= _float(thresholds["margin_delta_q75"])
        reason = "weak_baseline_strong_challenger_q75" if applies else "r14_a_gate_not_met"
    elif candidate_id == "R14_B_conflict_plus_challenger_margin":
        applies = explicit_conflict and support_score >= 2 and raw_margin_delta >= _float(thresholds["margin_delta_q70"])
        reason = "explicit_conflict_strong_challenger_q70" if applies else "r14_b_gate_not_met"
    elif candidate_id == "R14_C_low_conf_with_challenger_veto":
        applies = low_conf_or_small_margin and support_score >= 3 and raw_margin_delta >= _float(thresholds["margin_delta_q80"])
        reason = "uncertainty_stronger_challenger_q80" if applies else "r14_c_gate_not_met"
    elif candidate_id == "R14_D_near_miss_proxy_no_clean_rank1":
        applies = _top1_margin(rows) <= _float(thresholds["margin_q35"]) and weak and support_score >= 2
        reason = "small_margin_weak_baseline_support2" if applies else "r14_d_gate_not_met"
    else:
        applies = False
        reason = "unknown_candidate"
    return applies, reason, {
        "challenger_support_score": support_score,
        "support_parts": support_parts,
        "baseline_weak_reasons": weak_reasons,
        "clean_rank1_veto": clean_rank1_veto,
        "taxonomy_empty": taxonomy_empty,
    }


def _build_margin_thresholds(preds_by_candidate: dict[str, np.ndarray], groups: list[int]) -> dict[str, Any]:
    deltas: list[float] = []
    for preds in preds_by_candidate.values():
        start = 0
        for size in groups:
            stop = start + size
            group_preds = preds[start:stop]
            if len(group_preds) > 1:
                order = np.lexsort((np.arange(size), -group_preds))
                if int(order[0]) != 0:
                    deltas.append(float(group_preds[order[0]] - group_preds[0]))
            start = stop
    if not deltas:
        deltas = [0.0]
    values = np.array(deltas, dtype=np.float64)
    return {
        "margin_delta_q70": round(float(np.quantile(values, 0.70)), 8),
        "margin_delta_q75": round(float(np.quantile(values, 0.75)), 8),
        "margin_delta_q80": round(float(np.quantile(values, 0.80)), 8),
        "margin_delta_source": "OOF_prediction_delta_candidate_top_minus_baseline_top1",
    }


def _score_candidate(
    *,
    candidate: dict[str, Any],
    preds: np.ndarray,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cid = candidate["candidate_id"]
    baseline_hit1 = candidate_hit1 = hit1_gain = hit1_loss = 0
    baseline_hit5 = candidate_hit5 = hit5_gain = hit5_loss = 0
    applied_groups = vetoed_groups = baseline_rank1_groups = rank1_loss_count = 0
    loss_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
    rank1_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    taxonomy_rows: list[dict[str, Any]] = []
    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = preds[start:stop]
        group_meta = meta[group_idx]
        rows = feature_rows[start:stop]
        baseline_order = np.arange(size)
        raw_order = np.lexsort((np.arange(size), -group_preds))
        raw_top_idx = int(raw_order[0])
        raw_delta = float(group_preds[raw_top_idx] - group_preds[0]) if len(group_preds) else 0.0
        baseline_rank = _first_positive_rank(group_labels, baseline_order)
        raw_rank = _first_positive_rank(group_labels, raw_order)
        gate_applies, gate_reason, gate_meta = _gate_decision(
            candidate_id=cid,
            rows=rows,
            group_meta=group_meta,
            raw_top_idx=raw_top_idx,
            raw_margin_delta=raw_delta,
            thresholds=thresholds,
        )
        candidate_order = raw_order if gate_applies else baseline_order
        if gate_applies:
            applied_groups += 1
        else:
            vetoed_groups += 1
        candidate_rank = _first_positive_rank(group_labels, candidate_order)
        base_h1 = baseline_rank == 1
        cand_h1 = candidate_rank == 1
        base_h5 = baseline_rank is not None and baseline_rank <= 5
        cand_h5 = candidate_rank is not None and candidate_rank <= 5
        gain_flag = (not base_h1) and cand_h1
        loss_flag = base_h1 and not cand_h1
        baseline_rank1_groups += int(base_h1)
        rank1_loss_count += int(loss_flag)
        baseline_hit1 += int(base_h1)
        candidate_hit1 += int(cand_h1)
        hit1_gain += int(gain_flag)
        hit1_loss += int(loss_flag)
        baseline_hit5 += int(base_h5)
        candidate_hit5 += int(cand_h5)
        hit5_gain += int((not base_h5) and cand_h5)
        hit5_loss += int(base_h5 and not cand_h5)
        local_top_idx = int(candidate_order[0])
        top_row = rows[local_top_idx]
        dims = {
            "query_family": _clean(group_meta.get("query_family")),
            "candidate_top_family": _clean(top_row.get("candidate_family")),
            "source_family": _clean(group_meta.get("source_family")),
            "province": _clean(group_meta.get("province")),
            "oof_fold": _clean(group_meta.get("oof_fold")),
            "gate_reason": gate_reason,
            "support_score": str(gate_meta["challenger_support_score"]),
            "taxonomy_empty": str(bool(gate_meta["taxonomy_empty"])),
            "baseline_rank_bucket": _rank_bucket(baseline_rank),
        }
        for dimension, key in dims.items():
            item = loss_stats[(dimension, key)]
            item["groups"] += 1
            item["baseline_hit1"] += int(base_h1)
            item["candidate_hit1"] += int(cand_h1)
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)
        detail = {
            "candidate_id": cid,
            "group_id": _clean(group_meta.get("group_id")),
            "query": _clean(group_meta.get("query")),
            "source_family": dims["source_family"],
            "province": dims["province"],
            "oof_fold": dims["oof_fold"],
            "baseline_positive_rank": baseline_rank,
            "raw_candidate_positive_rank": raw_rank,
            "candidate_positive_rank": candidate_rank,
            "gate_applies": gate_applies,
            "gate_reason": gate_reason,
            "challenger_support_score": gate_meta["challenger_support_score"],
            "challenger_support_parts": gate_meta["support_parts"],
            "baseline_weak_reasons": gate_meta["baseline_weak_reasons"],
            "clean_rank1_veto": gate_meta["clean_rank1_veto"],
            "taxonomy_empty": gate_meta["taxonomy_empty"],
            "candidate_top_quota_id": _clean(top_row.get("quota_id")),
            "candidate_top_family": _clean(top_row.get("candidate_family")),
            "raw_margin_delta": round(raw_delta, 8),
            "flip_type": "gain" if gain_flag else "loss" if loss_flag else "neutral",
        }
        if gain_flag or loss_flag:
            flips.append(detail)
        if base_h1:
            rank1_rows.append(
                {
                    "candidate_id": cid,
                    "group_id": detail["group_id"],
                    "gate_reason": gate_reason,
                    "clean_rank1_veto": gate_meta["clean_rank1_veto"],
                    "challenger_support_score": gate_meta["challenger_support_score"],
                    "raw_candidate_positive_rank": raw_rank,
                    "candidate_positive_rank": candidate_rank,
                    "rank1_demoted": loss_flag,
                }
            )
        gate_rows.append(
            {
                "candidate_id": cid,
                "gate_reason": gate_reason,
                "gate_applies": gate_applies,
                "clean_rank1_veto": gate_meta["clean_rank1_veto"],
                "taxonomy_empty": gate_meta["taxonomy_empty"],
                "challenger_support_score": gate_meta["challenger_support_score"],
                "gain": int(gain_flag),
                "loss": int(loss_flag),
                "net": int(gain_flag) - int(loss_flag),
            }
        )
        fallback_rows.append(
            {
                "candidate_id": cid,
                "group_id": detail["group_id"],
                "baseline_hit1": base_h1,
                "raw_candidate_hit1": raw_rank == 1,
                "candidate_hit1": cand_h1,
                "candidate_override": bool(local_top_idx != 0),
                "gate_applies": gate_applies,
                "clean_rank1_veto": gate_meta["clean_rank1_veto"],
                "override_outcome": detail["flip_type"],
                "rank1_safe_contract": True,
            }
        )
        taxonomy_rows.append(
            {
                "candidate_id": cid,
                "taxonomy_slice": "taxonomy_empty" if gate_meta["taxonomy_empty"] else "taxonomy_present",
                "gain": int(gain_flag),
                "loss": int(loss_flag),
                "net": int(gain_flag) - int(loss_flag),
                "groups": 1,
            }
        )
        start = stop
    total = len(groups)
    metrics = {
        "candidate_id": cid,
        "objective_variant": candidate["objective_variant"],
        "feature_toggle": candidate["feature_toggle"],
        "groups": total,
        "baseline_hit1": baseline_hit1,
        "candidate_hit1": candidate_hit1,
        "baseline_hit1_rate": round(baseline_hit1 / total, 6) if total else 0.0,
        "candidate_hit1_rate": round(candidate_hit1 / total, 6) if total else 0.0,
        "hit1_gain": hit1_gain,
        "hit1_loss": hit1_loss,
        "hit1_net": hit1_gain - hit1_loss,
        "baseline_hit5": baseline_hit5,
        "candidate_hit5": candidate_hit5,
        "hit5_gain": hit5_gain,
        "hit5_loss": hit5_loss,
        "hit5_net": hit5_gain - hit5_loss,
        "applied_groups": applied_groups,
        "applied_group_rate": round(applied_groups / total, 6) if total else 0.0,
        "vetoed_groups": vetoed_groups,
        "baseline_rank1_groups": baseline_rank1_groups,
        "rank1_loss_count": rank1_loss_count,
        "baseline_rank1_demotion_rate": round(rank1_loss_count / baseline_rank1_groups, 6) if baseline_rank1_groups else 0.0,
    }
    loss_rows = [
        {
            "candidate_id": cid,
            "slice_dimension": dimension,
            "slice_key": key,
            **values,
            "baseline_hit1_rate": round(values["baseline_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
            "candidate_hit1_rate": round(values["candidate_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
        }
        for (dimension, key), values in loss_stats.items()
    ]
    return metrics, loss_rows, rank1_rows, gate_rows, fallback_rows, flips, taxonomy_rows


def _summarise_gate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "applied": 0, "vetoed": 0, "gain": 0, "loss": 0, "net": 0})
    for row in rows:
        key = (row["candidate_id"], row["gate_reason"], str(row["challenger_support_score"]))
        item = acc[key]
        item["groups"] += 1
        item["applied"] += int(row["gate_applies"])
        item["vetoed"] += int(not row["gate_applies"])
        item["gain"] += int(row["gain"])
        item["loss"] += int(row["loss"])
        item["net"] += int(row["net"])
    return [
        {"candidate_id": cid, "gate_reason": reason, "challenger_support_score": score, **values}
        for (cid, reason, score), values in sorted(acc.items(), key=lambda item: (item[0][0], -item[1]["groups"]))
    ]


def _summarise_taxonomy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "gain": 0, "loss": 0, "net": 0})
    for row in rows:
        item = acc[(row["candidate_id"], row["taxonomy_slice"])]
        item["groups"] += 1
        item["gain"] += int(row["gain"])
        item["loss"] += int(row["loss"])
        item["net"] += int(row["net"])
    return [{"candidate_id": cid, "taxonomy_slice": slc, **values} for (cid, slc), values in sorted(acc.items())]


def _source_fold_robustness(scorecard_rows: list[dict[str, Any]], loss_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for score in scorecard_rows:
        cid = score["candidate_id"]
        source_rows = [row for row in loss_rows if row["candidate_id"] == cid and row["slice_dimension"] in {"source_family", "province", "oof_fold"}]
        positive = [row for row in source_rows if int(row["net"]) > 0]
        max_net = max((int(row["net"]) for row in positive), default=0)
        total_positive_net = sum(int(row["net"]) for row in positive)
        concentration = round(max_net / total_positive_net, 6) if total_positive_net else 0.0
        negative_slices = sum(1 for row in source_rows if int(row["net"]) < 0)
        rows.append(
            {
                "candidate_id": cid,
                "hit1_net": score["hit1_net"],
                "positive_source_fold_net": total_positive_net,
                "max_positive_net_share": concentration,
                "negative_source_fold_slices": negative_slices,
                "status": "pass" if concentration <= 0.35 and negative_slices <= 2 else "warn",
            }
        )
    return rows


def _approval_status(row: dict[str, Any], robustness_rows: list[dict[str, Any]]) -> str:
    if int(row["hit1_net"]) <= 0:
        return "fail_non_positive_top1_net"
    if int(row["rank1_loss_count"]) > 1:
        return "fail_rank1_loss_budget_gt_1"
    if int(row["hit5_net"]) < 0:
        return "fail_hit5_negative"
    robust = next((item for item in robustness_rows if item["candidate_id"] == row["candidate_id"]), {})
    if robust.get("status") == "warn":
        return "hold_source_fold_robustness_warning"
    return "pass_dev_oof_candidate"


def _leakage_rows(candidates: list[dict[str, Any]], all_features: list[str]) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        features = _candidate_features(candidate["feature_toggle"], all_features)
        forbidden = sorted(set(features) & FORBIDDEN_FEATURES)
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "feature_toggle": candidate["feature_toggle"],
                "feature_count": len(features),
                "forbidden_feature_present": "|".join(forbidden),
                "status": "fail" if forbidden else "pass",
            }
        )
    return rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    top_rows = report["scorecard_rows"][:10]
    lines = [
        "# 14.3 Rank1-Safe Source-Robust Dev/OOF Training",
        "",
        "Explicitly authorized dev/OOF-only training. Heldout/hard were not read, and no online GoalSearcher path was changed.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", report["metrics"]["candidate_count"]],
                ["group_count", report["metrics"]["group_count"]],
                ["matrix_rows", report["metrics"]["matrix_rows"]],
                ["best_candidate_id", report["metrics"]["best_candidate_id"]],
                ["best_hit1_net", report["metrics"]["best_hit1_net"]],
                ["best_hit1_gain", report["metrics"]["best_hit1_gain"]],
                ["best_hit1_loss", report["metrics"]["best_hit1_loss"]],
                ["best_rank1_loss_count", report["metrics"]["best_rank1_loss_count"]],
                ["approval_candidate_count", report["metrics"]["approval_candidate_count"]],
                ["heldout_used_for_selection", report["metrics"]["heldout_used_for_selection"]],
            ]
        ),
        "",
        "## Candidate Scorecard",
        "",
        _md_table(
            [["rank", "candidate_id", "hit1_net", "hit1_gain", "hit1_loss", "rank1_loss_count", "hit5_net", "approval_status"]]
            + [
                [
                    row["scorecard_rank"],
                    row["candidate_id"],
                    row["hit1_net"],
                    row["hit1_gain"],
                    row["hit1_loss"],
                    row["rank1_loss_count"],
                    row["hit5_net"],
                    row["approval_status"],
                ]
                for row in top_rows
            ]
        ),
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.3 rank1-safe source-robust dev/OOF training 已完成。\n"
        f"结果：best={m['best_candidate_id']}，hit1_net={m['best_hit1_net']}，"
        f"gain={m['best_hit1_gain']}，loss={m['best_hit1_loss']}，"
        f"approval_candidate_count={m['approval_candidate_count']}。\n"
        "下一步建议：14.4 scorecard/loss/source robustness freeze gate，只读决定是否 freeze；仍不跑 heldout/hard。\n"
        "禁止：用 heldout/hard 做选择、上线、改 GoalSearcher、改阈值、把 dev/OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.3 rank1-safe source-robust dev/OOF training summary" not in text:
        row = f"""          <tr>
            <td>14.3 rank1-safe source-robust dev/OOF training summary</td>
            <td>OOF LightGBM candidates with rank1 veto, strong challenger gate, loss slices, rank1 preservation, and source/fold robustness audits.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.3 rank1-safe source-robust dev/OOF training")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--candidate-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--feature-whitelist", type=Path, default=DEFAULT_FEATURE_WHITELIST)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--dev-oof-only", action="store_true", required=True)
    parser.add_argument("--emit-loss-audit", action="store_true", required=True)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260526)
    args = parser.parse_args()

    started = time.perf_counter()
    all_features = _load_training_features(args.feature_whitelist)
    df, labels, groups, meta, feature_rows = _load_dev_matrix(args.data_dir, all_features)
    candidates = _read_csv(args.candidate_plan)
    leakage = _leakage_rows(candidates, all_features)
    if any(row["status"] == "fail" for row in leakage):
        raise RuntimeError(f"leakage gate failed: {leakage}")

    preds_by_candidate: dict[str, np.ndarray] = {}
    fold_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates, 1):
        features = _candidate_features(candidate["feature_toggle"], all_features)
        preds, candidate_fold_rows, candidate_importance_rows = _train_candidate(
            candidate=candidate,
            df=df,
            labels=labels,
            groups=groups,
            meta=meta,
            features=features,
            num_boost_round=args.num_boost_round,
            seed=args.seed + idx,
        )
        preds_by_candidate[candidate["candidate_id"]] = preds
        fold_rows.extend(candidate_fold_rows)
        importance_rows.extend(candidate_importance_rows)
        print(f"[14.3] trained {idx}/{len(candidates)} {candidate['candidate_id']}", file=sys.stderr)

    thresholds = {**_top1_thresholds(feature_rows, groups), **_build_margin_thresholds(preds_by_candidate, groups)}
    scorecard_rows: list[dict[str, Any]] = []
    loss_rows: list[dict[str, Any]] = []
    rank1_rows: list[dict[str, Any]] = []
    gate_detail_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    flip_rows: list[dict[str, Any]] = []
    taxonomy_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        metrics, candidate_loss, candidate_rank1, candidate_gate, candidate_fallback, candidate_flips, candidate_taxonomy = _score_candidate(
            candidate=candidate,
            preds=preds_by_candidate[candidate["candidate_id"]],
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
            thresholds=thresholds,
        )
        metrics["feature_count"] = len(_candidate_features(candidate["feature_toggle"], all_features))
        scorecard_rows.append(metrics)
        loss_rows.extend(candidate_loss)
        rank1_rows.extend(candidate_rank1)
        gate_detail_rows.extend(candidate_gate)
        fallback_rows.extend(candidate_fallback)
        flip_rows.extend(candidate_flips)
        taxonomy_rows.extend(candidate_taxonomy)

    robustness_rows = _source_fold_robustness(scorecard_rows, loss_rows)
    for row in scorecard_rows:
        row["approval_status"] = _approval_status(row, robustness_rows)
    scorecard_rows.sort(key=lambda row: (row["approval_status"] != "pass_dev_oof_candidate", -int(row["hit1_net"]), int(row["hit1_loss"]), int(row["rank1_loss_count"])))
    for rank, row in enumerate(scorecard_rows, 1):
        row["scorecard_rank"] = rank
    approved_rows = [row for row in scorecard_rows if row["approval_status"] == "pass_dev_oof_candidate"]
    best = scorecard_rows[0] if scorecard_rows else {}

    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_execution_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_execution_summary.md")),
        "candidate_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.csv")),
        "rank1_preservation_report_csv": str(output_prefix.with_name(output_prefix.name + "_rank1_preservation_report.csv")),
        "strong_challenger_gate_coverage_csv": str(output_prefix.with_name(output_prefix.name + "_strong_challenger_gate_coverage.csv")),
        "source_fold_robustness_csv": str(output_prefix.with_name(output_prefix.name + "_source_fold_robustness.csv")),
        "taxonomy_empty_separate_audit_csv": str(output_prefix.with_name(output_prefix.name + "_taxonomy_empty_separate_audit.csv")),
        "threshold_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_threshold_manifest.csv")),
        "loss_audit_by_slice_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_by_slice.csv")),
        "fallback_contract_report_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.csv")),
        "leakage_gate_report_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.csv")),
        "feature_importance_csv": str(output_prefix.with_name(output_prefix.name + "_feature_importance.csv")),
        "hit1_flips_jsonl": str(output_prefix.with_name(output_prefix.name + "_hit1_flips.jsonl")),
    }
    report = {
        "stage": "14.3 rank1-safe source-robust dev/OOF training",
        "explicit_user_go": True,
        "dev_oof_only": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "offline_training_executed": True,
        "metrics": {
            "candidate_count": len(scorecard_rows),
            "group_count": len(groups),
            "matrix_rows": len(labels),
            "best_candidate_id": best.get("candidate_id", ""),
            "best_hit1_net": best.get("hit1_net", 0),
            "best_hit1_gain": best.get("hit1_gain", 0),
            "best_hit1_loss": best.get("hit1_loss", 0),
            "best_rank1_loss_count": best.get("rank1_loss_count", 0),
            "best_hit5_net": best.get("hit5_net", 0),
            "approval_candidate_count": len(approved_rows),
            "heldout_used_for_selection": False,
            "hard_used_for_selection": False,
            "goal_searcher_changed": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        },
        "thresholds": thresholds,
        "scorecard_rows": scorecard_rows,
        "approval_candidates": approved_rows,
        "artifacts": artifacts,
        "decision": "dev_oof_training_completed_freeze_gate_required",
        "anti_drift_conclusion": (
            "14.3 trained/evaluated only dev/OOF offline candidates from the balanced OSS matrix. It did not read heldout/hard, "
            "did not validate, did not release, did not edit GoalSearcher, and did not tune online thresholds."
        ),
        "next_stage": {
            "recommended": "14.4 scorecard/loss/source robustness freeze gate: read-only decide whether any approved candidate can freeze; do not run heldout/hard.",
            "default": "do_not_validate_yet",
        },
    }
    _write_csv(Path(artifacts["candidate_scorecard_csv"]), scorecard_rows, ["scorecard_rank", "candidate_id", "objective_variant", "feature_toggle", "feature_count", "groups", "baseline_hit1", "candidate_hit1", "baseline_hit1_rate", "candidate_hit1_rate", "hit1_gain", "hit1_loss", "hit1_net", "baseline_hit5", "candidate_hit5", "hit5_gain", "hit5_loss", "hit5_net", "applied_groups", "applied_group_rate", "vetoed_groups", "baseline_rank1_groups", "rank1_loss_count", "baseline_rank1_demotion_rate", "approval_status"])
    _write_csv(Path(artifacts["rank1_preservation_report_csv"]), rank1_rows, ["candidate_id", "group_id", "gate_reason", "clean_rank1_veto", "challenger_support_score", "raw_candidate_positive_rank", "candidate_positive_rank", "rank1_demoted"])
    _write_csv(Path(artifacts["strong_challenger_gate_coverage_csv"]), _summarise_gate_rows(gate_detail_rows), ["candidate_id", "gate_reason", "challenger_support_score", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_csv(Path(artifacts["source_fold_robustness_csv"]), robustness_rows, ["candidate_id", "hit1_net", "positive_source_fold_net", "max_positive_net_share", "negative_source_fold_slices", "status"])
    _write_csv(Path(artifacts["taxonomy_empty_separate_audit_csv"]), _summarise_taxonomy(taxonomy_rows), ["candidate_id", "taxonomy_slice", "groups", "gain", "loss", "net"])
    _write_csv(Path(artifacts["threshold_manifest_csv"]), [{"threshold": key, "value": value, "split": "dev_oof_only"} for key, value in thresholds.items()], ["threshold", "value", "split"])
    _write_csv(Path(artifacts["loss_audit_by_slice_csv"]), loss_rows, ["candidate_id", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["fallback_contract_report_csv"]), fallback_rows, ["candidate_id", "group_id", "baseline_hit1", "raw_candidate_hit1", "candidate_hit1", "candidate_override", "gate_applies", "clean_rank1_veto", "override_outcome", "rank1_safe_contract"])
    _write_csv(Path(artifacts["leakage_gate_report_csv"]), leakage, ["candidate_id", "feature_toggle", "feature_count", "forbidden_feature_present", "status"])
    _write_csv(Path(artifacts["feature_importance_csv"]), importance_rows, ["candidate_id", "feature", "gain_sum"])
    _write_jsonl(Path(artifacts["hit1_flips_jsonl"]), flip_rows)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "best_candidate": best}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
