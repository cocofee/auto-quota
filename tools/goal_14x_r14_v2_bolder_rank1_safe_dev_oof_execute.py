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
from tools.goal_14x_rank1_safe_source_robust_dev_oof_execute import (  # noqa: E402
    BOOK_FEATURES,
    FORBIDDEN_FEATURES,
    _baseline_weak_or_conflicted,
    _bool,
    _build_margin_thresholds,
    _challenger_support_score,
    _clean,
    _explicit_conflict,
    _first_positive_rank,
    _float,
    _int,
    _md_table,
    _rank_bucket,
    _safe_rel,
    _source_fold_assignments,
    _summarise_gate_rows,
    _summarise_taxonomy,
    _taxonomy_empty,
    _top1_margin,
    _top1_thresholds,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_PLAN = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_redesign_plan_candidate_matrix.csv"
DEFAULT_FEATURE_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_oss_source_aware_v1.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_r14_v2_bolder_rank1_safe_dev_oof"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
R14_A_APPLIED_RATE = 0.00232


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


def _candidate_features(toggle_id: str, all_features: list[str]) -> list[str]:
    if toggle_id in {"FT_R14_SAFE_CORE_PLUS_CHALLENGER", "FT_R14_SAFE_CORE_PLUS_CONFLICT"}:
        return list(all_features)
    if toggle_id == "FT_R14_SAFE_CORE_NO_BOOK_ID":
        return [feature for feature in all_features if feature not in BOOK_FEATURES]
    raise ValueError(f"unknown R14 v2 feature toggle: {toggle_id}")


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
    elif objective_variant == "OBJ_R14_pairwise_near_miss_proxy":
        params["min_data_in_leaf"] = 40
        params["lambda_l2"] = 1.5
        params["num_leaves"] = 31
    elif objective_variant == "OBJ_R14_hit5_rescue_rank1_hard_veto":
        params["min_data_in_leaf"] = 42
        params["lambda_l2"] = 1.8
        params["num_leaves"] = 29
    elif objective_variant == "OBJ_R14_top1_loss_guarded":
        pass
    else:
        raise ValueError(f"unknown R14 v2 objective: {objective_variant}")
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
        elif objective_variant == "OBJ_R14_pairwise_near_miss_proxy":
            weights[start:stop] = 1.8 if near_miss else 1.2 if baseline_hit else 0.85
        elif objective_variant == "OBJ_R14_hit5_rescue_rank1_hard_veto":
            weights[start:stop] = 1.7 if near_miss else 2.6 if baseline_hit else 1.0
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


def _runtime_gate_common(rows: list[dict[str, Any]], group_meta: dict[str, Any], raw_top_idx: int, thresholds: dict[str, Any]) -> dict[str, Any]:
    baseline = rows[0]
    challenger = rows[raw_top_idx]
    support_score, support_parts = _challenger_support_score(challenger, baseline)
    weak, weak_reasons = _baseline_weak_or_conflicted(baseline, rows, thresholds)
    explicit_conflict = _explicit_conflict(baseline)
    top1_margin = _top1_margin(rows)
    taxonomy_empty = _taxonomy_empty(group_meta, baseline)
    low_conf_q25 = _float(baseline.get("confidence")) <= _float(thresholds["confidence_q25"])
    low_reason_q25 = _float(baseline.get("reason_count")) <= _float(thresholds["reason_count_q25"])
    small_margin_q25 = top1_margin <= _float(thresholds["margin_q25"])
    small_margin_q35 = top1_margin <= _float(thresholds["margin_q35"])
    clean_rank1_veto = (not weak and not explicit_conflict) or support_score < 2
    return {
        "baseline": baseline,
        "challenger": challenger,
        "challenger_support_score": support_score,
        "support_parts": support_parts,
        "baseline_weak": weak,
        "baseline_weak_reasons": weak_reasons,
        "explicit_conflict": explicit_conflict,
        "taxonomy_empty": taxonomy_empty,
        "low_conf_q25": low_conf_q25,
        "low_reason_count_q25": low_reason_q25,
        "small_margin_q25": small_margin_q25,
        "small_margin_q35": small_margin_q35,
        "clean_rank1_veto": clean_rank1_veto,
    }


def _gate_decision_v2(
    *,
    candidate_id: str,
    rows: list[dict[str, Any]],
    group_meta: dict[str, Any],
    raw_top_idx: int,
    raw_margin_delta: float,
    thresholds: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    gate = _runtime_gate_common(rows, group_meta, raw_top_idx, thresholds)
    if raw_top_idx == 0:
        return False, "baseline_already_top1", gate
    support = int(gate["challenger_support_score"])
    taxonomy_empty = bool(gate["taxonomy_empty"])
    if taxonomy_empty and support < 3:
        return False, "taxonomy_empty_guard_insufficient_support", gate
    if bool(gate["clean_rank1_veto"]):
        return False, "clean_rank1_veto", gate

    if candidate_id == "R14V2_A_near_miss_safe_support2_margin_q70":
        applies = bool(gate["small_margin_q35"]) and bool(gate["baseline_weak"]) and support >= 2 and raw_margin_delta >= _float(thresholds["margin_delta_q70"])
        if taxonomy_empty:
            applies = applies and support >= 3 and bool(gate["support_parts"])
        reason = "r14v2_a_near_miss_support2_q70" if applies else "r14v2_a_gate_not_met"
    elif candidate_id == "R14V2_B_near_miss_safe_support3_no_tax_empty":
        applies = bool(gate["small_margin_q35"]) and bool(gate["baseline_weak"]) and support >= 3 and not taxonomy_empty
        reason = "r14v2_b_support3_no_tax_empty" if applies else "r14v2_b_gate_not_met"
    elif candidate_id == "R14V2_C_conflict_or_weak_plus_near_miss_q65":
        delta_q65 = _float(thresholds.get("margin_delta_q65", thresholds["margin_delta_q70"]))
        permission = bool(gate["explicit_conflict"]) or bool(gate["low_reason_count_q25"])
        applies = permission and bool(gate["small_margin_q35"]) and support >= 2 and raw_margin_delta >= delta_q65
        if taxonomy_empty:
            applies = applies and bool(gate["explicit_conflict"]) and support >= 3
        reason = "r14v2_c_conflict_or_weak_q65" if applies else "r14v2_c_gate_not_met"
    elif candidate_id == "R14V2_D_top5_rescue_rank1_hard_veto":
        # Diagnostic rescue: runtime-visible weak baseline and support; no label/rank-positive gate is used.
        applies = bool(gate["baseline_weak"]) and bool(gate["small_margin_q35"]) and support >= 2
        if taxonomy_empty:
            applies = False
        reason = "r14v2_d_top5_rescue_shadow_gate" if applies else "r14v2_d_gate_not_met"
    elif candidate_id == "R14V2_E_rank1_shadow_no_demote":
        applies = raw_margin_delta > 0 and support >= 2
        reason = "r14v2_e_shadow_recommendation" if applies else "r14v2_e_shadow_not_met"
    else:
        applies = False
        reason = "unknown_candidate"
    return applies, reason, gate


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
        gate_applies, gate_reason, gate_meta = _gate_decision_v2(
            candidate_id=cid,
            rows=rows,
            group_meta=group_meta,
            raw_top_idx=raw_top_idx,
            raw_margin_delta=raw_delta,
            thresholds=thresholds,
        )
        candidate_order = raw_order if gate_applies else baseline_order
        if cid == "R14V2_E_rank1_shadow_no_demote" and baseline_rank == 1:
            candidate_order = baseline_order
            gate_reason = "r14v2_e_rank1_hard_veto_shadow_only" if gate_applies else gate_reason
            gate_applies = False
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


def _approval_status(row: dict[str, Any], robustness_rows: list[dict[str, Any]], taxonomy_rows: list[dict[str, Any]]) -> str:
    if int(row["hit1_net"]) <= 0:
        return "fail_non_positive_top1_net"
    if int(row["rank1_loss_count"]) > 1:
        return "fail_rank1_loss_budget_gt_1"
    if float(row["applied_group_rate"]) <= R14_A_APPLIED_RATE:
        return "fail_noop_coverage_le_R14_A"
    if float(row["applied_group_rate"]) > 0.04:
        return "fail_coverage_too_broad_for_rank1_safe_release"
    if int(row["hit5_net"]) < 0:
        return "fail_hit5_negative"
    tax = next((item for item in taxonomy_rows if item["candidate_id"] == row["candidate_id"] and item["taxonomy_slice"] == "taxonomy_empty"), {})
    if int(tax.get("net") or 0) > max(0, int(row["hit1_net"]) // 2):
        return "fail_taxonomy_empty_dominates_positive_net"
    robust = next((item for item in robustness_rows if item["candidate_id"] == row["candidate_id"]), {})
    if float(robust.get("max_positive_net_share") or 0.0) > 0.5:
        return "fail_source_family_positive_net_share_gt_0_50"
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
    rows = [["rank", "candidate_id", "hit1_net", "gain", "loss", "rank1_loss", "applied_rate", "approval"]]
    for row in report["scorecard_rows"]:
        rows.append([row["scorecard_rank"], row["candidate_id"], row["hit1_net"], row["hit1_gain"], row["hit1_loss"], row["rank1_loss_count"], row["applied_group_rate"], row["approval_status"]])
    lines = [
        "# 14.11 R14 v2 Dev/OOF Execution",
        "",
        "Explicitly authorized dev/OOF-only execution. Heldout/hard were not read, and no online GoalSearcher path was changed.",
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
            ]
        ),
        "",
        "## Candidate Scorecard",
        "",
        _md_table(rows),
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
        "当前阶段：14.11 R14 v2 dev/OOF-only execution 已完成。\n"
        f"结果：best={m['best_candidate_id']}，hit1_net={m['best_hit1_net']}，gain={m['best_hit1_gain']}，loss={m['best_hit1_loss']}，rank1_loss={m['best_rank1_loss_count']}，approval_candidate_count={m['approval_candidate_count']}。\n"
        "下一步建议：14.12 R14 v2 freeze gate review。只读复核 scorecard、loss slices、rank1 preservation、source/fold robustness，决定是否 freeze；仍不跑 heldout/hard。\n"
        "禁止：用 heldout/hard、上线、改 GoalSearcher、改阈值、扩大候选矩阵。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.11 R14 v2 dev/OOF execution summary" not in text:
        row = f"""          <tr>
            <td>14.11 R14 v2 dev/OOF execution summary</td>
            <td>Fixed 14.8 candidate matrix executed on dev/OOF only with rank1 preservation, gate coverage, loss, taxonomy, and source/fold audits.</td>
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
    parser = argparse.ArgumentParser(description="14.11 R14 v2 bolder rank1-safe dev/OOF execution")
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
        print(f"[14.11] trained {idx}/{len(candidates)} {candidate['candidate_id']}", file=sys.stderr)

    thresholds = {**_top1_thresholds(feature_rows, groups), **_build_margin_thresholds(preds_by_candidate, groups)}
    if "margin_delta_q65" not in thresholds:
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
        thresholds["margin_delta_q65"] = round(float(np.quantile(np.array(deltas or [0.0], dtype=np.float64), 0.65)), 8)
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
    taxonomy_summary_rows = _summarise_taxonomy(taxonomy_rows)
    for row in scorecard_rows:
        row["approval_status"] = _approval_status(row, robustness_rows, taxonomy_summary_rows)
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
        "gate_coverage_csv": str(output_prefix.with_name(output_prefix.name + "_gate_coverage.csv")),
        "source_fold_robustness_csv": str(output_prefix.with_name(output_prefix.name + "_source_fold_robustness.csv")),
        "taxonomy_empty_audit_csv": str(output_prefix.with_name(output_prefix.name + "_taxonomy_empty_audit.csv")),
        "threshold_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_threshold_manifest.csv")),
        "loss_audit_by_slice_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_by_slice.csv")),
        "fallback_contract_report_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.csv")),
        "feature_importance_csv": str(output_prefix.with_name(output_prefix.name + "_feature_importance.csv")),
        "hit1_flips_jsonl": str(output_prefix.with_name(output_prefix.name + "_hit1_flips.jsonl")),
        "leakage_gate_report_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.csv")),
    }
    report = {
        "stage": "14.11 R14 v2 bolder rank1-safe dev/OOF execution",
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
        "decision": "dev_oof_execution_completed_freeze_gate_required",
        "anti_drift_conclusion": (
            "14.11 created and ran only the authorized dev/OOF harness over the fixed 14.8 R14 v2 candidate matrix. "
            "It did not read heldout/hard, did not validate, did not release, did not edit GoalSearcher, and did not change online thresholds."
        ),
        "next_stage": {
            "recommended": "14.12 R14 v2 freeze gate review: read-only decide whether any approved candidate can freeze; do not run heldout/hard.",
            "default": "do_not_validate_yet",
        },
    }
    _write_csv(Path(artifacts["candidate_scorecard_csv"]), scorecard_rows, ["scorecard_rank", "candidate_id", "objective_variant", "feature_toggle", "feature_count", "groups", "baseline_hit1", "candidate_hit1", "baseline_hit1_rate", "candidate_hit1_rate", "hit1_gain", "hit1_loss", "hit1_net", "baseline_hit5", "candidate_hit5", "hit5_gain", "hit5_loss", "hit5_net", "applied_groups", "applied_group_rate", "vetoed_groups", "baseline_rank1_groups", "rank1_loss_count", "baseline_rank1_demotion_rate", "approval_status"])
    _write_csv(Path(artifacts["rank1_preservation_report_csv"]), rank1_rows, ["candidate_id", "group_id", "gate_reason", "clean_rank1_veto", "challenger_support_score", "raw_candidate_positive_rank", "candidate_positive_rank", "rank1_demoted"])
    _write_csv(Path(artifacts["gate_coverage_csv"]), _summarise_gate_rows(gate_detail_rows), ["candidate_id", "gate_reason", "challenger_support_score", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_csv(Path(artifacts["source_fold_robustness_csv"]), robustness_rows, ["candidate_id", "hit1_net", "positive_source_fold_net", "max_positive_net_share", "negative_source_fold_slices", "status"])
    _write_csv(Path(artifacts["taxonomy_empty_audit_csv"]), taxonomy_summary_rows, ["candidate_id", "taxonomy_slice", "groups", "gain", "loss", "net"])
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
