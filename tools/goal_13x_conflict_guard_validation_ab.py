from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.goal_search import GoalSearcher  # noqa: E402
from src.goal_search.national_index import clean_text  # noqa: E402
from tools.goal_10x_offline_ranking_experiment_dev_oof_execute import (  # noqa: E402
    _candidate_features,
    _load_dev_matrix,
    _load_training_features,
)
from tools.goal_13x_oss_xml_source_aware_matrix_build import _configure_db_dirs  # noqa: E402
from tools.goal_13x_top1_loss_guarded_dev_oof_execute import (  # noqa: E402
    _candidate_gate_applies,
    _group_weights_guarded,
    _objective_params_guarded,
)
from tools.goal_build_ltr_features import _build_feature_row, _query_signal, _query_text  # noqa: E402
from tools.goal_eval import _row_id, _with_leakage_controls  # noqa: E402


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded"
DEFAULT_FEATURE_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_oss_source_aware_v1.json"
DEFAULT_FROZEN_MANIFEST = AGENT_STATE / "goal_13x_top1_loss_guarded_freeze_gate_review_frozen_candidate_manifest.json"
DEFAULT_LOCAL_ASSETS_DB_DIR = PROJECT_ROOT.parent / "auto-quota-local-assets-20260522" / "db"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return text or "<empty>"


def _expected_ids(row: dict[str, Any]) -> set[str]:
    raw = row.get("expected_ids") or row.get("stored_ids") or row.get("expected_id") or []
    if isinstance(raw, list):
        values = raw
    else:
        try:
            parsed = json.loads(str(raw))
            values = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            values = str(raw).split("|")
    return {clean_text(value) for value in values if clean_text(value)}


def _normalise_eval_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    item = dict(row)
    bill_name = clean_text(item.get("bill_name") or item.get("name") or item.get("query"))
    bill_text = clean_text(item.get("bill_text") or item.get("description"))
    item["sample_id"] = clean_text(item.get("sample_id") or item.get("idx") or index)
    item["bill_name"] = bill_name
    item["name"] = bill_name
    item["bill_text"] = bill_text or bill_name
    item["description"] = bill_text
    item["source_file"] = clean_text(item.get("source_file") or "<empty>")
    item["project_name"] = clean_text(item.get("project_name"))
    item["unit"] = clean_text(item.get("unit"))
    item["specialty"] = clean_text(item.get("specialty"))
    item["province"] = clean_text(item.get("province"))
    return item


def _first_positive_rank(labels: np.ndarray, order: np.ndarray) -> int | None:
    hits = np.flatnonzero(labels[order] > 0)
    return int(hits[0] + 1) if len(hits) else None


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
        weight=_group_weights_guarded(candidate["objective_variant"], labels, groups),
        feature_name=features,
        free_raw_data=False,
    )
    booster = lgb.train(
        _objective_params_guarded(candidate["objective_variant"], seed),
        train_data,
        num_boost_round=num_boost_round,
        valid_sets=[train_data],
        valid_names=["dev_train_fixed_conflict_guard"],
        callbacks=[],
    )
    return booster, features, {"train_groups": len(groups), "train_rows": len(df), "feature_count": len(features), "num_boost_round": num_boost_round}


