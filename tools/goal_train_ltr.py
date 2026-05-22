from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_train_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_train_summary.md"
DEFAULT_IMPORTANCE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_feature_importance.csv"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "data" / "goal_search" / "goal_ltr_v1.txt"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"


def _read_group(path: Path) -> list[int]:
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_group_meta(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_feature_whitelist(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} does not contain training_features")
    return [str(feature) for feature in features]


def _load_matrix(data_dir: Path, split: str, features: list[str]) -> tuple[pd.DataFrame, np.ndarray, list[int], list[dict[str, Any]]]:
    matrix_path = data_dir / f"ltr_matrix_{split}.csv"
    group_path = data_dir / f"ltr_group_{split}.txt"
    meta_path = data_dir / f"ltr_group_{split}.jsonl"

    df = pd.read_csv(matrix_path, encoding="utf-8-sig")
    missing = [feature for feature in features if feature not in df.columns]
    if missing:
        raise ValueError(f"{matrix_path} missing features: {missing[:10]}")
    labels = df["label"].astype(np.int32).to_numpy()
    groups = _read_group(group_path)
    meta = _read_group_meta(meta_path)
    if sum(groups) != len(df):
        raise ValueError(f"{split} group sum {sum(groups)} != matrix rows {len(df)}")
    if len(groups) != len(meta):
        raise ValueError(f"{split} group count {len(groups)} != meta rows {len(meta)}")
    return df[features].astype(np.float32), labels, groups, meta


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
    ranked = labels[order]
    hits = np.flatnonzero(ranked > 0)
    return int(hits[0] + 1) if len(hits) else None


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(float(np.median(values)), 6) if values else None


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _evaluate_split(
    *,
    split: str,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    preds: np.ndarray,
    details_path: Path,
) -> dict[str, Any]:
    details_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(groups)
    positive_groups = 0
    baseline_hit1 = baseline_hit5 = 0
    ltr_hit1 = ltr_hit5 = 0
    hit1_gain = hit1_loss = hit5_gain = hit5_loss = 0
    baseline_ranks: list[float] = []
    ltr_ranks: list[float] = []
    ndcg_base_1: list[float] = []
    ndcg_base_5: list[float] = []
    ndcg_ltr_1: list[float] = []
    ndcg_ltr_5: list[float] = []
    start = 0

    with details_path.open("w", encoding="utf-8") as handle:
        for group_idx, size in enumerate(groups):
            stop = start + size
            group_labels = labels[start:stop]
            group_preds = preds[start:stop]
            baseline_order = np.arange(size)
            ltr_order = np.lexsort((np.arange(size), -group_preds))

            has_positive = bool(np.any(group_labels > 0))
            positive_groups += int(has_positive)
            base_rank = _first_positive_rank(group_labels, baseline_order)
            rerank = _first_positive_rank(group_labels, ltr_order)
            if base_rank is not None:
                baseline_ranks.append(float(base_rank))
            if rerank is not None:
                ltr_ranks.append(float(rerank))

            base_h1 = bool(base_rank == 1)
            base_h5 = bool(base_rank is not None and base_rank <= 5)
            ltr_h1 = bool(rerank == 1)
            ltr_h5 = bool(rerank is not None and rerank <= 5)
            baseline_hit1 += int(base_h1)
            baseline_hit5 += int(base_h5)
            ltr_hit1 += int(ltr_h1)
            ltr_hit5 += int(ltr_h5)
            hit1_gain += int((not base_h1) and ltr_h1)
            hit1_loss += int(base_h1 and not ltr_h1)
            hit5_gain += int((not base_h5) and ltr_h5)
            hit5_loss += int(base_h5 and not ltr_h5)

            ndcg_base_1.append(_ndcg_at(group_labels, baseline_order, 1))
            ndcg_base_5.append(_ndcg_at(group_labels, baseline_order, 5))
            ndcg_ltr_1.append(_ndcg_at(group_labels, ltr_order, 1))
            ndcg_ltr_5.append(_ndcg_at(group_labels, ltr_order, 5))

            item = dict(meta[group_idx])
            item.update(
                {
                    "split": split,
                    "group_index": group_idx + 1,
                    "has_positive": has_positive,
                    "positive_count": int(np.sum(group_labels > 0)),
                    "baseline_positive_rank": base_rank,
                    "ltr_positive_rank": rerank,
                    "baseline_hit1": base_h1,
                    "baseline_hit5": base_h5,
                    "ltr_hit1": ltr_h1,
                    "ltr_hit5": ltr_h5,
                    "hit1_delta": int(ltr_h1) - int(base_h1),
                    "hit5_delta": int(ltr_h5) - int(base_h5),
                    "ltr_top_original_rank": int(ltr_order[0] + 1) if size else None,
                    "ltr_top_score": round(float(group_preds[ltr_order[0]]), 8) if size else None,
                }
            )
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            start = stop

    return {
        "split": split,
        "groups": total,
        "positive_groups": positive_groups,
        "positive_group_rate": _rate(positive_groups, total),
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
    }


def _write_importance(path: Path, booster: lgb.Booster, features: list[str]) -> list[dict[str, Any]]:
    gain = booster.feature_importance(importance_type="gain")
    split = booster.feature_importance(importance_type="split")
    rows = [
        {"feature": feature, "gain": float(g), "split": int(s)}
        for feature, g, s in zip(features, gain, split, strict=True)
    ]
    rows.sort(key=lambda row: (row["gain"], row["split"]), reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["feature", "gain", "split"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines: list[str] = [
        "# Goal LTR Train Summary",
        "",
        "Stage 2 offline trial: trained LightGBM LambdaRank on dev only, then reranked existing Top80 feature rows for dev/heldout/hard. No search integration.",
        "",
        "## Training",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["model_path", report["model_path"]],
                ["train_split", report["train_split"]],
                ["num_boost_round", report["num_boost_round"]],
                ["features", len(report["features"])],
                ["elapsed_sec", report["elapsed_sec"]],
                ["no_search_integration", report["no_search_integration"]],
            ]
        ),
        "",
        "## Offline Metrics",
        "",
        _md_table(
            [["split", "groups", "positive_rate", "baseline_top1", "ltr_top1", "top1_net", "baseline_top5", "ltr_top5", "top5_net", "baseline_ndcg5", "ltr_ndcg5"]]
            + [
                [
                    item["split"],
                    item["groups"],
                    item["positive_group_rate"],
                    item["baseline_hit1_rate"],
                    item["ltr_hit1_rate"],
                    item["hit1_net"],
                    item["baseline_hit5_rate"],
                    item["ltr_hit5_rate"],
                    item["hit5_net"],
                    item["baseline_ndcg5"],
                    item["ltr_ndcg5"],
                ]
                for item in report["evaluations"]
            ]
        ),
        "",
        "## Top Feature Importance",
        "",
        _md_table([["feature", "gain", "split"]] + [[row["feature"], round(row["gain"], 3), row["split"]] for row in report["feature_importance_top20"]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Goal LTR v1 LightGBM LambdaRank offline trial")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--model-output", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--importance-csv", default=str(DEFAULT_IMPORTANCE_CSV))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    parser.add_argument("--num-boost-round", type=int, default=160)
    parser.add_argument("--learning-rate", type=float, default=0.045)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-data-in-leaf", type=int, default=25)
    parser.add_argument("--seed", type=int, default=20260521)
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    features = _load_feature_whitelist(Path(args.whitelist))
    train_x, train_y, train_group, _train_meta = _load_matrix(data_dir, "dev", features)

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
    train_data = lgb.Dataset(train_x, label=train_y, group=train_group, feature_name=features, free_raw_data=False)
    booster = lgb.train(
        params,
        train_data,
        num_boost_round=args.num_boost_round,
        valid_sets=[train_data],
        valid_names=["dev_train"],
        callbacks=[lgb.log_evaluation(period=40)],
    )
    model_path = Path(args.model_output)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_path))

    evaluations: list[dict[str, Any]] = []
    for split in ("dev", "heldout", "hard"):
        x, y, group, meta = (train_x, train_y, train_group, _train_meta) if split == "dev" else _load_matrix(data_dir, split, features)
        preds = booster.predict(x, num_iteration=booster.current_iteration())
        evaluations.append(
            _evaluate_split(
                split=split,
                labels=y,
                groups=group,
                meta=meta,
                preds=preds,
                details_path=Path(args.details_dir) / f"goal_ltr_eval_{split}_details.jsonl",
            )
        )

    importance_rows = _write_importance(Path(args.importance_csv), booster, features)
    report = {
        "stage": "Goal LTR v1 / stage 2 offline LightGBM LambdaRank trial",
        "no_search_integration": True,
        "train_split": "dev",
        "eval_splits": ["dev", "heldout", "hard"],
        "model_path": str(model_path),
        "importance_csv": args.importance_csv,
        "whitelist": args.whitelist,
        "features": features,
        "params": params,
        "num_boost_round": args.num_boost_round,
        "best_iteration": booster.best_iteration,
        "current_iteration": booster.current_iteration(),
        "evaluations": evaluations,
        "feature_importance_top20": importance_rows[:20],
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "model_path": report["model_path"],
                    "train_split": report["train_split"],
                    "num_boost_round": report["num_boost_round"],
                    "elapsed_sec": report["elapsed_sec"],
                    "no_search_integration": True,
                },
                "evaluations": evaluations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
