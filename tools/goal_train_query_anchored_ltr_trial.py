from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter
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
DEFAULT_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_query_anchored_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ltr_trial_summary.md"
DEFAULT_AUDIT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_loader_audit_summary.json"
DEFAULT_MATRIX_SUMMARY_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_query_anchored_ranking_matrix_dry_run_summary.json"


def _clean(value: Any) -> str:
    return str(value or "").strip()


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


def _load_matrix(data_dir: Path, split: str, features: list[str]) -> tuple[pd.DataFrame, np.ndarray, list[int], list[dict[str, Any]], list[dict[str, Any]]]:
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


def _candidate_brief(row: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    result = {
        "quota_id": _clean(row.get("quota_id")),
        "quota_name": _clean(row.get("quota_name")),
        "quota_book": _clean(row.get("quota_book")),
        "candidate_rank": int(float(row.get("candidate_rank") or 0)),
        "candidate_family": _clean(row.get("candidate_family")),
    }
    if score is not None:
        result["ltr_score"] = round(float(score), 8)
    return result


def _feature_delta(
    baseline: dict[str, Any],
    challenger: dict[str, Any],
    important_features: list[str],
) -> dict[str, Any]:
    deltas: list[dict[str, Any]] = []
    for feature in important_features:
        try:
            base = float(baseline.get(feature) or 0.0)
            new = float(challenger.get(feature) or 0.0)
        except (TypeError, ValueError):
            continue
        diff = new - base
        if diff:
            deltas.append({"feature": feature, "baseline": round(base, 6), "model_top": round(new, 6), "delta": round(diff, 6)})
    deltas.sort(key=lambda row: abs(float(row["delta"])), reverse=True)
    return {
        "deltas": deltas[:12],
        "same_family": _clean(baseline.get("candidate_family")) == _clean(challenger.get("candidate_family")),
        "same_book": _clean(baseline.get("quota_book")) == _clean(challenger.get("quota_book")),
        "model_family_conflict": int(float(challenger.get("family_conflict") or 0)),
        "model_book_conflict": int(float(challenger.get("book_conflict") or 0)),
        "model_param_conflict_count": int(float(challenger.get("param_conflict_count") or 0)),
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _top_counter(counter: Counter[str], limit: int = 30) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _evaluate_split(
    *,
    split: str,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    preds: np.ndarray,
    output_dir: Path,
    important_features: list[str],
) -> dict[str, Any]:
    total = len(groups)
    baseline_hit1 = baseline_hit5 = 0
    ltr_hit1 = ltr_hit5 = 0
    hit1_gain = hit1_loss = hit5_gain = hit5_loss = 0
    baseline_ranks: list[float] = []
    ltr_ranks: list[float] = []
    ndcg_base_1: list[float] = []
    ndcg_base_5: list[float] = []
    ndcg_ltr_1: list[float] = []
    ndcg_ltr_5: list[float] = []
    details: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    gain_loss_buckets: dict[str, Counter[str]] = {
        "hit1_gain_family": Counter(),
        "hit1_loss_family": Counter(),
        "hit1_gain_province": Counter(),
        "hit1_loss_province": Counter(),
        "hit1_gain_source": Counter(),
        "hit1_loss_source": Counter(),
        "hit1_gain_positive_rank": Counter(),
        "hit1_loss_positive_rank": Counter(),
    }

    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = preds[start:stop]
        group_features = feature_rows[start:stop]
        baseline_order = np.arange(size)
        ltr_order = np.lexsort((np.arange(size), -group_preds))
        base_rank = _first_positive_rank(group_labels, baseline_order)
        ltr_rank = _first_positive_rank(group_labels, ltr_order)
        base_h1 = bool(base_rank == 1)
        base_h5 = bool(base_rank is not None and base_rank <= 5)
        model_h1 = bool(ltr_rank == 1)
        model_h5 = bool(ltr_rank is not None and ltr_rank <= 5)
        baseline_hit1 += int(base_h1)
        baseline_hit5 += int(base_h5)
        ltr_hit1 += int(model_h1)
        ltr_hit5 += int(model_h5)
        hit1_gain += int((not base_h1) and model_h1)
        hit1_loss += int(base_h1 and not model_h1)
        hit5_gain += int((not base_h5) and model_h5)
        hit5_loss += int(base_h5 and not model_h5)
        if base_rank is not None:
            baseline_ranks.append(float(base_rank))
        if ltr_rank is not None:
            ltr_ranks.append(float(ltr_rank))
        ndcg_base_1.append(_ndcg_at(group_labels, baseline_order, 1))
        ndcg_base_5.append(_ndcg_at(group_labels, baseline_order, 5))
        ndcg_ltr_1.append(_ndcg_at(group_labels, ltr_order, 1))
        ndcg_ltr_5.append(_ndcg_at(group_labels, ltr_order, 5))

        top_idx = int(ltr_order[0])
        positive_indices = [int(idx) for idx in np.flatnonzero(group_labels > 0)]
        group_meta = dict(meta[group_idx])
        query_family = _clean(group_meta.get("query_family")) or "<empty>"
        province = _clean(group_meta.get("province")) or "<empty>"
        source_file = _clean(group_meta.get("source_file")) or "<empty>"
        base_name = "hit1_gain" if (not base_h1 and model_h1) else "hit1_loss" if (base_h1 and not model_h1) else ""
        if base_name:
            gain_loss_buckets[f"{base_name}_family"][query_family] += 1
            gain_loss_buckets[f"{base_name}_province"][province] += 1
            gain_loss_buckets[f"{base_name}_source"][source_file] += 1
            gain_loss_buckets[f"{base_name}_positive_rank"][str(base_rank)] += 1

        baseline_row = group_features[0]
        top_row = group_features[top_idx]
        item = {
            "split": split,
            "group_index": group_idx + 1,
            "group_id": group_meta.get("group_id"),
            "sample_id": group_meta.get("sample_id"),
            "source_file": source_file,
            "project_name": group_meta.get("project_name"),
            "province": province,
            "query": group_meta.get("query"),
            "query_family": query_family,
            "expected_ids": group_meta.get("expected_ids"),
            "positive_count": int(np.sum(group_labels > 0)),
            "baseline_positive_rank": base_rank,
            "ltr_positive_rank": ltr_rank,
            "baseline_hit1": base_h1,
            "ltr_hit1": model_h1,
            "baseline_hit5": base_h5,
            "ltr_hit5": model_h5,
            "hit1_delta": int(model_h1) - int(base_h1),
            "hit5_delta": int(model_h5) - int(base_h5),
            "baseline_top": _candidate_brief(baseline_row, float(group_preds[0])),
            "model_top": _candidate_brief(top_row, float(group_preds[top_idx])),
            "positive_candidates": [_candidate_brief(group_features[idx], float(group_preds[idx])) for idx in positive_indices[:5]],
            "feature_delta": _feature_delta(baseline_row, top_row, important_features),
        }
        details.append(item)
        if item["hit1_delta"] != 0:
            flips.append(item)
        start = stop

    details_path = output_dir / f"eval_{split}_details.jsonl"
    flips_path = output_dir / f"eval_{split}_hit1_flips.jsonl"
    _write_jsonl(details_path, details)
    _write_jsonl(flips_path, flips)

    bucket_rows: list[dict[str, Any]] = []
    for bucket_name, counter in gain_loss_buckets.items():
        for row in _top_counter(counter):
            bucket_rows.append({"split": split, "bucket": bucket_name, **row})

    return {
        "split": split,
        "groups": total,
        "baseline_hit1": baseline_hit1,
        "baseline_hit1_rate": _rate(baseline_hit1, total),
        "baseline_hit5": baseline_hit5,
        "baseline_hit5_rate": _rate(baseline_hit5, total),
        "ltr_hit1": ltr_hit1,
        "ltr_hit1_rate": _rate(ltr_hit1, total),
        "ltr_hit5": ltr_hit5,
        "ltr_hit5_rate": _rate(ltr_hit5, total),
        "hit1_gain": hit1_gain,
        "hit1_loss": hit1_loss,
        "hit1_net": hit1_gain - hit1_loss,
        "hit5_gain": hit5_gain,
        "hit5_loss": hit5_loss,
        "hit5_net": hit5_gain - hit5_loss,
        "baseline_rank_avg": _mean(baseline_ranks),
        "baseline_rank_median": _median(baseline_ranks),
        "ltr_rank_avg": _mean(ltr_ranks),
        "ltr_rank_median": _median(ltr_ranks),
        "baseline_ndcg1": _mean(ndcg_base_1),
        "baseline_ndcg5": _mean(ndcg_base_5),
        "ltr_ndcg1": _mean(ndcg_ltr_1),
        "ltr_ndcg5": _mean(ndcg_ltr_5),
        "details_jsonl": str(details_path),
        "hit1_flips_jsonl": str(flips_path),
        "gain_loss_buckets": bucket_rows,
    }


def _write_importance(path: Path, booster: lgb.Booster, features: list[str]) -> list[dict[str, Any]]:
    gains = booster.feature_importance(importance_type="gain")
    splits = booster.feature_importance(importance_type="split")
    rows = [
        {"feature": feature, "gain": float(gain), "split": int(split)}
        for feature, gain, split in zip(features, gains, splits, strict=True)
    ]
    rows.sort(key=lambda row: (row["gain"], row["split"]), reverse=True)
    _write_csv(path, rows, ["feature", "gain", "split"])
    return rows


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
    lines = [
        "# Goal Query-Anchored LTR Trial",
        "",
        "Stage 6.7 offline trial. LightGBM LambdaRank is trained on anchor-clean dev only and evaluated on existing heldout/hard matrices. No search integration, no rerank switch, no rule changes.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["training_ready_gate", report["training_ready_gate"]],
                ["model_path", report["model_path"]],
                ["train_split", report["train_split"]],
                ["num_boost_round", report["num_boost_round"]],
                ["features", len(report["features"])],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Offline Metrics",
        "",
        _md_table(
            [
                ["split", "eligible", "groups", "baseline_top1_all", "ltr_top1_all", "baseline_top1_matrix", "ltr_top1_matrix", "top1_gain", "top1_loss", "top1_net", "baseline_top5", "ltr_top5", "top5_net"],
                *[
                    [
                        item["split"],
                        item.get("eligible_anchor_rows", ""),
                        item["groups"],
                        item.get("baseline_hit1_rate_on_eligible", ""),
                        item.get("ltr_hit1_rate_on_eligible", ""),
                        item["baseline_hit1_rate"],
                        item["ltr_hit1_rate"],
                        item["hit1_gain"],
                        item["hit1_loss"],
                        item["hit1_net"],
                        item["baseline_hit5_rate"],
                        item["ltr_hit5_rate"],
                        item["hit5_net"],
                    ]
                    for item in report["evaluations"]
                ],
            ]
        ),
        "",
        "## Top Feature Importance",
        "",
        _md_table([["feature", "gain", "split"], *[[row["feature"], round(row["gain"], 3), row["split"]] for row in report["feature_importance_top20"]]]),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 6.7 dev-only query-anchored LambdaRank trial")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--feature-whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--loader-audit", default=str(DEFAULT_AUDIT_JSON))
    parser.add_argument("--matrix-summary", default=str(DEFAULT_MATRIX_SUMMARY_JSON))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--num-boost-round", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=0.04)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-data-in-leaf", type=int, default=25)
    parser.add_argument("--seed", type=int, default=20260522)
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    features = _load_features(Path(args.feature_whitelist))
    loader_audit = _read_json(Path(args.loader_audit))
    matrix_summary = _read_json(Path(args.matrix_summary))
    training_ready = bool(loader_audit.get("training_ready"))
    if not training_ready:
        raise ValueError(f"loader audit is not training_ready: {loader_audit.get('training_blockers')}")

    train_x, train_y, train_groups, train_meta, train_feature_rows = _load_matrix(data_dir, "dev", features)
    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 5, 10],
        "learning_rate": args.learning_rate,
        "num_leaves": args.num_leaves,
        "min_data_in_leaf": args.min_data_in_leaf,
        "feature_fraction": 0.90,
        "bagging_fraction": 0.90,
        "bagging_freq": 1,
        "label_gain": [0, 1],
        "verbosity": -1,
        "seed": args.seed,
        "feature_fraction_seed": args.seed,
        "bagging_seed": args.seed,
        "data_random_seed": args.seed,
        "num_threads": 0,
    }
    train_data = lgb.Dataset(train_x, label=train_y, group=train_groups, feature_name=features, free_raw_data=False)
    booster = lgb.train(
        params,
        train_data,
        num_boost_round=args.num_boost_round,
        valid_sets=[train_data],
        valid_names=["dev_train"],
        callbacks=[lgb.log_evaluation(period=60)],
    )
    model_path = output_dir / "goal_query_anchored_ltr_dev_trial.txt"
    booster.save_model(str(model_path))
    importance_path = output_dir / "feature_importance.csv"
    importance_rows = _write_importance(importance_path, booster, features)
    important_features = [row["feature"] for row in importance_rows[:30]]

    evaluations: list[dict[str, Any]] = []
    all_bucket_rows: list[dict[str, Any]] = []
    split_payloads = {
        "dev": (train_x, train_y, train_groups, train_meta, train_feature_rows),
    }
    for split in ("heldout", "hard"):
        split_payloads[split] = _load_matrix(data_dir, split, features)

    for split, (x, labels, groups, meta, feature_rows) in split_payloads.items():
        preds = booster.predict(x, num_iteration=booster.current_iteration())
        evaluation = _evaluate_split(
            split=split,
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
            preds=preds,
            output_dir=output_dir,
            important_features=important_features,
        )
        split_summary = next((item for item in matrix_summary.get("splits", []) if item.get("split") == split), {})
        eligible_rows = int(split_summary.get("eligible_anchor_rows") or split_summary.get("input_rows") or evaluation["groups"])
        recall_gap_groups = int(split_summary.get("recall_gap_groups") or max(0, eligible_rows - evaluation["groups"]))
        evaluation["eligible_anchor_rows"] = eligible_rows
        evaluation["recall_gap_groups"] = recall_gap_groups
        evaluation["top80_recall_rate"] = _rate(evaluation["groups"], eligible_rows)
        evaluation["baseline_hit1_rate_on_eligible"] = _rate(evaluation["baseline_hit1"], eligible_rows)
        evaluation["ltr_hit1_rate_on_eligible"] = _rate(evaluation["ltr_hit1"], eligible_rows)
        evaluation["baseline_hit5_rate_on_eligible"] = _rate(evaluation["baseline_hit5"], eligible_rows)
        evaluation["ltr_hit5_rate_on_eligible"] = _rate(evaluation["ltr_hit5"], eligible_rows)
        all_bucket_rows.extend(evaluation.pop("gain_loss_buckets"))
        evaluations.append(evaluation)

    bucket_path = output_dir / "gain_loss_buckets.csv"
    _write_csv(bucket_path, all_bucket_rows, ["split", "bucket", "key", "count"])

    report = {
        "stage": "Goal LTR v1 / stage 6.7 dev-only query anchored LambdaRank trial",
        "eval_only": True,
        "offline_training": True,
        "no_search_integration": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "training_ready_gate": training_ready,
        "train_split": "dev",
        "eval_splits": ["heldout", "hard"],
        "data_dir": str(data_dir),
        "feature_whitelist": str(Path(args.feature_whitelist)),
        "loader_audit": str(Path(args.loader_audit)),
        "matrix_summary": str(Path(args.matrix_summary)),
        "model_path": str(model_path),
        "features": features,
        "params": params,
        "num_boost_round": args.num_boost_round,
        "current_iteration": booster.current_iteration(),
        "evaluations": evaluations,
        "feature_importance_top20": importance_rows[:20],
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "artifacts": {
            "model_path": str(model_path),
            "feature_importance_csv": str(importance_path),
            "gain_loss_buckets_csv": str(bucket_path),
            "dev_details_jsonl": str(output_dir / "eval_dev_details.jsonl"),
            "heldout_details_jsonl": str(output_dir / "eval_heldout_details.jsonl"),
            "hard_details_jsonl": str(output_dir / "eval_hard_details.jsonl"),
            "dev_hit1_flips_jsonl": str(output_dir / "eval_dev_hit1_flips.jsonl"),
            "heldout_hit1_flips_jsonl": str(output_dir / "eval_heldout_hit1_flips.jsonl"),
            "hard_hit1_flips_jsonl": str(output_dir / "eval_hard_hit1_flips.jsonl"),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
    }
    Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "offline_training": report["offline_training"],
                    "no_search_integration": report["no_search_integration"],
                    "training_ready_gate": report["training_ready_gate"],
                    "model_path": report["model_path"],
                    "features": len(features),
                    "elapsed_sec": report["elapsed_sec"],
                },
                "evaluations": evaluations,
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