def _build_validation_matrix(rows: list[dict[str, Any]], split: str, top_k: int) -> tuple[pd.DataFrame, np.ndarray, list[int], list[dict[str, Any]], list[dict[str, Any]]]:
    leakage_args = SimpleNamespace(allow_answer_priors=False, exclude_sample_id="", exclude_source_file="", exclude_project_name="")
    searchers: dict[str, GoalSearcher] = {}
    feature_rows: list[dict[str, Any]] = []
    labels: list[int] = []
    groups: list[int] = []
    meta: list[dict[str, Any]] = []
    recall_missing_rows: list[dict[str, Any]] = []

    for index, raw in enumerate(rows, 1):
        row = _normalise_eval_row(raw, index)
        expected = _expected_ids(row)
        province = clean_text(row.get("province"))
        group_id = f"{split}:{index}:{_row_id(row, index)}"
        if not expected or not province:
            recall_missing_rows.append(
                {
                    "split": split,
                    "group_id": group_id,
                    "sample_id": _row_id(row, index),
                    "province": province,
                    "source_file": row.get("source_file", ""),
                    "query": _query_text(row),
                    "expected_ids": sorted(expected),
                    "recall_gap_reason": "missing_expected_or_province",
                }
            )
            continue
        if province not in searchers:
            searchers[province] = GoalSearcher(province)
        searcher = searchers[province]
        query_signal = _query_signal(row)
        hits = searcher.search(_with_leakage_controls(row, leakage_args), top_k=top_k)
        top_ids = [hit.quota_id for hit in hits]
        baseline_rank = next((rank for rank, quota_id in enumerate(top_ids, 1) if quota_id in expected), None)
        if baseline_rank is None:
            recall_missing_rows.append(
                {
                    "split": split,
                    "group_id": group_id,
                    "sample_id": _row_id(row, index),
                    "province": province,
                    "source_file": row.get("source_file", ""),
                    "query": _query_text(row),
                    "query_family": query_signal.family or "<empty>",
                    "expected_ids": sorted(expected),
                    "candidate_count": len(hits),
                    "top_ids": top_ids[:10],
                    "recall_gap_reason": "expected_id_not_in_topk",
                }
            )
            continue
        group_rows = []
        for hit_rank, hit in enumerate(hits, 1):
            if hit.quota_id not in searcher.index.by_quota_id:
                continue
            item = _build_feature_row(
                split=split,
                row=row,
                row_index=index,
                hit_rank=hit_rank,
                hit=hit,
                searcher=searcher,
                query_signal=query_signal,
                expected=expected,
            )
            item["group_id"] = group_id
            group_rows.append(item)
        if not group_rows or not any(_int(item.get("label")) for item in group_rows):
            recall_missing_rows.append(
                {
                    "split": split,
                    "group_id": group_id,
                    "sample_id": _row_id(row, index),
                    "province": province,
                    "source_file": row.get("source_file", ""),
                    "query": _query_text(row),
                    "query_family": query_signal.family or "<empty>",
                    "expected_ids": sorted(expected),
                    "candidate_count": len(group_rows),
                    "recall_gap_reason": "positive_filtered_after_feature_build",
                }
            )
            continue
        feature_rows.extend(group_rows)
        labels.extend(_int(item.get("label")) for item in group_rows)
        groups.append(len(group_rows))
        meta.append(
            {
                "split": split,
                "group_id": group_id,
                "sample_id": _row_id(row, index),
                "source_file": row.get("source_file", ""),
                "province": province,
                "bucket": clean_text(row.get("bucket")),
                "query": _query_text(row),
                "query_family": query_signal.family or "<empty>",
                "expected_ids": sorted(expected),
                "positive_rank": baseline_rank,
                "candidate_count": len(group_rows),
            }
        )
    return pd.DataFrame(feature_rows), np.array(labels, dtype=np.int8), groups, meta, recall_missing_rows


