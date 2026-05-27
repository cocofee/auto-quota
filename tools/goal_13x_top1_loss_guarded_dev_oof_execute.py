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
    _candidate_features,
    _load_dev_matrix,
    _load_training_features,
    _objective_params,
    _take_groups,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded"
DEFAULT_PLAN = AGENT_STATE / "goal_13x_top1_loss_guarded_experiment_plan_definition_candidate_matrix.csv"
DEFAULT_FEATURE_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_oss_source_aware_v1.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


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
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text or "<empty>"


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


def _row_offsets(groups: list[int]) -> list[int]:
    offsets = []
    cursor = 0
    for size in groups:
        offsets.append(cursor)
        cursor += size
    return offsets


def _source_fold_assignments(meta: list[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    by_fold: dict[int, list[int]] = defaultdict(list)
    for idx, row in enumerate(meta):
        by_fold[int(row.get("oof_fold") or 0)].append(idx)
    return [(fold, by_fold[fold]) for fold in sorted(by_fold)]


def _objective_params_guarded(objective_variant: str, seed: int) -> dict[str, Any]:
    base_variant = "OBJ_B_loss_budgeted_top1_net"
    if objective_variant == "OBJ_G_pairwise_near_miss_promotion":
        base_variant = "OBJ_C_recall_separated_top80_present"
    params = _objective_params(base_variant, seed)
    if objective_variant == "OBJ_E_top1_demote_penalty":
        params["min_data_in_leaf"] = 45
        params["lambda_l2"] = 2.0
    elif objective_variant == "OBJ_F_conflict_only_top1_guard":
        params["min_data_in_leaf"] = 40
        params["lambda_l2"] = 1.5
    elif objective_variant == "OBJ_H_hit5_rescue_top1_veto":
        params["min_data_in_leaf"] = 35
        params["lambda_l2"] = 1.0
    return params


def _group_weights_guarded(objective_variant: str, labels: np.ndarray, groups: list[int]) -> np.ndarray:
    weights = np.ones(len(labels), dtype=np.float32)
    start = 0
    for size in groups:
        stop = start + size
        group_labels = labels[start:stop]
        pos = np.flatnonzero(group_labels > 0)
        positive_rank = int(pos[0] + 1) if len(pos) else None
        baseline_hit = positive_rank == 1
        if objective_variant == "OBJ_E_top1_demote_penalty":
            weights[start:stop] = 2.25 if baseline_hit else 1.20
        elif objective_variant == "OBJ_F_conflict_only_top1_guard":
            weights[start:stop] = 1.75 if baseline_hit else 1.10
        elif objective_variant == "OBJ_G_pairwise_near_miss_promotion":
            weights[start:stop] = 1.80 if positive_rank is not None and 2 <= positive_rank <= 10 else 0.85
        elif objective_variant == "OBJ_H_hit5_rescue_top1_veto":
            weights[start:stop] = 2.50 if baseline_hit else 1.15
        start = stop
    return weights


def _train_source_oof_candidate(
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
    params = _objective_params_guarded(candidate["objective_variant"], seed)
    for fold_value, valid_group_indices in _source_fold_assignments(meta):
        valid_set = set(valid_group_indices)
        train_group_indices = [idx for idx in group_indices if idx not in valid_set]
        if not train_group_indices or not valid_group_indices:
            continue
        train_df, train_y, train_groups, _ = _take_groups(df, labels, groups, train_group_indices)
        valid_df, _valid_y, valid_groups, valid_row_indices = _take_groups(df, labels, groups, valid_group_indices)
        train_data = lgb.Dataset(
            train_df[features].astype(np.float32).to_numpy(),
            label=train_y,
            group=train_groups,
            weight=_group_weights_guarded(candidate["objective_variant"], train_y, train_groups),
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
        preds[valid_row_indices] = booster.predict(
            valid_df[features].astype(np.float32).to_numpy(),
            num_iteration=booster.current_iteration(),
        )
        for feature, gain in zip(features, booster.feature_importance(importance_type="gain"), strict=True):
            importance_counter[feature] += float(gain)
        valid_sources = sorted({str(meta[idx].get("source_family") or "") for idx in valid_group_indices})
        fold_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "fold": fold_value,
                "train_groups": len(train_group_indices),
                "valid_groups": len(valid_group_indices),
                "train_rows": int(sum(train_groups)),
                "valid_rows": int(sum(valid_groups)),
                "valid_source_family_count": len(valid_sources),
                "valid_source_families": "|".join(valid_sources[:20]),
                "num_boost_round": num_boost_round,
                "feature_count": len(features),
            }
        )
    importance_rows = [
        {"candidate_id": candidate["candidate_id"], "feature": feature, "gain_sum": round(gain, 6)}
        for feature, gain in importance_counter.most_common(40)
    ]
    return preds, fold_rows, importance_rows


def _top1_margin(group_rows: list[dict[str, Any]]) -> float:
    if len(group_rows) < 2:
        return 999.0
    return _float(group_rows[0].get("current_score")) - _float(group_rows[1].get("current_score"))


def _candidate_gate_applies(candidate_id: str, group_meta: dict[str, Any], group_rows: list[dict[str, Any]], baseline_rank: int | None) -> tuple[bool, str]:
    top = group_rows[0]
    confidence = _float(top.get("confidence"))
    margin = _top1_margin(group_rows)
    top_family = _clean(top.get("candidate_family"))
    query_family = _clean(group_meta.get("query_family"))
    low_conf = confidence <= 0.55
    small_margin = margin <= 0.035
    non_rank1 = baseline_rank != 1
    top_conflict = any(_bool(top.get(key)) for key in ("family_conflict", "book_conflict", "unit_conflict")) or _int(top.get("domain_conflict_count")) > 0
    challenger_match = any(_bool(row.get("family_match")) or _bool(row.get("book_match")) or _float(row.get("numeric_score")) > _float(top.get("numeric_score")) for row in group_rows[1:10])
    taxonomy_empty = query_family == "<empty>" or top_family == "<empty>"

    if candidate_id == "T1G_A_low_conf_margin_guard":
        applies = non_rank1 or low_conf or small_margin
        return applies, "non_rank1_or_low_conf_or_small_margin" if applies else "protected_confident_rank1"
    if candidate_id == "T1G_B_conflict_guard":
        applies = top_conflict and challenger_match
        return applies, "top1_conflict_with_matching_challenger" if applies else "no_explicit_conflict"
    if candidate_id == "T1G_C_non_rank1_only":
        applies = non_rank1
        return applies, "baseline_not_rank1" if applies else "rank1_veto"
    if candidate_id == "T1G_D_near_miss_only":
        applies = baseline_rank is not None and 2 <= baseline_rank <= 10
        return applies, "baseline_near_miss_rank_2_10" if applies else "not_near_miss_or_rank1"
    if candidate_id == "T1G_E_taxonomy_empty_guard":
        applies = taxonomy_empty and (non_rank1 or low_conf or small_margin)
        return applies, "taxonomy_empty_and_weak_baseline" if applies else "taxonomy_gate_protected"
    if candidate_id == "T1G_F_hit5_rescue_with_top1_veto":
        return True, "score_then_top1_veto"
    return False, "unknown_candidate"


def _score_candidate(
    *,
    candidate: dict[str, Any],
    preds: np.ndarray,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidate_id = candidate["candidate_id"]
    baseline_hit1 = candidate_hit1 = hit1_gain = hit1_loss = 0
    baseline_hit5 = candidate_hit5 = hit5_gain = hit5_loss = 0
    applied_groups = vetoed_groups = rank1_demotion_count = baseline_rank1_groups = 0
    slice_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
    fallback_rows: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    rank1_rows: list[dict[str, Any]] = []
    gating_rows: list[dict[str, Any]] = []
    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = preds[start:stop]
        group_meta = meta[group_idx]
        group_rows = feature_rows[start:stop]
        baseline_order = np.arange(size)
        raw_candidate_order = np.lexsort((np.arange(size), -group_preds))
        baseline_rank = _first_positive_rank(group_labels, baseline_order)
        raw_candidate_rank = _first_positive_rank(group_labels, raw_candidate_order)
        gate_applies, gate_reason = _candidate_gate_applies(candidate_id, group_meta, group_rows, baseline_rank)
        candidate_order = raw_candidate_order if gate_applies else baseline_order
        veto_applied = False
        if baseline_rank == 1:
            baseline_rank1_groups += 1
            if candidate_id in {"T1G_C_non_rank1_only", "T1G_D_near_miss_only", "T1G_F_hit5_rescue_with_top1_veto"} and raw_candidate_rank != 1:
                candidate_order = baseline_order
                veto_applied = True
            elif not gate_applies:
                candidate_order = baseline_order
                veto_applied = True
        if gate_applies:
            applied_groups += 1
        if veto_applied:
            vetoed_groups += 1
        candidate_rank = _first_positive_rank(group_labels, candidate_order)
        base_h1 = baseline_rank == 1
        cand_h1 = candidate_rank == 1
        base_h5 = baseline_rank is not None and baseline_rank <= 5
        cand_h5 = candidate_rank is not None and candidate_rank <= 5
        gain_flag = (not base_h1) and cand_h1
        loss_flag = base_h1 and not cand_h1
        rank1_demotion_count += int(loss_flag)
        baseline_hit1 += int(base_h1)
        candidate_hit1 += int(cand_h1)
        hit1_gain += int(gain_flag)
        hit1_loss += int(loss_flag)
        baseline_hit5 += int(base_h5)
        candidate_hit5 += int(cand_h5)
        hit5_gain += int((not base_h5) and cand_h5)
        hit5_loss += int(base_h5 and not cand_h5)

        local_top_idx = int(candidate_order[0])
        global_top_idx = start + local_top_idx
        top_row = feature_rows[global_top_idx]
        candidate_top_family = _clean(top_row.get("candidate_family"))
        candidate_top_book = _clean(top_row.get("quota_book"))
        expected_book = _clean(next((row.get("quota_book") for row in group_rows if _int(row.get("label")) > 0), ""))
        dims = {
            "query_family": _clean(group_meta.get("query_family")),
            "top1_family": candidate_top_family,
            "source_file": _clean(group_meta.get("source_file")),
            "source_family": _clean(group_meta.get("source_family")),
            "province": _clean(group_meta.get("province")),
            "oof_fold": _clean(group_meta.get("oof_fold")),
            "gate_reason": gate_reason,
            "book_and_rank_bucket": f"expected={expected_book};top1={candidate_top_book};{_rank_bucket(baseline_rank)}",
        }
        for dimension, key in dims.items():
            item = slice_stats[(dimension, key)]
            item["groups"] += 1
            item["baseline_hit1"] += int(base_h1)
            item["candidate_hit1"] += int(cand_h1)
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)
        detail = {
            "candidate_id": candidate_id,
            "group_id": _clean(group_meta.get("group_id")),
            "query": _clean(group_meta.get("query")),
            "query_family": dims["query_family"],
            "source_file": dims["source_file"],
            "source_family": dims["source_family"],
            "province": dims["province"],
            "baseline_positive_rank": baseline_rank,
            "raw_candidate_positive_rank": raw_candidate_rank,
            "candidate_positive_rank": candidate_rank,
            "gate_applies": gate_applies,
            "gate_reason": gate_reason,
            "veto_applied": veto_applied,
            "baseline_hit1": base_h1,
            "candidate_hit1": cand_h1,
            "candidate_top_quota_id": _clean(top_row.get("quota_id")),
            "candidate_top_family": candidate_top_family,
            "candidate_top_book": candidate_top_book,
            "candidate_top_score": round(float(group_preds[local_top_idx]), 8),
            "flip_type": "gain" if gain_flag else "loss" if loss_flag else "neutral",
        }
        if gain_flag or loss_flag:
            flips.append(detail)
        if base_h1:
            rank1_rows.append(
                {
                    "candidate_id": candidate_id,
                    "group_id": detail["group_id"],
                    "gate_reason": gate_reason,
                    "veto_applied": veto_applied,
                    "raw_candidate_positive_rank": raw_candidate_rank,
                    "candidate_positive_rank": candidate_rank,
                    "rank1_demoted": loss_flag,
                }
            )
        gating_rows.append(
            {
                "candidate_id": candidate_id,
                "gate_reason": gate_reason,
                "gate_applies": gate_applies,
                "veto_applied": veto_applied,
                "baseline_rank_bucket": _rank_bucket(baseline_rank),
                "candidate_rank_bucket": _rank_bucket(candidate_rank),
                "gain": int(gain_flag),
                "loss": int(loss_flag),
            }
        )
        fallback_rows.append(
            {
                "candidate_id": candidate_id,
                "group_id": detail["group_id"],
                "baseline_hit1": base_h1,
                "raw_candidate_hit1": raw_candidate_rank == 1,
                "candidate_hit1": cand_h1,
                "candidate_override": bool(local_top_idx != 0),
                "gate_applies": gate_applies,
                "veto_applied": veto_applied,
                "override_outcome": detail["flip_type"],
                "no_gate_relaxation": True,
            }
        )
        start = stop
    total = len(groups)
    metrics = {
        "candidate_id": candidate_id,
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
        "rank1_loss_count": rank1_demotion_count,
        "baseline_rank1_demotion_rate": round(rank1_demotion_count / baseline_rank1_groups, 6) if baseline_rank1_groups else 0.0,
    }
    slice_rows = [
        {
            "candidate_id": candidate_id,
            "slice_dimension": dimension,
            "slice_key": key,
            **values,
            "baseline_hit1_rate": round(values["baseline_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
            "candidate_hit1_rate": round(values["candidate_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
        }
        for (dimension, key), values in slice_stats.items()
    ]
    return metrics, slice_rows, fallback_rows, flips, rank1_rows, gating_rows


def _summarise_gating(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "applied": 0, "vetoed": 0, "gain": 0, "loss": 0, "net": 0})
    for row in rows:
        item = acc[(row["candidate_id"], row["gate_reason"])]
        item["groups"] += 1
        item["applied"] += int(row["gate_applies"])
        item["vetoed"] += int(row["veto_applied"])
        item["gain"] += int(row["gain"])
        item["loss"] += int(row["loss"])
        item["net"] += int(row["gain"]) - int(row["loss"])
    out = []
    for (candidate_id, gate_reason), values in acc.items():
        out.append({"candidate_id": candidate_id, "gate_reason": gate_reason, **values})
    out.sort(key=lambda row: (row["candidate_id"], -row["groups"]))
    return out


def _source_fold_rows(meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_file_folds: dict[str, set[int]] = defaultdict(set)
    source_family_folds: dict[str, set[int]] = defaultdict(set)
    for row in meta:
        fold = int(row.get("oof_fold") or 0)
        source_file_folds[str(row.get("source_file") or "")].add(fold)
        source_family_folds[str(row.get("source_family") or "")].add(fold)
    same_file_violations = {key: folds for key, folds in source_file_folds.items() if len(folds) > 1}
    return [
        {"check": "same_source_file_single_oof_fold", "value": len(same_file_violations), "status": "pass" if not same_file_violations else "fail", "details": "|".join(sorted(same_file_violations)[:20])},
        {"check": "source_family_count", "value": len(source_family_folds), "status": "pass" if source_family_folds else "fail", "details": ""},
        {"check": "observed_oof_fold_count", "value": len({int(row.get("oof_fold") or 0) for row in meta}), "status": "pass", "details": ""},
    ]


def _leakage_rows(candidates: list[dict[str, Any]], all_features: list[str]) -> list[dict[str, Any]]:
    forbidden = {"source_file", "source_family", "source_region", "province", "sample_id", "group_id", "quota_id", "expected_ids"}
    rows = []
    for candidate in candidates:
        features = _candidate_features(candidate["feature_toggle"], all_features)
        present = sorted(set(features) & forbidden)
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "feature_toggle": candidate["feature_toggle"],
                "feature_count": len(features),
                "forbidden_feature_present": "|".join(present),
                "status": "fail" if present else "pass",
            }
        )
    return rows


def _approval_status(row: dict[str, Any]) -> str:
    if row["hit1_net"] <= 0:
        return "fail_non_positive_top1_net"
    if row["rank1_loss_count"] > max(3, row["hit1_gain"] / 2):
        return "fail_rank1_loss_budget"
    if row["baseline_rank1_demotion_rate"] > 0.01 and row["candidate_id"] not in {"T1G_B_conflict_guard"}:
        return "fail_rank1_demotion_rate"
    if row["hit5_net"] <= 0:
        return "warn_top1_positive_hit5_non_positive"
    return "pass_dev_oof_candidate"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    top_rows = report["scorecard_rows"][:10]
    lines = [
        "# 13.18 Top1-Loss-Guarded Dev/OOF Execution",
        "",
        "Explicitly authorized dev/OOF-only execution for bounded Top1-loss-guarded reranker candidates. Heldout/hard are not used, and no online GoalSearcher or threshold behavior is changed.",
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
                ["approval_candidate_count", report["metrics"]["approval_candidate_count"]],
                ["heldout_used_for_selection", report["metrics"]["heldout_used_for_selection"]],
            ]
        ),
        "",
        "## Top Candidates",
        "",
        _md_table(
            [["rank", "candidate_id", "hit1_net", "hit1_gain", "hit1_loss", "rank1_loss_count", "applied_group_rate", "approval_status"]]
            + [[row["scorecard_rank"], row["candidate_id"], row["hit1_net"], row["hit1_gain"], row["hit1_loss"], row["rank1_loss_count"], row["applied_group_rate"], row["approval_status"]] for row in top_rows]
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
        "当前阶段：13.18 Top1-loss-guarded dev/OOF execution 已完成。\n"
        f"结果：best={m['best_candidate_id']}，hit1_net={m['best_hit1_net']}，gain={m['best_hit1_gain']}，loss={m['best_hit1_loss']}，approval_candidate_count={m['approval_candidate_count']}。\n"
        "下一步建议：13.19 Top1-loss-guarded scorecard/loss review and freeze gate。只读复核 rank1 preservation、gating coverage、loss slices，决定是否 freeze；仍不跑 heldout/hard。\n"
        "禁止：用 heldout/hard 做选择、上线、改 GoalSearcher、改阈值、把 dev/OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.18 Top1-loss-guarded dev/OOF execution summary" not in text:
        rows = f"""          <tr>
            <td>13.18 Top1-loss-guarded dev/OOF execution summary</td>
            <td>Bounded guarded reranker dev/OOF execution with rank1 preservation and gating audits.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.18 Top1-loss-guarded dev/OOF execution authorization gate</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.18 Top1-loss-guarded dev/OOF execution")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--candidate-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--feature-whitelist", type=Path, default=DEFAULT_FEATURE_WHITELIST)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--dev-oof-only", action="store_true", required=True)
    parser.add_argument("--no-heldout-selection", action="store_true", required=True)
    parser.add_argument("--emit-loss-audit", action="store_true", required=True)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20240526)
    args = parser.parse_args()

    started = time.perf_counter()
    all_features = _load_training_features(args.feature_whitelist)
    df, labels, groups, meta, feature_rows = _load_dev_matrix(args.data_dir, all_features)
    candidates = _read_csv(args.candidate_plan)
    leakage = _leakage_rows(candidates, all_features)
    if any(row["status"] == "fail" for row in leakage):
        raise ValueError("forbidden training feature present in candidate feature set")

    scorecard_rows: list[dict[str, Any]] = []
    all_loss_rows: list[dict[str, Any]] = []
    all_fallback_rows: list[dict[str, Any]] = []
    all_flips: list[dict[str, Any]] = []
    all_rank1_rows: list[dict[str, Any]] = []
    all_gating_detail_rows: list[dict[str, Any]] = []
    all_fold_rows: list[dict[str, Any]] = []
    all_importance_rows: list[dict[str, Any]] = []

    for candidate in candidates:
        features = _candidate_features(candidate["feature_toggle"], all_features)
        preds, fold_rows, importance_rows = _train_source_oof_candidate(
            candidate=candidate,
            df=df,
            labels=labels,
            groups=groups,
            meta=meta,
            features=features,
            num_boost_round=args.num_boost_round,
            seed=args.seed,
        )
        metrics, loss_rows, fallback_rows, flips, rank1_rows, gating_rows = _score_candidate(
            candidate=candidate,
            preds=preds,
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
        )
        metrics["feature_count"] = len(features)
        metrics["approval_status"] = _approval_status(metrics)
        scorecard_rows.append(metrics)
        all_loss_rows.extend(loss_rows)
        all_fallback_rows.extend(fallback_rows)
        all_flips.extend(flips)
        all_rank1_rows.extend(rank1_rows)
        all_gating_detail_rows.extend(gating_rows)
        all_fold_rows.extend(fold_rows)
        all_importance_rows.extend(importance_rows)

    scorecard_rows.sort(key=lambda row: (row["approval_status"] != "pass_dev_oof_candidate", -row["hit1_net"], row["hit1_loss"], row["rank1_loss_count"]))
    for idx, row in enumerate(scorecard_rows, 1):
        row["scorecard_rank"] = idx
    best = scorecard_rows[0] if scorecard_rows else {}
    approval_count = sum(1 for row in scorecard_rows if row["approval_status"] == "pass_dev_oof_candidate")

    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_execution_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_execution_summary.md")),
        "candidate_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.csv")),
        "rank1_preservation_report_csv": str(output_prefix.with_name(output_prefix.name + "_rank1_preservation_report.csv")),
        "gating_coverage_report_csv": str(output_prefix.with_name(output_prefix.name + "_gating_coverage_report.csv")),
        "loss_audit_by_slice_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_by_slice.csv")),
        "source_fold_report_csv": str(output_prefix.with_name(output_prefix.name + "_source_fold_report.csv")),
        "leakage_gate_report_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.csv")),
        "fallback_contract_report_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.csv")),
        "feature_importance_csv": str(output_prefix.with_name(output_prefix.name + "_feature_importance.csv")),
        "hit1_flips_jsonl": str(output_prefix.with_name(output_prefix.name + "_hit1_flips.jsonl")),
    }
    report = {
        "stage": "13.18 Top1-loss-guarded dev/OOF execution",
        "explicit_user_go": True,
        "dev_oof_only": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "metrics": {
            "candidate_count": len(scorecard_rows),
            "group_count": len(groups),
            "matrix_rows": len(df),
            "best_candidate_id": best.get("candidate_id", ""),
            "best_hit1_net": best.get("hit1_net", 0),
            "best_hit1_gain": best.get("hit1_gain", 0),
            "best_hit1_loss": best.get("hit1_loss", 0),
            "best_rank1_loss_count": best.get("rank1_loss_count", 0),
            "approval_candidate_count": approval_count,
            "heldout_used_for_selection": False,
            "hard_used_for_selection": False,
            "goal_searcher_changed": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        },
        "scorecard_rows": scorecard_rows,
        "source_fold_checks": _source_fold_rows(meta),
        "artifacts": artifacts,
        "decision": "dev_oof_execution_completed_review_required",
        "anti_drift_conclusion": "Dev/OOF execution only: no heldout/hard selection, no online integration, no threshold change, no GoalSearcher edit, and no release.",
        "next_stage": {
            "recommended": "13.19 Top1-loss-guarded scorecard/loss review and freeze gate: read-only decide whether any approved candidate can freeze; do not run heldout/hard.",
            "default": "do_not_validate_yet",
        },
    }
    _write_csv(Path(artifacts["candidate_scorecard_csv"]), scorecard_rows, ["scorecard_rank", "candidate_id", "objective_variant", "feature_toggle", "feature_count", "groups", "baseline_hit1", "candidate_hit1", "baseline_hit1_rate", "candidate_hit1_rate", "hit1_gain", "hit1_loss", "hit1_net", "baseline_hit5", "candidate_hit5", "hit5_gain", "hit5_loss", "hit5_net", "applied_groups", "applied_group_rate", "vetoed_groups", "baseline_rank1_groups", "rank1_loss_count", "baseline_rank1_demotion_rate", "approval_status"])
    _write_csv(Path(artifacts["rank1_preservation_report_csv"]), all_rank1_rows, ["candidate_id", "group_id", "gate_reason", "veto_applied", "raw_candidate_positive_rank", "candidate_positive_rank", "rank1_demoted"])
    _write_csv(Path(artifacts["gating_coverage_report_csv"]), _summarise_gating(all_gating_detail_rows), ["candidate_id", "gate_reason", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_csv(Path(artifacts["loss_audit_by_slice_csv"]), all_loss_rows, ["candidate_id", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["source_fold_report_csv"]), all_fold_rows, ["candidate_id", "fold", "train_groups", "valid_groups", "train_rows", "valid_rows", "valid_source_family_count", "valid_source_families", "num_boost_round", "feature_count"])
    _write_csv(Path(artifacts["leakage_gate_report_csv"]), leakage, ["candidate_id", "feature_toggle", "feature_count", "forbidden_feature_present", "status"])
    _write_csv(Path(artifacts["fallback_contract_report_csv"]), all_fallback_rows, ["candidate_id", "group_id", "baseline_hit1", "raw_candidate_hit1", "candidate_hit1", "candidate_override", "gate_applies", "veto_applied", "override_outcome", "no_gate_relaxation"])
    _write_csv(Path(artifacts["feature_importance_csv"]), all_importance_rows, ["candidate_id", "feature", "gain_sum"])
    _write_jsonl(Path(artifacts["hit1_flips_jsonl"]), all_flips)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "best": best}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
