from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

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
)
from tools.goal_13x_conflict_guard_validation_ab import (  # noqa: E402
    DEFAULT_LOCAL_ASSETS_DB_DIR,
    _build_validation_matrix,
    _clean,
    _first_positive_rank,
    _md_table,
    _rank_bucket,
    _read_json,
    _read_jsonl,
)
from tools.goal_13x_oss_xml_source_aware_matrix_build import _configure_db_dirs  # noqa: E402
from tools.goal_14x_rank1_safe_source_robust_dev_oof_execute import (  # noqa: E402
    _candidate_features,
    _gate_decision,
    _group_weights,
    _objective_params,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_FEATURE_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_oss_source_aware_v1.json"
DEFAULT_FROZEN_MANIFEST = AGENT_STATE / "goal_14x_rank1_safe_source_robust_freeze_gate_frozen_candidate.json"
DEFAULT_DEV_OOF_SUMMARY = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_execution_summary.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
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


def _source_family(row: dict[str, Any]) -> str:
    source_family = _clean(row.get("source_family"))
    if source_family != "<empty>":
        return source_family
    source_file = _clean(row.get("source_file"))
    province = _clean(row.get("province"))
    return f"{province}|{source_file}"


def _train_fixed_candidate(
    *,
    data_dir: Path,
    feature_whitelist: Path,
    candidate: dict[str, Any],
    num_boost_round: int,
    seed: int,
) -> tuple[lgb.Booster, list[str], dict[str, Any]]:
    all_features = _load_training_features(feature_whitelist)
    df, labels, groups, _meta, _feature_rows = _load_dev_matrix(data_dir, all_features)
    features = _candidate_features(candidate["feature_toggle"], all_features)
    train_data = lgb.Dataset(
        df[features].astype(np.float32).to_numpy(),
        label=labels,
        group=groups,
        weight=_group_weights(candidate["objective_variant"], labels, groups),
        feature_name=features,
        free_raw_data=False,
    )
    booster = lgb.train(
        _objective_params(candidate["objective_variant"], seed),
        train_data,
        num_boost_round=num_boost_round,
        valid_sets=[train_data],
        valid_names=["dev_train_fixed_r14"],
        callbacks=[],
    )
    return booster, features, {
        "train_groups": len(groups),
        "train_rows": len(df),
        "feature_count": len(features),
        "num_boost_round": num_boost_round,
        "seed": seed,
        "training_source": "14.2 balanced OSS dev/OOF matrix only",
    }


def _load_fixed_thresholds(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    thresholds = dict(payload.get("thresholds") or {})
    if not thresholds:
        raise ValueError(f"missing dev/OOF thresholds in {path}")
    if thresholds.get("calibration_split") != "dev_oof_only":
        raise ValueError(f"unexpected threshold calibration split: {thresholds.get('calibration_split')}")
    return thresholds


def _score_split(
    *,
    split: str,
    candidate: dict[str, Any],
    booster: lgb.Booster,
    features: list[str],
    df,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    recall_missing_rows: list[dict[str, Any]],
    thresholds: dict[str, Any],
    elapsed_sec: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidate_id = candidate["candidate_id"]
    for feature in features:
        if feature not in df.columns:
            df[feature] = 0
    preds = booster.predict(df[features].astype(np.float32).to_numpy(), num_iteration=booster.current_iteration()) if len(df) else np.array([], dtype=np.float32)

    detail_rows: list[dict[str, Any]] = []
    loss_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
    gate_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "applied": 0, "vetoed": 0, "gain": 0, "loss": 0, "net": 0})
    source_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "gain": 0, "loss": 0, "net": 0})
    rank1_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    flip_rows: list[dict[str, Any]] = []

    baseline_hit1 = candidate_hit1 = hit1_gain = hit1_loss = 0
    baseline_hit5 = candidate_hit5 = hit5_gain = hit5_loss = 0
    applied_groups = vetoed_groups = baseline_rank1_groups = rank1_loss_count = 0
    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = np.array(preds[start:stop], dtype=np.float32)
        group_meta = meta[group_idx]
        rows = feature_rows[start:stop]
        baseline_order = np.arange(size)
        raw_order = np.lexsort((np.arange(size), -group_preds))
        raw_top_idx = int(raw_order[0])
        raw_delta = float(group_preds[raw_top_idx] - group_preds[0]) if len(group_preds) else 0.0
        baseline_rank = _first_positive_rank(group_labels, baseline_order)
        raw_candidate_rank = _first_positive_rank(group_labels, raw_order)
        gate_applies, gate_reason, gate_meta = _gate_decision(
            candidate_id=candidate_id,
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
            "province": _clean(group_meta.get("province")),
            "source_file": _clean(group_meta.get("source_file")),
            "source_family": _source_family(group_meta),
            "bucket": _clean(group_meta.get("bucket")),
            "query_family": _clean(group_meta.get("query_family")),
            "candidate_top_family": _clean(top_row.get("candidate_family")),
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
        for dimension in ("province", "source_file", "source_family", "bucket"):
            item = source_stats[(dimension, dims[dimension])]
            item["groups"] += 1
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)
        gate_item = gate_stats[(gate_reason, str(gate_meta["challenger_support_score"]))]
        gate_item["groups"] += 1
        gate_item["applied"] += int(gate_applies)
        gate_item["vetoed"] += int(not gate_applies)
        gate_item["gain"] += int(gain_flag)
        gate_item["loss"] += int(loss_flag)
        gate_item["net"] += int(gain_flag) - int(loss_flag)

        detail = {
            "split": split,
            "candidate_id": candidate_id,
            "group_id": _clean(group_meta.get("group_id")),
            "sample_id": _clean(group_meta.get("sample_id")),
            "province": dims["province"],
            "source_file": dims["source_file"],
            "source_family": dims["source_family"],
            "bucket": dims["bucket"],
            "query": _clean(group_meta.get("query")),
            "query_family": dims["query_family"],
            "baseline_rank": baseline_rank,
            "raw_candidate_rank": raw_candidate_rank,
            "candidate_rank": candidate_rank,
            "gate_applies": gate_applies,
            "gate_reason": gate_reason,
            "challenger_support_score": gate_meta["challenger_support_score"],
            "challenger_support_parts": gate_meta["support_parts"],
            "baseline_weak_reasons": gate_meta["baseline_weak_reasons"],
            "clean_rank1_veto": gate_meta["clean_rank1_veto"],
            "taxonomy_empty": gate_meta["taxonomy_empty"],
            "raw_margin_delta": round(raw_delta, 8),
            "candidate_top_quota_id": _clean(top_row.get("quota_id")),
            "candidate_top_family": dims["candidate_top_family"],
            "candidate_top_score": round(float(group_preds[local_top_idx]), 8),
            "baseline_hit1": bool(base_h1),
            "candidate_hit1": bool(cand_h1),
            "flip_type": "gain" if gain_flag else "loss" if loss_flag else "neutral",
        }
        detail_rows.append(detail)
        if gain_flag or loss_flag:
            flip_rows.append(detail)
        if base_h1:
            rank1_rows.append(
                {
                    "split": split,
                    "candidate_id": candidate_id,
                    "group_id": detail["group_id"],
                    "gate_reason": gate_reason,
                    "clean_rank1_veto": gate_meta["clean_rank1_veto"],
                    "challenger_support_score": gate_meta["challenger_support_score"],
                    "raw_candidate_rank": raw_candidate_rank,
                    "candidate_rank": candidate_rank,
                    "rank1_demoted": loss_flag,
                }
            )
        fallback_rows.append(
            {
                "split": split,
                "candidate_id": candidate_id,
                "group_id": detail["group_id"],
                "baseline_hit1": base_h1,
                "raw_candidate_hit1": raw_candidate_rank == 1,
                "candidate_hit1": cand_h1,
                "candidate_override": bool(local_top_idx != 0),
                "gate_applies": gate_applies,
                "clean_rank1_veto": gate_meta["clean_rank1_veto"],
                "override_outcome": detail["flip_type"],
                "rank1_safe_contract": True,
            }
        )
        start = stop

    groups_count = len(groups)
    total_rows = groups_count + len(recall_missing_rows)
    metrics = {
        "split": split,
        "candidate_id": candidate_id,
        "rows": total_rows,
        "rerankable_top80_present_groups": groups_count,
        "recall_missing_groups": len(recall_missing_rows),
        "top80_recall_rate": round(groups_count / total_rows, 6) if total_rows else 0.0,
        "baseline_hit1": baseline_hit1,
        "candidate_hit1": candidate_hit1,
        "baseline_hit1_rate": round(baseline_hit1 / groups_count, 6) if groups_count else 0.0,
        "candidate_hit1_rate": round(candidate_hit1 / groups_count, 6) if groups_count else 0.0,
        "hit1_gain": hit1_gain,
        "hit1_loss": hit1_loss,
        "hit1_net": hit1_gain - hit1_loss,
        "baseline_hit5": baseline_hit5,
        "candidate_hit5": candidate_hit5,
        "hit5_gain": hit5_gain,
        "hit5_loss": hit5_loss,
        "hit5_net": hit5_gain - hit5_loss,
        "applied_groups": applied_groups,
        "applied_group_rate": round(applied_groups / groups_count, 6) if groups_count else 0.0,
        "vetoed_groups": vetoed_groups,
        "baseline_rank1_groups": baseline_rank1_groups,
        "rank1_loss_count": rank1_loss_count,
        "baseline_rank1_demotion_rate": round(rank1_loss_count / baseline_rank1_groups, 6) if baseline_rank1_groups else 0.0,
        "heldout_or_hard_used_for_selection": False,
        "elapsed_sec": round(elapsed_sec, 3),
    }
    loss_rows = [
        {
            "candidate_id": candidate_id,
            "split": split,
            "slice_dimension": dimension,
            "slice_key": key,
            **values,
            "baseline_hit1_rate": round(values["baseline_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
            "candidate_hit1_rate": round(values["candidate_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
        }
        for (dimension, key), values in loss_stats.items()
    ]
    gate_rows = [{"candidate_id": candidate_id, "split": split, "gate_reason": reason, "challenger_support_score": score, **values} for (reason, score), values in gate_stats.items()]
    source_rows = [{"candidate_id": candidate_id, "split": split, "slice_dimension": dimension, "slice_key": key, **values} for (dimension, key), values in source_stats.items()]
    loss_rows.sort(key=lambda row: (row["loss"], row["groups"]), reverse=True)
    source_rows.sort(key=lambda row: (row["net"], row["gain"], row["groups"]), reverse=True)
    gate_rows.sort(key=lambda row: (row["gate_reason"], row["challenger_support_score"]))
    return metrics, detail_rows, recall_missing_rows, loss_rows, gate_rows, source_rows, rank1_rows + fallback_rows + flip_rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        f"# 14.6 {m['split']} R14_A A/B Validation",
        "",
        "Heldout/hard A/B validation for the frozen R14_A rank1-safe source-robust reranker. The candidate, objective, features, and thresholds are fixed from dev/OOF; this split is not used for selection or tuning.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_id", m["candidate_id"]],
                ["rows", m["rows"]],
                ["rerankable_top80_present_groups", m["rerankable_top80_present_groups"]],
                ["recall_missing_groups", m["recall_missing_groups"]],
                ["hit1 gain/loss/net", f"{m['hit1_gain']}/{m['hit1_loss']}/{m['hit1_net']}"],
                ["rank1_loss_count", m["rank1_loss_count"]],
                ["applied_group_rate", m["applied_group_rate"]],
                ["hit5 gain/loss/net", f"{m['hit5_gain']}/{m['hit5_loss']}/{m['hit5_net']}"],
            ]
        ),
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.6 heldout/hard A/B validation for frozen R14_A")
    parser.add_argument("--split", choices=["heldout", "hard"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--frozen-candidate-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST)
    parser.add_argument("--dev-oof-summary", type=Path, default=DEFAULT_DEV_OOF_SUMMARY)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--feature-whitelist", type=Path, default=DEFAULT_FEATURE_WHITELIST)
    parser.add_argument("--db-dir", default=str(DEFAULT_LOCAL_ASSETS_DB_DIR))
    parser.add_argument("--top-k", type=int, default=80)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260527)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    manifest = _read_json(args.frozen_candidate_manifest)
    if manifest.get("candidate_id") != args.candidate_id:
        raise ValueError(f"candidate mismatch: manifest={manifest.get('candidate_id')} arg={args.candidate_id}")
    if args.candidate_id != "R14_A_rank1_veto_strong_challenger":
        raise ValueError("14.6 is scoped only to frozen R14_A")
    if manifest.get("freeze_status") != "freeze_with_risk_notes":
        raise ValueError(f"candidate is not frozen for validation: {manifest.get('freeze_status')}")

    thresholds = _load_fixed_thresholds(args.dev_oof_summary)
    _configure_db_dirs(args.db_dir)
    candidate = {
        "candidate_id": args.candidate_id,
        "objective_variant": manifest["objective_variant"],
        "feature_toggle": manifest["feature_toggle"],
    }
    booster, features, train_metrics = _train_fixed_candidate(
        data_dir=args.data_dir,
        feature_whitelist=args.feature_whitelist,
        candidate=candidate,
        num_boost_round=args.num_boost_round,
        seed=args.seed,
    )
    rows = _read_jsonl(args.input)
    df, labels, groups, meta, recall_missing_rows = _build_validation_matrix(rows, args.split, args.top_k)
    feature_rows = df.to_dict("records")
    metrics, detail_rows, recall_rows, loss_rows, gate_rows, source_rows, contract_rows = _score_split(
        split=args.split,
        candidate=candidate,
        booster=booster,
        features=features,
        df=df,
        labels=labels,
        groups=groups,
        meta=meta,
        feature_rows=feature_rows,
        recall_missing_rows=recall_missing_rows,
        thresholds=thresholds,
        elapsed_sec=time.perf_counter() - started,
    )

    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "details_jsonl": str(output_prefix.with_name(output_prefix.name + "_details.jsonl")),
        "recall_missing_jsonl": str(output_prefix.with_name(output_prefix.name + "_recall_missing.jsonl")),
        "loss_slices_csv": str(output_prefix.with_name(output_prefix.name + "_loss_slices.csv")),
        "gate_coverage_csv": str(output_prefix.with_name(output_prefix.name + "_gate_coverage.csv")),
        "source_slices_csv": str(output_prefix.with_name(output_prefix.name + "_source_slices.csv")),
        "rank1_fallback_contract_jsonl": str(output_prefix.with_name(output_prefix.name + "_rank1_fallback_contract.jsonl")),
    }
    report = {
        "stage": f"14.6 {args.split} heldout/hard A/B validation for frozen R14_A",
        "explicit_user_go": True,
        "split": args.split,
        "frozen_candidate": manifest,
        "fixed_thresholds": thresholds,
        "train_fixed_candidate_metrics": train_metrics,
        "metrics": metrics,
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "14.6 validation only: fixed R14_A, fixed dev/OOF thresholds, no heldout/hard selection or tuning, "
            "no candidate switch, no matrix rebuild, no release, no GoalSearcher edit, and no online threshold change."
        ),
    }
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_jsonl(Path(artifacts["details_jsonl"]), detail_rows)
    _write_jsonl(Path(artifacts["recall_missing_jsonl"]), recall_rows)
    _write_jsonl(Path(artifacts["rank1_fallback_contract_jsonl"]), contract_rows)
    _write_csv(Path(artifacts["loss_slices_csv"]), loss_rows, ["candidate_id", "split", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["gate_coverage_csv"]), gate_rows, ["candidate_id", "split", "gate_reason", "challenger_support_score", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_csv(Path(artifacts["source_slices_csv"]), source_rows, ["candidate_id", "split", "slice_dimension", "slice_key", "groups", "gain", "loss", "net"])
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
