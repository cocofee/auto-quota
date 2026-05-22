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

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "data" / "goal_search" / "goal_ltr_v1.txt"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "goal_search" / "ltr_feature_whitelist_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_summary.md"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_variants.csv"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"


PARAM_MATCH_FEATURES = (
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


def _read_group(path: Path) -> list[int]:
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_feature_whitelist(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} does not contain training_features")
    return [str(feature) for feature in features]


def _load_split(
    data_dir: Path,
    split: str,
    features: list[str],
) -> tuple[pd.DataFrame, np.ndarray, list[int], list[dict[str, Any]]]:
    matrix_path = data_dir / f"ltr_matrix_{split}.csv"
    group_path = data_dir / f"ltr_group_{split}.txt"
    features_path = data_dir / f"ltr_features_{split}.jsonl"

    df = pd.read_csv(matrix_path, encoding="utf-8-sig")
    missing = [feature for feature in features if feature not in df.columns]
    if missing:
        raise ValueError(f"{matrix_path} missing features: {missing[:10]}")
    labels = df["label"].astype(np.int32).to_numpy()
    groups = _read_group(group_path)
    feature_rows = list(_iter_jsonl(features_path))

    if sum(groups) != len(df):
        raise ValueError(f"{split} group sum {sum(groups)} != matrix rows {len(df)}")
    if len(feature_rows) != len(df):
        raise ValueError(f"{split} feature rows {len(feature_rows)} != matrix rows {len(df)}")

    return df[features].astype(np.float32), labels, groups, feature_rows


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


def _same_non_empty(left: str, right: str) -> bool:
    return bool(left and right and left == right)


def _has_param_support(row: dict[str, Any]) -> bool:
    return any(_int(row, feature) > 0 for feature in PARAM_MATCH_FEATURES)


def _gate_facts(
    baseline_row: dict[str, Any],
    ltr_row: dict[str, Any],
    baseline_score: float,
    ltr_score: float,
) -> dict[str, Any]:
    baseline_family = _clean(baseline_row.get("candidate_family"))
    ltr_family = _clean(ltr_row.get("candidate_family"))
    baseline_book = _clean(baseline_row.get("quota_book"))
    ltr_book = _clean(ltr_row.get("quota_book"))

    same_family = _same_non_empty(baseline_family, ltr_family)
    same_book = _same_non_empty(baseline_book, ltr_book)
    no_family_conflict = _int(ltr_row, "family_conflict") == 0 and _int(ltr_row, "has_family_conflict_reason") == 0
    no_book_conflict = _int(ltr_row, "book_conflict") == 0 and _int(ltr_row, "has_book_conflict_reason") == 0
    no_param_conflict = _int(ltr_row, "param_conflict_count") == 0 and _int(ltr_row, "has_param_conflict_reason") == 0
    strict_same_family_book_param = same_family and same_book and no_family_conflict and no_book_conflict and no_param_conflict

    return {
        "baseline_family": baseline_family,
        "ltr_family": ltr_family,
        "baseline_book": baseline_book,
        "ltr_book": ltr_book,
        "same_family": same_family,
        "same_book": same_book,
        "no_family_conflict": no_family_conflict,
        "no_book_conflict": no_book_conflict,
        "no_param_conflict": no_param_conflict,
        "strict_same_family_book_param": strict_same_family_book_param,
        "ltr_param_support": _has_param_support(ltr_row),
        "score_margin": float(ltr_score - baseline_score),
    }


def _gate_decision(
    *,
    variant: dict[str, Any],
    ltr_top_idx: int,
    facts: dict[str, Any],
) -> tuple[bool, str]:
    if ltr_top_idx == 0:
        return True, "same_as_baseline"
    if variant["mode"] == "raw":
        return True, "raw_ltr"
    if variant["mode"] == "strict":
        if facts["strict_same_family_book_param"]:
            return True, "strict_same_family_book_param"
        return False, "blocked_not_strict_safe"
    if variant["mode"] == "strict_or_margin":
        if facts["strict_same_family_book_param"]:
            return True, "strict_same_family_book_param"
        if facts["score_margin"] >= variant["margin"]:
            return True, "large_score_margin"
        return False, "blocked_by_safety_gate"
    raise ValueError(f"unknown gate mode: {variant['mode']}")


def _gated_order(raw_order: np.ndarray, allow_override: bool) -> np.ndarray:
    if allow_override:
        return raw_order
    remaining = [idx for idx in raw_order.tolist() if idx != 0]
    return np.array([0, *remaining], dtype=np.int64)


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


def _summarize_variant(
    split: str,
    variant: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    total = len(rows)
    positive_groups = sum(int(row["has_positive"]) for row in rows)
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
    prevented_raw_loss = sum(
        int(row["baseline_hit1"] and (not row["raw_ltr_hit1"]) and row["gated_hit1"]) for row in rows
    )
    blocked_raw_gain = sum(
        int((not row["baseline_hit1"]) and row["raw_ltr_hit1"] and (not row["gated_hit1"])) for row in rows
    )
    passed_raw_gain = sum(int((not row["baseline_hit1"]) and row["raw_ltr_hit1"] and row["gated_hit1"]) for row in rows)
    passed_raw_loss = sum(int(row["baseline_hit1"] and (not row["raw_ltr_hit1"]) and (not row["gated_hit1"])) for row in rows)

    reason_counts = Counter(row["gate_reason"] for row in rows)
    decision_counts = Counter("allowed" if row["gate_allowed"] else "blocked" for row in rows if not row["raw_same_as_baseline"])
    raw_override_count = sum(int(not row["raw_same_as_baseline"]) for row in rows)
    gated_override_count = sum(int((not row["raw_same_as_baseline"]) and row["gate_allowed"]) for row in rows)
    ranks = [float(row["gated_positive_rank"]) for row in rows if row["gated_positive_rank"] is not None]

    return {
        "split": split,
        "variant": variant["name"],
        "mode": variant["mode"],
        "margin": variant.get("margin"),
        "groups": total,
        "positive_groups": positive_groups,
        "positive_group_rate": _rate(positive_groups, total),
        "baseline_hit1": baseline_hit1,
        "baseline_hit1_rate": _rate(baseline_hit1, total),
        "baseline_hit5": baseline_hit5,
        "baseline_hit5_rate": _rate(baseline_hit5, total),
        "raw_ltr_hit1": raw_hit1,
        "raw_ltr_hit1_rate": _rate(raw_hit1, total),
        "raw_ltr_hit5": raw_hit5,
        "raw_ltr_hit5_rate": _rate(raw_hit5, total),
        "raw_ltr_hit1_gain": raw_hit1_gain,
        "raw_ltr_hit1_loss": raw_hit1_loss,
        "raw_ltr_hit1_net": raw_hit1_gain - raw_hit1_loss,
        "gated_hit1": gated_hit1,
        "gated_hit1_rate": _rate(gated_hit1, total),
        "gated_hit5": gated_hit5,
        "gated_hit5_rate": _rate(gated_hit5, total),
        "gated_hit1_gain": hit1_gain,
        "gated_hit1_loss": hit1_loss,
        "gated_hit1_net": hit1_gain - hit1_loss,
        "gated_hit5_gain": hit5_gain,
        "gated_hit5_loss": hit5_loss,
        "gated_hit5_net": hit5_gain - hit5_loss,
        "raw_override_count": raw_override_count,
        "gated_override_count": gated_override_count,
        "gated_override_rate": _rate(gated_override_count, raw_override_count),
        "prevented_raw_hit1_loss": prevented_raw_loss,
        "blocked_raw_hit1_gain": blocked_raw_gain,
        "passed_raw_hit1_gain": passed_raw_gain,
        "passed_raw_hit1_loss": passed_raw_loss,
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
    feature_rows: list[dict[str, Any]],
    preds: np.ndarray,
    variants: list[dict[str, Any]],
    details_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    details_path.parent.mkdir(parents=True, exist_ok=True)
    variant_rows: dict[str, list[dict[str, Any]]] = {variant["name"]: [] for variant in variants}
    start = 0

    with details_path.open("w", encoding="utf-8") as handle:
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
            ltr_top_row = group_rows[ltr_top_idx]
            facts = _gate_facts(
                baseline_row,
                ltr_top_row,
                float(group_preds[baseline_idx]),
                float(group_preds[ltr_top_idx]),
            )
            baseline_metrics = _base_metrics("baseline", group_labels, baseline_order)
            raw_metrics = _base_metrics("raw_ltr", group_labels, raw_order)
            has_positive = bool(np.any(group_labels > 0))

            for variant in variants:
                allow_override, gate_reason = _gate_decision(variant=variant, ltr_top_idx=ltr_top_idx, facts=facts)
                order = _gated_order(raw_order, allow_override)
                gated_metrics = _base_metrics("gated", group_labels, order)
                gated_top_idx = int(order[0])
                gated_top_row = group_rows[gated_top_idx]

                detail = {
                    "split": split,
                    "variant": variant["name"],
                    "group_index": group_idx + 1,
                    "group_id": _clean(baseline_row.get("group_id")),
                    "sample_id": _clean(baseline_row.get("sample_id")),
                    "source_file": _clean(baseline_row.get("source_file")),
                    "project_name": _clean(baseline_row.get("project_name")),
                    "province": _clean(baseline_row.get("province")),
                    "query": _clean(baseline_row.get("query")),
                    "expected_ids": _clean(baseline_row.get("expected_ids")),
                    "has_positive": has_positive,
                    "positive_count": int(np.sum(group_labels > 0)),
                    "raw_same_as_baseline": ltr_top_idx == baseline_idx,
                    "gate_allowed": bool(allow_override),
                    "gate_reason": gate_reason,
                    "score_margin": round(float(facts["score_margin"]), 8),
                    "same_family": bool(facts["same_family"]),
                    "same_book": bool(facts["same_book"]),
                    "no_family_conflict": bool(facts["no_family_conflict"]),
                    "no_book_conflict": bool(facts["no_book_conflict"]),
                    "no_param_conflict": bool(facts["no_param_conflict"]),
                    "strict_same_family_book_param": bool(facts["strict_same_family_book_param"]),
                    "ltr_param_support": bool(facts["ltr_param_support"]),
                    "baseline_top_original_rank": 1,
                    "baseline_top_score": round(float(group_preds[baseline_idx]), 8),
                    "baseline_top_id": _clean(baseline_row.get("quota_id")),
                    "baseline_top_name": _clean(baseline_row.get("quota_name")),
                    "baseline_top_family": _clean(baseline_row.get("candidate_family")),
                    "baseline_top_book": _clean(baseline_row.get("quota_book")),
                    "baseline_top": _candidate_label(baseline_row),
                    "raw_ltr_top_original_rank": int(ltr_top_idx + 1),
                    "raw_ltr_top_score": round(float(group_preds[ltr_top_idx]), 8),
                    "raw_ltr_top_id": _clean(ltr_top_row.get("quota_id")),
                    "raw_ltr_top_name": _clean(ltr_top_row.get("quota_name")),
                    "raw_ltr_top_family": _clean(ltr_top_row.get("candidate_family")),
                    "raw_ltr_top_book": _clean(ltr_top_row.get("quota_book")),
                    "raw_ltr_top": _candidate_label(ltr_top_row),
                    "gated_top_original_rank": int(gated_top_idx + 1),
                    "gated_top_score": round(float(group_preds[gated_top_idx]), 8),
                    "gated_top_id": _clean(gated_top_row.get("quota_id")),
                    "gated_top_name": _clean(gated_top_row.get("quota_name")),
                    "gated_top_family": _clean(gated_top_row.get("candidate_family")),
                    "gated_top_book": _clean(gated_top_row.get("quota_book")),
                    "gated_top": _candidate_label(gated_top_row),
                    **baseline_metrics,
                    **raw_metrics,
                    **gated_metrics,
                }
                detail.update(
                    {
                        "hit1_delta_vs_baseline": int(detail["gated_hit1"]) - int(detail["baseline_hit1"]),
                        "hit5_delta_vs_baseline": int(detail["gated_hit5"]) - int(detail["baseline_hit5"]),
                        "raw_hit1_delta_vs_baseline": int(detail["raw_ltr_hit1"]) - int(detail["baseline_hit1"]),
                        "raw_hit5_delta_vs_baseline": int(detail["raw_ltr_hit5"]) - int(detail["baseline_hit5"]),
                    }
                )
                variant_rows[variant["name"]].append(detail)
                handle.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")
            start = stop

    summaries = [_summarize_variant(split, variant, variant_rows[variant["name"]]) for variant in variants]
    return summaries, variant_rows


def _make_variants(margins: list[float]) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = [
        {"name": "raw_ltr", "mode": "raw"},
        {"name": "strict_same_family_book_param", "mode": "strict"},
    ]
    for margin in margins:
        label = str(margin).replace(".", "_")
        variants.append(
            {
                "name": f"strict_same_family_book_param_or_margin_{label}",
                "mode": "strict_or_margin",
                "margin": margin,
            }
        )
    return variants


def _write_variants_csv(path: Path, summaries: list[dict[str, Any]]) -> None:
    fields = [
        "split",
        "variant",
        "mode",
        "margin",
        "groups",
        "baseline_hit1",
        "baseline_hit1_rate",
        "raw_ltr_hit1",
        "raw_ltr_hit1_rate",
        "raw_ltr_hit1_gain",
        "raw_ltr_hit1_loss",
        "raw_ltr_hit1_net",
        "gated_hit1",
        "gated_hit1_rate",
        "gated_hit1_gain",
        "gated_hit1_loss",
        "gated_hit1_net",
        "prevented_raw_hit1_loss",
        "blocked_raw_hit1_gain",
        "gated_hit5",
        "gated_hit5_rate",
        "gated_hit5_net",
        "raw_override_count",
        "gated_override_count",
        "gated_override_rate",
        "gated_ndcg5",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)


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
    rows = report["variant_summaries"]
    compact = [
        [
            item["split"],
            item["variant"],
            item["gated_hit1_rate"],
            item["gated_hit1_net"],
            item["gated_hit1_gain"],
            item["gated_hit1_loss"],
            item["prevented_raw_hit1_loss"],
            item["blocked_raw_hit1_gain"],
            item["gated_hit5_rate"],
            item["gated_hit5_net"],
            item["gated_override_rate"],
        ]
        for item in rows
    ]
    lines = [
        "# Goal LTR Safety Gate Summary",
        "",
        "Stage 2.2 offline safety-gate experiment. It does not modify the search pipeline and does not connect LTR online.",
        "",
        "Gate rule: LTR may override baseline Top1 only when the LTR top candidate is strict same family/book with no family/book/parameter conflict, or when the LTR score margin over baseline Top1 reaches the variant threshold.",
        "",
        "When the gate blocks an override, baseline Top1 is kept and the remaining candidates are ordered by LTR score for Top5 diagnostics.",
        "",
        "## Inputs",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["model_path", report["model_path"]],
                ["whitelist", report["whitelist"]],
                ["splits", ", ".join(report["splits"])],
                ["features", len(report["features"])],
                ["elapsed_sec", report["elapsed_sec"]],
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
                    "top1_rate",
                    "top1_net",
                    "gain",
                    "loss",
                    "prevented_loss",
                    "blocked_gain",
                    "top5_rate",
                    "top5_net",
                    "override_rate",
                ],
                *compact,
            ]
        ),
        "",
        "## Notes",
        "",
        "- This is an offline gate experiment only.",
        "- Heldout/hard are evaluation views; do not treat the best heldout threshold as a trained production threshold yet.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Goal LTR v1 offline safety gates")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--whitelist", default=str(DEFAULT_WHITELIST))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--margins", nargs="+", type=float, default=[0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    features = _load_feature_whitelist(Path(args.whitelist))
    booster = lgb.Booster(model_file=str(Path(args.model)))
    variants = _make_variants(args.margins)

    variant_summaries: list[dict[str, Any]] = []
    detail_paths: dict[str, str] = {}
    for split in args.splits:
        x, y, group, feature_rows = _load_split(data_dir, split, features)
        preds = booster.predict(x, num_iteration=booster.current_iteration())
        details_path = Path(args.details_dir) / f"goal_ltr_safety_gate_details_{split}.jsonl"
        summaries, _variant_rows = _evaluate_split(
            split=split,
            labels=y,
            groups=group,
            feature_rows=feature_rows,
            preds=preds,
            variants=variants,
            details_path=details_path,
        )
        variant_summaries.extend(summaries)
        detail_paths[split] = str(details_path)

    report = {
        "stage": "Goal LTR v1 / stage 2.2 offline safety gate trial",
        "no_search_integration": True,
        "model_path": str(Path(args.model)),
        "whitelist": str(Path(args.whitelist)),
        "splits": args.splits,
        "features": features,
        "variants": variants,
        "variant_summaries": variant_summaries,
        "details_jsonl": detail_paths,
        "variants_csv": args.variants_csv,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)
    _write_variants_csv(Path(args.variants_csv), variant_summaries)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_search_integration": True,
                    "splits": args.splits,
                    "variants": [variant["name"] for variant in variants],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "variant_summaries": variant_summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