def _score_split(
    *,
    split: str,
    candidate: dict[str, Any],
    booster: lgb.Booster,
    features: list[str],
    df: pd.DataFrame,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    recall_missing_rows: list[dict[str, Any]],
    elapsed_sec: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidate_id = candidate["candidate_id"]
    for feature in features:
        if feature not in df.columns:
            df[feature] = 0
    preds = booster.predict(df[features].astype(np.float32).to_numpy(), num_iteration=booster.current_iteration()) if len(df) else np.array([], dtype=np.float32)
    detail_rows: list[dict[str, Any]] = []
    slice_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
    gate_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"groups": 0, "applied": 0, "vetoed": 0, "gain": 0, "loss": 0, "net": 0})
    source_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "gain": 0, "loss": 0, "net": 0})
    baseline_hit1 = candidate_hit1 = hit1_gain = hit1_loss = 0
    baseline_hit5 = candidate_hit5 = hit5_gain = hit5_loss = 0
    applied_groups = vetoed_groups = rank1_loss_count = baseline_rank1_groups = 0
    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = np.array(preds[start:stop], dtype=np.float32)
        group_rows = feature_rows[start:stop]
        group_meta = meta[group_idx]
        baseline_order = np.arange(size)
        raw_candidate_order = np.lexsort((np.arange(size), -group_preds))
        baseline_rank = _first_positive_rank(group_labels, baseline_order)
        raw_candidate_rank = _first_positive_rank(group_labels, raw_candidate_order)
        gate_applies, gate_reason = _candidate_gate_applies(candidate_id, group_meta, group_rows, baseline_rank)
        candidate_order = raw_candidate_order if gate_applies else baseline_order
        veto_applied = False
        if baseline_rank == 1:
            baseline_rank1_groups += 1
            if not gate_applies:
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
        top_row = feature_rows[start + local_top_idx]
        candidate_top_family = _clean(top_row.get("candidate_family"))
        candidate_top_book = _clean(top_row.get("quota_book"))
        dims = {
            "province": _clean(group_meta.get("province")),
            "source_file": _clean(group_meta.get("source_file")),
            "bucket": _clean(group_meta.get("bucket")),
            "query_family": _clean(group_meta.get("query_family")),
            "top1_family": candidate_top_family,
            "gate_reason": gate_reason,
            "book_and_rank_bucket": f"top1={candidate_top_book};{_rank_bucket(baseline_rank)}",
        }
        for dimension, key in dims.items():
            item = slice_stats[(dimension, key)]
            item["groups"] += 1
            item["baseline_hit1"] += int(base_h1)
            item["candidate_hit1"] += int(cand_h1)
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)
        for dimension in ("province", "source_file"):
            item = source_stats[(dimension, dims[dimension])]
            item["groups"] += 1
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)
        gate_item = gate_stats[gate_reason]
        gate_item["groups"] += 1
        gate_item["applied"] += int(gate_applies)
        gate_item["vetoed"] += int(veto_applied)
        gate_item["gain"] += int(gain_flag)
        gate_item["loss"] += int(loss_flag)
        gate_item["net"] += int(gain_flag) - int(loss_flag)
        detail_rows.append(
            {
                "split": split,
                "candidate_id": candidate_id,
                "group_id": group_meta["group_id"],
                "sample_id": group_meta["sample_id"],
                "province": dims["province"],
                "source_file": dims["source_file"],
                "bucket": dims["bucket"],
                "query": group_meta["query"],
                "query_family": dims["query_family"],
                "gate_reason": gate_reason,
                "gate_applies": gate_applies,
                "veto_applied": veto_applied,
                "baseline_rank": baseline_rank,
                "raw_candidate_rank": raw_candidate_rank,
                "candidate_rank": candidate_rank,
                "baseline_hit1": bool(base_h1),
                "candidate_hit1": bool(cand_h1),
                "flip_type": "gain" if gain_flag else "loss" if loss_flag else "neutral",
                "candidate_top_quota_id": _clean(top_row.get("quota_id")),
                "candidate_top_family": candidate_top_family,
                "candidate_top_book": candidate_top_book,
                "candidate_top_score": round(float(group_preds[local_top_idx]), 8),
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
        for (dimension, key), values in slice_stats.items()
    ]
    gate_rows = [{"candidate_id": candidate_id, "split": split, "gate_reason": key, **values} for key, values in gate_stats.items()]
    source_rows = [{"candidate_id": candidate_id, "split": split, "slice_dimension": dimension, "slice_key": key, **values} for (dimension, key), values in source_stats.items()]
    loss_rows.sort(key=lambda row: (row["loss"], row["groups"]), reverse=True)
    source_rows.sort(key=lambda row: (row["net"], row["gain"], row["groups"]), reverse=True)
    return metrics, detail_rows, recall_missing_rows, loss_rows, gate_rows, source_rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        f"# 13.21 {m['split']} Conflict Guard A/B Validation",
        "",
        "Heldout/hard validation for the frozen T1G_B conflict-gated reranker. The candidate and gate are fixed; validation is not used for selection or threshold tuning.",
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
    parser = argparse.ArgumentParser(description="13.21 heldout/hard A/B validation for frozen T1G_B conflict guard")
    parser.add_argument("--split", choices=["heldout", "hard"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--frozen-candidate-manifest", type=Path, default=DEFAULT_FROZEN_MANIFEST)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--feature-whitelist", type=Path, default=DEFAULT_FEATURE_WHITELIST)
    parser.add_argument("--db-dir", default=str(DEFAULT_LOCAL_ASSETS_DB_DIR))
    parser.add_argument("--top-k", type=int, default=80)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20240526)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    started = time.perf_counter()
    manifest = _read_json(args.frozen_candidate_manifest)
    if manifest.get("candidate_id") != args.candidate_id:
        raise ValueError(f"candidate mismatch: manifest={manifest.get('candidate_id')} arg={args.candidate_id}")
    if manifest.get("deployability") != "deployable":
        raise ValueError("frozen candidate is not deployable")
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
    metrics, detail_rows, recall_rows, loss_rows, gate_rows, source_rows = _score_split(
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
    }
    report = {
        "stage": f"13.21 {args.split} heldout/hard A/B validation for frozen T1G_B_conflict_guard",
        "explicit_user_go": True,
        "split": args.split,
        "frozen_candidate": manifest,
        "train_fixed_candidate_metrics": train_metrics,
        "metrics": metrics,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Validation execution only: fixed deployable conflict guard, no candidate reselection, no threshold tuning, no label-derived gate, no online integration, and no GoalSearcher edit.",
    }
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_jsonl(Path(artifacts["details_jsonl"]), detail_rows)
    _write_jsonl(Path(artifacts["recall_missing_jsonl"]), recall_rows)
    _write_csv(Path(artifacts["loss_slices_csv"]), loss_rows, ["candidate_id", "split", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["gate_coverage_csv"]), gate_rows, ["candidate_id", "split", "gate_reason", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_csv(Path(artifacts["source_slices_csv"]), source_rows, ["candidate_id", "split", "slice_dimension", "slice_key", "groups", "gain", "loss", "net"])
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
